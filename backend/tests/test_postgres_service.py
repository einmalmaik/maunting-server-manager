from unittest.mock import MagicMock, patch

import pytest

from services import postgres_service
from services.postgres_service import PostgresServiceError


def test_identifier_validation_rejects_unsafe_names():
    with pytest.raises(ValueError):
        postgres_service._validate_identifier("public; drop database postgres")
    with pytest.raises(ValueError):
        postgres_service._validate_identifier("../secret")


def test_extension_whitelist_allows_pgcrypto():
    assert postgres_service._validate_extension_name("pgcrypto") == "pgcrypto"
    assert postgres_service._validate_extension_name("  PGCrypto  ") == "pgcrypto"


def test_extension_whitelist_rejects_unknown():
    with pytest.raises(ValueError):
        postgres_service._validate_extension_name("postgis")
    with pytest.raises(ValueError):
        postgres_service._validate_extension_name("pg_stat_statements")


def test_extension_whitelist_rejects_unsafe_names():
    with pytest.raises(ValueError):
        postgres_service._validate_extension_name("pgcrypto; DROP DATABASE postgres")
    with pytest.raises(ValueError):
        postgres_service._validate_extension_name("")


def test_ensure_internal_postgres_proxies_to_agent():
    mock_client = MagicMock()
    mock_client.postgres_ensure.return_value = {"ok": True, "status": "running"}
    mock_db = MagicMock()
    mock_server = MagicMock()
    with patch.object(postgres_service, "_client_for_server", return_value=mock_client), \
         patch.object(postgres_service, "_admin_password", return_value="secret"):
        postgres_service.ensure_internal_postgres(mock_db, mock_server)
    mock_client.postgres_ensure.assert_called_once_with(admin_password="secret")


def test_ensure_internal_postgres_no_node_logs_only():
    mock_db = MagicMock()
    with patch.object(postgres_service, "get_local_node", return_value=None):
        # Must not raise when no node (startup soft-fail path)
        postgres_service.ensure_internal_postgres(mock_db)


def test_provision_uses_node_client_not_psycopg2():
    """Phase 7: no psycopg2 in panel postgres_service module."""
    import services.postgres_service as mod
    assert not hasattr(mod, "psycopg2")
    source = open(mod.__file__, encoding="utf-8").read()
    assert "import psycopg2" not in source
    assert "from psycopg2" not in source


def test_create_database_proxies_provision(db, test_server):
    mock_client = MagicMock()
    mock_client.postgres_provision.return_value = {"ok": True}
    mock_client.postgres_ensure.return_value = {"ok": True}

    with patch.object(postgres_service, "_client_for_server", return_value=mock_client), \
         patch.object(postgres_service, "_client_for_server_id", return_value=mock_client), \
         patch.object(postgres_service, "_admin_password", return_value="admin-pw"), \
         patch.object(
             postgres_service.AuthService,
             "encrypt_secret",
             side_effect=lambda p, aad="": f"enc:{p}",
         ):
        cred = postgres_service.create_database(db, test_server.id)

    assert cred["database_name"].startswith(f"msm_s{test_server.id}_")
    assert "password" in cred
    mock_client.postgres_provision.assert_called_once()
    call_payload = mock_client.postgres_provision.call_args[0][0]
    assert call_payload["admin_password"] == "admin-pw"
    assert "owner_password" in call_payload
    assert "user_password" in call_payload
