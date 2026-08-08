"""Ownership, DIS-Schutz und Secret-Abweisung fuer AI-Memory."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import AiMemoryEntry, AiMemoryPreference, User
from services import audit_service, permission_service
from services.ai_context_service import redact_sensitive_text
from services.dis_client import DisClient


MAX_ENTRIES_PER_SCOPE = 100
MAX_CONTEXT_CHARS = 6_000


def _aad(entry_id: str) -> str:
    return f"msm:ai:memory:{entry_id}"


def scope_identity(
    db: Session, user: User, scope: str, server_id: int | None
) -> tuple[str, int | None, int | None]:
    if scope == "user":
        if server_id is not None:
            raise HTTPException(status_code=422, detail="User-Memory akzeptiert keinen Server")
        return f"user:{user.id}", user.id, None
    if scope == "server":
        if server_id is None or not permission_service.has_server_permission(
            db=db, user=user, server_id=server_id, key="server.view"
        ):
            raise HTTPException(status_code=404, detail="Server nicht gefunden")
        return f"server:{server_id}:user:{user.id}", user.id, server_id
    if scope == "panel":
        if server_id is not None:
            raise HTTPException(status_code=422, detail="Panel-Memory akzeptiert keinen Server")
        return "panel", None, None
    raise HTTPException(status_code=422, detail="Unbekannter Memory-Scope")


def preference(db: Session, user_id: int) -> bool:
    row = db.get(AiMemoryPreference, user_id)
    return True if row is None else row.enabled


def set_preference(db: Session, user: User, enabled: bool) -> bool:
    row = db.get(AiMemoryPreference, user.id)
    if row is None:
        row = AiMemoryPreference(user_id=user.id, enabled=enabled)
        db.add(row)
    else:
        row.enabled = enabled
        row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return row.enabled


def _safe_value(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 2_000:
        raise HTTPException(status_code=422, detail="Memory-Inhalt ist leer oder zu gross")
    if redact_sensitive_text(normalized) != normalized:
        raise HTTPException(status_code=422, detail="Memory darf keine Zugangsdaten enthalten")
    return normalized


def list_entries(db: Session, user: User, scope: str, server_id: int | None) -> list[tuple[AiMemoryEntry, str]]:
    identity, _, _ = scope_identity(db, user, scope, server_id)
    rows = db.query(AiMemoryEntry).filter(AiMemoryEntry.scope_identity == identity).order_by(AiMemoryEntry.key).all()
    return [(row, DisClient.decrypt(row.value_encrypted, aad=_aad(row.id))) for row in rows]


def upsert_entry(
    db: Session, *, user: User, scope: str, server_id: int | None, key: str, value: str
) -> tuple[AiMemoryEntry, str]:
    identity, owner_id, normalized_server_id = scope_identity(db, user, scope, server_id)
    if scope == "panel" and not permission_service.has_global_permission(db, user, "panel.settings.write"):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    safe_value = _safe_value(value)
    row = db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == identity, AiMemoryEntry.key == key
    ).first()
    action = "ai.memory.updated"
    if row is None:
        if db.query(AiMemoryEntry).filter(AiMemoryEntry.scope_identity == identity).count() >= MAX_ENTRIES_PER_SCOPE:
            raise HTTPException(status_code=409, detail="Memory-Scope ist voll")
        row = AiMemoryEntry(
            id=str(uuid4()), owner_user_id=owner_id, server_id=normalized_server_id,
            scope=scope, scope_identity=identity, key=key, value_encrypted="",
        )
        db.add(row)
        action = "ai.memory.created"
    row.value_encrypted = DisClient.encrypt(safe_value, aad=_aad(row.id))
    row.updated_at = datetime.now(timezone.utc)
    audit_service.record_privileged_action(
        db, user_id=user.id, action=action, target_type="ai_memory", target_id=row.id,
        details={"scope": scope}, origin="direct",
    )
    try:
        db.commit()
    except IntegrityError as exc:
        # Zwei parallele Schreibvorgaenge auf denselben (scope, key). Die
        # UNIQUE-Bedingung hat den Verlierer abgewiesen. Das ist ein
        # verstaendlicher Konflikt und kein Serverfehler: der naechste Versuch
        # findet die Zeile vor und nimmt den Update-Zweig.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Memory-Eintrag wurde parallel geaendert. Bitte erneut versuchen.",
        ) from exc
    db.refresh(row)
    return row, safe_value


def delete_entry(db: Session, user: User, entry_id: str) -> None:
    try:
        canonical = str(UUID(entry_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=404, detail="Memory-Eintrag nicht gefunden") from exc
    row = db.get(AiMemoryEntry, canonical)
    if row is None:
        raise HTTPException(status_code=404, detail="Memory-Eintrag nicht gefunden")
    if row.scope == "panel":
        allowed = permission_service.has_global_permission(db, user, "panel.settings.write")
    else:
        allowed = row.owner_user_id == user.id and (
            row.server_id is None or permission_service.has_server_permission(db, user, row.server_id, "server.view")
        )
    if not allowed:
        raise HTTPException(status_code=404, detail="Memory-Eintrag nicht gefunden")
    audit_service.record_privileged_action(
        db, user_id=user.id, action="ai.memory.deleted", target_type="ai_memory",
        target_id=row.id, details={"scope": row.scope}, origin="direct",
    )
    db.delete(row)
    db.commit()


def provider_memory_context(db: Session, user: User, server_id: int | None) -> str | None:
    if not preference(db, user.id):
        return None
    identities = ["panel", f"user:{user.id}"]
    if server_id is not None:
        identities.append(f"server:{server_id}:user:{user.id}")
    rows = db.query(AiMemoryEntry).filter(AiMemoryEntry.scope_identity.in_(identities)).order_by(
        AiMemoryEntry.scope, AiMemoryEntry.key
    ).all()
    lines: list[str] = []
    used = 0
    for row in rows:
        value = DisClient.decrypt(row.value_encrypted, aad=_aad(row.id))
        # Der Block ist zeilenbasiert und jede Zeile traegt ihren Scope. Ein Wert
        # mit Zeilenumbruch koennte deshalb beliebig viele gefaelschte
        # "[panel] ..."-Zeilen vortaeuschen — ein Benutzer wuerde sich damit im
        # eigenen Kontext panelweite Vorgaben andichten. Der Schluessel ist
        # bereits auf [A-Za-z0-9_.-] begrenzt (schemas/ai_memory.py), der Wert
        # ist es bewusst nicht: er soll frei formulierbar bleiben.
        flattened = " ".join(str(value).splitlines())
        line = f"[{row.scope}] {row.key}: {flattened}"
        if used + len(line) > MAX_CONTEXT_CHARS:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines) or None
