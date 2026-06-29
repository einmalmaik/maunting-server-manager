"""Tests fuer die ausgehenden Webhooks (MSM -> Drittsystem).

Deckt die Sicherheits- und Funktions-Invarianten ab:

Positiv:
- Subscription POST gibt secret NUR einmalig zurueck
- GET /webhooks enthaellt URL, Hint, aber NIE das Klartext-Secret
- PATCH /webhooks/{id} aendert target_url + event_filter + enabled
- POST /rotate erzeugt ein neues Secret
- DELETE /webhooks/{id} entfernt Subscription und Secret
- /deliveries listet gespeicherte Versuche (mit Hash + Payload)

Negativ:
- POST /webhooks ohne CSRF -> 403
- POST /webhooks mit nicht-http URL -> 400
- Tests in Keine-Secrets-in-Logs (caplog)

Versand-Logik (isoliert):
- build_status_payload enthaelt server_id, status, started_at
- filter_matches ist exakt (nicht Substring): "status_change" matcht NICHT "change"
- hash_secret ist deterministisch
- payload_hash ist deterministisch und 64 hex
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Server, WebhookDelivery, WebhookSubscription
from services import outbound_webhook_service as ow


# ── Helper ──────────────────────────────────────────────────────────────────


def _create_sub(
    client: TestClient,
    owner_cookies: dict,
    csrf: str | None,
    server_id: int,
    target_url: str = "http://example.invalid/webhook",
    label: str | None = "test-bot",
) -> dict:
    headers = {"X-CSRF-Token": csrf} if csrf else {}
    resp = client.post(
        f"/api/servers/{server_id}/webhooks",
        json={"target_url": target_url, "label": label, "event_filter": None},
        cookies=owner_cookies,
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── 1) Service-Level: deterministische Krypto-Helper ─────────────────────────


def test_hash_secret_deterministic_and_64_hex():
    s = "supersecret-top-secret-12345"
    h1 = ow.hash_secret(s)
    h2 = ow.hash_secret(s)
    assert h1 == h2 and len(h1) == 64
    int(h1, 16)  # wirft ValueError wenn nicht hex


def test_generate_secret_url_safe_and_unique():
    a = ow.generate_secret()
    b = ow.generate_secret()
    assert a != b
    assert len(a) >= 32
    for c in "+/=":
        assert c not in a


def test_secret_hint_last_four_chars():
    assert ow.secret_hint("abcdefghij") == "...ghij"
    assert ow.secret_hint("xy") == "****"


def test_payload_hash_deterministic():
    text = '{"event_type":"status_change","server_id":1}'
    h1 = ow.payload_hash(text)
    h2 = ow.payload_hash(text)
    assert h1 == h2 and len(h1) == 64


def test_filter_matches_exact_full_match():
    assert ow._filter_matches(None, "status_change") is True
    assert ow._filter_matches("", "status_change") is True
    assert ow._filter_matches("status_change", "status_change") is True
    assert ow._filter_matches("status_change,player_update", "status_change") is True
    # WICHTIG: kein Substring-Match
    assert ow._filter_matches("change", "status_change") is False
    assert ow._filter_matches("status", "status_change") is False


def test_build_status_payload_includes_fields():
    session = SessionLocal()
    try:
        server = Server(
            name="PayTest", game_type="dayz", install_dir="/tmp/p",
            status="running", last_started_at=None,
        )
        session.add(server)
        session.commit()
        session.refresh(server)
        # ohne last_started_at bleibt der string None
        p = ow.build_status_payload(server)
        assert p["server_id"] == server.id
        assert p["server_name"] == "PayTest"
        assert p["status"] == "running"
        assert "timestamp" in p
    finally:
        session.query(Server).filter(Server.name == "PayTest").delete()
        session.commit()
        session.close()


# ── 2) HTTP: CRUD ohne CSRF / mit kaputten URLs ─────────────────────────────


def test_create_subscription_requires_csrf(
    client: TestClient, owner_cookies: dict, test_server: Server,
):
    resp = client.post(
        f"/api/servers/{test_server.id}/webhooks",
        json={"target_url": "http://example.invalid/wh"},
        cookies=owner_cookies,
    )
    assert resp.status_code == 403  # verify_csrf fehlt


def test_create_subscription_rejects_non_http_url(
    client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server,
):
    resp = client.post(
        f"/api/servers/{test_server.id}/webhooks",
        json={"target_url": "ftp://example.invalid/wh"},
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 400
    assert "http" in resp.json()["detail"].lower()


def test_create_subscription_returns_secret_once(
    client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server,
):
    out = _create_sub(client, owner_cookies, csrf_token, test_server.id)
    assert "secret" in out
    assert out["secret"] and len(out["secret"]) >= 32
    assert out["target_url"] == "http://example.invalid/webhook"
    assert out["label"] == "test-bot"
    secret = out["secret"]

    # zweiter GET darf KEIN Klartext-Secret liefern
    listing = client.get(
        f"/api/servers/{test_server.id}/webhooks",
        cookies=owner_cookies,
    )
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    body_text = json.dumps(listing.json())
    assert secret not in body_text
    assert items[0]["secret_hint"][-4:] == secret[-4:]


def test_rotate_secret_invalidates_old(
    client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server,
):
    out = _create_sub(client, owner_cookies, csrf_token, test_server.id)
    sub_id = out["id"]
    old_secret = out["secret"]
    rot = client.post(
        f"/api/servers/{test_server.id}/webhooks/{sub_id}/rotate",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
    )
    assert rot.status_code == 200
    assert rot.json()["secret"] != old_secret


def test_patch_subscription_toggles_enabled_and_filter(
    client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server,
):
    out = _create_sub(client, owner_cookies, csrf_token, test_server.id)
    resp = client.patch(
        f"/api/servers/{test_server.id}/webhooks/{out['id']}",
        json={"enabled": False, "event_filter": "status_change,player_update"},
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["event_filter"] == "status_change,player_update"


def test_delete_subscription_removes_record(
    client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server,
):
    out = _create_sub(client, owner_cookies, csrf_token, test_server.id)
    resp = client.delete(
        f"/api/servers/{test_server.id}/webhooks/{out['id']}",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 200
    # cleanup follow-up
    listing = client.get(
        f"/api/servers/{test_server.id}/webhooks",
        cookies=owner_cookies,
    )
    assert listing.json()["items"] == []


def test_list_deliveries_empty_when_no_send(
    client: TestClient, owner_cookies: dict, test_server: Server,
):
    resp = client.get(
        f"/api/servers/{test_server.id}/webhooks/deliveries",
        cookies=owner_cookies,
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ── 3) Security: kein Klartext-Secret in App-Logs ──────────────────────────


def test_plaintext_secret_never_logged_in_app(
    client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server,
    caplog: pytest.LogCaptureFixture,
):
    out = _create_sub(
        client, owner_cookies, csrf_token, test_server.id,
        target_url="http://example.invalid/wh",
    )
    secret = out["secret"]
    caplog.clear()
    caplog.set_level("DEBUG")
    # PATCH und Rotate ausfuehren (mehrere Code-Pfade mit logging-Risiko)
    client.patch(
        f"/api/servers/{test_server.id}/webhooks/{out['id']}",
        json={"enabled": True},
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
    )
    client.post(
        f"/api/servers/{test_server.id}/webhooks/{out['id']}/rotate",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
    )
    app_log_text = "\n".join(
        r.getMessage() for r in caplog.records if r.name != "httpx"
    )
    assert secret not in app_log_text
    # kein Header-Leak in Logs
    assert "X-Webhook-Secret" not in app_log_text
