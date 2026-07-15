from __future__ import annotations

import io
import socket
import tarfile
import zipfile
from unittest.mock import patch

from fastapi.testclient import TestClient

from services import file_service, runtime_service
from services.source_service import SourceInstallError, _validate_public_https


def test_chunked_upload_search_move_and_disk(client: TestClient, auth_headers: dict, servers_dir) -> None:
    (servers_dir / "9").mkdir()
    upload_id = "a" * 32
    init = client.post(
        "/files/upload/init",
        params={"server_id": "9"},
        headers=auth_headers,
        json={"upload_id": upload_id, "path": "", "filename": "world.dat", "total_size": 5},
    )
    assert init.status_code == 200, init.text
    chunk = client.put(
        f"/files/upload/{upload_id}/chunk",
        params={"server_id": "9"},
        headers=auth_headers,
        files={"chunk": ("chunk", b"hello")},
    )
    assert chunk.status_code == 200, chunk.text
    done = client.post(
        f"/files/upload/{upload_id}/finalize",
        params={"server_id": "9"},
        headers=auth_headers,
    )
    assert done.status_code == 200, done.text
    assert (servers_dir / "9" / "world.dat").read_bytes() == b"hello"

    (servers_dir / "9" / "data").mkdir()
    moved = client.post(
        "/files/move",
        params={"server_id": "9"},
        headers=auth_headers,
        json={"source_path": "world.dat", "target_path": "data/world.dat"},
    )
    assert moved.status_code == 200, moved.text
    search = client.get(
        "/files/search", params={"server_id": "9", "q": "world"}, headers=auth_headers
    )
    assert search.status_code == 200
    assert search.json()["results"][0]["path"] == "data/world.dat"
    disk = client.get("/files/disk", params={"server_id": "9"}, headers=auth_headers)
    assert disk.status_code == 200
    assert disk.json()["used_bytes"] >= 5
    assert disk.json()["free_bytes"] > 0


def test_archive_traversal_is_rejected(servers_dir) -> None:
    root = servers_dir / "10"
    root.mkdir()
    archive = root / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../escape.txt", "bad")
    try:
        file_service.extract_archive("10", "unsafe.zip")
        raise AssertionError("unsafe archive was accepted")
    except (file_service.PathEscapeError, file_service.PathValidationError):
        pass
    assert not (servers_dir / "escape.txt").exists()


def test_backup_restore_can_rollback_until_finalized(servers_dir) -> None:
    root = servers_dir / "11"
    root.mkdir()
    (root / "world.txt").write_text("old", encoding="utf-8")
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as handle:
        payload = b"new"
        info = tarfile.TarInfo("world.txt")
        info.size = len(payload)
        handle.addfile(info, io.BytesIO(payload))
    archive.seek(0)

    file_service.restore_backup_archive("11", archive)
    assert (root / "world.txt").read_text(encoding="utf-8") == "new"
    file_service.rollback_backup_restore("11")
    assert (root / "world.txt").read_text(encoding="utf-8") == "old"


def test_node_port_check_detects_live_tcp_listener() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    try:
        port = listener.getsockname()[1]
        result = runtime_service.ports_available([(port, "tcp")], "127.0.0.1")
    finally:
        listener.close()
    assert result["available"] is False
    assert result["conflicts"] == [{"port": port, "protocol": "tcp"}]


def test_firewall_endpoint_uses_validated_contract(client: TestClient, auth_headers: dict) -> None:
    with patch("services.runtime_service.firewall", return_value={"ok": True, "results": []}) as call:
        response = client.post(
            "/runtime/firewall/open",
            headers=auth_headers,
            json={
                "server_name": "game-1",
                "ports": [{"port": 27015, "protocol": "udp", "role": "game"}],
            },
        )
    assert response.status_code == 200, response.text
    call.assert_called_once_with("open", [(27015, "udp", "game")], "game-1")


def test_source_url_rejects_private_resolution() -> None:
    with patch("services.source_service.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 0))]):
        try:
            _validate_public_https("https://example.invalid/archive.zip")
            raise AssertionError("private source address was accepted")
        except SourceInstallError:
            pass
