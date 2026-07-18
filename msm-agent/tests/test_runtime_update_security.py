from __future__ import annotations

import io
import os
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from routers import runtime


def _archive(entries: list[tuple[str, bytes | None, str]]) -> io.BytesIO:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        for name, content, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "../../outside"
                archive.addfile(info)
            else:
                body = content or b""
                info.size = len(body)
                info.mode = 0o640
                archive.addfile(info, io.BytesIO(body))
    payload.seek(0)
    return payload


def _valid_archive() -> io.BytesIO:
    return _archive(
        [
            ("msm-agent", None, "dir"),
            ("msm-agent/main.py", b"app = object()\n", "file"),
            ("msm-agent/requirements.txt", b"fastapi\n", "file"),
        ]
    )


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../outside", "file"),
        ("/absolute", "file"),
        ("msm-agent/link", "symlink"),
    ],
)
def test_update_archive_rejects_traversal_and_links(
    tmp_path: Path, name: str, kind: str
) -> None:
    archive_path = tmp_path / "update.tar.gz"
    archive_path.write_bytes(_archive([(name, b"bad", kind)]).getvalue())

    with pytest.raises(ValueError):
        runtime._extract_update_archive(archive_path, tmp_path / "extract")
    assert not (tmp_path / "outside").exists()


def test_update_archive_rejects_excessive_expanded_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "update.tar.gz"
    archive_path.write_bytes(
        _archive(
            [
                ("msm-agent/main.py", b"1234", "file"),
                ("msm-agent/requirements.txt", b"x", "file"),
            ]
        ).getvalue()
    )
    monkeypatch.setattr(runtime, "MAX_UPDATE_EXTRACTED_SIZE", 4)

    with pytest.raises(ValueError, match="size limit"):
        runtime._extract_update_archive(archive_path, tmp_path / "extract")


def test_update_archive_rejects_excessive_file_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "update.tar.gz"
    archive_path.write_bytes(_valid_archive().getvalue())
    monkeypatch.setattr(runtime, "MAX_UPDATE_FILE_COUNT", 2)

    with pytest.raises(ValueError, match="too many"):
        runtime._extract_update_archive(archive_path, tmp_path / "extract")


def test_update_upload_copy_is_bounded() -> None:
    destination = io.BytesIO()
    with pytest.raises(ValueError, match="upload limit"):
        runtime._copy_bounded(io.BytesIO(b"12345"), destination, limit=4)


def test_clean_replacement_removes_stale_files_and_preserves_runtime_state(tmp_path: Path) -> None:
    agent_dir = tmp_path / "msm-agent"
    source_dir = tmp_path / "source"
    agent_dir.mkdir()
    source_dir.mkdir()
    (agent_dir / "stale.py").write_text("old", encoding="utf-8")
    (agent_dir / "main.py").write_text("old", encoding="utf-8")
    (agent_dir / ".env").write_text("secret", encoding="utf-8")
    (agent_dir / "venv").mkdir()
    (source_dir / "main.py").write_text("new", encoding="utf-8")

    runtime._replace_agent_tree(agent_dir, source_dir)

    assert not (agent_dir / "stale.py").exists()
    assert (agent_dir / "main.py").read_text(encoding="utf-8") == "new"
    assert (agent_dir / ".env").read_text(encoding="utf-8") == "secret"
    assert (agent_dir / "venv").is_dir()


def test_dependency_install_is_checked_and_bounded(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    source_dir = tmp_path / "source"
    pip = agent_dir / "venv" / "bin" / "pip"
    pip.parent.mkdir(parents=True)
    pip.write_text("", encoding="utf-8")
    source_dir.mkdir()
    requirements = source_dir / "requirements.txt"
    requirements.write_text("fastapi\n", encoding="utf-8")

    with patch("routers.runtime.subprocess.run") as run:
        runtime._install_dependencies(agent_dir, source_dir)

    assert run.call_args.kwargs["check"] is True
    assert run.call_args.kwargs["timeout"] == runtime.PIP_TIMEOUT_SECONDS


def test_update_does_not_replace_files_when_dependency_install_fails(
    client: TestClient, auth_headers: dict
) -> None:
    with patch(
        "routers.runtime._install_dependencies",
        side_effect=subprocess.CalledProcessError(1, ["pip"]),
    ), patch("routers.runtime._replace_agent_tree") as replace, patch(
        "routers.runtime._schedule_restart"
    ) as restart:
        response = client.post(
            "/runtime/update",
            headers=auth_headers,
            files={"file": ("update.tar.gz", _valid_archive(), "application/gzip")},
        )

    assert response.status_code == 500
    assert response.json()["detail"] == "Dependency installation failed"
    replace.assert_not_called()
    restart.assert_not_called()


def test_update_reports_restart_as_scheduled_not_completed(
    client: TestClient, auth_headers: dict
) -> None:
    with patch("routers.runtime._install_dependencies"), patch(
        "routers.runtime._replace_agent_tree"
    ), patch("routers.runtime._schedule_restart"):
        response = client.post(
            "/runtime/update",
            headers=auth_headers,
            files={"file": ("update.tar.gz", _valid_archive(), "application/gzip")},
        )

    assert response.status_code == 200, response.text
    assert response.json()["restart_status"] == "scheduled"
    assert "erfolgreich" not in response.json()["message"].lower()
