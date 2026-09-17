"""Tests für geräteübergreifende Anruferkennung und Handoff (Cross-Device Call Handoff).

Prüft:
- Registrierung und Abfrage des aktiven Anrufs über Plattformen hinweg
- Nahtlose Übergabe (Handoff / Transfer) von Gerät A auf Gerät B im selben Raum
- Verlassen des alten Anrufs bei Beitritt zu einem neuen Raum (Discord-Verhalten)
- Beenden und Verlassen des aktiven Anrufs
"""

from __future__ import annotations

import time
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User, UserFriend, ChatGroup, ChatGroupMember
from services import livekit_service
from services.auth_service import AuthService
from services.call_room_service import (
    CallRoomService,
    GroupCallRoomRegistry,
    UserActiveCallRegistry,
)
from services.sync_event_service import SyncEventService

API_KEY = "APItestkey01"
API_SECRET = "test-secret-fuer-anrufe"


@pytest.fixture(autouse=True)
def _raeume_leeren():
    CallRoomService.clear_all_for_testing()
    GroupCallRoomRegistry.clear_all_for_testing()
    UserActiveCallRegistry.clear_all_for_testing()
    SyncEventService.clear_all_for_testing()
    yield
    CallRoomService.clear_all_for_testing()
    GroupCallRoomRegistry.clear_all_for_testing()
    UserActiveCallRegistry.clear_all_for_testing()
    SyncEventService.clear_all_for_testing()


@pytest.fixture(autouse=True)
def _livekit_lokal(monkeypatch):
    monkeypatch.setattr(livekit_service.settings, "livekit_api_key", API_KEY)
    monkeypatch.setattr(livekit_service.settings, "livekit_api_secret", API_SECRET)
    monkeypatch.setattr(livekit_service.settings, "livekit_url", "wss://panel.test/livekit")
    monkeypatch.setattr(livekit_service, "_twirp", lambda *a, **k: {"participants": []})


def _user(db: Session, username: str) -> User:
    user = User(
        username=username,
        email=f"{username}@test.local",
        password_hash=AuthService.hash_password("StrongTestPass123!"),
        is_active=True,
        email_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": "StrongTestPass123!", "otp_code": None},
    )
    assert response.status_code == 200
    return dict(response.cookies)


def _csrf(cookies: dict[str, str]) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _befreunde(db: Session, a: User, b: User) -> None:
    db.add(UserFriend(user_id=a.id, friend_id=b.id, status="accepted"))
    db.commit()


# ── Unit-Tests für UserActiveCallRegistry ─────────────────────────────────────


def test_registry_register_and_get() -> None:
    call, prev, is_handoff = UserActiveCallRegistry.register(
        user_id=1,
        raum="raum-abc-12345678",
        art="direkt",
        device_id="phone-1",
        device_type="mobile",
        partner_id=2,
        partner_username="bob",
    )
    assert call["raum"] == "raum-abc-12345678"
    assert call["art"] == "direkt"
    assert call["device_id"] == "phone-1"
    assert call["device_type"] == "mobile"
    assert call["partner"]["username"] == "bob"
    assert prev is None
    assert not is_handoff

    active = UserActiveCallRegistry.get(1)
    assert active is not None
    assert active["device_id"] == "phone-1"


def test_registry_handoff_same_room_different_device() -> None:
    UserActiveCallRegistry.register(
        user_id=1,
        raum="raum-abc-12345678",
        art="direkt",
        device_id="phone-1",
        device_type="mobile",
        partner_id=2,
        partner_username="bob",
    )

    call, prev, is_handoff = UserActiveCallRegistry.register(
        user_id=1,
        raum="raum-abc-12345678",
        art="direkt",
        device_id="pc-web-2",
        device_type="web",
    )
    assert is_handoff is True
    assert prev is not None
    assert prev["device_id"] == "phone-1"
    assert call["device_id"] == "pc-web-2"
    assert call["device_type"] == "web"
    # Partner info bleibt erhalten
    assert call["partner"]["username"] == "bob"


def test_registry_supersede_different_room() -> None:
    UserActiveCallRegistry.register(
        user_id=1,
        raum="raum-alter-12345678",
        art="direkt",
        device_id="phone-1",
    )

    call, prev, is_handoff = UserActiveCallRegistry.register(
        user_id=1,
        raum="raum-neuer-12345678",
        art="gruppe",
        group_id=42,
        group_name="Gaming",
        device_id="pc-web-2",
    )
    assert is_handoff is False
    assert prev is not None
    assert prev["raum"] == "raum-alter-12345678"
    assert call["raum"] == "raum-neuer-12345678"
    assert call["group_name"] == "Gaming"


def test_registry_leave_and_heartbeat() -> None:
    UserActiveCallRegistry.register(
        user_id=1,
        raum="raum-abc-12345678",
        art="direkt",
        device_id="phone-1",
    )

    assert UserActiveCallRegistry.heartbeat(1, device_id="phone-1") is True
    assert UserActiveCallRegistry.heartbeat(1, device_id="wrong-device") is False

    # Leave mit falschem Gerät tut nichts
    assert UserActiveCallRegistry.leave(1, device_id="wrong-device") is None
    assert UserActiveCallRegistry.get(1) is not None

    # Leave mit passendem Gerät entfernt den Anruf
    left = UserActiveCallRegistry.leave(1, device_id="phone-1")
    assert left is not None
    assert UserActiveCallRegistry.get(1) is None


# ── API-Integrationstests ────────────────────────────────────────────────────


def test_get_active_call_empty(client: TestClient, db: Session) -> None:
    alice = _user(db, "alice_empty")
    c_alice = _login(client, alice.username)

    resp = client.get("/api/social/calls/active", cookies=c_alice)
    assert resp.status_code == 200
    daten = resp.json()
    assert daten["has_active_call"] is False
    assert daten["call"] is None


def test_token_registers_active_call_and_handoff(
    client: TestClient, db: Session
) -> None:
    alice = _user(db, "alice_handoff")
    bob = _user(db, "bob_handoff")
    _befreunde(db, alice, bob)

    c_alice = _login(client, alice.username)
    c_bob = _login(client, bob.username)

    # 1. Alice ruft Bob an
    resp_invite = client.post(
        f"/api/social/calls/invite/{bob.id}?mode=audio",
        cookies=c_alice,
        headers=_csrf(c_alice),
    )
    assert resp_invite.status_code == 200
    raum = resp_invite.json()["signaling_token"]

    # 2. Alice tritt mit dem Telefon (APK) bei
    resp_token1 = client.post(
        "/api/social/calls/token",
        json={
            "art": "direkt",
            "raum": raum,
            "device_id": "phone-apk-alice",
            "device_type": "mobile",
            "mode": "audio",
        },
        cookies=c_alice,
        headers=_csrf(c_alice),
    )
    assert resp_token1.status_code == 200

    # 3. Alice öffnet das Webpanel auf dem PC: GET /active zeigt den aktiven Anruf!
    resp_active = client.get("/api/social/calls/active", cookies=c_alice)
    assert resp_active.status_code == 200
    act_data = resp_active.json()
    assert act_data["has_active_call"] is True
    assert act_data["call"]["raum"] == raum
    assert act_data["call"]["device_id"] == "phone-apk-alice"
    assert act_data["call"]["device_type"] == "mobile"
    assert act_data["call"]["partner"]["user_id"] == bob.id
    assert act_data["call"]["partner"]["username"] == bob.username

    # 4. Handoff: Alice tritt nun auf dem PC demselben Anruf bei
    _, sse_queue = SyncEventService.subscribe(alice.id)
    _, sse_queue_bob = SyncEventService.subscribe(bob.id)

    resp_token2 = client.post(
        "/api/social/calls/token",
        json={
            "art": "direkt",
            "raum": raum,
            "device_id": "pc-web-alice",
            "device_type": "web",
            "mode": "audio",
        },
        cookies=c_alice,
        headers=_csrf(c_alice),
    )
    assert resp_token2.status_code == 200

    # Ereignisse prüfen: call_transferred an altes Gerät gesendet!
    events = []
    while not sse_queue.empty():
        events.append(sse_queue.get_nowait())

    transferred_event = next(
        (e for e in events if e.get("type") == "call_transferred"), None
    )
    assert transferred_event is not None
    assert transferred_event["raum"] == raum
    assert transferred_event["old_device_id"] == "phone-apk-alice"
    assert transferred_event["new_device_id"] == "pc-web-alice"

    # Partner Bob erhält call_partner_transferred
    events_bob = []
    while not sse_queue_bob.empty():
        events_bob.append(sse_queue_bob.get_nowait())
    partner_event = next(
        (e for e in events_bob if e.get("type") == "call_partner_transferred"), None
    )
    assert partner_event is not None
    assert partner_event["raum"] == raum

    # Jetzt ist der PC als aktives Gerät hinterlegt
    resp_active2 = client.get("/api/social/calls/active", cookies=c_alice)
    assert resp_active2.json()["call"]["device_id"] == "pc-web-alice"


def test_joining_different_call_supersedes_old(
    client: TestClient, db: Session
) -> None:
    alice = _user(db, "alice_diff")
    bob = _user(db, "bob_diff")
    charlie = _user(db, "charlie_diff")
    _befreunde(db, alice, bob)
    _befreunde(db, alice, charlie)

    c_alice = _login(client, alice.username)

    # Anruf 1 mit Bob
    resp1 = client.post(
        f"/api/social/calls/invite/{bob.id}",
        cookies=c_alice,
        headers=_csrf(c_alice),
    )
    raum1 = resp1.json()["signaling_token"]
    client.post(
        "/api/social/calls/token",
        json={"art": "direkt", "raum": raum1, "device_id": "phone-1"},
        cookies=c_alice,
        headers=_csrf(c_alice),
    )

    # Anruf 2 mit Charlie
    resp2 = client.post(
        f"/api/social/calls/invite/{charlie.id}",
        cookies=c_alice,
        headers=_csrf(c_alice),
    )
    raum2 = resp2.json()["signaling_token"]

    _, sse_queue = SyncEventService.subscribe(alice.id)
    _, sse_queue_bob = SyncEventService.subscribe(bob.id)

    # Alice tritt Anruf 2 auf dem PC bei -> verlässt Anruf 1
    resp_token2 = client.post(
        "/api/social/calls/token",
        json={"art": "direkt", "raum": raum2, "device_id": "pc-2"},
        cookies=c_alice,
        headers=_csrf(c_alice),
    )
    assert resp_token2.status_code == 200

    events = []
    while not sse_queue.empty():
        events.append(sse_queue.get_nowait())

    superseded_event = next(
        (e for e in events if e.get("type") == "call_superseded"), None
    )
    assert superseded_event is not None
    assert superseded_event["old_raum"] == raum1
    assert superseded_event["new_raum"] == raum2

    # Partner Bob erhält Benachrichtigung, dass der alte Anruf beendet wurde
    events_bob = []
    while not sse_queue_bob.empty():
        events_bob.append(sse_queue_bob.get_nowait())
    bob_cancel_event = next(
        (e for e in events_bob if e.get("type") in ("direct_call_cancelled", "call_ended_remotely")), None
    )
    assert bob_cancel_event is not None

    # Aktiver Anruf ist jetzt raum2
    resp_active = client.get("/api/social/calls/active", cookies=c_alice)
    assert resp_active.json()["call"]["raum"] == raum2


def test_leave_and_remote_terminate(client: TestClient, db: Session) -> None:
    alice = _user(db, "alice_term")
    bob = _user(db, "bob_term")
    _befreunde(db, alice, bob)

    c_alice = _login(client, alice.username)

    resp_inv = client.post(
        f"/api/social/calls/invite/{bob.id}",
        cookies=c_alice,
        headers=_csrf(c_alice),
    )
    raum = resp_inv.json()["signaling_token"]
    client.post(
        "/api/social/calls/token",
        json={"art": "direkt", "raum": raum, "device_id": "phone-1"},
        cookies=c_alice,
        headers=_csrf(c_alice),
    )

    _, sse_queue_bob = SyncEventService.subscribe(bob.id)

    # Remote terminate (Auflegen über anderes Gerät)
    resp_term = client.post(
        "/api/social/calls/active/terminate",
        cookies=c_alice,
        headers=_csrf(c_alice),
    )
    assert resp_term.status_code == 200

    # Kein aktiver Anruf mehr
    resp_act = client.get("/api/social/calls/active", cookies=c_alice)
    assert resp_act.json()["has_active_call"] is False

    # Partner Bob wurde benachrichtigt
    events_bob = []
    while not sse_queue_bob.empty():
        events_bob.append(sse_queue_bob.get_nowait())
    bob_term_event = next(
        (e for e in events_bob if e.get("type") in ("direct_call_cancelled", "call_ended_remotely")), None
    )
    assert bob_term_event is not None


def test_leave_unaccepted_call_cancels_call(client: TestClient, db: Session) -> None:
    alice = _user(db, "alice_unacc")
    bob = _user(db, "bob_unacc")
    _befreunde(db, alice, bob)

    c_alice = _login(client, alice.username)
    c_bob = _login(client, bob.username)

    resp_inv = client.post(
        f"/api/social/calls/invite/{bob.id}",
        cookies=c_alice,
        headers=_csrf(c_alice),
    )
    raum = resp_inv.json()["signaling_token"]

    _, sse_queue_bob = SyncEventService.subscribe(bob.id)

    # Alice legt auf bevor Bob angenommen hat
    resp_leave = client.post(
        "/api/social/calls/leave",
        json={"raum": raum, "device_id": "phone-alice"},
        cookies=c_alice,
        headers=_csrf(c_alice),
    )
    assert resp_leave.status_code == 200

    # Bob hat direct_call_cancelled erhalten
    events_bob = []
    while not sse_queue_bob.empty():
        events_bob.append(sse_queue_bob.get_nowait())
    cancel_ev = next((e for e in events_bob if e.get("type") == "direct_call_cancelled"), None)
    assert cancel_ev is not None

    # Für Bob gibt es keine ausstehende Einladung mehr
    resp_pending = client.get("/api/social/calls/pending", cookies=c_bob)
    assert resp_pending.json()["has_pending_call"] is False


def test_leave_accepted_call_preserves_room_for_rejoining(client: TestClient, db: Session) -> None:
    alice = _user(db, "alice_acc")
    bob = _user(db, "bob_acc")
    _befreunde(db, alice, bob)

    c_alice = _login(client, alice.username)
    c_bob = _login(client, bob.username)

    resp_inv = client.post(
        f"/api/social/calls/invite/{bob.id}",
        cookies=c_alice,
        headers=_csrf(c_alice),
    )
    raum = resp_inv.json()["signaling_token"]

    # Alice holt Token
    client.post(
        "/api/social/calls/token",
        json={"art": "direkt", "raum": raum, "device_id": "phone-alice"},
        cookies=c_alice,
        headers=_csrf(c_alice),
    )
    # Bob nimmt an (holt Token)
    client.post(
        "/api/social/calls/token",
        json={"art": "direkt", "raum": raum, "device_id": "desktop-bob"},
        cookies=c_bob,
        headers=_csrf(c_bob),
    )

    _, sse_queue_bob = SyncEventService.subscribe(bob.id)

    # Alice verlässt den Raum (z. B. versehentlicher Reload / Navigation)
    resp_leave = client.post(
        "/api/social/calls/leave",
        json={"raum": raum, "device_id": "phone-alice"},
        cookies=c_alice,
        headers=_csrf(c_alice),
    )
    assert resp_leave.status_code == 200

    # Weil der Anruf angenommen war, darf KEIN direct_call_cancelled gesendet werden!
    events_bob = []
    while not sse_queue_bob.empty():
        events_bob.append(sse_queue_bob.get_nowait())
    cancel_ev = next((e for e in events_bob if e.get("type") == "direct_call_cancelled"), None)
    assert cancel_ev is None

    # Raum ist im CallRoomService noch bekannt
    assert CallRoomService.is_known(raum) is True

    # Alice kann ihren aktiven Anruf (weil Bob noch wartet) über /active wieder abfragen und beitreten
    resp_act_alice = client.get("/api/social/calls/active", cookies=c_alice)
    assert resp_act_alice.json()["has_active_call"] is True
    assert resp_act_alice.json()["call"]["raum"] == raum


def test_unaccepted_call_does_not_offer_active_call_to_callee(client: TestClient, db: Session) -> None:
    alice = _user(db, "alice_unacc_rejoin")
    bob = _user(db, "bob_unacc_rejoin")
    _befreunde(db, alice, bob)

    c_alice = _login(client, alice.username)
    c_bob = _login(client, bob.username)

    # Alice ruft Bob an
    resp_inv = client.post(
        f"/api/social/calls/invite/{bob.id}",
        cookies=c_alice,
        headers=_csrf(c_alice),
    )
    raum = resp_inv.json()["signaling_token"]

    # Bob hat noch NICHT angenommen!
    # Bob darf keinen aktiven Anruf (/active) sehen, da er noch nicht angenommen hat:
    resp_act_bob = client.get("/api/social/calls/active", cookies=c_bob)
    assert resp_act_bob.json()["has_active_call"] is False

    # Stattdessen muss Bob die ausstehende Einladung unter /pending sehen:
    resp_pen_bob = client.get("/api/social/calls/pending", cookies=c_bob)
    assert resp_pen_bob.json()["has_pending_call"] is True
    assert resp_pen_bob.json()["call"]["signaling_token"] == raum


