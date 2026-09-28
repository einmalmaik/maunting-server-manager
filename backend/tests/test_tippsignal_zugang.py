"""Das Tippsignal geht durch dieselbe Tür wie das Lesen.

Bis 29.09.2026 stand vor `/e2ee/typing` und dem `typing`-Rahmen des
WebSockets nur `assert_mailbox_token`. Das tut nichts, solange für eine
Mailbox kein Nachweis hinterlegt ist, und auf ableitbaren Kennungen (Gruppe,
Geräte, Direktchat-Paar) gibt es nie einen. Die Kennungen sind aus kleinen
Ganzzahlen nachrechenbar. Jedes angemeldete Konto konnte damit „tippt gerade"
samt `sender_id` und `sender_username` in fremde Gruppen, an fremde Geräte
und an Konten schicken, die nur Freunde annehmen, ohne je eine Nachricht
schreiben zu dürfen.

Jetzt gilt `assert_mailbox_zugang`, dieselbe Prüfung wie beim Lesen einer
Mailbox: Besitznachweis oder Teilnahme.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User, UserFriend
from services.auth_service import AuthService
from services.social_service import SocialService
from services.sync_event_service import SyncEventService

HERKUNFT = {"origin": "http://localhost:3000"}


@pytest.fixture(autouse=True)
def _leere_abonnenten():
    SyncEventService.clear_all_for_testing()
    yield
    SyncEventService.clear_all_for_testing()


def _abonniere(user_id: int, mailboxen: list[str]) -> str:
    conn_id, _ = SyncEventService.subscribe(user_id=user_id)
    SyncEventService.set_mailboxes(conn_id, mailboxen, user_id=user_id)
    return conn_id


def _tippsignale(conn_id: str) -> list[dict]:
    sub = SyncEventService._subscribers.get(conn_id)
    if sub is None:
        return []
    raus = []
    while not sub.queue.empty():
        raus.append(sub.queue.get_nowait())
    return [e for e in raus if e.get("type") == "e2ee_typing_signal"]


def _dritter(db: Session) -> User:
    user = AuthService.create_user(db, "dritter", "dritter@test.de", "DritterPass123!")
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


def _tippe(client: TestClient, kekse: dict, mailbox: str):
    return client.post(
        "/api/social/e2ee/typing",
        json={"blind_mailbox_id": mailbox, "status": "typing"},
        cookies=kekse,
        headers={"X-CSRF-Token": kekse.get("__Secure-csrf_token", "")},
    )


# ── Fremde bleiben draussen ─────────────────────────────────────────────────


def test_fremder_tippt_nicht_in_eine_gruppe(
    client: TestClient, db: Session, owner_user: User, regular_user: User, owner_cookies: dict
) -> None:
    gruppe = SocialService.create_group(db, regular_user)
    dritter = _dritter(db)
    SocialService.join_group_by_invite_code(db, dritter, gruppe.invite_code)
    mid = SocialService.derive_group_blind_mailbox_id(gruppe.id)
    mitglied = _abonniere(dritter.id, [mid])

    antwort = _tippe(client, owner_cookies, mid)

    assert antwort.status_code == 403
    assert _tippsignale(mitglied) == []


def test_fremder_tippt_nicht_an_fremde_geraete(
    client: TestClient, db: Session, owner_user: User, regular_user: User, owner_cookies: dict
) -> None:
    geraete = SocialService.derive_user_device_mailbox_id(regular_user.id)
    opfer = _abonniere(regular_user.id, [geraete])

    antwort = _tippe(client, owner_cookies, geraete)

    assert antwort.status_code == 403
    assert _tippsignale(opfer) == []


def test_fremder_tippt_nicht_bei_einem_konto_nur_fuer_freunde(
    client: TestClient, db: Session, owner_user: User, regular_user: User, owner_cookies: dict
) -> None:
    regular_user.social_privacy = "friends"
    db.commit()
    mid = SocialService.derive_blind_mailbox_id(owner_user.id, regular_user.id)
    opfer = _abonniere(regular_user.id, [])

    antwort = _tippe(client, owner_cookies, mid)

    assert antwort.status_code == 403
    assert _tippsignale(opfer) == []


def test_fremder_tippt_auch_ueber_den_websocket_nicht_in_eine_gruppe(
    client: TestClient, db: Session, owner_user: User, regular_user: User, owner_cookies: dict
) -> None:
    gruppe = SocialService.create_group(db, regular_user)
    mid = SocialService.derive_group_blind_mailbox_id(gruppe.id)
    mitglied = _abonniere(regular_user.id, [mid])

    with client.websocket_connect("/api/social/ws", cookies=owner_cookies, headers=HERKUNFT) as ws:
        ws.send_json({"type": "typing", "blind_mailbox_id": mid, "status": "typing"})
        # Der Empfangsteil arbeitet Rahmen für Rahmen ab: kommt das Pong,
        # ist das Tippsignal schon behandelt.
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

    assert _tippsignale(mitglied) == []


# ── Wer dazugehört, tippt weiter ────────────────────────────────────────────


def test_mitglied_tippt_in_seiner_gruppe(
    client: TestClient, db: Session, owner_user: User, regular_user: User, owner_cookies: dict
) -> None:
    gruppe = SocialService.create_group(db, owner_user)
    SocialService.join_group_by_invite_code(db, regular_user, gruppe.invite_code)
    mid = SocialService.derive_group_blind_mailbox_id(gruppe.id)
    mitglied = _abonniere(regular_user.id, [mid])

    antwort = _tippe(client, owner_cookies, mid)

    assert antwort.status_code == 200, antwort.text
    assert len(_tippsignale(mitglied)) == 1


def test_mitglied_tippt_ueber_den_websocket(
    client: TestClient, db: Session, owner_user: User, regular_user: User, owner_cookies: dict
) -> None:
    gruppe = SocialService.create_group(db, owner_user)
    SocialService.join_group_by_invite_code(db, regular_user, gruppe.invite_code)
    mid = SocialService.derive_group_blind_mailbox_id(gruppe.id)
    mitglied = _abonniere(regular_user.id, [mid])

    with client.websocket_connect("/api/social/ws", cookies=owner_cookies, headers=HERKUNFT) as ws:
        ws.send_json({"type": "typing", "blind_mailbox_id": mid, "status": "typing"})
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

    assert len(_tippsignale(mitglied)) == 1


def test_freund_tippt_im_direktchat(
    client: TestClient, db: Session, owner_user: User, regular_user: User, owner_cookies: dict
) -> None:
    db.add(UserFriend(user_id=owner_user.id, friend_id=regular_user.id, status="accepted"))
    db.commit()
    mid = SocialService.derive_blind_mailbox_id(owner_user.id, regular_user.id)
    empfaenger = _abonniere(regular_user.id, [])

    antwort = _tippe(client, owner_cookies, mid)

    assert antwort.status_code == 200, antwort.text
    assert len(_tippsignale(empfaenger)) == 1


def test_bestehendes_gespraech_traegt_das_tippsignal(
    client: TestClient, db: Session, owner_user: User, regular_user: User, owner_cookies: dict
) -> None:
    # Kein Freund, aber ein offenes Profil und eine erste Nachricht: die
    # Gesprächszeile steht, ab dann gehört er dazu.
    regular_user.social_privacy = "public"
    db.commit()
    SocialService.ensure_direct_chat(db, owner_user.id, regular_user.id)
    mid = SocialService.derive_blind_mailbox_id(owner_user.id, regular_user.id)
    empfaenger = _abonniere(regular_user.id, [])

    antwort = _tippe(client, owner_cookies, mid)

    assert antwort.status_code == 200, antwort.text
    assert len(_tippsignale(empfaenger)) == 1
