from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.api import deps
from app.main import app
from app.models import User


def _user_with_permissions(permissions: list[str]) -> User:
    return User(
        id=1,
        username="tester",
        password_hash="x",
        role="user",
        permissions=json.dumps(permissions),
        is_active=True,
    )


def test_autorestart_get_requires_view_permission(monkeypatch):
    app.dependency_overrides.clear()
    app.dependency_overrides[deps.get_current_user] = lambda: _user_with_permissions([])
    app.dependency_overrides[deps.require_server] = lambda: "alpha"

    with TestClient(app) as client:
        response = client.get("/api/autorestart")

    app.dependency_overrides.clear()
    assert response.status_code == 403


def test_autorestart_post_requires_manage_permission(monkeypatch):
    app.dependency_overrides.clear()
    app.dependency_overrides[deps.get_current_user] = lambda: _user_with_permissions(["autorestart.view"])
    app.dependency_overrides[deps.require_server] = lambda: "alpha"

    with TestClient(app) as client:
        response = client.post("/api/autorestart", json={"mode": "off", "times": "", "interval_hours": ""})

    app.dependency_overrides.clear()
    assert response.status_code == 403
