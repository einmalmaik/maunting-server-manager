from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import deps, servers
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


@pytest.fixture()
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_server_select_requires_servers_view_permission(client, monkeypatch: pytest.MonkeyPatch):
    app.dependency_overrides[deps.get_current_user] = lambda: _user_with_permissions([])
    monkeypatch.setattr(servers, "get_server_dir", lambda name: SimpleNamespace(is_dir=lambda: True))

    response = client.post("/api/servers/select", json={"name": "alpha"})

    assert response.status_code == 403


def test_server_select_succeeds_with_servers_view_permission(client, monkeypatch: pytest.MonkeyPatch):
    app.dependency_overrides[deps.get_current_user] = lambda: _user_with_permissions(["servers.view"])
    monkeypatch.setattr(servers, "get_server_dir", lambda name: SimpleNamespace(is_dir=lambda: True))

    response = client.post("/api/servers/select", json={"name": "alpha"})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "current_server": "alpha"}


def test_dashboard_requires_dashboard_view_permission(client):
    app.dependency_overrides[deps.get_current_user] = lambda: _user_with_permissions([])
    app.dependency_overrides[deps.get_current_server] = lambda: "alpha"

    response = client.get("/api/dashboard")

    assert response.status_code == 403


def test_action_status_requires_dashboard_view_permission(client):
    app.dependency_overrides[deps.get_current_user] = lambda: _user_with_permissions([])
    app.dependency_overrides[deps.require_server] = lambda: "alpha"

    response = client.get("/api/actions/status")

    assert response.status_code == 403
