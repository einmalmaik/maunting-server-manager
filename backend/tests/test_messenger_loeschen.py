"""Loeschen einer Nachricht: Umschlaege raus, Anhang raus, und nur fuer Befugte.

Bis 09/2026 war "fuer alle loeschen" eine reine Anzeigesache: Der Client schickte
einen Steuerumschlag, die Gegenseite blendete die Zeile aus, und Chiffretext wie
Anhang blieben liegen. Ein neu eingerichtetes Geraet holte die Mailbox von vorn
und bekam die geloeschte Nachricht zurueck.

Geprueft wird hier das, was der Server dazu beitraegt: dass er die Umschlaege
einer Nachricht samt ihrer Geraetekopien entfernt, dass er dabei nichts anderes
mitnimmt, und dass Fremde weder in eine Mailbox noch an einen fremden Anhang
kommen.
"""

from __future__ import annotations

import base64
import hashlib
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import ChatMedia, DirectChat, E2eeBlindEnvelope, User
from services.auth_service import AuthService
from services.chat_media_service import ChatMediaService
from services.social_service import SocialService


def _umschlag(payload: bytes = b"test-secret-payload-bytes-12345678") -> str:
    roh = os.urandom(12) + payload + os.urandom(16)
    return "sv-e2ee-group-v1:" + base64.b64encode(roh).decode("ascii")


def _lege_chat_an(db: Session, a: User, b: User) -> str:
    """Direktchat zwischen zwei Konten samt seiner blinden Mailbox-Kennung."""
    mailbox = SocialService.derive_blind_mailbox_id(a.id, b.id)
    # Seit Stufe 6b nennt die Zeile keine Menschen mehr: nur noch die Kennung
    # der Mailbox. Wer dazugehoert, rechnet `gegenueber_aus_mailbox` aus.
    db.add(DirectChat(blind_mailbox_id=mailbox))
    db.commit()
    return mailbox


def _saee(db: Session, mailbox: str, client_uuid: str) -> E2eeBlindEnvelope:
    env = E2eeBlindEnvelope(
        blind_mailbox_id=mailbox,
        ciphertext_envelope=_umschlag(os.urandom(24)),
        client_uuid=client_uuid,
    )
    db.add(env)
    db.commit()
    return env


def test_loeschen_nimmt_alle_geraetekopien_einer_nachricht(
    db: Session, owner_user: User, regular_user: User
):
    """Eine Nachricht liegt als eine Kopie je Zielgeraet da. Alle muessen weg."""
    mailbox = _lege_chat_an(db, owner_user, regular_user)
    kennung = str(uuid4())

    _saee(db, mailbox, kennung)
    _saee(db, mailbox, f"{kennung}#geraet-a")
    _saee(db, mailbox, f"{kennung}#geraet-b")
    # Eine andere Nachricht im selben Gespraech und eine im Nachbargespraech.
    andere = _saee(db, mailbox, str(uuid4()))
    fremde_mailbox = SocialService.derive_blind_mailbox_id(owner_user.id, 99999)
    unbeteiligt = _saee(db, fremde_mailbox, kennung)

    entfernt = SocialService.delete_blind_envelopes(
        db, blind_mailbox_id=mailbox, client_uuid=kennung, user_id=owner_user.id
    )

    assert entfernt == 3
    uebrig = (
        db.query(E2eeBlindEnvelope)
        .filter(E2eeBlindEnvelope.blind_mailbox_id == mailbox)
        .all()
    )
    assert [e.id for e in uebrig] == [andere.id]
    # Dieselbe Kennung in einer anderen Mailbox geht niemanden etwas an.
    assert db.query(E2eeBlindEnvelope).filter_by(id=unbeteiligt.id).first() is not None


def test_loeschen_trifft_keine_kennung_mit_gleichem_anfang(
    db: Session, owner_user: User, regular_user: User
):
    """`#` trennt die Geraetekopie ab. Eine laengere Kennung ist eine andere Nachricht."""
    mailbox = _lege_chat_an(db, owner_user, regular_user)
    kennung = str(uuid4())

    _saee(db, mailbox, kennung)
    nachbar = _saee(db, mailbox, f"{kennung}-zwei")

    entfernt = SocialService.delete_blind_envelopes(
        db, blind_mailbox_id=mailbox, client_uuid=kennung, user_id=owner_user.id
    )

    assert entfernt == 1
    assert db.query(E2eeBlindEnvelope).filter_by(id=nachbar.id).first() is not None


def test_fremder_kommt_nicht_an_die_mailbox(
    db: Session, owner_user: User, regular_user: User
):
    """Wer nicht zum Gespraech gehoert, loescht dort auch nichts."""
    mailbox = _lege_chat_an(db, owner_user, regular_user)
    kennung = str(uuid4())
    env = _saee(db, mailbox, kennung)

    fremder = AuthService.create_user(db, "aussen", "aussen@test.de", "AussenPass123!")

    with pytest.raises(HTTPException) as fehler:
        SocialService.delete_blind_envelopes(
            db, blind_mailbox_id=mailbox, client_uuid=kennung, user_id=fremder.id
        )

    assert fehler.value.status_code == 403
    assert db.query(E2eeBlindEnvelope).filter_by(id=env.id).first() is not None


def test_fremde_geraete_mailbox_bleibt_zu(db: Session, owner_user: User, regular_user: User):
    """Die Geraete-Mailbox eines fremden Kontos ist ausrechenbar, aber verschlossen."""
    fremde_box = SocialService.derive_user_device_mailbox_id(regular_user.id)
    kennung = str(uuid4())
    env = _saee(db, fremde_box, kennung)

    with pytest.raises(HTTPException) as fehler:
        SocialService.delete_blind_envelopes(
            db, blind_mailbox_id=fremde_box, client_uuid=kennung, user_id=owner_user.id
        )

    assert fehler.value.status_code == 403
    assert db.query(E2eeBlindEnvelope).filter_by(id=env.id).first() is not None

    # Der Eigentuemer selbst darf.
    assert (
        SocialService.delete_blind_envelopes(
            db, blind_mailbox_id=fremde_box, client_uuid=kennung, user_id=regular_user.id
        )
        == 1
    )


def test_gruppenmitglied_darf_in_der_gruppenmailbox_loeschen(
    db: Session, owner_user: User, regular_user: User
):
    gruppe = SocialService.create_group(db, user=owner_user)
    SocialService.join_group_by_invite_code(db, user=regular_user, invite_code=gruppe.invite_code)
    gruppen_box = hashlib.sha256(f"msm:group:{gruppe.id}".encode("utf-8")).hexdigest()

    kennung = str(uuid4())
    _saee(db, gruppen_box, kennung)

    assert (
        SocialService.delete_blind_envelopes(
            db, blind_mailbox_id=gruppen_box, client_uuid=kennung, user_id=regular_user.id
        )
        == 1
    )

    aussen = AuthService.create_user(db, "nichtdabei", "nichtdabei@test.de", "NichtPass123!")
    _saee(db, gruppen_box, kennung)
    with pytest.raises(HTTPException) as fehler:
        SocialService.delete_blind_envelopes(
            db, blind_mailbox_id=gruppen_box, client_uuid=kennung, user_id=aussen.id
        )
    assert fehler.value.status_code == 403


def test_leere_kennung_loescht_nicht_die_halbe_mailbox(
    db: Session, owner_user: User, regular_user: User
):
    """Ohne Nachrichtenkennung gibt es kein Loeschen — sonst waere es ein Kahlschlag."""
    mailbox = _lege_chat_an(db, owner_user, regular_user)
    env = _saee(db, mailbox, str(uuid4()))

    with pytest.raises(HTTPException) as fehler:
        SocialService.delete_blind_envelopes(
            db, blind_mailbox_id=mailbox, client_uuid="   ", user_id=owner_user.id
        )

    assert fehler.value.status_code == 400
    assert db.query(E2eeBlindEnvelope).filter_by(id=env.id).first() is not None


def test_anhang_loescht_nur_der_absender(db: Session, owner_user: User, regular_user: User):
    """Der Blob liegt neben dem Umschlag. Loeschen darf ihn, wer ihn hochgeladen hat."""
    _lege_chat_an(db, owner_user, regular_user)
    mailbox = SocialService.derive_blind_mailbox_id(owner_user.id, regular_user.id)

    medium = ChatMediaService.upload_encrypted_media(
        db,
        uploader=owner_user,
        blind_mailbox_id=mailbox,
        ciphertext_blob="sv-blob-v1:AES-GCM-256:iv=abcdef123456:tag=987654:ciphertext=abcabcabcabc",
        file_name="anhang.bin",
    )

    # Der Empfaenger darf den Blob lesen, aber nicht loeschen.
    with pytest.raises(HTTPException) as fehler:
        ChatMediaService.delete_media(db, user=regular_user, media_id=medium.id)
    assert fehler.value.status_code == 403
    assert db.query(ChatMedia).filter_by(id=medium.id).first() is not None

    assert ChatMediaService.delete_media(db, user=owner_user, media_id=medium.id) is True
    assert db.query(ChatMedia).filter_by(id=medium.id).first() is None

    # Ein zweiter Versuch ist kein Fehler: weg ist weg, und genau das war das Ziel.
    assert ChatMediaService.delete_media(db, user=owner_user, media_id=medium.id) is False
