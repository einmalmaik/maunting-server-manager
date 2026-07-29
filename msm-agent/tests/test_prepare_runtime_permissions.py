from __future__ import annotations

import os
import stat
from pathlib import Path

from fastapi.testclient import TestClient


def test_prepare_runtime_repairs_required_executable(
    client: TestClient,
    auth_headers: dict,
    servers_dir: Path,
) -> None:
    root = servers_dir / "84"
    root.mkdir()
    startup = root / "PalServer.sh"
    startup.write_text("#!/bin/sh\n", encoding="utf-8")
    startup.chmod(0o640)

    response = client.post(
        "/files/prepare-runtime",
        params={"server_id": "84"},
        headers=auth_headers,
        json={
            "ensure_dirs": [],
            "required_files": ["PalServer.sh"],
            "executable_files": ["PalServer.sh"],
            "patches": [],
        },
    )

    assert response.status_code == 200, response.text
    if os.name == "posix":
        assert stat.S_IMODE(startup.stat().st_mode) == 0o750


def test_prepare_runtime_rejects_executable_not_declared_required(
    client: TestClient,
    auth_headers: dict,
    servers_dir: Path,
) -> None:
    root = servers_dir / "85"
    root.mkdir()
    startup = root / "PalServer.sh"
    startup.write_text("#!/bin/sh\n", encoding="utf-8")

    response = client.post(
        "/files/prepare-runtime",
        params={"server_id": "85"},
        headers=auth_headers,
        json={
            "ensure_dirs": [],
            "required_files": [],
            "executable_files": ["PalServer.sh"],
            "patches": [],
        },
    )

    assert response.status_code == 400
    if os.name == "posix":
        assert stat.S_IMODE(startup.stat().st_mode) != 0o750


def test_prepare_runtime_tolerates_chmod_eperm_when_already_executable(
    client: TestClient,
    auth_headers: dict,
    servers_dir: Path,
    monkeypatch,
) -> None:
    root = servers_dir / "86"
    root.mkdir()
    startup = root / "enshrouded_server.exe"
    startup.write_bytes(b"MZ-synthetic-pe")
    startup.chmod(0o777)

    real_chmod = Path.chmod

    def _chmod_eperm(self, mode, *args, **kwargs):  # noqa: ANN001
        if self.name == "enshrouded_server.exe":
            raise PermissionError(1, "Operation not permitted", str(self))
        return real_chmod(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", _chmod_eperm)

    response = client.post(
        "/files/prepare-runtime",
        params={"server_id": "86"},
        headers=auth_headers,
        json={
            "ensure_dirs": [],
            "required_files": ["enshrouded_server.exe"],
            "executable_files": ["enshrouded_server.exe"],
            "patches": [],
        },
    )

    assert response.status_code == 200, response.text
    if os.name == "posix":
        assert stat.S_IMODE(startup.stat().st_mode) & stat.S_IXOTH


def test_prepare_runtime_seeds_file_once_when_missing(
    client: TestClient,
    auth_headers: dict,
    servers_dir: Path,
) -> None:
    root = servers_dir / "88"
    root.mkdir()

    response = client.post(
        "/files/prepare-runtime",
        params={"server_id": "88"},
        headers=auth_headers,
        json={
            "ensure_dirs": ["cfg"],
            "required_files": [],
            "patches": [],
            "seed_files": [
                {
                    "file": "cfg/server.json",
                    "content": '{\n  "queryPort": 27015\n}\n',
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    seeded = root / "cfg" / "server.json"
    assert seeded.is_file()
    assert "27015" in seeded.read_text(encoding="utf-8")

    seeded.write_text('{"queryPort": 1}\n', encoding="utf-8")
    response = client.post(
        "/files/prepare-runtime",
        params={"server_id": "88"},
        headers=auth_headers,
        json={
            "ensure_dirs": ["cfg"],
            "required_files": [],
            "patches": [],
            "seed_files": [
                {
                    "file": "cfg/server.json",
                    "content": '{\n  "queryPort": 27015\n}\n',
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert seeded.read_text(encoding="utf-8") == '{"queryPort": 1}\n'


def test_prepare_runtime_accepts_readable_exe_without_execute_bit(
    client: TestClient,
    auth_headers: dict,
    servers_dir: Path,
    monkeypatch,
) -> None:
    root = servers_dir / "87"
    root.mkdir()
    startup = root / "enshrouded_server.exe"
    startup.write_bytes(b"MZ-synthetic-pe")
    startup.chmod(0o644)

    real_chmod = Path.chmod

    def _chmod_eperm(self, mode, *args, **kwargs):  # noqa: ANN001
        if self.name == "enshrouded_server.exe":
            raise PermissionError(1, "Operation not permitted", str(self))
        return real_chmod(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", _chmod_eperm)

    response = client.post(
        "/files/prepare-runtime",
        params={"server_id": "87"},
        headers=auth_headers,
        json={
            "ensure_dirs": [],
            "required_files": ["enshrouded_server.exe"],
            "executable_files": ["enshrouded_server.exe"],
            "patches": [],
        },
    )

    assert response.status_code == 200, response.text
