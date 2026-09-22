"""Zustellung an eine Mailbox statt an ein Konto.

Bis 09/2026 lief jede Echtzeitzustellung ueber `user_id`: `relay_blind_envelope`
schlug nach, wer gemeint ist, und `SyncEventService` kannte nur Konten. Das
setzt genau das Wissen voraus, das dem Server genommen werden soll — sobald
eine Mailbox-Kennung nicht mehr aus `group_id` faellt, findet er niemanden
mehr.

Der neue Weg: der Client sagt, welche Mailboxen ihn interessieren, und der
Server stellt dorthin zu. Dass die Kennung unratbar ist, traegt die Schranke —
dieselbe Logik wie beim Tresor. Was hier festgehalten wird:

* **Abonnieren ist kein Selbstbedienungsladen.** Wer eine fremde Mailbox
  abonnieren koennte, saehe, wann es sich dort regt. Das ist Verkehrsanalyse,
  auch ohne ein einziges Byte Klartext.
* **Eine fremde Verbindung gehoert einem anderen.** Die `conn_id` steht im
  `ready`-Signal und reist durch den Browser; sie allein darf kein Abo setzen.
* **Zwei Zweige haben frueher systemweit gesendet.** Beide sind hier
  festgenagelt, einer davon war von aussen erreichbar.
"""

from __future__ import annotations

import asyncio
import base64

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import User
from services.social_service import SocialService
from services.sync_event_service import MAX_MAILBOXES, SyncEventService


# ── Hilfen ──────────────────────────────────────────────────────────────────


GEHEIM = "b" * 64
TOKEN = "c" * 64


def _umschlag(marke: str) -> str:
    roh = b"N" * 12 + marke.encode("utf-8") + b"T" * 16
    return "sv-e2ee-group-v1:00112233445566ff." + base64.b64encode(roh).decode("ascii")


def _abonniere(user_id: int, mailboxen: list[str]) -> str:
    """Haengt einen Abonnenten ein und setzt seine Mailboxen direkt."""
    conn_id, _ = SyncEventService.subscribe(user_id=user_id)
    SyncEventService.set_mailboxes(conn_id, mailboxen, user_id=user_id)
    return conn_id


def _empfangen(conn_id: str) -> list[dict]:
    """Leert die Warteschlange eines Abonnenten."""
    sub = SyncEventService._subscribers.get(conn_id)
    if sub is None:
        return []
    raus = []
    while not sub.queue.empty():
        raus.append(sub.queue.get_nowait())
    return raus


@pytest.fixture(autouse=True)
def _leere_abonnenten():
    SyncEventService.clear_all_for_testing()
    yield
    SyncEventService.clear_all_for_testing()


def _gemeinsame_gruppe(db: Session, besitzer: User, mitglied: User):
    gruppe = SocialService.create_group(db, besitzer, "Abogruppe")
    SocialService.join_group_by_invite_code(db, mitglied, gruppe.invite_code)
    return gruppe


# ── Das Abo selbst ──────────────────────────────────────────────────────────


def test_wer_abonniert_hat_bekommt_das_signal(owner_user: User) -> None:
    conn = _abonniere(owner_user.id, [GEHEIM])

    erreicht = SyncEventService.publish({"type": "probe"}, mailbox_id=GEHEIM)

    assert erreicht == 1
    assert len(_empfangen(conn)) == 1


def test_wer_nicht_abonniert_hat_bekommt_nichts(owner_user: User) -> None:
    # Der eigentliche Punkt: ein verbundener Client ohne dieses Abo darf nicht
    # erfahren, dass sich in der Mailbox etwas regt.
    conn = _abonniere(owner_user.id, ["a" * 64])

    SyncEventService.publish({"type": "probe"}, mailbox_id=GEHEIM)

    assert _empfangen(conn) == []


def test_ein_abo_mit_angabe_ist_nie_systemweit(owner_user: User) -> None:
    """`publish(mailbox_id=...)` darf nicht in den Systemzweig fallen.

    Der Systemzweig greift, wenn *keine* Angabe gemacht wurde. Stuende
    `mailbox_id` nicht in dieser Bedingung, ginge jedes Mailbox-Signal an
    jeden verbundenen Client — das Gegenteil des Gemeinten.
    """
    conn_id, _ = SyncEventService.subscribe(user_id=owner_user.id)  # ohne Abo

    SyncEventService.publish({"type": "probe"}, mailbox_id=GEHEIM)

    assert _empfangen(conn_id) == []


def test_die_liste_ersetzt_und_haengt_nicht_an(owner_user: User) -> None:
    # Eine verlassene Gruppe muss sich abbestellen lassen.
    conn = _abonniere(owner_user.id, [GEHEIM, "a" * 64])
    SyncEventService.set_mailboxes(conn, ["a" * 64], user_id=owner_user.id)

    SyncEventService.publish({"type": "probe"}, mailbox_id=GEHEIM)

    assert _empfangen(conn) == []


def test_eine_fremde_verbindung_laesst_sich_nicht_umhaengen(
    owner_user: User, regular_user: User
) -> None:
    """Die `conn_id` allein ist kein Ausweis.

    Sie steht im `ready`-Signal und reist durch den Browser. Ohne die
    Kontopruefung koennte ein fremdes Konto die Abos einer anderen Verbindung
    setzen — und sich so in deren Mailboxen einhaengen.
    """
    conn = _abonniere(owner_user.id, [GEHEIM])

    gesetzt = SyncEventService.set_mailboxes(conn, ["a" * 64], user_id=regular_user.id)

    assert gesetzt == 0
    # Und das alte Abo steht unveraendert.
    SyncEventService.publish({"type": "probe"}, mailbox_id=GEHEIM)
    assert len(_empfangen(conn)) == 1


def test_die_zahl_der_abos_ist_gedeckelt(owner_user: User) -> None:
    conn_id, _ = SyncEventService.subscribe(user_id=owner_user.id)

    gesetzt = SyncEventService.set_mailboxes(
        conn_id, [f"{i:064x}" for i in range(MAX_MAILBOXES + 50)], user_id=owner_user.id
    )

    assert gesetzt == MAX_MAILBOXES


def test_kontoweg_und_mailboxweg_stehen_nebeneinander(owner_user: User) -> None:
    """Der Bestand darf nicht kippen: `user_id` erreicht weiter jeden Client."""
    conn = _abonniere(owner_user.id, [GEHEIM])

    SyncEventService.publish({"type": "probe"}, user_id=owner_user.id)

    assert len(_empfangen(conn)) == 1


# ── Der Besitznachweis als Eintrittskarte ───────────────────────────────────


def test_unbekannte_mailbox_ohne_nachweis_bleibt_403(
    db: Session, owner_user: User
) -> None:
    with pytest.raises(HTTPException) as fehler:
        SocialService.relay_blind_envelope(
            db,
            blind_mailbox_id=GEHEIM,
            ciphertext_envelope=_umschlag("ohne-nachweis"),
            sender_user_id=owner_user.id,
            client_uuid="ohne-nachweis-1",
        )
    assert fehler.value.status_code == 403


def test_unbekannte_mailbox_mit_nachweis_traegt(db: Session, owner_user: User) -> None:
    """Der Fall, fuer den die ganze Scheibe da ist.

    Eine Kennung, die der Server nicht ausrechnen kann, und trotzdem kommt der
    Umschlag an — weil das Token stimmt.
    """
    SocialService.register_blind_mailbox(
        db, user_id=owner_user.id, mailbox_id=GEHEIM, auth_token=TOKEN
    )

    umschlag = SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=GEHEIM,
        ciphertext_envelope=_umschlag("mit-nachweis"),
        sender_user_id=owner_user.id,
        client_uuid="mit-nachweis-1",
        mailbox_token=TOKEN,
    )
    assert umschlag.blind_mailbox_id == GEHEIM


def test_unbekannte_mailbox_mit_falschem_token_bleibt_403(
    db: Session, owner_user: User
) -> None:
    SocialService.register_blind_mailbox(
        db, user_id=owner_user.id, mailbox_id=GEHEIM, auth_token=TOKEN
    )

    with pytest.raises(HTTPException) as fehler:
        SocialService.relay_blind_envelope(
            db,
            blind_mailbox_id=GEHEIM,
            ciphertext_envelope=_umschlag("falsches-token"),
            sender_user_id=owner_user.id,
            client_uuid="falsches-token-1",
            mailbox_token="d" * 64,
        )
    assert fehler.value.status_code == 403


def test_hat_gueltigen_nachweis_ist_streng_wo_assert_nachsichtig_ist(
    db: Session, owner_user: User
) -> None:
    """Der Unterschied, an dem alles haengt.

    `assert_mailbox_token` sagt bei einer Mailbox ohne hinterlegten Nachweis
    still ja — sonst braeuchte der gesamte Bestand ploetzlich Token.
    `hat_gueltigen_nachweis` sagt dort nein, denn wer damit fragt, will keine
    Schranke pruefen, sondern eine Eintrittskarte sehen. Waere es andersherum,
    oeffnete jede erfundene Kennung sich selbst.
    """
    # Nichts hinterlegt.
    SocialService.assert_mailbox_token(db, GEHEIM, None)  # wirft nicht
    assert SocialService.hat_gueltigen_nachweis(db, GEHEIM, None) is False
    assert SocialService.hat_gueltigen_nachweis(db, GEHEIM, TOKEN) is False

    SocialService.register_blind_mailbox(
        db, user_id=owner_user.id, mailbox_id=GEHEIM, auth_token=TOKEN
    )
    assert SocialService.hat_gueltigen_nachweis(db, GEHEIM, TOKEN) is True


def test_die_unbekannte_mailbox_stellt_an_ihre_abonnenten_zu(
    db: Session, owner_user: User
) -> None:
    SocialService.register_blind_mailbox(
        db, user_id=owner_user.id, mailbox_id=GEHEIM, auth_token=TOKEN
    )
    conn = _abonniere(owner_user.id, [GEHEIM])

    SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=GEHEIM,
        ciphertext_envelope=_umschlag("zustellung"),
        sender_user_id=owner_user.id,
        client_uuid="zustellung-abo-1",
        mailbox_token=TOKEN,
    )

    ereignisse = _empfangen(conn)
    assert len(ereignisse) == 1
    assert ereignisse[0]["blind_mailbox_id"] == GEHEIM


# ── Die zwei systemweiten Zweige ────────────────────────────────────────────


def test_tippsignal_auf_eine_erfundene_kennung_geht_nicht_an_alle(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Der Zweig, der von aussen erreichbar war.

    `resolve_mailbox_target` kehrt bei einer Kennung, die zu nichts gehoert,
    ohne Ausnahme zurueck, und `broadcast_typing_signal` fing nur
    `HTTPException`. Das `publish(payload)` dahinter hatte keine Angabe und
    war damit systemweit: ein Tippsignal auf eine erfundene Kennung ging an
    **jeden** verbundenen Client, samt `sender_id` und `sender_username`.
    """
    unbeteiligt = _abonniere(regular_user.id, ["a" * 64])

    SocialService.broadcast_typing_signal(
        blind_mailbox_id="f" * 64,
        status="typing",
        sender_id=owner_user.id,
        sender_username=owner_user.username,
        db=db,
    )

    assert _empfangen(unbeteiligt) == []


def test_tippsignal_erreicht_die_abonnenten_seiner_mailbox(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Die Gegenprobe: still ist nicht dasselbe wie tot."""
    zuhoerer = _abonniere(regular_user.id, [GEHEIM])

    SocialService.broadcast_typing_signal(
        blind_mailbox_id=GEHEIM,
        status="typing",
        sender_id=owner_user.id,
        sender_username=owner_user.username,
        db=db,
    )

    assert len(_empfangen(zuhoerer)) == 1


def test_ein_genannter_empfaenger_muss_zur_mailbox_passen(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Ein `recipient_id` sprang frueher an der Mailbox-Aufloesung vorbei.

    Die einzige verbleibende Pruefung war die Blockierung. Jedes angemeldete
    Konto konnte damit jedem anderen „tippt gerade" schicken, mit einer frei
    erfundenen Kennung — eine Zustellung ohne jede Berechtigung.
    """
    opfer = _abonniere(regular_user.id, [])  # hoert nur auf sein Konto

    SocialService.broadcast_typing_signal(
        blind_mailbox_id="f" * 64,
        status="typing",
        sender_id=owner_user.id,
        sender_username=owner_user.username,
        db=db,
        recipient_id=regular_user.id,
    )

    assert _empfangen(opfer) == []


def test_ein_passender_empfaenger_geht_weiterhin_durch(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Der Bestand: im echten Gespraech traegt der Weg wie bisher."""
    empfaenger = _abonniere(regular_user.id, [])
    mid = SocialService.derive_blind_mailbox_id(owner_user.id, regular_user.id)

    SocialService.broadcast_typing_signal(
        blind_mailbox_id=mid,
        status="typing",
        sender_id=owner_user.id,
        sender_username=owner_user.username,
        db=db,
        recipient_id=regular_user.id,
    )

    assert len(_empfangen(empfaenger)) == 1


def test_die_alte_dm_kennung_gilt_auch(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Ein Gespraech, das vor dem Kennungswechsel begann, darf nicht verstummen."""
    empfaenger = _abonniere(regular_user.id, [])
    alt = SocialService.derive_legacy_direct_mailbox_id(owner_user.id, regular_user.id)

    SocialService.broadcast_typing_signal(
        blind_mailbox_id=alt,
        status="typing",
        sender_id=owner_user.id,
        sender_username=owner_user.username,
        db=db,
        recipient_id=regular_user.id,
    )

    assert len(_empfangen(empfaenger)) == 1


def test_die_gruppe_erreicht_ihre_mitglieder_wie_bisher(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Der wichtigste Bestandstest: der Kontoweg bleibt fuer bekannte Mailboxen."""
    gruppe = _gemeinsame_gruppe(db, owner_user, regular_user)
    mid = SocialService.derive_group_blind_mailbox_id(gruppe.id)
    mitglied = _abonniere(regular_user.id, [])  # ohne Mailbox-Abo

    SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=mid,
        ciphertext_envelope=_umschlag("an-die-gruppe"),
        sender_user_id=owner_user.id,
        client_uuid="an-die-gruppe-1",
    )

    assert len(_empfangen(mitglied)) == 1
