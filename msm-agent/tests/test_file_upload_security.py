from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_single_upload_streams_to_atomic_temp_and_replaces(
    client: TestClient, auth_headers: dict, servers_dir: Path
) -> None:
    root = servers_dir / "22"
    root.mkdir()
    target = root / "world.dat"
    target.write_bytes(b"old")

    response = client.post(
        "/files/upload",
        params={"server_id": "22", "path": "world.dat"},
        headers=auth_headers,
        files={"file": ("world.dat", b"new-data")},
    )

    assert response.status_code == 200, response.text
    assert target.read_bytes() == b"new-data"
    assert list(root.glob(".msm-upload-*")) == []


def test_oversized_single_upload_preserves_destination_and_cleans_temp(
    client: TestClient, auth_headers: dict, servers_dir: Path, monkeypatch
) -> None:
    root = servers_dir / "23"
    root.mkdir()
    target = root / "world.dat"
    target.write_bytes(b"old")
    monkeypatch.setattr("routers.files.MAX_SINGLE_UPLOAD_SIZE", 5)

    response = client.post(
        "/files/upload",
        params={"server_id": "23", "path": "world.dat"},
        headers=auth_headers,
        files={"file": ("world.dat", b"123456")},
    )

    assert response.status_code == 413
    assert target.read_bytes() == b"old"
    assert list(root.glob(".msm-upload-*")) == []
