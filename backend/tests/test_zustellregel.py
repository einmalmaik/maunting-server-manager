"""Wer zeigt eine Meldung — und wie oft.

Bis 10/2026 galt „irgendeine offene Verbindung unterdrückt den Push". Das war
in beide Richtungen falsch:

* Ein Fenster im Hintergrund verschluckte die Meldung ganz (nichts zeigte sie).
* Zwei offene Fenster oder MSS und Pontis auf demselben Gerät zeigten sie
  doppelt, weil jeder Client selbst meldete.
* `sende_an_konto` stellte an **alle** Adressen eines Kontos zu.

Die Regel seither: der Client sagt, ob er vorne oder hinten steht
(`POST /api/events/presence`). Der Server wählt daraus **einen** Zusteller je
Konto und schickt den Push nur, wenn keiner die Meldung selbst zeigen kann.
Jede Meldung trägt zusätzlich eine feste Kennung (`msg:<mailbox>:<id>`), damit
dieselbe Nachricht auf demselben Gerät nicht zweimal erscheint.

Was hier festgehalten wird:

* **Genau einer.** Zwei Vordergrund-Clients, ein `melden: true`.
* **Keiner heisst Push.** Ohne Zusteller geht der Push hinaus.
* **Altverhalten bleibt.** Meldet kein Client etwas (ältere Fassung), gilt
  weiter „offene Verbindung genügt" — ein Update darf niemanden stummmachen.
* **Gruppen sind kein Konto.** An einer Mailbox hängen mehrere Menschen; dort
  wird je Konto einer gewählt, nicht einer für alle.
* **Fremde Verbindungen lassen sich nicht umstellen.**
"""

from __future__ import annotations

import base64

import pytest
from sqlalchemy.orm import Session

from models import User, UserFriend
from services import webpush_service
from services.social_service import SocialService
from services.sync_event_service import SyncEventService

UNABLEITBAR = "d" * 64
TOKEN = "e" * 64
PUNKT = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
ENDPUNKT = "https://fcm.googleapis.com/fcm/send/geraet-a"


def _umschlag(marke: str) -> str:
    roh = b"N" * 12 + marke.encode("utf-8") + b"T" * 16
    return "sv-e2ee-group-v1:00112233445566ff." + base64.b64encode(roh).decode("ascii")


@pytest.fixture(autouse=True)
def _leere_abonnenten():
    SyncEventService.clear_all_for_testing()
    yield
    SyncEventService.clear_all_for_testing()


@pytest.fixture
def versand(monkeypatch) -> list[tuple]:
    """Fängt ab, was in den Hintergrundfaden ginge. Gemessen wird davor."""
    aufrufe: list[tuple] = []
    monkeypatch.setattr(
        webpush_service._versender, "submit", lambda fn, *a, **k: aufrufe.append(a)
    )
    return aufrufe


def _verbinde(user_id: int, *, zustand: str | None = None, mailboxes: tuple[str, ...] = ()):
    conn_id, queue = SyncEventService.subscribe(user_id=user_id, team_ids=[], is_admin=False)
    if mailboxes:
        SyncEventService.set_mailboxes(conn_id, set(mailboxes), user_id=user_id)
    if zustand:
        assert SyncEventService.set_praesenz(conn_id, zustand, user_id=user_id) is True
    return conn_id, queue


# ── Die Wahl ────────────────────────────────────────────────────────────────


def test_vordergrund_ist_zusteller() -> None:
    conn, _ = _verbinde(1, zustand="vordergrund")

    assert SyncEventService.zustelllage(user_id=1) == (conn, True)


def test_hintergrunddienst_ist_zusteller_wenn_niemand_vorne_steht() -> None:
    """Der Dauerdienst zeigt selbst — sonst käme beim geschlossenen Fenster
    nichts an."""
    conn, _ = _verbinde(1, zustand="hintergrund")

    assert SyncEventService.zustelllage(user_id=1) == (conn, True)


def test_vordergrund_schlaegt_hintergrund() -> None:
    _verbinde(1, zustand="hintergrund")
    vorne, _ = _verbinde(1, zustand="vordergrund")

    assert SyncEventService.zustelllage(user_id=1)[0] == vorne


def test_ohne_praesenz_gibt_es_keinen_zusteller_aber_altverhalten() -> None:
    """Ältere Fassungen melden nichts. `praesenz_bekannt=False` ist das Signal
    an den Aufrufer, weiter „offene Verbindung genügt" zu prüfen."""
    _verbinde(1)

    assert SyncEventService.zustelllage(user_id=1) == (None, False)


def test_praesenz_einer_fremden_verbindung_wird_abgelehnt() -> None:
    conn, _ = _verbinde(1, zustand="vordergrund")

    assert SyncEventService.set_praesenz(conn, "hintergrund", user_id=2) is False
    assert SyncEventService.zustelllage(user_id=1)[1] is True


# ── Zwei Fenster, eine Meldung ──────────────────────────────────────────────


def test_zwei_fenster_zeigen_die_meldung_einmal() -> None:
    erst, erste_queue = _verbinde(1, zustand="vordergrund")
    zweit, zweite_queue = _verbinde(1, zustand="vordergrund")

    SyncEventService.publish(
        {"type": "e2ee_blind_message", "ereignis": "msg:mb:1"},
        user_id=1,
        zusteller=erst,
        melden_markieren=True,
    )

    assert erste_queue.get_nowait()["melden"] is True
    assert zweite_queue.get_nowait()["melden"] is False
    assert zweit != erst


def test_ohne_zusteller_zeigt_keiner() -> None:
    _conn, queue = _verbinde(1, zustand="hintergrund")

    SyncEventService.publish(
        {"type": "e2ee_blind_message"},
        user_id=1,
        zusteller=None,
        melden_markieren=True,
    )

    assert queue.get_nowait()["melden"] is False


def test_gruppenmailbox_waehlt_je_konto_einen() -> None:
    """An einer Gruppenmailbox hängen mehrere Menschen. Ein Zusteller für alle
    hiesse: der Rest der Gruppe schweigt."""
    a1, _ = _verbinde(1, zustand="vordergrund", mailboxes=(UNABLEITBAR,))
    a2, _ = _verbinde(1, zustand="vordergrund", mailboxes=(UNABLEITBAR,))
    b1, _ = _verbinde(2, zustand="vordergrund", mailboxes=(UNABLEITBAR,))

    gewaehlt, bekannt = SyncEventService.zusteller_je_mailbox(UNABLEITBAR)

    assert bekannt is True
    assert len(gewaehlt) == 2
    # Genau einer der beiden Clients von Konto 1, und der von Konto 2.
    assert len({a1, a2} & gewaehlt) == 1
    assert b1 in gewaehlt


# ── Der Weg durch das Relais ────────────────────────────────────────────────


def test_relais_zeigt_die_meldung_nur_einem_client(
    db: Session, owner_user: User, versand: list
) -> None:
    """MSS und Pontis auf demselben Gerät: einer meldet, der andere schweigt.

    Der Push bleibt auf diesem Weg draußen vor der Rechnung — eine
    Mailbox-Adresse kennt kein Konto, also lässt sich nicht sagen, welchem
    Empfänger sie gehört (`E2eeMailboxPush` hat bewusst keine `user_id`).
    Genau deshalb zählt hier die Marke `melden`: sie entscheidet ohne Konto.
    """
    SocialService.register_blind_mailbox(db, owner_user.id, UNABLEITBAR, TOKEN)
    webpush_service.eintragen_mailbox(
        db, mailbox_id=UNABLEITBAR, endpoint=ENDPUNKT, p256dh=PUNKT, auth=AUTH
    )
    _erst, erste_queue = _verbinde(owner_user.id, zustand="vordergrund", mailboxes=(UNABLEITBAR,))
    _zweit, zweite_queue = _verbinde(owner_user.id, zustand="vordergrund", mailboxes=(UNABLEITBAR,))

    SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=UNABLEITBAR,
        ciphertext_envelope=_umschlag("regel-1"),
        sender_user_id=owner_user.id + 1,
        mailbox_token=TOKEN,
    )

    assert sorted(
        [erste_queue.get_nowait().get("melden"), zweite_queue.get_nowait().get("melden")]
    ) == [False, True]


# ── Der eigene Absender ────────────────────────────────────────────────────


def test_die_eigene_kopie_meldet_nichts(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Was jemand selbst geschrieben hat, poppt auf den eigenen Geräten nicht
    als „Neue Nachricht" auf.

    Die Echokopie ging an alle Verbindungen des Absenders, und ohne Marke gilt
    beim Dienst „melden" — er hätte den eigenen Text gemeldet. Sie trägt deshalb
    `melden: false`; ein Zusteller wird für sie gar nicht erst gewählt.
    """
    # Eine aus zwei Konten abgeleitete Mailbox: nur bei ihr kennt der Server
    # den Empfänger und schickt dem Absender die Kopie. Dafür müssen die beiden
    # sich schreiben dürfen.
    db.add(UserFriend(user_id=owner_user.id, friend_id=regular_user.id, status="accepted"))
    db.add(UserFriend(user_id=regular_user.id, friend_id=owner_user.id, status="accepted"))
    db.commit()
    mailbox = SocialService.derive_blind_mailbox_id(owner_user.id, regular_user.id)
    # Der Absender hört nicht auf die Mailbox des Empfängers — nur seine eigene
    # Kopie kommt hier an.
    _conn, queue = _verbinde(regular_user.id, zustand="hintergrund")

    SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=mailbox,
        ciphertext_envelope=_umschlag("echo-1"),
        sender_user_id=regular_user.id,
    )

    # Der Absender bekommt davor noch sein Errungenschafts-Signal; gesucht ist
    # die Echokopie der Nachricht.
    signal = None
    while not queue.empty():
        eintrag = queue.get_nowait()
        if eintrag.get("type") == "e2ee_blind_message":
            signal = eintrag
            break
    assert signal is not None
    assert signal["melden"] is False


def test_relais_schickt_wenn_niemand_die_meldung_zeigt(db: Session, owner_user: User, versand: list) -> None:
    SocialService.register_blind_mailbox(db, owner_user.id, UNABLEITBAR, TOKEN)
    webpush_service.eintragen_mailbox(
        db, mailbox_id=UNABLEITBAR, endpoint=ENDPUNKT, p256dh=PUNKT, auth=AUTH
    )

    SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=UNABLEITBAR,
        ciphertext_envelope=_umschlag("regel-2"),
        sender_user_id=owner_user.id + 1,
        mailbox_token=TOKEN,
    )

    assert len(versand) == 1
    assert [ziel[1] for ziel in versand[0][0]] == [ENDPUNKT]
