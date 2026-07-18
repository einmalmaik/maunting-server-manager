from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from database import Base
from models import Node
from services.multi_node_migration_service import migrate_multi_node_schema


def test_legacy_database_gets_node_columns_and_matching_local_token(
    tmp_path: Path, monkeypatch
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE servers ("
                "id INTEGER PRIMARY KEY, name VARCHAR(255), game_type VARCHAR(100), "
                "install_dir VARCHAR(500), status VARCHAR(50))"
            )
        )
        connection.execute(
            text(
                "INSERT INTO servers (id, name, game_type, install_dir, status) "
                "VALUES (1, 'Synthetic', 'test', '/tmp/test', 'stopped')"
            )
        )
    Base.metadata.tables["nodes"].create(engine)
    agent_env = tmp_path / "agent.env"
    agent_env.write_text('MSM_AGENT_TOKEN="synthetic-agent-token"\n', encoding="utf-8")
    monkeypatch.setattr(
        "services.multi_node_migration_service.settings.local_agent_env_file",
        str(agent_env),
    )
    sessions = sessionmaker(bind=engine)

    with patch(
        "services.multi_node_migration_service.DisClient.encrypt",
        return_value="encrypted-synthetic-token",
    ) as encrypt:
        migrate_multi_node_schema(engine, sessions)

    inspector = inspect(engine)
    assert "node_id" in {column["name"] for column in inspector.get_columns("servers")}
    assert "tls_fingerprint" in {
        column["name"] for column in inspector.get_columns("nodes")
    }
    db = sessions()
    try:
        node = db.query(Node).filter(Node.is_local.is_(True)).one()
        assert node.auth_token_enc == "encrypted-synthetic-token"
        assigned = db.execute(text("SELECT node_id FROM servers WHERE id = 1")).scalar_one()
        assert assigned == node.id
    finally:
        db.close()
    encrypt.assert_called_once_with(
        "synthetic-agent-token", aad="msm:node:auth_token"
    )


def test_missing_local_token_fails_closed(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    Base.metadata.tables["nodes"].create(engine)
    monkeypatch.setattr(
        "services.multi_node_migration_service.settings.local_agent_env_file",
        str(tmp_path / "missing.env"),
    )
    monkeypatch.delenv("MSM_LOCAL_AGENT_TOKEN", raising=False)
    sessions = sessionmaker(bind=engine)

    import pytest

    with pytest.raises(RuntimeError, match="token is missing"):
        migrate_multi_node_schema(engine, sessions)


def test_backend_only_mode_does_not_create_a_local_node(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'backend-only.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    migrate_multi_node_schema(
        engine,
        sessions,
        local_agent_enabled=False,
    )

    db = sessions()
    try:
        assert db.query(Node).count() == 0
    finally:
        db.close()


def test_backend_only_mode_rejects_a_stale_local_node(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'stale-local.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    db = sessions()
    try:
        db.add(
            Node(
                name="Synthetic stale local node",
                host="http://127.0.0.1:9000",
                auth_token_enc="synthetic-encrypted-token",
                is_local=True,
                status="unknown",
            )
        )
        db.commit()
    finally:
        db.close()

    import pytest

    with pytest.raises(RuntimeError, match="convert it to a verified remote node"):
        migrate_multi_node_schema(
            engine,
            sessions,
            local_agent_enabled=False,
        )
