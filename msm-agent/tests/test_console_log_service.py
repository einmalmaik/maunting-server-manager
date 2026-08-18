from pathlib import Path
from types import SimpleNamespace

import pytest

from services.console_log_service import read_declared_log_updates
from services import docker_service
from services.docker_service import DockerUnavailableError


def test_reads_declared_file_tail_and_redacts_secret(tmp_path: Path) -> None:
    target = tmp_path / "ShooterGame" / "Saved" / "Logs" / "ShooterGame.log"
    target.parent.mkdir(parents=True)
    target.write_text(
        "Server ready\nCommandline: ?ServerAdminPassword=synthetic-secret-not-real\n",
        encoding="utf-8",
    )
    positions: dict[str, int] = {}

    lines = read_declared_log_updates(
        root=tmp_path,
        sources=["ShooterGame/Saved/Logs/ShooterGame.log", "stdout"],
        redactors=["regex:ServerAdminPassword"],
        max_tail_bytes=4096,
        positions=positions,
    )

    text = "\n".join(lines)
    assert "Server ready" in text
    assert "synthetic-secret-not-real" not in text
    assert "[REDACTED]" in text


def test_rejects_symlink_escape_even_if_source_was_schema_valid(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-console.log"
    outside.write_text("must-not-leak\n", encoding="utf-8")
    (tmp_path / "escape.log").symlink_to(outside)

    lines = read_declared_log_updates(
        root=tmp_path,
        sources=["escape.log"],
        redactors=[],
        max_tail_bytes=4096,
        positions={},
    )

    assert lines == []


def test_managed_bind_root_uses_workdir_mount_inside_servers_root(
    tmp_path: Path, monkeypatch
) -> None:
    server_root = tmp_path / "ark_107"
    server_root.mkdir()
    container = SimpleNamespace(attrs={
        "Config": {"WorkingDir": "/home/container"},
        "Mounts": [{
            "Type": "bind",
            "Source": str(server_root),
            "Destination": "/home/container",
            "RW": True,
        }],
    })
    monkeypatch.setattr(docker_service.settings, "servers_dir", str(tmp_path))
    monkeypatch.setattr(docker_service, "_get_container", lambda _name: container)

    assert docker_service.managed_bind_root("msm-srv-107") == server_root


def test_managed_bind_root_rejects_mount_outside_servers_root(
    tmp_path: Path, monkeypatch
) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    container = SimpleNamespace(attrs={
        "Config": {"WorkingDir": "/data"},
        "Mounts": [{
            "Type": "bind",
            "Source": str(outside),
            "Destination": "/data",
            "RW": True,
        }],
    })
    monkeypatch.setattr(docker_service.settings, "servers_dir", str(managed))
    monkeypatch.setattr(docker_service, "_get_container", lambda _name: container)

    with pytest.raises(DockerUnavailableError):
        docker_service.managed_bind_root("msm-srv-107")