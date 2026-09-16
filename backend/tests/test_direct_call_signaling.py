from __future__ import annotations

import secrets

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from models import PanelSetting, User, UserFriend
from services.auth_service import AuthService
from services.direct_call_service import DirectCallInviteService
from services.panel_settings_service import PanelSettingsService
from services.sync_event_service import SyncEventService


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


def test_direct_call_requires_accepted_friend_and_publishes_invitation(
    db: Session, client: TestClient
) -> None:
    caller = _user(db, f"caller_{secrets.token_hex(4)}")
    recipient = _user(db, f"recipient_{secrets.token_hex(4)}")
    caller_cookies = _login(client, caller.username)
    recipient_cookies = _login(client, recipient.username)

    denied = client.post(
        f"/api/social/webrtc/call/{recipient.id}",
        cookies=caller_cookies,
        headers=_csrf(caller_cookies),
    )
    assert denied.status_code == 403

    db.add(UserFriend(user_id=caller.id, friend_id=recipient.id, status="accepted"))
    db.commit()
    conn_id, queue = SyncEventService.subscribe(recipient.id)
    try:
        response = client.post(
            f"/api/social/webrtc/call/{recipient.id}?mode=video",
            cookies=caller_cookies,
            headers=_csrf(caller_cookies),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["signaling_token"]
        assert payload["recipient_id"] == recipient.id

        event = queue.get_nowait()
        assert event["type"] == "direct_call_invitation"
        assert event["mode"] == "video"
        assert event["signaling_token"] == payload["signaling_token"]
        assert event["recipient_id"] == recipient.id

        with client.websocket_connect(
            "/api/social/webrtc/signal", cookies=caller_cookies
        ) as caller_ws:
            caller_ws.send_json({"action": "join", "token": payload["signaling_token"]})
            assert caller_ws.receive_json()["role"] == "initiator"
            with client.websocket_connect(
                "/api/social/webrtc/signal", cookies=recipient_cookies
            ) as recipient_ws:
                recipient_ws.send_json(
                    {"action": "join", "token": payload["signaling_token"]}
                )
                assert recipient_ws.receive_json()["role"] == "receiver"
                assert caller_ws.receive_json()["event"] == "peer_joined"
    finally:
        SyncEventService.unsubscribe(conn_id)
        DirectCallInviteService.clear_all_for_testing()


def test_direct_call_receiver_may_join_first(
    db: Session, client: TestClient
) -> None:
    caller = _user(db, f"caller_{secrets.token_hex(4)}")
    recipient = _user(db, f"recipient_{secrets.token_hex(4)}")
    caller_cookies = _login(client, caller.username)
    recipient_cookies = _login(client, recipient.username)

    db.add(UserFriend(user_id=caller.id, friend_id=recipient.id, status="accepted"))
    db.commit()
    try:
        response = client.post(
            f"/api/social/webrtc/call/{recipient.id}?mode=audio",
            cookies=caller_cookies,
            headers=_csrf(caller_cookies),
        )
        assert response.status_code == 200
        token = response.json()["signaling_token"]

        with client.websocket_connect(
            "/api/social/webrtc/signal", cookies=recipient_cookies
        ) as recipient_ws:
            recipient_ws.send_json({"action": "join", "token": token})
            assert recipient_ws.receive_json()["event"] == "joined"
            with client.websocket_connect(
                "/api/social/webrtc/signal", cookies=caller_cookies
            ) as caller_ws:
                caller_ws.send_json({"action": "join", "token": token})
                assert caller_ws.receive_json()["event"] == "joined"
                assert recipient_ws.receive_json()["event"] == "peer_joined"
    finally:
        DirectCallInviteService.clear_all_for_testing()


def test_direct_call_cancel_notifies_recipient_and_blocks_late_join(
    db: Session, client: TestClient
) -> None:
    caller = _user(db, f"caller_{secrets.token_hex(4)}")
    recipient = _user(db, f"recipient_{secrets.token_hex(4)}")
    caller_cookies = _login(client, caller.username)
    recipient_cookies = _login(client, recipient.username)

    db.add(UserFriend(user_id=caller.id, friend_id=recipient.id, status="accepted"))
    db.commit()
    conn_id, queue = SyncEventService.subscribe(recipient.id)
    try:
        response = client.post(
            f"/api/social/webrtc/call/{recipient.id}?mode=audio",
            cookies=caller_cookies,
            headers=_csrf(caller_cookies),
        )
        assert response.status_code == 200
        token = response.json()["signaling_token"]
        queue.get_nowait()

        cancel = client.post(
            f"/api/social/webrtc/call/{token}/cancel",
            cookies=caller_cookies,
            headers=_csrf(caller_cookies),
        )
        assert cancel.status_code == 200
        event = queue.get_nowait()
        assert event["type"] == "direct_call_cancelled"
        assert event["signaling_token"] == token

        try:
            with client.websocket_connect(
                "/api/social/webrtc/signal", cookies=recipient_cookies
            ) as recipient_ws:
                recipient_ws.send_json({"action": "join", "token": token})
                message = recipient_ws.receive_json()
                assert message.get("event") == "error"
        except WebSocketDisconnect:
            pass
    finally:
        SyncEventService.unsubscribe(conn_id)
        DirectCallInviteService.clear_all_for_testing()


def test_ice_servers_include_configured_turn(db: Session, owner_user: User) -> None:
    from routers.social import get_webrtc_ice_servers

    try:
        PanelSettingsService.set("webrtc_turn_servers", "turn:turn.example.com:3478", db=db)
        PanelSettingsService.set("webrtc_turn_username", "alice", db=db)
        PanelSettingsService.set("webrtc_turn_credential", "secret", db=db)
        result = get_webrtc_ice_servers(db=db, user=owner_user)
        turn = [
            server
            for server in result["ice_servers"]
            if any(str(url).startswith("turn") for url in server["urls"])
        ]
        assert turn and turn[0]["username"] == "alice"
        assert turn[0]["credential"] == "secret"
        assert any(
            "stun" in str(url) for server in result["ice_servers"] for url in server["urls"]
        )
    finally:
        for key in ("webrtc_turn_servers", "webrtc_turn_username", "webrtc_turn_credential"):
            db.query(PanelSetting).filter_by(key=key).delete()
        db.commit()
        PanelSettingsService.invalidate_cache()
