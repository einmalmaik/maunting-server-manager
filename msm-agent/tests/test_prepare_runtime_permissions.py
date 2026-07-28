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
