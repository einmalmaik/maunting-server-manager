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
* **Nur an bestehende Beziehungen.** Ohne das koennte jeder jedem in die
  Geraete-Mailbox schreiben.

Die zweite Schranke wurde mit Stufe 3e geweitet: sie kannte nur gemeinsame
Gruppen, was passte, solange hier nur Gruppenschluessel liefen. Seit auch das
Chatgeheimnis eines Direktchats diesen Weg nimmt, zaehlen zusaetzlich
bestaetigte Freundschaften und bestehende Chatzeilen — sonst haetten zwei
Freunde ohne gemeinsame Gruppe ihr Geheimnis nie ausgetauscht, und zwar
lautlos. Ein Fremder mit oeffentlichem Profil bleibt bewusst draussen.
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

    empfaenger, chat = SocialService.resolve_mailbox_target(
        db,
        sender_user_id=owner_user.id,
        blind_mailbox_id=ziel_mailbox,
        is_control=True,
    )

    assert empfaenger == regular_user.id
    # Und kein Direktchat: eine Geraete-Mailbox gehoert einem Konto, nicht
    # einem Gespraech. Eine Zeile in `direct_chats` waere hier die Auskunft,
    # dass diese beiden miteinander zu tun haben — fuer eine Zustellung, die
    # nur einen Schluessel traegt.
    assert chat is None


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

    empfaenger, _ = SocialService.resolve_mailbox_target(
        db,
        sender_user_id=owner_user.id,
        blind_mailbox_id=ziel_mailbox,
        is_control=False,
    )

    # Kein Ziel — und `relay_blind_envelope` macht daraus eine 403,
    # weil zu einer fremden Geraete-Mailbox weder Nachweis noch Teilnahme
    # vorliegt.
    assert empfaenger is None


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

    empfaenger, _ = SocialService.resolve_mailbox_target(
        db,
        sender_user_id=aussen.id,
        blind_mailbox_id=ziel_mailbox,
        is_control=True,
    )

    assert empfaenger is None


def test_die_eigene_geraetemailbox_bleibt_ohne_steuerkennzeichen_erreichbar(
    db: Session, owner_user: User
) -> None:
    """Der Bestand: Notizen und Kalender teilen ihre Schluessel darueber.

    Diese Zeile lag vor der neuen Schranke und muss dort bleiben — sonst
    braeuchten die anderen Nutzer dieser Mailbox ploetzlich ein Kennzeichen,
    das sie nie gesetzt haben.
    """
    eigene = SocialService.derive_user_device_mailbox_id(owner_user.id)

    empfaenger, _ = SocialService.resolve_mailbox_target(
        db,
        sender_user_id=owner_user.id,
        blind_mailbox_id=eigene,
        is_control=False,
    )
    assert empfaenger == owner_user.id


def test_die_gruppenmailbox_wird_nicht_mehr_aufgeloest(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Nachrichten gehen weiter dorthin — nur nicht mehr ueber Konten."""
    gruppe = _gemeinsame_gruppe(db, owner_user, regular_user)
    aussen = _dritter(db)
    gruppen_mailbox = SocialService.derive_group_blind_mailbox_id(gruppe.id)

    empfaenger, _ = SocialService.resolve_mailbox_target(
        db,
        sender_user_id=owner_user.id,
        blind_mailbox_id=gruppen_mailbox,
        is_control=False,
    )

    # Seit 09/2026 loest der Server eine Gruppenmailbox nicht mehr auf. Er
    # findet kein Konto — und genau das ist der Gewinn: er zaehlt nicht mehr
    # bei jeder Nachricht auf, wer in dieser Gruppe ist. Zugestellt wird ueber
    # das Abo, und die Berechtigung prueft `relay_blind_envelope` mit
    # `hat_zugang`.
    assert empfaenger is None
    assert SocialService.hat_zugang(db, owner_user.id, gruppen_mailbox, None) is True
    # Gegenprobe: ein Aussenstehender kommt dort nicht hinein.
    assert SocialService.hat_zugang(db, aussen.id, gruppen_mailbox, None) is False


def test_eine_unbekannte_kennung_bleibt_auch_als_steuerung_ohne_ziel(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Das Steuerkennzeichen ist kein Generalschluessel.

    Es oeffnet genau eine zusaetzliche Form von Kennung — die Geraete-Mailbox
    eines Mitglieds. Alles andere bleibt, wie es war.
    """
    _gemeinsame_gruppe(db, owner_user, regular_user)

    empfaenger, _ = SocialService.resolve_mailbox_target(
        db,
        sender_user_id=owner_user.id,
        blind_mailbox_id="f" * 64,
        is_control=True,
    )

    assert empfaenger is None


# ── Stufe 3e: das Chatgeheimnis nimmt denselben Weg ─────────────────────────


def _befreundet(db: Session, a: User, b: User) -> None:
    """Eine bestaetigte Freundschaft — ohne gemeinsame Gruppe."""
    anfrage = SocialService.send_friend_request(db, a.id, b.username)
    SocialService.accept_friend_request(db, b.id, anfrage["id"])


def test_steuerumschlag_an_einen_freund_ohne_gemeinsame_gruppe(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Der Fund aus der Laufzeitprobe zu Stufe 3e.

    Bis dahin zaehlten hier allein gemeinsame Gruppen — passend, solange nur
    Gruppenschluessel diesen Weg nahmen. Seit auch das Chatgeheimnis eines
    Direktchats darueber reist, war das zu eng: zwei Freunde ohne gemeinsame
    Gruppe bekamen ein 403, das Geheimnis erreichte die Gegenseite nie, und ihr
    Chat waere fuer immer auf der abgeleiteten Kennung stehen geblieben — der
    einen, die der Server selbst ausrechnen kann.

    Und zwar **lautlos**: der Client faengt die gescheiterte Zustellung ab,
    damit sie keine Nachricht aufhaelt. Ohne diesen Test waere der Ausfall
    nirgends zu sehen gewesen.
    """
    _befreundet(db, owner_user, regular_user)
    ziel_mailbox = SocialService.derive_user_device_mailbox_id(regular_user.id)

    empfaenger, chat = SocialService.resolve_mailbox_target(
        db,
        sender_user_id=owner_user.id,
        blind_mailbox_id=ziel_mailbox,
        is_control=True,
    )

    assert empfaenger == regular_user.id
    assert chat is None


def test_steuerumschlag_an_einen_bestehenden_chat_ohne_freundschaft(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Wer mir schon schreibt, darf mir auch ein Geheimnis zustellen.

    Der Weg, ueber den ein Gespraech mit einem Fremden nachtraeglich umzieht:
    die erste Nachricht legt die Chatzeile an, und ab da traegt sie die
    Berechtigung. Ohne diesen Zweig zoege ein Chat ohne Freundschaft nie um.

    Die Chatzeile entsteht hier so, wie sie im Betrieb entsteht — ueber ein
    oeffentliches Profil. Sie danach wieder zuzudrehen zeigt, dass die
    Berechtigung an der **Zeile** haengt und nicht an der Sichtbarkeit: sonst
    fiele ein Gespraech in dem Augenblick auseinander, in dem jemand sein
    Profil privat stellt.
    """
    regular_user.social_privacy = "public"
    db.commit()
    SocialService.ensure_direct_chat(db, owner_user.id, regular_user.id)
    regular_user.social_privacy = "private"
    db.commit()
    ziel_mailbox = SocialService.derive_user_device_mailbox_id(regular_user.id)

    empfaenger, _ = SocialService.resolve_mailbox_target(
        db,
        sender_user_id=owner_user.id,
        blind_mailbox_id=ziel_mailbox,
        is_control=True,
    )

    assert empfaenger == regular_user.id


def test_eine_offene_freundschaftsanfrage_reicht_nicht(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Angefragt ist nicht angenommen.

    Sonst waere die Anfrage selbst der Tuerdruecker: jeder koennte eine
    stellen, niemand muesste sie beantworten, und die Geraete-Mailbox staende
    trotzdem offen.
    """
    SocialService.send_friend_request(db, owner_user.id, regular_user.username)
    ziel_mailbox = SocialService.derive_user_device_mailbox_id(regular_user.id)

    empfaenger, _ = SocialService.resolve_mailbox_target(
        db,
        sender_user_id=owner_user.id,
        blind_mailbox_id=ziel_mailbox,
        is_control=True,
    )

    assert empfaenger is None


def test_ein_fremder_mit_oeffentlichem_profil_bleibt_draussen(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Die Grenze, die bewusst steht.

    Ein oeffentliches Profil erlaubt eine erste Nachricht — in die Mailbox des
    Gespraechs, die nur dieses eine Gespraech betrifft. Die Geraete-Mailbox ist
    etwas anderes: sie ist das Fenster, das mein Geraet bei **jedem** Gespraech
    abholt. Wer sie fuellen darf, kann mir jeden Chat lahmlegen.
    """
    aussen = _dritter(db)
    regular_user.social_privacy = "public"
    db.commit()
    ziel_mailbox = SocialService.derive_user_device_mailbox_id(regular_user.id)

    empfaenger, _ = SocialService.resolve_mailbox_target(
        db,
        sender_user_id=aussen.id,
        blind_mailbox_id=ziel_mailbox,
        is_control=True,
    )

    assert empfaenger is None


def test_blockierung_schliesst_auch_die_geraetemailbox(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Sonst waere dieser Weg die Hintertuer neben der verschlossenen Tuer.

    Geprueft wird gegen den Zustand **nach** einer bestehenden Beziehung: erst
    befreundet, dann blockiert. Genau dort liegt der Fehler, den man macht —
    die Kandidatenliste steht schon, und die Blockierung wird beim Auflisten
    vergessen.
    """
    _befreundet(db, owner_user, regular_user)
    SocialService.ensure_direct_chat(db, owner_user.id, regular_user.id)
    SocialService.block_user(db, regular_user.id, owner_user.id)
    ziel_mailbox = SocialService.derive_user_device_mailbox_id(regular_user.id)

    empfaenger, _ = SocialService.resolve_mailbox_target(
        db,
        sender_user_id=owner_user.id,
        blind_mailbox_id=ziel_mailbox,
        is_control=True,
    )

    assert empfaenger is None


def test_das_chatgeheimnis_geht_durch_das_relais(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Der ganze Weg, nicht nur die Aufloesung.

    `resolve_mailbox_target` ist die Auskunft; `relay_blind_envelope` ist die
    Tuer. Ein Test nur auf der Auskunft waere gruen, selbst wenn die Tuer
    daneben zu bliebe.
    """
    _befreundet(db, owner_user, regular_user)
    ziel_mailbox = SocialService.derive_user_device_mailbox_id(regular_user.id)

    umschlag = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=ziel_mailbox,
        ciphertext_envelope=_hybrid_umschlag("chatgeheimnis"),
        sender_user_id=owner_user.id,
        client_uuid="dm-secret-1",
        is_control=True,
        control_type="dm_secret",
    )

    assert umschlag.blind_mailbox_id == ziel_mailbox


def test_eine_nachricht_in_die_geraetemailbox_des_freundes_bleibt_verboten(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Die Schranke, die durch die Erweiterung **nicht** weicher wird.

    Waere sie es, haette ein Freund einen Kanal an jedem Verlauf und jeder
    Blockierung vorbei — sichtbar fuer niemanden.
    """
    _befreundet(db, owner_user, regular_user)
    ziel_mailbox = SocialService.derive_user_device_mailbox_id(regular_user.id)

    with pytest.raises(HTTPException) as fehler:
        SocialService.relay_blind_envelope(
            db,
            blind_mailbox_id=ziel_mailbox,
            ciphertext_envelope=_hybrid_umschlag("keine-steuerung"),
            sender_user_id=owner_user.id,
            client_uuid="dm-secret-2",
            is_control=False,
        )

    assert fehler.value.status_code == 403
