"""Tests for Singra inbound webhook verification."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest

from services import singra_webhook_secret_service as secret_svc
from services.panel_settings_service import PanelSettingsService
from services.singra_webhook_handler import verify_request


@pytest.fixture(autouse=True)
def _reset_panel_settings(monkeypatch):
    monkeypatch.delenv("MSM_SINGRA_WEBHOOK_SECRET", raising=False)
    PanelSettingsService.invalidate_cache()
    PanelSettingsService.set(secret_svc._PANEL_KEY_ENC, "")
    yield
    PanelSettingsService.invalidate_cache()


def _sign(body: str, secret: str, ts: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{ts}.{body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def test_verify_accepts_valid_signature():
    secret_svc.rotate_panel_secret()
    secret = secret_svc.resolve_secret()
    body = json.dumps({"event": "webhook_test"})
    ts = str(int(datetime.now(timezone.utc).timestamp()))
    assert verify_request(body.encode(), ts, _sign(body, secret, ts)) is None


def test_verify_rejects_bad_signature():
    secret_svc.rotate_panel_secret()
    body = json.dumps({"event": "webhook_test"})
    ts = str(int(datetime.now(timezone.utc).timestamp()))
    assert verify_request(body.encode(), ts, "sha256=deadbeef") == "invalid_signature"


def test_verify_accepts_iso_8601_timestamp():
    secret_svc.rotate_panel_secret()
    secret = secret_svc.resolve_secret()
    body = json.dumps({"event": "webhook_test"})
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    assert verify_request(body.encode(), ts, _sign(body, secret, ts)) is None