"""Versionierte Skills, deren Schritte strikt auf MSM-Tools begrenzt sind."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import AiSkill, User
from services import audit_service
from services.ai_action_service import (
    CONFIG_EXTENSIONS,
    AiActionValidationError,
    create_proposal,
    execute_read_tool,
)
from services.ai_chat_service import get_or_create_primary_conversation
from services.ai_redaction import redact_sensitive_text
from services.ai_usage_service import complete_ai_usage, fail_ai_usage, reserve_ai_usage


# Bewusst eine eigene, engere Allowlist als der Chat. Ein Skill ist ein
# gespeicherter Ablauf, der spaeter ohne erneute Pruefung des Inhalts laeuft —
# `propose_config_update` und `propose_server_create` gehoeren deshalb nicht
# hinein, `search_workshop_mods` ebenso wenig (eine gespeicherte Suchanfrage ist
# kein wiederverwendbarer Ablauf).
SKILL_READ_TOOLS = {
    "read_server_status",
    "read_server_capacity",
    "read_server_logs",
    "read_config",
    "read_server_ports",
    "read_server_mods",
    "read_server_backups",
    "read_guardian_incidents",
    "read_ai_action_history",
    "read_mod_updates",
}
SKILL_WRITE_TOOLS = {"propose_server_lifecycle", "propose_backup"}
SKILL_TOOLS = SKILL_READ_TOOLS | SKILL_WRITE_TOOLS
# Diese Schritte nehmen keine Argumente entgegen.
SKILL_NO_ARGUMENT_TOOLS = (SKILL_READ_TOOLS - {"read_server_logs", "read_config"}) | {
    "propose_backup"
}


def _validate_step(step: dict) -> dict:
    if set(step) != {"tool_name", "arguments"}:
        raise HTTPException(status_code=422, detail="Skill-Schritt ist ungueltig")
    tool_name = step["tool_name"]
    arguments = step["arguments"]
    if tool_name not in SKILL_TOOLS or not isinstance(arguments, dict):
        raise HTTPException(status_code=422, detail="Skill-Tool ist nicht erlaubt")
    if redact_sensitive_text(json.dumps(arguments, ensure_ascii=True)) != json.dumps(
        arguments, ensure_ascii=True
    ):
        raise HTTPException(status_code=422, detail="Skill darf keine Zugangsdaten enthalten")
    if tool_name in SKILL_NO_ARGUMENT_TOOLS and arguments:
        raise HTTPException(status_code=422, detail="Skill-Schritt akzeptiert keine Argumente")
    if tool_name == "read_server_logs":
        if set(arguments) - {"lines"}:
            raise HTTPException(status_code=422, detail="Log-Schritt ist ungueltig")
        lines = arguments.get("lines", 100)
        if not isinstance(lines, int) or isinstance(lines, bool) or not 1 <= lines <= 200:
            raise HTTPException(status_code=422, detail="Log-Schritt ist ungueltig")
    if tool_name == "read_config":
        path = arguments.get("path")
        if set(arguments) != {"path"} or not isinstance(path, str) or len(path) > 256:
            raise HTTPException(status_code=422, detail="Config-Schritt ist ungueltig")
        if (
            path.startswith(("/", "\\"))
            or "\\" in path
            or ".." in path.split("/")
            or PurePosixPath(path).suffix.lower() not in CONFIG_EXTENSIONS
        ):
            raise HTTPException(status_code=422, detail="Config-Pfad ist nicht erlaubt")
    if tool_name == "propose_server_lifecycle" and (
        set(arguments) != {"operation"}
        or arguments.get("operation") not in {"start", "stop", "restart"}
    ):
        raise HTTPException(status_code=422, detail="Lifecycle-Schritt ist ungueltig")
    return {"tool_name": tool_name, "arguments": arguments}


def _steps(value: list[dict]) -> list[dict]:
    if not 1 <= len(value) <= 20:
        raise HTTPException(status_code=422, detail="Skill benoetigt 1 bis 20 Schritte")
    return [_validate_step(step) for step in value]


def response_steps(row: AiSkill) -> list[dict]:
    try:
        value = json.loads(row.steps_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Skill ist nicht verfuegbar") from exc
    if not isinstance(value, list):
        raise HTTPException(status_code=500, detail="Skill ist nicht verfuegbar")
    return _steps(value)


def latest_skills(db: Session, *, include_disabled: bool) -> list[AiSkill]:
    rows = db.query(AiSkill).order_by(AiSkill.skill_key, AiSkill.version.desc()).all()
    latest: dict[str, AiSkill] = {}
    for row in rows:
        latest.setdefault(row.skill_key, row)
    return [row for row in latest.values() if include_disabled or row.enabled]


def create_version(
    db: Session, *, user: User, skill_key: str, name: str, description: str,
    steps: list[dict], enabled: bool, require_existing: bool,
) -> AiSkill:
    latest = db.query(AiSkill).filter(AiSkill.skill_key == skill_key).order_by(
        AiSkill.version.desc()
    ).first()
    if require_existing and latest is None:
        raise HTTPException(status_code=404, detail="Skill nicht gefunden")
    if not require_existing and latest is not None:
        raise HTTPException(status_code=409, detail="Skill-Key existiert bereits")
    safe_name = name.strip()
    safe_description = description.strip()
    if (
        not safe_name
        or not safe_description
        or redact_sensitive_text(safe_name) != safe_name
        or redact_sensitive_text(safe_description) != safe_description
    ):
        raise HTTPException(status_code=422, detail="Skill-Metadaten sind ungueltig")
    normalized_steps = _steps(steps)
    row = AiSkill(
        id=str(uuid4()), skill_key=skill_key, version=(latest.version + 1 if latest else 1),
        name=safe_name, description=safe_description,
        steps_json=json.dumps(normalized_steps, ensure_ascii=True, separators=(",", ":")),
        enabled=enabled, created_by=user.id,
    )
    db.add(row)
    audit_service.record_privileged_action(
        db, user_id=user.id, action="ai.skill.version.created", target_type="ai_skill",
        target_id=row.id, details={"skill_key": skill_key, "version": row.version},
        origin="direct",
    )
    try:
        db.commit()
    except IntegrityError as exc:
        # Die Versionsnummer stammt aus einem ungesperrten SELECT. Legen zwei
        # Verwalter gleichzeitig eine neue Version an, weist uq_ai_skills_key_version
        # den Verlierer ab. Das ist ein Konflikt, kein Serverfehler.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Skill wurde parallel geaendert. Bitte erneut versuchen.",
        ) from exc
    db.refresh(row)
    return row


def get_skill(db: Session, skill_id: str) -> AiSkill:
    try:
        canonical = str(UUID(skill_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=404, detail="Skill nicht gefunden") from exc
    row = db.get(AiSkill, canonical)
    if row is None:
        raise HTTPException(status_code=404, detail="Skill nicht gefunden")
    return row


def _release_reservation(db: Session, correlation_id: str) -> None:
    """Gibt eine Reservierung frei, die einen Fehlschlag ueberlebt hat.

    Nach einem Rollback ist die Zeile in aller Regel ohnehin verschwunden, weil
    sie in derselben Transaktion entstand. Verlassen wollen wir uns darauf
    nicht: sobald ein Tool-Schritt zwischendurch committet, bliebe die
    Reservierung sonst dauerhaft auf `reserved` stehen und wuerde einen
    Nebenlaeufigkeitsplatz des Benutzers bis zum Prozessneustart belegen.
    """
    from models import AiUsageEvent

    try:
        event = (
            db.query(AiUsageEvent)
            .filter(AiUsageEvent.request_id == correlation_id)
            .first()
        )
        if event is not None and event.status == "reserved":
            fail_ai_usage(db, event)
            db.commit()
    except Exception:
        db.rollback()


def run_skill(
    db: Session, *, user: User, skill: AiSkill, server_id: int,
    correlation_id: str,
) -> tuple[list[dict], list[dict]]:
    """Fuehrt einen Skill gegen genau einen Server aus.

    Der Server kommt seit dem Einzelchat als Parameter und nicht mehr aus der
    Unterhaltung. Die Rechtepruefung bleibt dieselbe: jeder Schritt laeuft
    ueber `execute_read_tool` bzw. `create_proposal` und damit ueber
    `_resolve_server`.
    """
    latest = db.query(AiSkill).filter(AiSkill.skill_key == skill.skill_key).order_by(
        AiSkill.version.desc()
    ).first()
    if latest is None or latest.id != skill.id or not latest.enabled:
        raise HTTPException(status_code=409, detail="Skill ist deaktiviert")
    conversation = get_or_create_primary_conversation(db, user)

    # Ein Skill-Lauf ruft keinen Provider auf, verbraucht also keine Tokens —
    # er loest aber bis zu 20 Tool-Aufrufe gegen Docker, Dateisystem und Node
    # aus. Ohne Reservierung liefe er komplett an den Rollenkontingenten vorbei:
    # `requests_per_minute` und `concurrent_operations` haetten fuer Skills gar
    # keine Wirkung, obwohl Zielpunkt 6 sie fuer KI-Vorgaenge fordert. Deshalb
    # wird mit null Tokens reserviert: die Zaehl- und Nebenlaeufigkeitsgrenzen
    # greifen, die Token- und Kostengrenzen bleiben korrekt unberuehrt.
    usage_event = reserve_ai_usage(
        db,
        user,
        request_id=correlation_id,
        estimated_tokens=0,
        estimated_cost_microunits=0,
        server_id=server_id,
    )
    db.flush()

    read_results: list[dict] = []
    proposals = []
    # Jeder Schritt durchlaeuft dieselbe RBAC- und Allowlist-Pruefung wie ein
    # Chat-Tool-Call. Deren AiActionValidationError ist ein ValueError; ohne
    # diese Umsetzung haette FastAPI daraus einen 500 gemacht, obwohl es ein
    # regulaerer Berechtigungs- oder Validierungsfall ist.
    try:
        for index, step in enumerate(response_steps(skill), start=1):
            # Der Skill selbst speichert keine Server-ID — er ist ein Ablauf,
            # kein Serverbezug. Sie wird hier je Schritt eingesetzt und
            # anschliessend von `_resolve_server` gegen die Rechte geprueft.
            step_arguments = {**step["arguments"], "server_id": server_id}
            if step["tool_name"] in SKILL_READ_TOOLS:
                read_results.append({
                    "tool_name": step["tool_name"],
                    "result": execute_read_tool(
                        db, user=user,
                        tool_name=step["tool_name"], arguments=step_arguments,
                    ),
                })
            else:
                proposals.append(create_proposal(
                    db, user=user, conversation=conversation, tool_name=step["tool_name"],
                    arguments=step_arguments, correlation_id=correlation_id,
                    # Ein Skill-Schritt braucht keine vom Modell formulierte
                    # Begruendung — seine Herkunft ist die praezisere Angabe.
                    rationale_fallback=(
                        f"Schritt {index} aus Skill \"{skill.name}\" (Version {skill.version})",
                        "Der Ablauf wurde vom Betreiber als Skill hinterlegt und geprueft.",
                    ),
                ))
    except AiActionValidationError as exc:
        db.rollback()
        _release_reservation(db, correlation_id)
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        # Eine haengende Reservierung wuerde sonst dauerhaft einen
        # Nebenlaeufigkeitsplatz des Benutzers blockieren.
        _release_reservation(db, correlation_id)
        raise
    complete_ai_usage(db, usage_event, actual_tokens=0, actual_cost_microunits=0)
    audit_service.record_privileged_action(
        db, user_id=user.id, action="ai.skill.run", target_type="ai_skill",
        target_id=skill.id, details={"version": skill.version, "proposal_count": len(proposals)},
        origin="ai", correlation_id=correlation_id,
    )
    db.commit()
    return read_results, [
        {"id": row.id, "tool_name": row.tool_name, "preview": json.loads(row.preview_json), "status": row.status}
        for row in proposals
    ]
