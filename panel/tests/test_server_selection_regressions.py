from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import deps, servers
from app.main import app
from app.models import User
from app.shell import CommandResult, PanelCommandError
from app.shell import get_server_dir


class DummyRequest:
    def __init__(self, session: dict[str, str]):
        self.session = session


def _owner_user() -> User:
    return User(id=1, username="owner", password_hash="x", role="owner", is_active=True)


def test_deleted_session_server_clears_selection_without_default_fallback(monkeypatch):
    request = DummyRequest({"current_server": "default"})

    monkeypatch.setattr(deps, "_server_exists", lambda name: name == "alpha")
    monkeypatch.setattr(deps, "get_settings", lambda: SimpleNamespace(default_server_name="alpha"))

    assert deps.get_current_server(request) is None
    assert "current_server" not in request.session


def test_existing_default_server_is_used_when_session_is_empty(monkeypatch):
    request = DummyRequest({})

    monkeypatch.setattr(deps, "_server_exists", lambda name: name == "default")
    monkeypatch.setattr(deps, "get_settings", lambda: SimpleNamespace(default_server_name="default"))

    assert deps.get_current_server(request) == "default"


def test_get_server_dir_normalizes_dayz_data_root_when_it_already_points_to_servers(
    monkeypatch,
    tmp_path,
):
    servers_root = tmp_path / "servers"
    monkeypatch.setenv("CONAN_DATA_ROOT", str(servers_root))

    assert get_server_dir("alpha") == servers_root / "alpha"


def test_servers_list_uses_dependency_current_value_not_bridge_current(monkeypatch):
    monkeypatch.setattr(
        servers,
        "fetch_servers_list",
        lambda: {
            "servers": [{"name": "alpha", "display_name": None}],
            "current": "default",
        },
    )

    response = servers.list_servers(user=_owner_user(), current_server=None)

    assert response["servers"][0]["name"] == "alpha"
    assert response["current"] is None


def test_delete_server_rejects_dependency_resolved_current_server(monkeypatch):
    request = DummyRequest({})

    monkeypatch.setattr(servers, "get_server_dir", lambda name: SimpleNamespace(is_dir=lambda: True))

    with pytest.raises(Exception) as exc_info:
        servers.delete_server(
            request=request,
            name="alpha",
            user=_owner_user(),
            db=SimpleNamespace(),
            current_server="alpha",
        )

    assert getattr(exc_info.value, "status_code", None) == 409


def test_clone_server_selects_new_server_and_invokes_core(monkeypatch):
    request = DummyRequest({})
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        servers,
        "get_server_dir",
        lambda name: SimpleNamespace(is_dir=lambda: name == "alpha"),
    )

    def _fake_invoke(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace()

    monkeypatch.setattr(servers, "invoke_core_action", _fake_invoke)

    response = servers.clone_server(
        request=request,
        body=servers.ServerCloneBody(source="alpha", name="beta"),
        user=_owner_user(),
    )

    assert response == {
        "ok": True,
        "source": "alpha",
        "name": "beta",
        "current_server": "beta",
    }
    assert request.session["current_server"] == "beta"
    assert captured["args"] == ("server", "clone", "alpha", "beta")
    assert captured["kwargs"] == {}


def test_clone_server_rejects_missing_source(monkeypatch):
    request = DummyRequest({})

    monkeypatch.setattr(
        servers,
        "get_server_dir",
        lambda name: SimpleNamespace(is_dir=lambda: False),
    )

    with pytest.raises(Exception) as exc_info:
        servers.clone_server(
            request=request,
            body=servers.ServerCloneBody(source="alpha", name="beta"),
            user=_owner_user(),
        )

    assert getattr(exc_info.value, "status_code", None) == 404


def test_clone_server_rejects_existing_target(monkeypatch):
    request = DummyRequest({})

    monkeypatch.setattr(
        servers,
        "get_server_dir",
        lambda name: SimpleNamespace(is_dir=lambda: name in {"alpha", "beta"}),
    )

    with pytest.raises(Exception) as exc_info:
        servers.clone_server(
            request=request,
            body=servers.ServerCloneBody(source="alpha", name="beta"),
            user=_owner_user(),
        )

    assert getattr(exc_info.value, "status_code", None) == 409


def test_clone_server_rejects_identical_names():
    request = DummyRequest({})

    with pytest.raises(Exception) as exc_info:
        servers.clone_server(
            request=request,
            body=servers.ServerCloneBody(source="alpha", name="alpha"),
            user=_owner_user(),
        )

    assert getattr(exc_info.value, "status_code", None) == 409


def test_clone_server_maps_core_target_exists_error(monkeypatch):
    request = DummyRequest({})

    monkeypatch.setattr(
        servers,
        "get_server_dir",
        lambda name: SimpleNamespace(is_dir=lambda: name == "alpha"),
    )

    def _raise_conflict(*_args, **_kwargs):
        raise PanelCommandError(
            CommandResult(
                args=["server", "clone", "alpha", "beta"],
                returncode=1,
                stdout="",
                stderr='Target server "beta" already exists at /tmp/beta.',
            )
        )

    monkeypatch.setattr(servers, "invoke_core_action", _raise_conflict)

    with pytest.raises(Exception) as exc_info:
        servers.clone_server(
            request=request,
            body=servers.ServerCloneBody(source="alpha", name="beta"),
            user=_owner_user(),
        )

    assert getattr(exc_info.value, "status_code", None) == 409


def test_clone_server_maps_core_missing_source_error(monkeypatch):
    request = DummyRequest({})

    monkeypatch.setattr(
        servers,
        "get_server_dir",
        lambda name: SimpleNamespace(is_dir=lambda: name == "beta"),
    )

    def _raise_missing(*_args, **_kwargs):
        raise PanelCommandError(
            CommandResult(
                args=["server", "clone", "alpha", "beta"],
                returncode=1,
                stdout="",
                stderr='Source server "alpha" not found at /tmp/alpha.',
            )
        )

    monkeypatch.setattr(servers, "invoke_core_action", _raise_missing)

    with pytest.raises(Exception) as exc_info:
        servers.clone_server(
            request=request,
            body=servers.ServerCloneBody(source="alpha", name="beta"),
            user=_owner_user(),
        )

    assert getattr(exc_info.value, "status_code", None) == 404


def test_clone_server_requires_view_permission():
    app.dependency_overrides.clear()
    app.dependency_overrides[deps.get_current_user] = lambda: User(
        id=2,
        username="creator",
        password_hash="x",
        role="user",
        permissions='["servers.create"]',
        is_active=True,
    )

    with TestClient(app) as client:
        response = client.post("/api/servers/clone", json={"source": "alpha", "name": "beta"})

    app.dependency_overrides.clear()
    assert response.status_code == 403


def test_servers_current_requires_view_permission():
    app.dependency_overrides.clear()
    app.dependency_overrides[deps.get_current_user] = lambda: User(
        id=2,
        username="viewer",
        password_hash="x",
        role="user",
        permissions="[]",
        is_active=True,
    )
    app.dependency_overrides[deps.get_current_server] = lambda: "alpha"

    with TestClient(app) as client:
        response = client.get("/api/servers/current")

    app.dependency_overrides.clear()
    assert response.status_code == 403


def test_legacy_check_requires_view_permission():
    app.dependency_overrides.clear()
    app.dependency_overrides[deps.get_current_user] = lambda: User(
        id=2,
        username="viewer",
        password_hash="x",
        role="user",
        permissions="[]",
        is_active=True,
    )

    with TestClient(app) as client:
        response = client.get("/api/servers/legacy-check")

    app.dependency_overrides.clear()
    assert response.status_code == 403
