from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.orm import Session

from config import settings
from models import Node, Server
from services.local_node_handoff_service import (
    LocalNodeHandoffError,
    handoff_local_node,
)


def _topology(
    db: Session,
    root: Path,
    *,
    status: str = "running",
    legacy_root: bool = False,
) -> tuple[Node, Node, Server]:
    local = Node(
        name="Local",
        host="http://127.0.0.1:9000",
        auth_token_enc="local-token",
        is_local=True,
    )
    replacement = Node(
        name="Standalone agent",
        host="https://192.0.2.10:9000",
        auth_token_enc="replacement-token",
        tls_fingerprint="a" * 64,
        is_local=False,
        status="online",
    )
    db.add_all([local, replacement])
    db.flush()
    server = Server(
        name="Game",
        game_type="test",
        install_dir=str(root / ("test_1" if legacy_root else "1")),
        status=status,
        node=local,
        container_name="msm-srv-1" if status == "running" else None,
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    server_root = root / (f"test_{server.id}" if legacy_root else str(server.id))
    server_root.mkdir(parents=True)
    (server_root / "world.dat").write_text("world", encoding="utf-8")
    return local, replacement, server


def _shared_agent(root: Path, server: Server) -> Mock:
    client = Mock()
    client.health.return_value = {"status": "ok", "docker_connected": True}
    client.list_containers.return_value = [
        {"name": f"msm-srv-{server.id}", "status": server.status}
    ]
    client.files_read.side_effect = lambda server_id, path: (
        root / str(server_id) / path
    ).read_text(encoding="utf-8")
    return client


def test_handoff_proves_shared_storage_and_atomically_reassigns(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "servers_dir", str(tmp_path))
    local, replacement, server = _topology(db, tmp_path)
    client = _shared_agent(tmp_path, server)

    with patch(
        "services.local_node_handoff_service.NodeClient.from_node",
        return_value=client,
    ):
        result = handoff_local_node(db, replacement_node_id=replacement.id)

    db.expire_all()
    assert db.query(Node).filter(Node.id == local.id).first() is None
    assert db.query(Server).filter(Server.id == server.id).one().node_id == replacement.id
    assert result["data_moved"] is False
    assert result["source_data_retained"] is True
    assert list(tmp_path.rglob(".msm-handoff-*")) == []


def test_handoff_normalizes_legacy_local_root_for_agent_contract(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "servers_dir", str(tmp_path))
    _local, replacement, server = _topology(db, tmp_path, legacy_root=True)
    client = _shared_agent(tmp_path, server)

    with patch(
        "services.local_node_handoff_service.NodeClient.from_node",
        return_value=client,
    ):
        result = handoff_local_node(db, replacement_node_id=replacement.id)

    db.refresh(server)
    assert result["data_moved"] is True
    assert server.install_dir == str(tmp_path / str(server.id))
    assert not (tmp_path / f"test_{server.id}").exists()
    assert (tmp_path / str(server.id) / "world.dat").read_text(encoding="utf-8") == "world"


def test_handoff_rejects_agent_that_cannot_read_same_storage(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "servers_dir", str(tmp_path))
    local, replacement, server = _topology(db, tmp_path)
    client = _shared_agent(tmp_path, server)
    client.files_read.return_value = "wrong-runtime"
    client.files_read.side_effect = None

    with (
        patch(
            "services.local_node_handoff_service.NodeClient.from_node",
            return_value=client,
        ),
        pytest.raises(LocalNodeHandoffError, match="nicht dasselbe Datenverzeichnis"),
    ):
        handoff_local_node(db, replacement_node_id=replacement.id)

    db.expire_all()
    assert db.query(Node).filter(Node.id == local.id).one().is_local is True
    assert db.query(Server).filter(Server.id == server.id).one().node_id == local.id
    assert list(tmp_path.rglob(".msm-handoff-*")) == []


def test_failed_handoff_restores_legacy_directory_name(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "servers_dir", str(tmp_path))
    local, replacement, server = _topology(db, tmp_path, legacy_root=True)
    client = _shared_agent(tmp_path, server)
    client.files_read.return_value = "wrong-runtime"
    client.files_read.side_effect = None

    with (
        patch(
            "services.local_node_handoff_service.NodeClient.from_node",
            return_value=client,
        ),
        pytest.raises(LocalNodeHandoffError, match="nicht dasselbe Datenverzeichnis"),
    ):
        handoff_local_node(db, replacement_node_id=replacement.id)

    db.expire_all()
    stored = db.query(Server).filter(Server.id == server.id).one()
    assert stored.node_id == local.id
    assert stored.install_dir == str(tmp_path / f"test_{server.id}")
    assert (tmp_path / f"test_{server.id}" / "world.dat").is_file()
    assert not (tmp_path / str(server.id)).exists()


def test_handoff_rejects_missing_running_container(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "servers_dir", str(tmp_path))
    local, replacement, server = _topology(db, tmp_path)
    client = _shared_agent(tmp_path, server)
    client.list_containers.return_value = []

    with (
        patch(
            "services.local_node_handoff_service.NodeClient.from_node",
            return_value=client,
        ),
        pytest.raises(LocalNodeHandoffError, match="erwarteter Container"),
    ):
        handoff_local_node(db, replacement_node_id=replacement.id)

    assert db.query(Node).filter(Node.id == local.id).one().is_local is True


def test_handoff_blocks_transient_server_operations(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "servers_dir", str(tmp_path))
    local, replacement, _server = _topology(db, tmp_path, status="updating")

    with pytest.raises(LocalNodeHandoffError, match="Updatevorgaenge"):
        handoff_local_node(db, replacement_node_id=replacement.id)

    assert db.query(Node).filter(Node.id == local.id).one().is_local is True
