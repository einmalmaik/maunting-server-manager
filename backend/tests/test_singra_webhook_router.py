"""Router tests for POST /api/singra-webhook."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from services import singra_webhook_secret_service as secret_svc
from services.panel_settings_service import PanelSettingsService


@pytest.fixture(autouse=True)
def _reset_secrets(monkeypatch):
    monkeypatch.delenv("MSM_SINGRA_WEBHOOK_SECRET", raising=False)
    PanelSettingsService.invalidate_cache()
    PanelSettingsService.set(secret_svc._PANEL_KEY_ENC, "")
    yield
    PanelSettingsService.invalidate_cache()


def _post_signed(client: TestClient, payload: dict, event_id: str = "evt-1"):
    secret = secret_svc.resolve_secret()
    body = json.dumps(payload, separators=(",", ":"))
    ts = str(int(datetime.now(timezone.utc).timestamp()))
    sig = hmac.new(
        secret.encode("utf-8"),
        f"{ts}.{body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return client.post(
        "/api/singra-webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Singra-Timestamp": ts,
            "X-Singra-Signature": f"sha256={sig}",
            "X-Singra-Event-Id": event_id,
            "X-Singra-Event-Type": payload.get("event", ""),
        },
    )


def test_webhook_requires_secret(client: TestClient):
    res = _post_signed(client, {"event": "webhook_test", "data": {}})
    assert res.status_code == 503


def test_webhook_accepts_signed_test_event(client: TestClient):
    secret_svc.rotate_panel_secret()
    payload = {
        "event": "webhook_test",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "ticketId": "00000000-0000-0000-0000-000000000001",
            "guestName": "Test",
            "guestEmail": None,
            "message": "hi",
            "isStaff": False,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        },
    }
    res = _post_signed(client, payload, event_id="unique-evt-abc")
    assert res.status_code == 200
    assert res.json()["ok"] is True

    res2 = _post_signed(client, payload, event_id="unique-evt-abc")
    assert res2.status_code == 200
    assert res2.json().get("deduped") is True