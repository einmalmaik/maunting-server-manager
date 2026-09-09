from __future__ import annotations

import logging
import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from fastapi import HTTPException
from models import User
from services import permission_service
from services.ai_action_errors import AiActionValidationError
from services.ai_redaction import redact_sensitive_text
from services.ai_proposals.base import _AusfuehrungsRahmen, _Ausgefuehrt

from services.achievement_service import AchievementService

logger = logging.getLogger(__name__)

def _email_send_payload(db: Session, user: User, rest: dict) -> tuple[dict, dict]:
    recipient = str(rest.get("recipient", "")).strip()
    subject = str(rest.get("subject", "")).strip()
    body_text = str(rest.get("body_text", "")).strip()
    if not recipient or not subject or not body_text:
        raise AiActionValidationError("E-Mail-Versand erfordert recipient, subject und body_text")

    mailbox_id = rest.get("mailbox_id")
    body_html = rest.get("body_html")

    payload = {
        "recipient": recipient,
        "subject": redact_sensitive_text(subject),
        "body_text": redact_sensitive_text(body_text),
        "mailbox_id": int(mailbox_id) if mailbox_id else None,
        "body_html": str(body_html) if body_html else None,
    }
    preview = {
        "operation": "email_send",
        "recipient": recipient,
        "subject": redact_sensitive_text(subject),
        "body_preview": redact_sensitive_text(body_text)[:500],
        "mailbox_id": mailbox_id,
    }
    return payload, preview

def _message_friend_payload(db: Session, user: User, rest: dict) -> tuple[dict, dict]:
    friend_username = str(rest.get("friend_username", "")).strip()
    message_text = str(rest.get("message_text", "")).strip()
    if not friend_username or not message_text:
        raise AiActionValidationError("Freundesnachricht erfordert friend_username und message_text")

    from services.social_service import SocialService

    target = db.query(User).filter(User.username.ilike(friend_username)).first()
    if not target or not target.is_active:
        raise AiActionValidationError(f"Benutzer '{friend_username}' existiert nicht oder ist inaktiv.")

    # HARTE FREUNDESLISTEN-BINDUNG: Ausschließlich bestätigte Freunde
    if not SocialService.is_confirmed_friend(db, user.id, target.id):
        raise AiActionValidationError(
            f"Sicherheitsblockade: '{friend_username}' ist kein bestätigter Freund. "
            "Die KI darf Nachrichten ausschließlich an bereits bestätigte Freunde senden."
        )

    payload = {
        "friend_id": target.id,
        "friend_username": target.username,
        "message_text": redact_sensitive_text(message_text),
    }
    preview = {
        "operation": "message_friend",
        "friend_username": target.username,
        "message_preview": redact_sensitive_text(message_text)[:200],
    }
    return payload, preview

def _calendar_event_create_payload(db: Session, user: User, rest: dict) -> tuple[dict, dict]:
    title = str(rest.get("title", "")).strip()
    start_time = str(rest.get("start_time", "")).strip()
    end_time = str(rest.get("end_time", "")).strip()
    if not title or not start_time or not end_time:
        raise AiActionValidationError("Kalender-Eintrag erfordert title, start_time und end_time")

    description = rest.get("description")
    location = rest.get("location")
    calendar_id = rest.get("calendar_id")
    event_type = rest.get("event_type", "personal")
    team_id = rest.get("team_id")
    server_id = rest.get("server_id")
    color = rest.get("color")

    payload = {
        "title": redact_sensitive_text(title),
        "start_time": start_time,
        "end_time": end_time,
        "description": redact_sensitive_text(str(description)) if description else None,
        "location": redact_sensitive_text(str(location)) if location else None,
        "calendar_id": int(calendar_id) if calendar_id else None,
        "event_type": str(event_type) if event_type else "personal",
        "team_id": int(team_id) if team_id else None,
        "server_id": int(server_id) if server_id else None,
        "color": str(color).strip() if color else None,
    }
    preview = {
        "operation": "calendar_event_create",
        "title": redact_sensitive_text(title),
        "start_time": start_time,
        "end_time": end_time,
        "location": redact_sensitive_text(str(location)) if location else None,
        "calendar_id": calendar_id,
        "event_type": payload["event_type"],
        "team_id": payload["team_id"],
        "server_id": payload["server_id"],
    }
    return payload, preview

def _calendar_event_delete_payload(db: Session, user: User, rest: dict) -> tuple[dict, dict]:
    event_id = str(rest.get("event_id", "")).strip()
    if not event_id:
        raise AiActionValidationError("Termin-LÃ¶schung erfordert event_id")

    calendar_id = rest.get("calendar_id")
    payload = {
        "event_id": event_id,
        "calendar_id": int(calendar_id) if calendar_id else None,
    }
    preview = {
        "operation": "calendar_event_delete",
        "event_id": event_id,
        "calendar_id": calendar_id,
        "irreversible": True,
    }
    return payload, preview

def _calendar_event_update_payload(db: Session, user: User, rest: dict) -> tuple[dict, dict]:
    event_id = str(rest.get("event_id", "")).strip()
    if not event_id:
        raise AiActionValidationError("Termin-Ã„nderung erfordert event_id")

    title = rest.get("title")
    start_time = rest.get("start_time")
    end_time = rest.get("end_time")
    description = rest.get("description")
    location = rest.get("location")
    calendar_id = rest.get("calendar_id")
    event_type = rest.get("event_type")
    team_id = rest.get("team_id")
    server_id = rest.get("server_id")
    color = rest.get("color")

    payload = {
        "event_id": event_id,
        "title": str(title).strip() if title else None,
        "start_time": str(start_time).strip() if start_time else None,
        "end_time": str(end_time).strip() if end_time else None,
        "description": str(description).strip() if description else None,
        "location": str(location).strip() if location else None,
        "calendar_id": int(calendar_id) if calendar_id else None,
        "event_type": str(event_type).strip() if event_type else None,
        "team_id": int(team_id) if team_id else None,
        "server_id": int(server_id) if server_id else None,
        "color": str(color).strip() if color else None,
    }
    preview = {
        "operation": "calendar_event_update",
        "event_id": event_id,
        "title": payload["title"],
        "start_time": payload["start_time"],
        "end_time": payload["end_time"],
        "description": payload["description"],
        "location": payload["location"],
        "calendar_id": calendar_id,
        "event_type": payload["event_type"],
        "team_id": payload["team_id"],
        "server_id": payload["server_id"],
    }
    return payload, preview

def _note_create_payload(db: Session, user: User, rest: dict) -> tuple[dict, dict]:
    title = str(rest.get("title", "")).strip()
    if not title:
        raise AiActionValidationError("Notiz-Erstellung erfordert einen title")

    content = rest.get("content", "")
    category = rest.get("category", "personal")
    color = rest.get("color", "primary")
    is_pinned = bool(rest.get("is_pinned", False))
    note_type = rest.get("note_type", "personal")
    team_id = rest.get("team_id")

    payload = {
        "title": redact_sensitive_text(title),
        "content": redact_sensitive_text(str(content)) if content else "",
        "category": str(category).strip() if category else "personal",
        "color": str(color).strip() if color else "primary",
        "is_pinned": is_pinned,
        "note_type": str(note_type).strip() if note_type else "personal",
        "team_id": int(team_id) if team_id else None,
    }
    preview = {
        "operation": "note_create",
        "title": redact_sensitive_text(title),
        "category": payload["category"],
        "color": payload["color"],
        "note_type": payload["note_type"],
        "team_id": payload["team_id"],
        "is_pinned": is_pinned,
    }
    return payload, preview

def _note_update_payload(db: Session, user: User, rest: dict) -> tuple[dict, dict]:
    note_id = str(rest.get("note_id", "")).strip()
    if not note_id:
        raise AiActionValidationError("Notiz-Aktualisierung erfordert note_id")

    title = rest.get("title")
    content = rest.get("content")
    category = rest.get("category")
    color = rest.get("color")
    is_pinned = rest.get("is_pinned")
    is_archived = rest.get("is_archived")
    note_type = rest.get("note_type")
    team_id = rest.get("team_id")

    payload = {
        "note_id": note_id,
        "title": redact_sensitive_text(str(title)).strip() if title else None,
        "content": redact_sensitive_text(str(content)) if content is not None else None,
        "category": str(category).strip() if category else None,
        "color": str(color).strip() if color else None,
        "is_pinned": bool(is_pinned) if is_pinned is not None else None,
        "is_archived": bool(is_archived) if is_archived is not None else None,
        "note_type": str(note_type).strip() if note_type else None,
        "team_id": int(team_id) if team_id else None,
    }
    preview = {
        "operation": "note_update",
        "note_id": note_id,
        "title": payload["title"],
        "category": payload["category"],
        "color": payload["color"],
        "note_type": payload["note_type"],
        "team_id": payload["team_id"],
        "is_pinned": payload["is_pinned"],
        "is_archived": payload["is_archived"],
    }
    return payload, preview

def _note_delete_payload(db: Session, user: User, rest: dict) -> tuple[dict, dict]:
    note_id = str(rest.get("note_id", "")).strip()
    if not note_id:
        raise AiActionValidationError("Notiz-LÃ¶schung erfordert note_id")

    payload = {"note_id": note_id}
    preview = {
        "operation": "note_delete",
        "note_id": note_id,
        "irreversible": True,
    }
    return payload, preview

def _ausfuehren_email_send(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    from services.mailbox_service import MailboxService

    p = rahmen.payload
    result = MailboxService.send_email(
        db,
        user=rahmen.active_user,
        recipient=str(p["recipient"]),
        subject=str(p["subject"]),
        body_text=str(p["body_text"]),
        mailbox_id=int(p["mailbox_id"]) if p.get("mailbox_id") else None,
        body_html=str(p["body_html"]) if p.get("body_html") else None,
    )
    return _Ausgefuehrt(result=result)

def _ausfuehren_calendar_event_create(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    from services.calendar_service import CalendarService

    p = rahmen.payload
    result = CalendarService.create_event(
        db,
        user=rahmen.active_user,
        title=str(p["title"]),
        start_time=str(p["start_time"]),
        end_time=str(p["end_time"]),
        description=str(p["description"]) if p.get("description") else None,
        location=str(p["location"]) if p.get("location") else None,
        calendar_id=int(p["calendar_id"]) if p.get("calendar_id") else None,
        event_type=str(p.get("event_type", "personal")),
        team_id=int(p["team_id"]) if p.get("team_id") else None,
        server_id=int(p["server_id"]) if p.get("server_id") else None,
        color=str(p["color"]) if p.get("color") else None,
    )
    return _Ausgefuehrt(result=result)

def _ausfuehren_calendar_event_update(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    from services.calendar_service import CalendarService

    p = rahmen.payload
    result = CalendarService.update_event(
        db,
        user=rahmen.active_user,
        event_id=str(p["event_id"]),
        title=str(p["title"]) if p.get("title") else None,
        start_time=str(p["start_time"]) if p.get("start_time") else None,
        end_time=str(p["end_time"]) if p.get("end_time") else None,
        description=str(p["description"]) if p.get("description") else None,
        location=str(p["location"]) if p.get("location") else None,
        calendar_id=int(p["calendar_id"]) if p.get("calendar_id") else None,
        event_type=str(p["event_type"]) if p.get("event_type") else None,
        team_id=int(p["team_id"]) if p.get("team_id") else None,
        server_id=int(p["server_id"]) if p.get("server_id") else None,
        color=str(p["color"]) if p.get("color") else None,
    )
    return _Ausgefuehrt(result=result)

def _ausfuehren_calendar_event_delete(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    from services.calendar_service import CalendarService

    p = rahmen.payload
    result = CalendarService.delete_event(
        db,
        user=rahmen.active_user,
        event_id=str(p["event_id"]),
        calendar_id=int(p["calendar_id"]) if p.get("calendar_id") else None,
    )
    return _Ausgefuehrt(result=result)

def _ausfuehren_note_create(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    from services.notes_service import NotesService

    p = rahmen.payload
    result = NotesService.create_note(
        db,
        user=rahmen.active_user,
        title=str(p["title"]),
        content=str(p.get("content", "")),
        category=str(p.get("category", "personal")),
        color=str(p.get("color", "primary")),
        is_pinned=bool(p.get("is_pinned", False)),
        note_type=str(p.get("note_type", "personal")),
        team_id=int(p["team_id"]) if p.get("team_id") else None,
    )
    return _Ausgefuehrt(result=result)

def _ausfuehren_note_update(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    from services.notes_service import NotesService

    p = rahmen.payload
    result = NotesService.update_note(
        db,
        user=rahmen.active_user,
        note_id_or_uid=str(p["note_id"]),
        title=str(p["title"]) if p.get("title") else None,
        content=str(p["content"]) if p.get("content") is not None else None,
        category=str(p["category"]) if p.get("category") else None,
        color=str(p["color"]) if p.get("color") else None,
        is_pinned=p.get("is_pinned"),
        is_archived=p.get("is_archived"),
        note_type=str(p["note_type"]) if p.get("note_type") else None,
        team_id=int(p["team_id"]) if p.get("team_id") else None,
    )
    return _Ausgefuehrt(result=result)

def _ausfuehren_note_delete(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    from services.notes_service import NotesService

    p = rahmen.payload
    result = NotesService.delete_note(
        db,
        user=rahmen.active_user,
        note_id_or_uid=str(p["note_id"]),
    )
    return _Ausgefuehrt(result=result)

def _ausfuehren_message_friend(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    import base64
    import hashlib
    import os
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from services.social_service import SocialService

    SocialService.assert_social_enabled(db)

    p = rahmen.payload
    friend_id = int(p["friend_id"])
    friend_username = str(p["friend_username"])
    message_text = str(p["message_text"])

    if not SocialService.is_confirmed_friend(db, rahmen.active_user.id, friend_id):
        raise AiActionValidationError("Freundschaft ist nicht mehr bestätigt.")

    ids = sorted([rahmen.active_user.id, friend_id])
    blind_mailbox_id = hashlib.sha256(f"msm:dm:{ids[0]}:{ids[1]}".encode("utf-8")).hexdigest()

    # DIS-kompatible direkte AES-256-GCM Kanalverschlüsselung
    channel_key_bytes = hashlib.sha256(f"msm:dm:key:{ids[0]}:{ids[1]}".encode("utf-8")).digest()
    aad = f"msm:dm:aad:{ids[0]}:{ids[1]}".encode("utf-8")

    envelope_payload = json.dumps({
        "sender_id": rahmen.active_user.id,
        "sender_username": rahmen.active_user.username,
        "text": message_text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }).encode("utf-8")

    aesgcm = AESGCM(channel_key_bytes)
    iv = os.urandom(12)
    # DIS Wire-Format: base64(IV(12) || ciphertext || authTag(16))
    encrypted = aesgcm.encrypt(iv, envelope_payload, aad)
    ciphertext_b64 = base64.b64encode(iv + encrypted).decode("ascii")

    SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=blind_mailbox_id,
        ciphertext_envelope=f"sv-e2ee-v1:{ciphertext_b64}",
    )
    AchievementService.unlock_achievement(db, rahmen.active_user.id, "social_zero_knowledge")

    return _Ausgefuehrt(result={"sent": True, "friend": friend_username})
