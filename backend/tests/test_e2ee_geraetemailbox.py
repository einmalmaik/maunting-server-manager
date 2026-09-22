"""Der Weg, auf dem ein Gruppenschluessel sein Ziel erreicht.

Bis 09/2026 lief er durch die Gruppenmailbox, und das hatte zwei Folgen:

1. Die Gruppenmailbox liess sich nicht mit einem Besitznachweis verschliessen.
   Wer den Schluessel noch nicht hatte, haette ihn hinter genau dem Schloss
   holen muessen, das er aufsperren sollte. Eine Laufzeitprobe zeigte das
   Mitglied danach vor einer stummen Mailbox — es sah die Nachricht nicht
   einmal als „Verschluesselte Nachricht".
2. Das Lesefenster verhungerte: die allermeisten Umschlaege darin waren
   Schluesselzustellungen fuer fremde Geraete, die niemand sonst oeffnen kann.

Seitdem geht die Zustellung in die **Geraete-Mailbox des Empfaengers**. Diese
Datei haelt fest, wie eng diese neue Tuer ist — denn sie ist eine Tuer zu
jemand anderem, und `msm:devices:<id>` ist aus einer kleinen Ganzzahl
nachrechenbar.

Zwei Schranken, und keine davon ist Zierde:

* **Nur Steuerumschlaege.** Eine Nachricht in einer fremden Geraete-Mailbox
  waere ein Chat am Gespraech vorbei, den kein Verlauf und keine Blockierung je
  zu sehen bekaeme.
* **Nur an Mitglieder gemeinsamer Gruppen.** Ohne das koennte jeder jedem in
  die Geraete-Mailbox schreiben.
"""

from __future__ import annotations

import base64

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import User
from services.social_service import SocialService


# ── Hilfen ──────────────────────────────────────────────────────────────────


def _gruppen_umschlag(marke: str) -> str:
    """Ein formgueltiger Gruppenumschlag — eine Nachricht, keine Steuerung."""
    roh = b"N" * 12 + marke.encode("utf-8") + b"T" * 16
    return "sv-e2ee-group-v1:00112233445566ff." + base64.b64encode(roh).decode("ascii")


def _hybrid_umschlag(marke: str) -> str:
    """Ein formgueltiger Hybridumschlag — so reist eine Schluesselzustellung."""
    schluessel = base64.b64encode(b"K" * 64).decode("ascii")
    roh = b"N" * 12 + marke.encode("utf-8") + b"T" * 16
    return f"sv-e2ee-hybrid-v1:{schluessel}.{base64.b64encode(roh).decode('ascii')}"


def _dritter(db: Session) -> User:
    """Ein Konto ohne jede Gruppe mit den anderen."""
    vorhanden = db.query(User).filter(User.username == "aussenstehender").first()
    if vorhanden:
        return vorhanden
    from services.auth_service import AuthService

    user = AuthService.create_user(
        db, "aussenstehender", "aussen@test.de", "AussenPass123!"
    )
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


def _gemeinsame_gruppe(db: Session, besitzer: User, mitglied: User):
    gruppe = SocialService.create_group(db, besitzer, "Zustellgruppe")
    SocialService.join_group_by_invite_code(db, mitglied, gruppe.invite_code)
    return gruppe


# ── Wer wohin zustellen darf ────────────────────────────────────────────────


def test_steuerumschlag_an_ein_gruppenmitglied_findet_sein_ziel(
    db: Session, owner_user: User, regular_user: User
) -> None:
    _gemeinsame_gruppe(db, owner_user, regular_user)
    ziel_mailbox = SocialService.derive_user_device_mailbox_id(regular_user.id)

    empfaenger, mitglieder, chat, gruppe = SocialService.resolve_mailbox_target(
        db,
        sender_user_id=owner_user.id,
        blind_mailbox_id=ziel_mailbox,
        is_control=True,
    )

    assert empfaenger == regular_user.id
    # Eine Geraete-Mailbox gehoert einem Konto, nicht einer Gruppe: der
    # Zustellweg darf dabei keine Mitgliederliste auffalten.
    assert mitglieder == []
    assert chat is None
    assert gruppe is None


def test_ohne_steuerkennzeichen_bleibt_die_fremde_geraetemailbox_zu(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Der Fall, der den Weg zum verdeckten Chat machen wuerde.

    Der Server kann nicht in den Umschlag sehen. Er kann aber darauf bestehen,
    dass er als Steuerung deklariert ist — und kein Client zeigt Steuerung je
    als Text an. Ohne diese Zeile waere die Geraete-Mailbox ein zweiter
    Nachrichtenweg ohne Verlauf, ohne Blockierung und ohne Anzeige.
    """
    _gemeinsame_gruppe(db, owner_user, regular_user)
    ziel_mailbox = SocialService.derive_user_device_mailbox_id(regular_user.id)

    empfaenger, mitglieder, _, _ = SocialService.resolve_mailbox_target(
        db,
        sender_user_id=owner_user.id,
        blind_mailbox_id=ziel_mailbox,
        is_control=False,
    )

    # Kein Ziel — und `relay_blind_envelope` macht daraus eine 403.
    assert empfaenger is None
    assert mitglieder == []


def test_relay_lehnt_die_nachricht_in_die_fremde_geraetemailbox_ab(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Dieselbe Zusage eine Ebene hoeher, am Produktionspfad."""
    _gemeinsame_gruppe(db, owner_user, regular_user)
    ziel_mailbox = SocialService.derive_user_device_mailbox_id(regular_user.id)

    with pytest.raises(HTTPException) as fehler:
        SocialService.relay_blind_envelope(
            db,
            blind_mailbox_id=ziel_mailbox,
            ciphertext_envelope=_gruppen_umschlag("keine-steuerung"),
            sender_user_id=owner_user.id,
            client_uuid="keine-steuerung-1",
            is_control=False,
        )
    assert fehler.value.status_code == 403


def test_steuerumschlag_geht_durch_das_relais(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Und die Gegenprobe: derselbe Weg mit Steuerkennzeichen traegt.

    Ohne diese Zusage koennte die Schranke oben beliebig streng sein und
    niemandem faellt auf, dass gar nichts mehr ankommt.
    """
    _gemeinsame_gruppe(db, owner_user, regular_user)
    ziel_mailbox = SocialService.derive_user_device_mailbox_id(regular_user.id)

    umschlag = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=ziel_mailbox,
        ciphertext_envelope=_hybrid_umschlag("zustellung"),
        sender_user_id=owner_user.id,
        client_uuid="zustellung-1",
        is_control=True,
        control_type="group_key",
    )
    assert umschlag.blind_mailbox_id == ziel_mailbox


def test_ein_aussenstehender_erreicht_keine_geraetemailbox(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Der Missbrauchsfall.

    `msm:devices:<id>` ist aus einer kleinen Ganzzahl nachrechenbar. Ohne die
    Gruppenschranke koennte jedes Konto jedem anderen in die Geraete-Mailbox
    schreiben — Schluessel unterschieben, das Lesefenster zumuellen, oder
    schlicht feststellen, wer existiert.
    """
    _gemeinsame_gruppe(db, owner_user, regular_user)
    aussen = _dritter(db)
    ziel_mailbox = SocialService.derive_user_device_mailbox_id(regular_user.id)

    empfaenger, mitglieder, _, _ = SocialService.resolve_mailbox_target(
        db,
        sender_user_id=aussen.id,
        blind_mailbox_id=ziel_mailbox,
        is_control=True,
    )

    assert empfaenger is None
    assert mitglieder == []


def test_die_eigene_geraetemailbox_bleibt_ohne_steuerkennzeichen_erreichbar(
    db: Session, owner_user: User
) -> None:
    """Der Bestand: Notizen und Kalender teilen ihre Schluessel darueber.

    Diese Zeile lag vor der neuen Schranke und muss dort bleiben — sonst
    braeuchten die anderen Nutzer dieser Mailbox ploetzlich ein Kennzeichen,
    das sie nie gesetzt haben.
    """
    eigene = SocialService.derive_user_device_mailbox_id(owner_user.id)

    empfaenger, _, _, _ = SocialService.resolve_mailbox_target(
        db,
        sender_user_id=owner_user.id,
        blind_mailbox_id=eigene,
        is_control=False,
    )
    assert empfaenger == owner_user.id


def test_die_gruppenmailbox_bleibt_unveraendert_erreichbar(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Nachrichten gehen weiter dorthin. Nur die Steuerung ist ausgezogen."""
    gruppe = _gemeinsame_gruppe(db, owner_user, regular_user)
    gruppen_mailbox = SocialService.derive_group_blind_mailbox_id(gruppe.id)

    empfaenger, mitglieder, _, gid = SocialService.resolve_mailbox_target(
        db,
        sender_user_id=owner_user.id,
        blind_mailbox_id=gruppen_mailbox,
        is_control=False,
    )

    assert empfaenger is None
    assert gid == gruppe.id
    assert set(mitglieder) == {owner_user.id, regular_user.id}


def test_eine_unbekannte_kennung_bleibt_auch_als_steuerung_ohne_ziel(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Das Steuerkennzeichen ist kein Generalschluessel.

    Es oeffnet genau eine zusaetzliche Form von Kennung — die Geraete-Mailbox
    eines Mitglieds. Alles andere bleibt, wie es war.
    """
    _gemeinsame_gruppe(db, owner_user, regular_user)

    empfaenger, mitglieder, _, _ = SocialService.resolve_mailbox_target(
        db,
        sender_user_id=owner_user.id,
        blind_mailbox_id="f" * 64,
        is_control=True,
    )

    assert empfaenger is None
    assert mitglieder == []
