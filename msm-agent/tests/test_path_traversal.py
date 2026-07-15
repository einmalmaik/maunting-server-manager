"""Path-traversal protection for file_service and /files endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services import file_service
from services.file_service import PathEscapeError, PathValidationError


def test_safe_path_blocks_dotdot(servers_dir: Path) -> None:
    (servers_dir / "1").mkdir()
    with pytest.raises(PathValidationError):
        file_service.safe_path("1", "../../etc/passwd")


def test_safe_path_blocks_absolute(servers_dir: Path) -> None:
    (servers_dir / "1").mkdir()
    with pytest.raises(PathValidationError):
        file_service.safe_path("1", "/etc/passwd")


def test_safe_path_allows_nested(servers_dir: Path) -> None:
    root = servers_dir / "1"
    root.mkdir()
    (root / "cfg").mkdir()
    target = file_service.safe_path("1", "cfg/server.ini")
    assert str(target).startswith(str(root.resolve()))


def test_server_id_escape_rejected(servers_dir: Path) -> None:
    with pytest.raises(PathValidationError):
        file_service.server_root("../escape")


def test_read_endpoint_blocks_traversal(client: TestClient, auth_headers: dict, servers_dir: Path) -> None:
    (servers_dir / "1").mkdir()
    r = client.get(
        "/files/read",
        params={"server_id": "1", "path": "../../etc/passwd"},
        headers=auth_headers,
    )
    assert r.status_code in (400, 403)


def test_write_and_read_roundtrip(client: TestClient, auth_headers: dict, servers_dir: Path) -> None:
    (servers_dir / "42").mkdir()
    w = client.post(
        "/files/write",
        params={"server_id": "42", "path": "hello.txt"},
        headers=auth_headers,
        json={"content": "hello world"},
    )
    assert w.status_code == 200
    r = client.get(
        "/files/read",
        params={"server_id": "42", "path": "hello.txt"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["content"] == "hello world"


def test_list_endpoint(client: TestClient, auth_headers: dict, servers_dir: Path) -> None:
    d = servers_dir / "7"
    d.mkdir()
    (d / "a.txt").write_text("x", encoding="utf-8")
    r = client.get(
        "/files/list",
        params={"server_id": "7", "path": ""},
        headers=auth_headers,
    )
    assert r.status_code == 200
    names = {e["name"] for e in r.json()}
    assert "a.txt" in names


def test_create_dir_and_delete(client: TestClient, auth_headers: dict, servers_dir: Path) -> None:
    (servers_dir / "9").mkdir()
    c = client.post(
        "/files/create-dir",
        params={"server_id": "9", "path": "mods"},
        headers=auth_headers,
    )
    assert c.status_code == 200
    assert (servers_dir / "9" / "mods").is_dir()
    d = client.delete(
        "/files/delete",
        params={"server_id": "9", "path": "mods"},
        headers=auth_headers,
    )
    assert d.status_code == 200
    assert not (servers_dir / "9" / "mods").exists()
