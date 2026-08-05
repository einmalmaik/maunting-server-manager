"""Allowlist, Preview, Bestaetigung und Ausfuehrung fuer AI-Aktionen."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import difflib
import hashlib
import hmac
import json
from pathlib import PurePosixPath
import secrets
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import AiActionProposal, AiConversation, Server, User
from services import audit_service, permission_service
from services.actor_context import ActorContext
from services.ai_context_service import redact_sensitive_text
from services.dis_client import DisClient
from services.server_file_access_service import read_server_text, write_server_text


CONFIRMATION_TTL = timedelta(minutes=5)
MAX_CONFIG_CHARS = 64_000
MAX_DIFF_CHARS = 16_000
MAX_DIFF_LINES = 200
CONFIG_EXTENSIONS = {
    ".cfg", ".conf", ".ini", ".json", ".properties", ".toml", ".txt", ".yaml", ".yml"
}
WRITE_TOOLS = {"propose_server_lifecycle", "propose_backup", "propose_config_update"}
READ_TOOLS = {
    "read_server_status",
    "read_server_capacity",
    "read_server_logs",
    "read_config",
}


def provider_tool_definitions() -> list[dict]:
    """Feste OpenAI-Tool-Allowlist; keine freie Command-Ausfuehrung."""
    return [
        {
            "type": "function",
            "function": {
                "name": "read_server_status",
                "description": "Liest den minimierten Status des aktuellen Servers.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_server_capacity",
                "description": "Liest minimierte, zuletzt bekannte Kapazitaetswerte des aktuellen Servers und Nodes.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_server_logs",
                "description": "Liest einen begrenzten, redigierten Log-Ausschnitt des aktuellen Servers.",
                "parameters": {
                    "type": "object",
                    "properties": {"lines": {"type": "integer", "minimum": 1, "maximum": 200}},
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_config",
                "description": "Liest eine erlaubte Text-Konfigurationsdatei revisionssicher.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "maxLength": 256}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "propose_server_lifecycle",
                "description": "Schlaegt Start, Stop oder Neustart zur manuellen Bestaetigung vor.",
                "parameters": {
                    "type": "object",
                    "properties": {"operation": {"type": "string", "enum": ["start", "stop", "restart"]}},
                    "required": ["operation"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "propose_backup",
                "description": "Schlaegt ein Server-Backup zur manuellen Bestaetigung vor.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "propose_config_update",
                "description": "Schlaegt eine revisionsgebundene Config-Aenderung vor. Niemals Secrets einfuegen.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "maxLength": 256},
                        "content": {"type": "string", "maxLength": MAX_CONFIG_CHARS},
                        "expected_revision": {"type": ["string", "null"]},
                    },
                    "required": ["path", "content", "expected_revision"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def execute_read_tool(
    db: Session,
    *,
    user: User,
    conversation: AiConversation,
    tool_name: str,
    arguments: dict,
) -> dict:
    if conversation.server_id is None or tool_name not in READ_TOOLS:
        raise AiActionValidationError("Read-Tool ist in diesem Kontext nicht erlaubt")
    server = db.get(Server, conversation.server_id)
    if server is None or not permission_service.has_server_permission(
        db, user, server.id, "server.view"
    ):
        raise AiActionValidationError("Server nicht gefunden")
    if tool_name == "read_server_status":
        if arguments:
            raise AiActionValidationError("Status-Tool akzeptiert keine Argumente")
        return {
            "server_id": server.id,
            "game": server.game_type,
            "status": server.status,
            "cpu_limit_percent": server.cpu_limit_percent,
            "ram_limit_mb": server.ram_limit_mb,
            "disk_limit_gb": server.disk_limit_gb,
        }
    if tool_name == "read_server_capacity":
        if arguments:
            raise AiActionValidationError("Kapazitaets-Tool akzeptiert keine Argumente")
        node = server.node
        if node is None:
            return {"server_id": server.id, "node_status": "unassigned"}
        from services.node_capacity import allocatable_ram_mb, sum_allocated_ram_mb

        allocated_ram_mb = sum_allocated_ram_mb(db, node.id)
        return {
            "server_id": server.id,
            "node_status": node.status,
            "cpu_total": node.cpu_total,
            "cpu_percent": node.cpu_percent,
            "ram_total_bytes": node.ram_total,
            "ram_used_bytes": node.ram_used,
            "ram_allocated_mb": allocated_ram_mb,
            "ram_allocatable_mb": allocatable_ram_mb(node, allocated_ram_mb),
            "disk_total_bytes": node.disk_total,
            "disk_used_bytes": node.disk_used,
        }
    if tool_name == "read_server_logs":
        if set(arguments) - {"lines"}:
            raise AiActionValidationError("Log-Tool hat ungueltige Argumente")
        lines = arguments.get("lines", 100)
        if not isinstance(lines, int) or isinstance(lines, bool) or not 1 <= lines <= 200:
            raise AiActionValidationError("Ungueltige Log-Zeilenanzahl")
        from services import docker_service

        content = docker_service.logs(server.container_name, lines=lines, node=server.node)
        redacted = redact_sensitive_text(content)
        return {
            "server_id": server.id,
            "lines_requested": lines,
            "content": redacted[-24_000:],
            "truncated": len(redacted) > 24_000,
            "redacted": redacted != content,
        }
    if set(arguments) != {"path"} or not permission_service.has_server_permission(
        db, user, server.id, "server.files.read"
    ):
        raise AiActionValidationError("Config-Lesezugriff ist nicht erlaubt")
    path = _config_path(arguments["path"])
    result = read_server_text(db, server_id=server.id, relative_path=path)
    content = str(result["content"])
    redacted = redact_sensitive_text(content)
    return {
        "path": path,
        "revision": result["revision"],
        "content": redacted[:24_000],
        "truncated": len(redacted) > 24_000,
        "redacted": redacted != content,
    }


class AiActionValidationError(ValueError):
    pass


class AiActionStateError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _aad(proposal_id: str) -> str:
    return f"msm:ai:action-proposal:v1:{proposal_id}"


def _json_object(value: str) -> dict:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AiActionStateError("AI_ACTION_PAYLOAD_INVALID") from exc
    if not isinstance(decoded, dict):
        raise AiActionStateError("AI_ACTION_PAYLOAD_INVALID")
    return decoded


def _permission_for(tool_name: str, payload: dict) -> str:
    if tool_name == "propose_server_lifecycle":
        return {
            "start": "server.start",
            "stop": "server.stop",
            "restart": "server.restart",
        }.get(str(payload.get("operation")), "")
    if tool_name == "propose_backup":
        return "server.backups.create"
    if tool_name == "propose_config_update":
        return "server.files.write"
    return ""


def _require_tool_permission(
    db: Session, user: User, server_id: int, tool_name: str, payload: dict
) -> None:
    permission = _permission_for(tool_name, payload)
    if not permission or not permission_service.has_server_permission(
        db, user, server_id, permission
    ):
        raise AiActionValidationError("AI-Aktion ist nicht erlaubt")
    if tool_name == "propose_config_update" and not permission_service.has_server_permission(
        db, user, server_id, "server.files.read"
    ):
        raise AiActionValidationError("Config-Vorschlag benoetigt Lese- und Schreibrecht")


def _config_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or "\\" in value:
        raise AiActionValidationError("Ungueltiger Config-Pfad")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() not in CONFIG_EXTENSIONS:
        raise AiActionValidationError("Config-Pfad ist nicht erlaubt")
    return path.as_posix()


def _config_payload(db: Session, server_id: int, arguments: dict) -> tuple[dict, dict, str | None]:
    if set(arguments) != {"path", "content", "expected_revision"}:
        raise AiActionValidationError("Config-Tool hat ungueltige Argumente")
    path = _config_path(arguments["path"])
    content = arguments["content"]
    expected = arguments["expected_revision"]
    if not isinstance(content, str) or len(content) > MAX_CONFIG_CHARS:
        raise AiActionValidationError("Config-Inhalt ist zu gross oder ungueltig")
    if redact_sensitive_text(content) != content:
        raise AiActionValidationError("Config-Vorschlag enthaelt moegliche Zugangsdaten")
    if expected is not None and (
        not isinstance(expected, str) or not expected.startswith("sha256:") or len(expected) != 71
    ):
        raise AiActionValidationError("Ungueltige Config-Revision")

    try:
        current = read_server_text(db, server_id=server_id, relative_path=path)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        current = None
    current_revision = str(current["revision"]) if current is not None else None
    if current_revision != expected:
        raise AiActionValidationError("Config wurde seit der Analyse veraendert")
    old_content = str(current["content"]) if current is not None else ""
    # Auch entfernte Zeilen koennen Zugangsdaten enthalten. Deshalb wird nur
    # aus redigierten Inhalten eine sichtbare Vorschau erzeugt.
    preview_old_content = redact_sensitive_text(old_content)
    preview_content = redact_sensitive_text(content)
    diff_lines = list(difflib.unified_diff(
        preview_old_content.splitlines(),
        preview_content.splitlines(),
        fromfile=f"{path}:vorher",
        tofile=f"{path}:nachher",
        lineterm="",
    ))
    truncated = len(diff_lines) > MAX_DIFF_LINES
    diff = "\n".join(diff_lines[:MAX_DIFF_LINES])[:MAX_DIFF_CHARS]
    preview = {
        "path": path,
        "change": "create" if current is None else "update",
        "diff": diff,
        "diff_truncated": truncated or len("\n".join(diff_lines[:MAX_DIFF_LINES])) > MAX_DIFF_CHARS,
        "restart_required": True,
    }
    return {
        "path": path,
        "content": content,
        "create_only": current is None,
    }, preview, current_revision


def create_proposal(
    db: Session,
    *,
    user: User,
    conversation: AiConversation,
    tool_name: str,
    arguments: dict,
    correlation_id: str,
) -> AiActionProposal:
    if tool_name not in WRITE_TOOLS or conversation.server_id is None:
        raise AiActionValidationError("Tool ist in diesem Kontext nicht erlaubt")
    server = db.get(Server, conversation.server_id)
    if server is None:
        raise AiActionValidationError("Server nicht gefunden")

    if tool_name == "propose_server_lifecycle":
        if set(arguments) != {"operation"} or arguments.get("operation") not in {"start", "stop", "restart"}:
            raise AiActionValidationError("Ungueltige Lifecycle-Aktion")
        payload = {"operation": arguments["operation"]}
        preview = {
            "operation": arguments["operation"],
            "current_status": server.status,
            "restart_required": arguments["operation"] == "restart",
        }
        expected_revision = None
    elif tool_name == "propose_backup":
        if arguments:
            raise AiActionValidationError("Backup-Tool akzeptiert keine Argumente")
        payload = {}
        preview = {"operation": "backup", "current_status": server.status}
        expected_revision = None
    else:
        payload, preview, expected_revision = _config_payload(db, server.id, arguments)

    _require_tool_permission(db, user, server.id, tool_name, payload)
    proposal_id = str(uuid4())
    encrypted = DisClient.encrypt(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        aad=_aad(proposal_id),
    )
    proposal = AiActionProposal(
        id=proposal_id,
        conversation_id=conversation.id,
        user_id=user.id,
        server_id=server.id,
        tool_name=tool_name,
        payload_encrypted=encrypted,
        preview_json=json.dumps(preview, ensure_ascii=True, separators=(",", ":")),
        expected_revision=expected_revision,
        requires_confirmation=True,
        correlation_id=str(UUID(correlation_id)),
    )
    db.add(proposal)
    db.flush()
    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="ai.action.proposed",
        target_type="server",
        target_id=server.id,
        details={"proposal_id": proposal.id, "tool": tool_name},
        origin="ai",
        correlation_id=proposal.correlation_id,
    )
    return proposal


def owned_proposal(db: Session, proposal_id: str, user: User) -> AiActionProposal | None:
    try:
        canonical = str(UUID(proposal_id))
    except (TypeError, ValueError, AttributeError):
        return None
    proposal = db.query(AiActionProposal).filter(
        AiActionProposal.id == canonical,
        AiActionProposal.user_id == user.id,
    ).first()
    if proposal is None or not permission_service.has_server_permission(
        db, user, proposal.server_id, "server.view"
    ):
        return None
    return proposal


def confirm_proposal(
    db: Session, *, proposal_id: str, user: User, now: datetime | None = None
) -> tuple[AiActionProposal, str]:
    proposal = owned_proposal(db, proposal_id, user)
    if proposal is None:
        raise AiActionStateError("AI_ACTION_NOT_FOUND")
    proposal = db.query(AiActionProposal).filter(AiActionProposal.id == proposal.id).with_for_update().one()
    if proposal.status != "proposed":
        raise AiActionStateError("AI_ACTION_NOT_PROPOSED")
    payload = _json_object(DisClient.decrypt(proposal.payload_encrypted, aad=_aad(proposal.id)))
    try:
        _require_tool_permission(db, user, proposal.server_id, proposal.tool_name, payload)
    except AiActionValidationError as exc:
        raise AiActionStateError("AI_ACTION_ACCESS_REVOKED") from exc
    token = secrets.token_urlsafe(32)
    current = now or datetime.now(timezone.utc)
    proposal.confirmation_token_hash = hashlib.sha256(token.encode()).hexdigest()
    proposal.confirmation_expires_at = current + CONFIRMATION_TTL
    proposal.confirmed_at = current
    proposal.status = "confirmed"
    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="ai.action.confirmed",
        target_type="server",
        target_id=proposal.server_id,
        details={"proposal_id": proposal.id, "tool": proposal.tool_name, "confirmed": True},
        origin="ai",
        correlation_id=proposal.correlation_id,
    )
    db.commit()
    db.refresh(proposal)
    return proposal, token


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def execute_proposal(
    db: Session,
    *,
    proposal_id: str,
    user: User,
    confirmation_token: str,
    now: datetime | None = None,
) -> tuple[AiActionProposal, dict]:
    proposal = owned_proposal(db, proposal_id, user)
    if proposal is None:
        raise AiActionStateError("AI_ACTION_NOT_FOUND")
    proposal = db.query(AiActionProposal).filter(AiActionProposal.id == proposal.id).with_for_update().one()
    current = now or datetime.now(timezone.utc)
    token_hash = hashlib.sha256(confirmation_token.encode()).hexdigest()
    if proposal.status != "confirmed" or not proposal.confirmation_token_hash:
        raise AiActionStateError("AI_ACTION_NOT_CONFIRMED")
    if proposal.confirmation_expires_at is None or _utc(proposal.confirmation_expires_at) <= current:
        proposal.status = "expired"
        proposal.confirmation_token_hash = None
        db.commit()
        raise AiActionStateError("AI_ACTION_CONFIRMATION_EXPIRED")
    if not hmac.compare_digest(proposal.confirmation_token_hash, token_hash):
        raise AiActionStateError("AI_ACTION_CONFIRMATION_INVALID")
    active_user = db.query(User).filter(User.id == user.id, User.is_active.is_(True)).first()
    if active_user is None:
        raise AiActionStateError("AI_ACTION_ACCESS_REVOKED")
    payload = _json_object(DisClient.decrypt(proposal.payload_encrypted, aad=_aad(proposal.id)))
    try:
        _require_tool_permission(db, active_user, proposal.server_id, proposal.tool_name, payload)
    except AiActionValidationError as exc:
        raise AiActionStateError("AI_ACTION_ACCESS_REVOKED") from exc

    proposal.status = "executing"
    proposal.confirmation_token_hash = None
    db.commit()
    try:
        if proposal.tool_name == "propose_server_lifecycle":
            from services.server_action_service import request_lifecycle_operation

            result = request_lifecycle_operation(
                db,
                server_id=proposal.server_id,
                operation=str(payload["operation"]),
                actor=ActorContext.for_user(
                    active_user, origin="ai", correlation_id=proposal.correlation_id
                ),
                idempotency_key=proposal.id,
            )
            proposal.task_id = result.get("task_id")
        elif proposal.tool_name == "propose_backup":
            from services.backup_orchestrator import create_server_backup
            from services.server_lifecycle_service import get_server_lifecycle_lock

            lock = get_server_lifecycle_lock(proposal.server_id)
            if not lock.acquire(blocking=False):
                raise AiActionStateError("AI_ACTION_SERVER_BUSY")
            try:
                backup = create_server_backup(
                    proposal.server_id, db, name="AI-confirmed snapshot"
                )
                result = {"backup_id": backup.id}
            finally:
                lock.release()
        elif proposal.tool_name == "propose_config_update":
            from services.server_lifecycle_service import get_server_lifecycle_lock

            lock = get_server_lifecycle_lock(proposal.server_id)
            if not lock.acquire(blocking=False):
                raise AiActionStateError("AI_ACTION_SERVER_BUSY")
            try:
                result = write_server_text(
                    db,
                    user=active_user,
                    server_id=proposal.server_id,
                    relative_path=str(payload["path"]),
                    content=str(payload["content"]),
                    expected_revision=proposal.expected_revision,
                    create_only=bool(payload.get("create_only")),
                )
            finally:
                lock.release()
        else:
            raise AiActionStateError("AI_ACTION_TOOL_NOT_ALLOWED")

        proposal = db.get(AiActionProposal, proposal.id)
        assert proposal is not None
        proposal.status = "succeeded"
        proposal.executed_at = datetime.now(timezone.utc)
        audit_service.record_privileged_action(
            db,
            user_id=active_user.id,
            action="ai.action.executed",
            target_type="server",
            target_id=proposal.server_id,
            details={
                "proposal_id": proposal.id,
                "tool": proposal.tool_name,
                "confirmed": True,
                "succeeded": True,
                **({"task_id": proposal.task_id} if proposal.task_id else {}),
            },
            origin="ai",
            correlation_id=proposal.correlation_id,
        )
        db.commit()
        db.refresh(proposal)
        return proposal, result
    except Exception as exc:
        db.rollback()
        failed = db.get(AiActionProposal, proposal.id)
        if failed is not None:
            failed.status = "failed"
            failed.error_code = (
                exc.code if isinstance(exc, AiActionStateError) else "AI_ACTION_EXECUTION_FAILED"
            )
            failed.executed_at = datetime.now(timezone.utc)
            audit_service.record_privileged_action(
                db,
                user_id=active_user.id,
                action="ai.action.executed",
                target_type="server",
                target_id=failed.server_id,
                details={
                    "proposal_id": failed.id,
                    "tool": failed.tool_name,
                    "confirmed": True,
                    "succeeded": False,
                    "error_code": failed.error_code,
                },
                origin="ai",
                correlation_id=failed.correlation_id,
            )
            db.commit()
        if isinstance(exc, AiActionStateError):
            raise
        if isinstance(exc, HTTPException) and exc.status_code == 409:
            raise AiActionStateError("AI_ACTION_REVISION_CONFLICT") from exc
        raise AiActionStateError("AI_ACTION_EXECUTION_FAILED") from exc


def reconcile_interrupted_actions(db: Session) -> int:
    rows = db.query(AiActionProposal).filter(AiActionProposal.status == "executing").all()
    for row in rows:
        row.status = "failed"
        row.error_code = "AI_ACTION_INTERRUPTED"
        row.executed_at = datetime.now(timezone.utc)
    if rows:
        db.commit()
    return len(rows)
