"""Phase 7: panel proxies managed Postgres to NodeClient (no panel psycopg2)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services import postgres_service
from services.node_client import NodeClient
from services.postgres_service import PostgresServiceError


def test_panel_postgres_service_has_no_psycopg2_import():
    import inspect
    import services.postgres_service as mod

    src = inspect.getsource(mod)
    assert "import psycopg2" not in src
    assert "from psycopg2" not in src
    assert "docker_service" not in src


def test_node_client_postgres_methods_exist():
    assert hasattr(NodeClient, "postgres_ensure")
    assert hasattr(NodeClient, "postgres_provision")
    assert hasattr(NodeClient, "postgres_dump")
    assert hasattr(NodeClient, "postgres_restore")
    assert hasattr(NodeClient, "postgres_query")


def test_rotate_user_password_proxies(db, test_server):
    from models import PostgresUser

    user = PostgresUser(
        server_id=test_server.id,
        username="msm_s1_u1",
        password_mask="****xxxx",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    mock_client = MagicMock()
    mock_client.postgres_rotate_user.return_value = {"ok": True}

    with patch.object(postgres_service, "_client_for_server_id", return_value=mock_client), \
         patch.object(postgres_service, "_admin_password", return_value="admin"):
        result = postgres_service.rotate_user_password(db, test_server.id, user.id)

    assert "password" in result
    assert result["username"] == "msm_s1_u1"
    mock_client.postgres_rotate_user.assert_called_once()
    payload = mock_client.postgres_rotate_user.call_args[0][0]
    assert payload["role_name"] == "msm_s1_u1"
    assert payload["admin_password"] == "admin"
    # plaintext password only in response, not stored as plain on user row
    db.refresh(user)
    assert user.password_mask.startswith("****")


def test_list_tables_owner_query(db, test_server):
    from models import PostgresDatabase

    pg = PostgresDatabase(
        server_id=test_server.id,
        name="msm_s1_db1",
        owner_role="msm_s1_o1",
        owner_password_encrypted="enc",
    )
    db.add(pg)
    db.commit()
    db.refresh(pg)

    mock_client = MagicMock()
    mock_client.postgres_query.return_value = [
        {"schema": "public", "name": "players", "row_estimate": 0, "size_bytes": 0}
    ]

    with patch.object(postgres_service, "_client_for_server_id", return_value=mock_client), \
         patch.object(postgres_service, "_owner_password", return_value="owner-pw"):
        tables = postgres_service.list_tables(db, test_server.id, pg.id)

    assert len(tables) == 1
    assert tables[0]["name"] == "players"
    call = mock_client.postgres_query.call_args[0][0]
    assert call["action"] == "list_tables"
    assert call["owner_password"] == "owner-pw"
    assert call["database_name"] == "msm_s1_db1"


def test_drop_server_resources_calls_agent(db, test_server):
    from models import PostgresDatabase, PostgresUser

    db.add(
        PostgresDatabase(
            server_id=test_server.id,
            name="msm_s1_db1",
            owner_role="msm_s1_o1",
            owner_password_encrypted="enc",
        )
    )
    db.add(
        PostgresUser(
            server_id=test_server.id, username="msm_s1_u1", password_mask="****"
        )
    )
    db.commit()

    mock_client = MagicMock()
    mock_client.postgres_drop.return_value = {"ok": True}

    with patch.object(postgres_service, "_client_for_server_id", return_value=mock_client), \
         patch.object(postgres_service, "_admin_password", return_value="admin"):
        postgres_service.drop_server_resources(db, test_server.id)

    mock_client.postgres_drop.assert_called_once()
    payload = mock_client.postgres_drop.call_args[0][0]
    assert "msm_s1_db1" in payload["databases"]
    assert "msm_s1_o1" in payload["owners"]
    assert "msm_s1_u1" in payload["users"]

    # metadata cleared
    from models import PostgresDatabase as PD, PostgresUser as PU

    assert db.query(PD).filter(PD.server_id == test_server.id).count() == 0
    assert db.query(PU).filter(PU.server_id == test_server.id).count() == 0


def test_client_missing_raises():
    mock_db = MagicMock()
    mock_server = MagicMock()
    mock_server.id = 99
    with patch.object(postgres_service, "client_for_server", return_value=None), \
         patch.object(postgres_service, "get_local_node", return_value=None):
        with pytest.raises(PostgresServiceError, match="Node-Agent"):
            postgres_service._client_for_server(mock_db, mock_server)
