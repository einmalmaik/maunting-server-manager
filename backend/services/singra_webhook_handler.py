"""Inbound Singra widget webhook verification and side-effects."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from models.singra_webhook_event import SingraWebhookEvent
from services.email_service import EmailService
from services.singra_webhook_secret_service import resolve_secret

_log = logging.getLogger("msm.singra_webhook")

_MAX_SKEW_SECONDS = 300
_MAX_BODY_BYTES = 64 * 1024


def _parse_signature_header(header: str | None) -> list[str]:
    if not header:
        return []
    parts: list[str] = []
    for part in header.split(","):
        piece = part.strip()
        if piece.lower().startswith("sha256="):
            piece = piece[7:]
        if piece:
            parts.append(piece)
    return parts


def verify_request(
    raw_body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
) -> str | None:
    """Returns None if valid, else an error code string."""
    if len(raw_body) > _MAX_BODY_BYTES:
        return "payload_too_large"
    secret = resolve_secret()
    if not secret:
        return "secret_not_configured"

    if not timestamp_header:
        return "missing_timestamp"
    
    ts = None
    try:
        ts = int(timestamp_header)
    except ValueError:
        try:
            iso_str = timestamp_header
            if iso_str.endswith("Z"):
                iso_str = iso_str[:-1] + "+00:00"
            dt = datetime.fromisoformat(iso_str)
            ts = int(dt.timestamp())
        except ValueError:
            return "invalid_timestamp"

    now = int(datetime.now(timezone.utc).timestamp())
    if abs(now - ts) > _MAX_SKEW_SECONDS:
        return "stale_webhook"

    expected = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp_header}.{raw_body.decode('utf-8')}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    supplied = _parse_signature_header(signature_header)
    for sig in supplied:
        try:
            if hmac.compare_digest(sig, expected):
                return None
        except Exception:
            continue
    return "invalid_signature"


def already_processed(db: Session, event_id: str | None) -> bool:
    if not event_id or len(event_id) > 64:
        return False
    return (
        db.query(SingraWebhookEvent)
        .filter(SingraWebhookEvent.event_id == event_id)
        .first()
        is not None
    )


def mark_processed(db: Session, event_id: str | None) -> None:
    if not event_id or len(event_id) > 64:
        return
    if already_processed(db, event_id):
        return
    db.add(SingraWebhookEvent(event_id=event_id))
    db.commit()


async def handle_verified_payload(
    db: Session,
    *,
    event_type: str | None,
    payload: dict[str, Any],
) -> None:
    data = payload.get("data") or {}
    guest_email = data.get("guestEmail") or data.get("guest_email")
    if isinstance(guest_email, str):
        guest_email = guest_email.strip() or None
    else:
        guest_email = None

    if event_type == "webhook_test":
        _log.info("singra webhook_test received")
        return

    if event_type == "ticket_created":
        await _notify_team(data)
        if guest_email:
            await _send_guest_mail(
                guest_email,
                subject=data.get("subject") or "Support-Anfrage erhalten",
                body=(
                    f"Hallo {data.get('guestName', '')},\n\n"
                    "wir haben deine Nachricht erhalten und melden uns sobald wie möglich.\n\n"
                    f"Betreff: {data.get('subject', '—')}\n"
                ),
            )
        return

    if event_type == "ticket_replied" and data.get("isStaff") and guest_email:
        await _send_guest_mail(
            guest_email,
            subject=data.get("subject") or "Antwort auf dein Support-Ticket",
            body=str(data.get("message") or ""),
        )


async def _notify_team(data: dict[str, Any]) -> None:
    if not EmailService.is_configured():
        return
    notify_to = EmailService._get_setting("smtp_from").strip()
    if not notify_to:
        return
    subject = f"[MSM Support] {data.get('subject') or 'Neues Widget-Ticket'}"
    body = (
        f"Neues Support-Widget-Ticket\n\n"
        f"Von: {data.get('guestName', '—')}\n"
        f"E-Mail: {data.get('guestEmail') or '—'}\n"
        f"Ticket: {data.get('ticketId', '—')}\n\n"
        f"{data.get('message', '')}\n"
    )
    await EmailService.send_email(notify_to, subject, body)


async def _send_guest_mail(to: str, subject: str, body: str) -> None:
    if not EmailService.is_configured():
        _log.warning("guest email skipped: SMTP not configured")
        return
    await EmailService.send_email(to, subject, body)


def parse_json_payload(raw_body: bytes) -> dict[str, Any]:
    return json.loads(raw_body.decode("utf-8"))