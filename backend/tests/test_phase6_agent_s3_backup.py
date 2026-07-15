"""Phase 6: agent-direct S3 backup orchestration (mocked agent + DIS)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.backup_orchestrator import (
    _create_remote_agent_s3_backup,
    restore_via_agent_s3,
)
from services.node_client import NodeClientError


def test_remote_agent_s3_backup_success(db):
    from models import Node, Server

    node = Node(
        name="R",
        host="https://10.0.0.9:9000",
        auth_token_enc="enc",
        tls_fingerprint="a" * 64,
        is_local=False,
        status="online",
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    server = Server(
        name="s",
        game_type="test",
        install_dir="/tmp/x",
        node_id=node.id,
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.node_service.ensure_node_online"), \
         patch("services.backup_config_service.BackupConfigService.get_backup_password", return_value="pw"), \
         patch("services.backup_config_service.BackupConfigService.get_backup_salt", return_value="c2FsdA=="), \
         patch("services.backup_crypto_service.BackupCryptoService.derive_raw_key_b64", return_value="dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGt5dA=="), \
         patch("services.s3_service.S3Service.get_ephemeral_agent_s3_config", return_value={
             "endpoint": "http://minio:9000",
             "access_key": "ak",
             "secret_key": "sk",
             "bucket": "msm",
             "region": "",
         }), \
         patch("services.node_client.NodeClient.from_node") as fn:
        client = MagicMock()
        client.backup_create_s3.return_value = {
            "ok": True,
            "s3_key": "msm-backups/servers/1/x.enc",
            "size_bytes": 5 * 1024 * 1024,
            "sha256": "abc",
        }
        fn.return_value = client
        backup = _create_remote_agent_s3_backup(server.id, db, node, name="n1")

    assert backup.s3_key
    assert backup.encrypted is True
    assert backup.size_mb == 5
    client.backup_create_s3.assert_called_once()
    call_kw = client.backup_create_s3.call_args.kwargs
    assert "encryption_key_b64" in call_kw
    assert call_kw["s3_config"]["access_key"] == "ak"


def test_remote_agent_s3_backup_failure_deletes_record(db):
    from models import Backup, Node, Server

    node = Node(
        name="R2",
        host="https://10.0.0.8:9000",
        auth_token_enc="enc",
        tls_fingerprint="b" * 64,
        is_local=False,
        status="online",
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    server = Server(
        name="s2",
        game_type="test",
        install_dir="/tmp/y",
        node_id=node.id,
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)

    with patch("services.node_service.ensure_node_online"), \
         patch("services.backup_config_service.BackupConfigService.get_backup_password", return_value="pw"), \
         patch("services.backup_config_service.BackupConfigService.get_backup_salt", return_value="c2FsdA=="), \
         patch("services.backup_crypto_service.BackupCryptoService.derive_raw_key_b64", return_value="dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGt5dA=="), \
         patch("services.s3_service.S3Service.get_ephemeral_agent_s3_config", return_value={
             "endpoint": "",
             "access_key": "ak",
             "secret_key": "sk",
             "bucket": "b",
             "region": "",
         }), \
         patch("services.node_client.NodeClient.from_node") as fn:
        client = MagicMock()
        client.backup_create_s3.side_effect = NodeClientError("down")
        fn.return_value = client
        with pytest.raises(RuntimeError, match="Remote-Backup"):
            _create_remote_agent_s3_backup(server.id, db, node)

    assert db.query(Backup).filter(Backup.server_id == server.id).count() == 0


def test_restore_via_agent_s3_calls_client():
    server = SimpleNamespace(id=7, node=SimpleNamespace(is_local=False, status="online", id=1))
    backup = SimpleNamespace(s3_key="k/path.enc", s3_bucket="bucket")

    with patch("services.node_service.resolve_server_node", return_value=server.node), \
         patch("services.node_service.ensure_node_online"), \
         patch("services.backup_config_service.BackupConfigService.get_backup_password", return_value="pw"), \
         patch("services.backup_config_service.BackupConfigService.get_backup_salt", return_value="c2FsdA=="), \
         patch("services.backup_crypto_service.BackupCryptoService.derive_raw_key_b64", return_value="dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGt5dA=="), \
         patch("services.s3_service.S3Service.get_ephemeral_agent_s3_config", return_value={
             "endpoint": "",
             "access_key": "ak",
             "secret_key": "sk",
             "bucket": "other",
             "region": "",
         }), \
         patch("services.node_client.NodeClient.from_node") as fn:
        client = MagicMock()
        fn.return_value = client
        restore_via_agent_s3(server, backup)

    client.backup_restore_s3.assert_called_once()
    call_kw = client.backup_restore_s3.call_args.kwargs
    assert call_kw["s3_key"] == "k/path.enc"
    assert call_kw["s3_config"]["bucket"] == "bucket"


def test_create_server_backup_routes_remote_to_agent(db):
    from models import Node, Server
    from services.backup_orchestrator import create_server_backup

    node = Node(
        name="R3",
        host="https://10.0.0.7:9000",
        auth_token_enc="enc",
        tls_fingerprint="c" * 64,
        is_local=False,
        status="online",
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    server = Server(
        name="s3",
        game_type="test",
        install_dir="/tmp/z",
        node_id=node.id,
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)

    fake = MagicMock()
    fake.id = 99
    with patch("services.backup_config_service.BackupConfigService.is_backup_password_set", return_value=True), \
         patch("services.backup_config_service.BackupConfigService.is_s3_configured", return_value=True), \
         patch("services.backup_orchestrator._create_remote_agent_s3_backup", return_value=fake) as remote:
        out = create_server_backup(server.id, db)
    assert out is fake
    remote.assert_called_once()
