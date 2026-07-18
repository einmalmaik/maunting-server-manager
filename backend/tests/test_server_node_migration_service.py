from __future__ import annotations

import tarfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.orm import Session

from models import Node, Server
from config import settings
from services.server_node_migration_service import (
    ServerNodeMigrationError,
    migrate_server_to_node,
)


def _topology(db: Session, *, bind_ip: str | None = None) -> tuple[Server, Node, Node]:
    source = Node(
        name="Synthetic source",
        host="https://192.0.2.10:9443",
        auth_token_enc="synthetic-source-token",
        tls_fingerprint="a" * 64,
        is_local=False,
        status="online",
    )
    target = Node(
        name="Synthetic target",
        host="https://192.0.2.20:9443",
        auth_token_enc="synthetic-target-token",
        tls_fingerprint="b" * 64,
        is_local=False,
        status="online",
    )
    db.add_all([source, target])
    db.flush()
    server = Server(
        name="Synthetic game server",
        game_type="test",
        install_dir="/synthetic/server",
        status="stopped",
        node=source,
        public_bind_ip=bind_ip,
    )
    server.set_port("game", 27015, "udp")
    db.add(server)
    db.commit()
    db.refresh(server)
    return server, source, target


def _archive_writer(_server, _source_client, archive_path: str, _db) -> None:
    payload = Path(archive_path).parent / "synthetic.txt"
    payload.write_text("synthetic migration data", encoding="utf-8")
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(payload, arcname="synthetic.txt")


def _clients(source: Node, target: Node) -> tuple[Mock, Mock, object]:
    source_client = Mock()
    source_client.health.return_value = {"status": "ok", "docker_connected": True}
    source_client.list_containers.return_value = []
    target_client = Mock()
    target_client.health.return_value = {"status": "ok", "docker_connected": True}
    target_client.list_containers.return_value = []
    target_client.ports_available.return_value = {"available": True, "conflicts": []}

    def factory(node, **_kwargs):
        return source_client if node.id == source.id else target_client

    return source_client, target_client, factory


def test_migration_switches_node_only_after_target_restore(db: Session, tmp_path: Path) -> None:
    server, source, target = _topology(db)
    source_client, target_client, factory = _clients(source, target)

    with (
        patch("services.server_node_migration_service.NodeClient.from_node", side_effect=factory),
        patch("services.server_node_migration_service._write_source_archive", side_effect=_archive_writer),
        patch("services.backup_paths.read_pg_dump_from_archive", return_value={}),
    ):
        result = migrate_server_to_node(
            db,
            server_id=server.id,
            target_node_id=target.id,
            target_bind_ip="198.51.100.20",
            work_dir=str(tmp_path),
        )

    db.refresh(server)
    assert result == {
        "ok": True,
        "server_id": server.id,
        "source_node_id": source.id,
        "target_node_id": target.id,
        "source_retained": True,
        "cleanup_pending": False,
    }
    assert server.node_id == target.id
    assert server.public_bind_ip == "198.51.100.20"
    assert server.install_dir == str(Path(settings.servers_dir) / str(server.id))
    target_client.files_restore_archive.assert_called_once()
    target_client.files_finalize_restore.assert_called_once_with(server.id)
    target_client.files_rollback_restore.assert_not_called()
    source_client.files_delete_server_root.assert_not_called()


def test_failed_target_restore_keeps_source_assignment_and_requests_rollback(
    db: Session, tmp_path: Path
) -> None:
    server, source, target = _topology(db)
    _source_client, target_client, factory = _clients(source, target)
    target_client.files_restore_archive.side_effect = RuntimeError("synthetic target failure")

    with (
        patch("services.server_node_migration_service.NodeClient.from_node", side_effect=factory),
        patch("services.server_node_migration_service._write_source_archive", side_effect=_archive_writer),
        patch("services.backup_paths.read_pg_dump_from_archive", return_value={}),
        pytest.raises(ServerNodeMigrationError, match="nicht sicher migriert"),
    ):
        migrate_server_to_node(
            db,
            server_id=server.id,
            target_node_id=target.id,
            work_dir=str(tmp_path),
        )

    db.refresh(server)
    assert server.node_id == source.id
    target_client.files_rollback_restore.assert_called_once_with(server.id)


def test_fixed_source_ip_requires_explicit_target_bind_ip(db: Session, tmp_path: Path) -> None:
    server, _source, target = _topology(db, bind_ip="192.0.2.10")

    with pytest.raises(ServerNodeMigrationError, match="Ziel-Bind-IP ausdrücklich"):
        migrate_server_to_node(
            db,
            server_id=server.id,
            target_node_id=target.id,
            work_dir=str(tmp_path),
        )


def test_finalize_failure_keeps_committed_target_and_reports_cleanup(
    db: Session, tmp_path: Path
) -> None:
    server, source, target = _topology(db)
    _source_client, target_client, factory = _clients(source, target)
    target_client.files_finalize_restore.side_effect = RuntimeError("synthetic cleanup failure")

    with (
        patch("services.server_node_migration_service.NodeClient.from_node", side_effect=factory),
        patch("services.server_node_migration_service._write_source_archive", side_effect=_archive_writer),
        patch("services.backup_paths.read_pg_dump_from_archive", return_value={}),
    ):
        result = migrate_server_to_node(
            db,
            server_id=server.id,
            target_node_id=target.id,
            work_dir=str(tmp_path),
        )

    db.refresh(server)
    assert server.node_id == target.id
    assert result["cleanup_pending"] is True
    target_client.files_rollback_restore.assert_not_called()
