"""Kein Konto füllt die Platte über den Messenger.

Drei Wege standen offen: Nachrichten ohne Größengrenze (im Test kam eine mit
28 MB an), Stories, die nach 24 Stunden nur ausgeblendet, aber nie gelöscht
wurden, und Anhänge ohne Kontingent.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from models import ChatMedia, ChatStory, User
from schemas.social import MAX_E2EE_ENVELOPE_CHARS, E2eeBlindEnvelopeCreate
from services import chat_media_service, social_service
from services.auth_service import AuthService
from services.chat_media_service import ChatMediaService
from services.chat_media_validator import (
    MAX_STORY_MEDIA_URL_CHARS,
    ChatMediaSecurityError,
    validate_story_media_url,
)
from services.social_service import SocialService

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _umschlag(nutzlast: bytes = b"probe") -> str:
    roh = os.urandom(12) + nutzlast + os.urandom(16)
    return "sv-e2ee-group-v1:00112233445566ff." + base64.b64encode(roh).decode("ascii")


def _paar(db: Session, prefix: str) -> tuple[User, User, str]:
    a = AuthService.create_user(db, f"{prefix}_a", f"{prefix}_a@probe.example", "ProbePass123!")
    b = AuthService.create_user(db, f"{prefix}_b", f"{prefix}_b@probe.example", "ProbePass123!")
    a.social_privacy = b.social_privacy = "public"
    db.commit()
    SocialService.ensure_direct_chat(db, a.id, b.id)
    return a, b, SocialService.derive_blind_mailbox_id(a.id, b.id)


# ── Nachrichten ─────────────────────────────────────────────────────────────


def test_zu_grosse_nachricht_scheitert_schon_am_schema() -> None:
    with pytest.raises(ValidationError):
        E2eeBlindEnvelopeCreate(
            blind_mailbox_id="a" * 64,
            ciphertext_envelope="sv-e2ee-group-v1:" + "A" * MAX_E2EE_ENVELOPE_CHARS,
        )


def test_zu_grosse_nachricht_scheitert_auch_am_websocket_weg(db: Session) -> None:
    """Der WebSocket reicht das rohe JSON ohne Schema an den Dienst."""
    a, _, box = _paar(db, "gross")
    zu_gross = _umschlag(os.urandom(MAX_E2EE_ENVELOPE_CHARS))

    with pytest.raises(HTTPException) as fehler:
        SocialService.relay_blind_envelope(
            db, blind_mailbox_id=box, ciphertext_envelope=zu_gross, sender_user_id=a.id
        )
    assert fehler.value.status_code == 400
    # Knapp unter der Grenze geht es.
    passend = _umschlag(os.urandom(MAX_E2EE_ENVELOPE_CHARS // 2))
    assert SocialService.relay_blind_envelope(
        db, blind_mailbox_id=box, ciphertext_envelope=passend, sender_user_id=a.id
    ).id


def test_tagesbudget_bremst_viele_kleine_nachrichten(db: Session, monkeypatch) -> None:
    a, b, box = _paar(db, "budget")
    monkeypatch.setattr(social_service, "E2EE_RELAY_KIB_PRO_TAG", 3)

    SocialService.relay_blind_envelope(
        db, blind_mailbox_id=box, ciphertext_envelope=_umschlag(os.urandom(1500)), sender_user_id=a.id
    )
    with pytest.raises(HTTPException) as fehler:
        SocialService.relay_blind_envelope(
            db, blind_mailbox_id=box, ciphertext_envelope=_umschlag(os.urandom(1500)), sender_user_id=a.id
        )
    assert fehler.value.status_code == 429
    # Das Budget gilt je Konto: das Gegenüber schreibt weiter.
    assert SocialService.relay_blind_envelope(
        db, blind_mailbox_id=box, ciphertext_envelope=_umschlag(b"antwort"), sender_user_id=b.id
    ).id


# ── Stories ─────────────────────────────────────────────────────────────────


def test_story_bild_mit_fuellmaterial_wird_abgewiesen() -> None:
    bild = "data:image/png;base64," + base64.b64encode(_PNG).decode("ascii")
    assert validate_story_media_url(bild) == bild

    # b64decode überlas fremde Zeichen: ein kleines Bild trug so beliebig viel mit.
    with pytest.raises(ChatMediaSecurityError):
        validate_story_media_url(bild + "!" * 1000)
    with pytest.raises(ChatMediaSecurityError):
        validate_story_media_url("https://bilder.example/" + "a" * MAX_STORY_MEDIA_URL_CHARS)


def test_stories_je_konto_sind_begrenzt(db: Session, owner_user: User, monkeypatch) -> None:
    monkeypatch.setattr(social_service, "MAX_AKTIVE_STORIES", 2)
    SocialService.create_story(db, owner_user, "eins")
    SocialService.create_story(db, owner_user, "zwei")

    with pytest.raises(HTTPException) as fehler:
        SocialService.create_story(db, owner_user, "drei")
    assert fehler.value.status_code == 429


def test_abgelaufene_stories_werden_geloescht(db: Session, owner_user: User) -> None:
    jetzt = datetime.now(timezone.utc)
    db.add_all(
        [
            ChatStory(user_id=owner_user.id, content="alt", expires_at=jetzt - timedelta(minutes=1)),
            ChatStory(user_id=owner_user.id, content="frisch", expires_at=jetzt + timedelta(hours=1)),
        ]
    )
    db.commit()

    assert SocialService.cleanup_expired_stories(db) == 1
    assert [s.content for s in db.query(ChatStory).filter_by(user_id=owner_user.id)] == ["frisch"]


# ── Anhänge ─────────────────────────────────────────────────────────────────


def test_anhaenge_haben_ein_kontingent_je_konto(db: Session, monkeypatch) -> None:
    a, b, box = _paar(db, "kontingent")
    blob = "sv-blob-v1:AES-GCM:iv=12345678:tag=87654321:ciphertext=EncryptedPayloadXYZ"
    monkeypatch.setattr(chat_media_service, "MEDIEN_KONTINGENT_BYTES", len(blob) * 2)

    for _ in range(2):
        ChatMediaService.upload_encrypted_media(
            db, uploader=a, blind_mailbox_id=box, ciphertext_blob=blob, file_name="bild.jpg"
        )
    with pytest.raises(HTTPException) as fehler:
        ChatMediaService.upload_encrypted_media(
            db, uploader=a, blind_mailbox_id=box, ciphertext_blob=blob, file_name="bild.jpg"
        )
    assert fehler.value.status_code == 413
    assert db.query(ChatMedia).filter_by(uploader_user_id=a.id).count() == 2
    # Das Kontingent gehört dem Hochladenden, nicht dem Gespräch.
    assert ChatMediaService.upload_encrypted_media(
        db, uploader=b, blind_mailbox_id=box, ciphertext_blob=blob, file_name="bild.jpg"
    ).id
