"""Der Server weiss nicht mehr, wer mit wem schreibt.

`direct_chats.user_a_id`, `user_b_id` und `initiated_by_user_id` standen im
Klartext. Das war die Tabelle, die auf „mit wem schreibt Konto 42?" antwortete:
ein `SELECT` je Konto, und das ganze soziale Netz lag offen — ohne eine einzige
Nachricht zu entschluesseln. Fuer die meisten Fragen ueber einen Menschen
braucht es den Inhalt gar nicht; es genuegt zu wissen, dass er mit einer
Suchtberatung schreibt, mit einer Kanzlei oder mit einer Gewerkschaft.

Was diese Datei festhaelt:

* **Die Zeile nennt niemanden.** Weder in der Spalte noch ueber einen Umweg.
* **Die Liste gibt es nicht mehr.** `GET /social/direct-chats` ist entfernt,
  `list_direct_chats` auch, und `/e2ee/sync` zaehlt Direktchats nicht auf.
* **Was bleibt, funktioniert.** Erstkontakt, Antwortrecht, Blockierung, Zugang
  zur gemeinsamen Mailbox — alles ohne die Spalten.
* **Und Fremde bleiben draussen.** Die Rechnung, die die Spalten ersetzt, darf
  keine offenere Tuer sein als der Nachschlag vorher.

*Was hier bewusst NICHT behauptet wird:* dass die Zuordnung dadurch
unrekonstruierbar waere. Eine abgeleitete Kennung
(`sha256("msm:dm:<min>:<max>")`) laesst sich fuer alle Paare durchprobieren.
Das Gegenmittel ist Stufe 3d — sobald beide Seiten das Chatgeheimnis haben,
zieht das Gespraech in eine Mailbox um, die aus Zufall entsteht. Was hier
verschwindet, ist die bequeme Liste und die Pflicht, sie zu fuehren.
"""

from __future__ import annotations

import base64
import os

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from dependencies import get_current_user, verify_csrf
from main import app
from models import DirectChat, E2eeBlindEnvelope, User, UserFriend
from services.auth_service import AuthService
from services.social_service import SocialService


def _umschlag(marke: bytes = b"probe") -> str:
    """Ein formgueltiger Umschlag. Was drinsteht, sieht dieser Server nie."""
    roh = os.urandom(12) + marke + os.urandom(16)
    # Ein Gruppenumschlag nennt seit 09/2026 die Kennung seines Schluessels vor
    # dem Chiffretext — dieselbe Form wie in `test_privacy_routing_logic.py`.
    return "sv-e2ee-group-v1:00112233445566ff." + base64.b64encode(roh).decode("ascii")


def _konto(db: Session, name: str, privacy: str = "public") -> User:
    nutzer = AuthService.create_user(db, name, f"{name}@probe.example", "ProbePass123!")
    nutzer.social_privacy = privacy
    db.commit()
    return nutzer


@pytest.fixture
def als(client: TestClient):
    """Der Client, angemeldet als ein bestimmtes Konto — CSRF beiseite."""

    def _setze(nutzer: User) -> TestClient:
        app.dependency_overrides[get_current_user] = lambda: nutzer
        app.dependency_overrides[verify_csrf] = lambda: None
        return client

    try:
        yield _setze
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(verify_csrf, None)


def test_die_zeile_nennt_niemanden(db: Session, owner_user: User, regular_user: User):
    # `regular_user` steht per Vorgabe auf „nur Freunde"; ohne das hier waere
    # schon `ensure_direct_chat` ein 403 und der Test prueefte nichts.
    regular_user.social_privacy = "public"
    db.commit()
    chat = SocialService.ensure_direct_chat(db, owner_user.id, regular_user.id)

    spalten = {s.key for s in sa_inspect(DirectChat).column_attrs}
    assert spalten == {"id", "blind_mailbox_id", "created_at", "updated_at"}
    # Und auch nicht ueber eine Beziehung, die dasselbe auf Umwegen liefert.
    assert not hasattr(chat, "user_a")
    assert not hasattr(chat, "get_other_user_id")

    # Was sie sagt: dass dieses Gespraech existiert.
    assert chat.blind_mailbox_id == SocialService.derive_blind_mailbox_id(
        owner_user.id, regular_user.id
    )


def test_die_liste_gibt_es_nicht_mehr(als, db: Session, owner_user: User, regular_user: User):
    """Kein Endpunkt beantwortet „mit wem schreibt dieses Konto?" — auch nicht leer.

    Ein Endpunkt, der immer `[]` liefert, saehe fuer einen alten Client wie
    „du hast keine Gespraeche" aus. Ein 404 ist die ehrlichere Auskunft.
    """
    regular_user.social_privacy = "public"
    db.commit()
    SocialService.ensure_direct_chat(db, owner_user.id, regular_user.id)
    c = als(owner_user)

    assert c.get("/api/social/direct-chats").status_code == 404
    assert c.get("/api/social/chats").status_code == 404
    assert not hasattr(SocialService, "list_direct_chats")


def test_der_erstkontakt_und_das_antwortrecht_bleiben(db: Session):
    """Die eigentliche Aufgabe der Tabelle — und sie erfuellt sie weiter.

    Ein Fremder darf ein oeffentliches Profil anschreiben. Dabei entsteht die
    Zeile, und ab da darf auch der Angeschriebene zurueckschreiben, obwohl er
    mit dem Fremden nicht befreundet ist und sein eigenes Profil zu ist.
    """
    fremder = _konto(db, "fremder_erstkontakt", privacy="public")
    zu = _konto(db, "zugeknoepft", privacy="private")

    # Ohne Zeile: der Zugeknoepfte ist nicht anschreibbar.
    darf, grund = SocialService.can_message_user(db, fremder.id, zu.id)
    assert darf is False
    assert grund

    # Der Zugeknoepfte schreibt zuerst — das darf er, `fremder` ist oeffentlich.
    SocialService.ensure_direct_chat(db, zu.id, fremder.id)

    # Und ab jetzt darf der Fremde antworten.
    darf, grund = SocialService.can_message_user(db, fremder.id, zu.id)
    assert darf is True
    assert grund is None


def test_blockieren_schneidet_den_weg_ab(db: Session):
    a = _konto(db, "blockierer", privacy="public")
    b = _konto(db, "blockierter", privacy="public")
    SocialService.ensure_direct_chat(db, a.id, b.id)
    assert SocialService.can_message_user(db, b.id, a.id)[0] is True

    SocialService.block_user(db, a.id, b.id)

    assert SocialService.can_message_user(db, b.id, a.id)[0] is False
    assert SocialService.can_message_user(db, a.id, b.id)[0] is False


def test_beide_kommen_an_die_mailbox_ein_dritter_nicht(db: Session):
    """Die Rechnung, die die Spalten ersetzt, darf keine offenere Tuer sein.

    `gegenueber_aus_mailbox` leitet die Kennung fuer jedes aktive Konto ab. Ohne
    die zusaetzlich verlangte Chatzeile passte sie rechnerisch zu **jedem**
    Paar — und jede Konto-Kombination waere eine offene Tuer.
    """
    a = _konto(db, "teilnehmer_a")
    b = _konto(db, "teilnehmer_b")
    c = _konto(db, "dritter_im_bunde")
    SocialService.ensure_direct_chat(db, a.id, b.id)
    box = SocialService.derive_blind_mailbox_id(a.id, b.id)

    SocialService.assert_mailbox_participant(db, a.id, box)
    SocialService.assert_mailbox_participant(db, b.id, box)

    with pytest.raises(HTTPException) as fehler:
        SocialService.assert_mailbox_participant(db, c.id, box)
    assert fehler.value.status_code == 403


def test_eine_ausgerechnete_kennung_ohne_gespraech_oeffnet_nichts(db: Session):
    """Der gefaehrlichste Fall dieser Datei.

    Zwei Konten, die nie miteinander zu tun hatten, haben trotzdem eine
    ableitbare gemeinsame Kennung — die Formel kennt keine Beziehung. Ohne die
    Chatzeile daneben muss sie verschlossen bleiben, sonst waere Stufe 6b eine
    Lockerung und keine Verschaerfung.
    """
    a = _konto(db, "nie_geschrieben_a")
    b = _konto(db, "nie_geschrieben_b")
    box = SocialService.derive_blind_mailbox_id(a.id, b.id)

    for wer in (a, b):
        with pytest.raises(HTTPException) as fehler:
            SocialService.assert_mailbox_participant(db, wer.id, box)
        assert fehler.value.status_code == 403


def test_freunde_kommen_auch_ohne_gespraechszeile_hinein(db: Session):
    """Die zweite Quelle, und sie stand schon vorher da.

    Eine Freundschaft leitet dieselbe Kennung ab. Sie ist der Weg, auf dem ein
    Altbestand ohne Chatzeile weiter lesbar bleibt.
    """
    a = _konto(db, "freund_ohne_zeile_a", privacy="private")
    b = _konto(db, "freund_ohne_zeile_b", privacy="private")
    db.add_all(
        [
            UserFriend(user_id=a.id, friend_id=b.id, status="accepted"),
            UserFriend(user_id=b.id, friend_id=a.id, status="accepted"),
        ]
    )
    db.commit()
    box = SocialService.derive_blind_mailbox_id(a.id, b.id)

    assert db.query(DirectChat).filter_by(blind_mailbox_id=box).first() is None
    SocialService.assert_mailbox_participant(db, a.id, box)
    SocialService.assert_mailbox_participant(db, b.id, box)


def test_die_zustellung_findet_ihr_ziel_ohne_die_spalten(db: Session):
    """`resolve_mailbox_target` hatte einen Schnellweg ueber die Zeile.

    Er ist weg; die Kandidatensuche findet dasselbe Ziel. Ohne diesen Test
    faende man erst im Betrieb heraus, dass eine Zustellung ins Leere laeuft.
    """
    a = _konto(db, "zusteller")
    b = _konto(db, "empfaenger")
    SocialService.ensure_direct_chat(db, a.id, b.id)
    box = SocialService.derive_blind_mailbox_id(a.id, b.id)

    ziel, zeile = SocialService.resolve_mailbox_target(db, a.id, box)

    assert ziel == b.id
    assert zeile is not None and zeile.blind_mailbox_id == box


def test_der_umschlag_kommt_an_und_ein_fremder_nicht_hinein(db: Session):
    a = _konto(db, "relay_a")
    b = _konto(db, "relay_b")
    fremd = _konto(db, "relay_fremd")
    SocialService.ensure_direct_chat(db, a.id, b.id)
    box = SocialService.derive_blind_mailbox_id(a.id, b.id)

    env = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=box,
        ciphertext_envelope=_umschlag(b"hin"),
        sender_user_id=a.id,
    )
    assert env.id is not None

    antwort = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=box,
        ciphertext_envelope=_umschlag(b"zurueck"),
        sender_user_id=b.id,
    )
    assert antwort.id is not None

    # Und der Dritte kommt nicht hinein — weder schreibend noch lesend.
    with pytest.raises(HTTPException) as fehler:
        SocialService.relay_blind_envelope(
            db,
            blind_mailbox_id=box,
            ciphertext_envelope=_umschlag(b"fremd"),
            sender_user_id=fremd.id,
        )
    assert fehler.value.status_code == 403
    assert db.query(E2eeBlindEnvelope).filter_by(blind_mailbox_id=box).count() == 2


def test_zwei_erstnachrichten_ergeben_eine_zeile(db: Session):
    """Der eindeutige Riegel auf `blind_mailbox_id` ersetzt `uq_direct_chats_users`.

    Ohne ihn koennte dieselbe Mailbox mehrfach entstehen, und `_direktchat_zeile`
    bekaeme je nach Laune eine andere davon.
    """
    a = _konto(db, "doppelt_a")
    b = _konto(db, "doppelt_b")

    erste = SocialService.ensure_direct_chat(db, a.id, b.id)
    zweite = SocialService.ensure_direct_chat(db, b.id, a.id)

    assert erste.id == zweite.id
    assert db.query(DirectChat).filter_by(blind_mailbox_id=erste.blind_mailbox_id).count() == 1
