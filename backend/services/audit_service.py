"""Zentrales Schreiben von Audit-Eintraegen ohne Secrets.

KISS: ein Helper fuer privilegierte SaaS-Aktionen. Kein zweiter Event-Bus.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from models import AuditLog

# Keys / Patterns, die nie in audit_logs.details landen duerfen.
_SECRET_KEY_RE = re.compile(
    r"(password|passwd|secret|token|authorization|api[_-]?key|credential)",
    re.IGNORECASE,
)
_MAX_DETAILS_LEN = 500
AUDIT_ORIGINS = frozenset({"direct", "ai", "external", "system"})


def _redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """Entfernt secret-aehnliche Schluessel aus einem Dict fuer Audit-Details."""
    safe: dict[str, Any] = {}
    for key, value in data.items():
        if _SECRET_KEY_RE.search(str(key)):
            safe[str(key)] = "[redacted]"
            continue
        if isinstance(value, dict):
            safe[str(key)] = _redact_mapping(value)
        elif isinstance(value, str) and len(value) > 64 and _SECRET_KEY_RE.search(key):
            safe[str(key)] = "[redacted]"
        else:
            # Keine langen freien Strings (SQL, dumps) ins Audit.
            if isinstance(value, str) and len(value) > 200:
                safe[str(key)] = value[:200] + "…"
            else:
                safe[str(key)] = value
    return safe


def sanitize_audit_details(details: str | dict[str, Any] | None) -> str | None:
    """Normalisiert Audit-Details und entfernt erkennbare Secrets."""
    if details is None:
        return None
    if isinstance(details, dict):
        text = json.dumps(_redact_mapping(details), ensure_ascii=True, sort_keys=True, default=str)
    else:
        text = str(details).strip()
        # Grobe Absicherung: bekannte Secret-Praefixe in freiem Text maskieren.
        if "password=" in text.lower() or "token=" in text.lower():
            text = re.sub(
                r"(?i)(password|token|secret|authorization)\s*=\s*\S+",
                r"\1=[redacted]",
                text,
            )
    if not text:
        return None
    if len(text) > _MAX_DETAILS_LEN:
        return text[:_MAX_DETAILS_LEN] + "…"
    return text


def record_privileged_action(
    db: Session,
    *,
    user_id: int | None,
    action: str,
    target_type: str | None = None,
    target_id: str | int | None = None,
    details: str | dict[str, Any] | None = None,
    origin: str = "direct",
    correlation_id: str | UUID | None = None,
    commit: bool = False,
) -> AuditLog:
    """Schreibt einen AuditLog-Eintrag fuer privilegierte Operator-Aktionen.

    Speichert wer/wann/was/Ziel. Nie Passwoerter, Tokens oder SQL-Payloads.
    Bei commit=False bleibt der Eintrag in der offenen Transaktion des Callers.
    """
    action_clean = (action or "").strip()
    if not action_clean or len(action_clean) > 64:
        raise ValueError("Ungueltiger Audit-Action-Key.")
    if target_type is not None and len(target_type) > 64:
        raise ValueError("Ungueltiger Audit-Target-Typ.")
    origin_clean = (origin or "").strip().lower()
    if origin_clean not in AUDIT_ORIGINS:
        raise ValueError("Ungueltige Audit-Herkunft.")
    try:
        correlation_clean = str(UUID(str(correlation_id))) if correlation_id else str(uuid4())
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Ungueltige Audit-Korrelations-ID.") from exc

    entry = AuditLog(
        user_id=user_id,
        action=action_clean,
        target_type=target_type,
        # Einheitlich als Text: die Aufrufer uebergeben teils Zahlen
        # (Benutzer, Server), teils UUIDs (Memory, Skills, Anhaenge).
        target_id=None if target_id is None else str(target_id),
        origin=origin_clean,
        correlation_id=correlation_clean,
        details=sanitize_audit_details(details),
    )
    db.add(entry)
    if commit:
        try:
            db.commit()
            db.refresh(entry)
        except Exception:
            db.rollback()
            raise
    return entry


# Ein Lese-Eintrag je Benutzer, Ziel und Zugriffsart in diesem Fenster. Ohne
# den Deckel fuellt das 5-Sekunden-Polling der Oberflaeche die Tabelle in
# Stunden — der Erkenntniswert bleibt derselbe: "hat in dem Zeitraum gelesen".
READ_ACCESS_DEDUPE = timedelta(minutes=10)

READ_ACCESS_ACTIONS = frozenset(
    {"server.console.read", "server.logs.read", "server.files.read"}
)


def record_read_access(
    db: Session,
    *,
    user_id: int,
    server_id: int,
    action: str,
    details: str | dict[str, Any] | None = None,
    origin: str = "direct",
) -> AuditLog | None:
    """Protokolliert Lesezugriff auf Konsole, Logs oder Dateien eines Servers.

    Wird an den Panel-Routern aufgerufen, nie in der Rechtepruefung — die
    bleibt read-only. KI-Laeufe protokollieren ihre Werkzeugaufrufe bereits im
    Lauf selbst und laufen nicht ueber diesen Helfer. Gibt None zurueck, wenn
    im Dedupe-Fenster bereits ein gleicher Eintrag existiert.
    """
    if action not in READ_ACCESS_ACTIONS:
        raise ValueError("Unbekannte Lesezugriffs-Aktion.")
    cutoff = datetime.now(timezone.utc) - READ_ACCESS_DEDUPE
    recent = (
        db.query(AuditLog.id)
        .filter(
            AuditLog.user_id == user_id,
            AuditLog.action == action,
            AuditLog.target_type == "server",
            AuditLog.target_id == str(server_id),
            AuditLog.created_at >= cutoff,
        )
        .first()
    )
    if recent is not None:
        return None
    return record_privileged_action(
        db,
        user_id=user_id,
        action=action,
        target_type="server",
        target_id=server_id,
        details=details,
        origin=origin,
        commit=True,
    )


def list_audit_logs(
    db: Session,
    *,
    limit: int = 50,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | int | None = None,
) -> list[AuditLog]:
    """Listet die neuesten Audit-Eintraege mit optionalen Filtern."""
    limit = min(max(int(limit), 1), 200)
    q = db.query(AuditLog).order_by(AuditLog.id.desc())
    if action:
        q = q.filter(AuditLog.action == action.strip())
    if target_type:
        q = q.filter(AuditLog.target_type == target_type.strip())
    if target_id is not None:
        q = q.filter(AuditLog.target_id == str(target_id))
    return q.limit(limit).all()
