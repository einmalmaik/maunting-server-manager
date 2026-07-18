"""Unit tests for agent managed Postgres (Phase 7) — mocked docker/psycopg2."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.postgres_service import (
    PostgresAgentError,
    dispatch_query,
    ensure_internal_postgres,
    provision,
    promote_owner,
    restore_sql,
    validate_identifier,
)


def test_validate_identifier_rejects_injection():
    with pytest.raises(PostgresAgentError):
        validate_identifier("public; drop database postgres")
    with pytest.raises(PostgresAgentError):
        validate_identifier("../secret")
    assert validate_identifier("msm_s1_db1") == "msm_s1_db1"


def test_ensure_starts_existing_container():
    with patch("services.postgres_service.docker_service.ensure_network", return_value={"ok": True}), \
         patch("services.postgres_service.docker_service.inspect_managed_state",
               return_value={"status": "exited"}), \
         patch("services.postgres_service.docker_service.start_managed",
               return_value={"ok": True}) as start, \
         patch("services.postgres_service.docker_service.ensure_managed_restart_policy",
               return_value={"ok": True}), \
         patch("services.postgres_service.os.makedirs"):
        result = ensure_internal_postgres("admin-secret")
    assert result["ok"] is True
    start.assert_called_once()


def test_ensure_creates_with_loopback_only():
    with patch("services.postgres_service.docker_service.ensure_network", return_value={"ok": True}), \
         patch("services.postgres_service.docker_service.inspect_managed_state", return_value=None), \
         patch("services.postgres_service.docker_service.run_managed_postgres",
               return_value={"ok": True}) as run, \
         patch("services.postgres_service.docker_service.ensure_managed_restart_policy",
               return_value={"ok": True}), \
         patch("services.postgres_service._connect_with_retry", return_value=MagicMock()), \
         patch("services.postgres_service.os.makedirs"):
        ensure_internal_postgres("admin-secret")
    assert run.call_count == 2
    bootstrap = run.call_args_list[0].kwargs
    sanitized = run.call_args_list[1].kwargs
    assert bootstrap["host_ip"] == "127.0.0.1"
    assert bootstrap["env"]["POSTGRES_USER"] == "msm_admin"
    # password present but we only check it was passed (not logged)
    assert bootstrap["env"]["POSTGRES_PASSWORD"] == "admin-secret"
    assert sanitized["env"] is None
    assert Path(bootstrap["data_dir"]).is_absolute()
    assert sanitized["data_dir"] == bootstrap["data_dir"]
    assert set(bootstrap["cap_adds"]) == {
        "CHOWN", "FOWNER", "SETUID", "SETGID", "DAC_OVERRIDE", "DAC_READ_SEARCH"
    }


def test_ensure_rejects_empty_password():
    with pytest.raises(PostgresAgentError):
        ensure_internal_postgres("")


def test_provision_executes_ddl():
    admin_conn = MagicMock()
    admin_cur = MagicMock()
    admin_conn.cursor.return_value.__enter__ = MagicMock(return_value=admin_cur)
    admin_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    db_conn = MagicMock()
    db_cur = MagicMock()
    db_conn.cursor.return_value.__enter__ = MagicMock(return_value=db_cur)
    db_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch("services.postgres_service._admin_connect", side_effect=[admin_conn, db_conn]):
        result = provision(
            admin_password="a",
            db_name="msm_s1_db1",
            owner_role="msm_s1_o1",
            owner_password="op",
            user_name="msm_s1_u1",
            user_password="up",
            power_user=False,
        )
    assert result["ok"] is True
    assert result["database_name"] == "msm_s1_db1"
    assert admin_cur.execute.call_count >= 4


def test_power_user_provision_never_grants_cluster_superuser():
    admin_conn = MagicMock()
    admin_cur = MagicMock()
    admin_conn.cursor.return_value.__enter__ = MagicMock(return_value=admin_cur)
    admin_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    db_conn = MagicMock()
    db_cur = MagicMock()
    db_conn.cursor.return_value.__enter__ = MagicMock(return_value=db_cur)
    db_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch("services.postgres_service._admin_connect", side_effect=[admin_conn, db_conn]):
        result = provision(
            admin_password="a",
            db_name="msm_s1_db1",
            owner_role="msm_s1_o1",
            owner_password="op",
            user_name="msm_s1_u1",
            user_password="up",
            power_user=True,
        )

    owner_ddl = str(admin_cur.execute.call_args_list[0].args[0])
    assert "NOSUPERUSER" in owner_ddl
    assert "PASSWORD %s SUPERUSER" not in owner_ddl
    assert result["is_superuser"] is False
    assert result["power_user"] is True


def test_promote_owner_remains_database_scoped():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with patch("services.postgres_service._admin_connect", return_value=conn):
        result = promote_owner(
            admin_password="a", owner_role="msm_s1_o1", new_password="rotated"
        )

    ddl = str(cur.execute.call_args.args[0])
    assert "NOSUPERUSER" in ddl
    assert "NOCREATEDB" in ddl
    assert "NOCREATEROLE" in ddl
    assert "WITH SUPERUSER" not in ddl
    assert result["scope"] == "database"


def test_dispatch_query_unknown_action():
    with pytest.raises(PostgresAgentError, match="Unknown"):
        dispatch_query("not_an_action", {})


def test_managed_postgres_name_guard():
    from services.docker_service import ContainerNameError, assert_managed_postgres_name
    from config import settings

    assert assert_managed_postgres_name(settings.managed_postgres_container_name) == (
        settings.managed_postgres_container_name
    )
    with pytest.raises(ContainerNameError):
        assert_managed_postgres_name("evil-container")


def test_restore_uses_database_owner_and_stdin_not_argv():
    with patch("services.postgres_service.ensure_internal_postgres"), \
         patch("services.postgres_service.dump_databases", return_value={"msm_s1_db1": "-- old"}), \
         patch("services.postgres_service.docker_service.exec_in_managed_stdin", return_value={"ok": True}) as execute:
        result = restore_sql(
            admin_password="admin-secret",
            dumps={"msm_s1_db1": "-- dump body"},
            owners={
                "msm_s1_db1": {
                    "owner_role": "msm_s1_o1",
                    "owner_password": "owner-secret",
                }
            },
        )

    assert result["ok"] is True
    args = execute.call_args.args
    assert args[1] == [
        "psql", "--no-psqlrc", "--set", "ON_ERROR_STOP=1",
        "--username", "msm_s1_o1", "--dbname", "msm_s1_db1"
    ]
    assert args[2] == "-- dump body"
    assert execute.call_args.kwargs["environment"] == {"PGPASSWORD": "owner-secret"}


@pytest.mark.parametrize(
    "malicious_sql",
    [
        "\\! id\nSELECT 1;",
        "  \\copy public.t FROM PROGRAM 'id'\n",
        "SELECT 1;\n\t\\set x y\n",
    ],
)
def test_restore_rejects_all_psql_meta_commands_before_stdin(malicious_sql: str):
    with patch("services.postgres_service.ensure_internal_postgres"), patch(
        "services.postgres_service.dump_databases", return_value={"msm_s1_db1": "SELECT 0;"}
    ), patch("services.postgres_service.docker_service.exec_in_managed_stdin") as execute:
        with pytest.raises(PostgresAgentError, match="meta-commands"):
            restore_sql(
                admin_password="admin-secret",
                dumps={"msm_s1_db1": malicious_sql},
                owners={"msm_s1_db1": {"owner_role": "msm_s1_o1", "owner_password": "owner"}},
            )

    execute.assert_not_called()


def test_restore_rolls_back_prior_database_when_later_dump_has_meta_command():
    with patch("services.postgres_service.ensure_internal_postgres"), patch(
        "services.postgres_service.dump_databases",
        return_value={"msm_s1_db1": "SELECT 'old';", "msm_s1_db2": "SELECT 'old2';"},
    ), patch(
        "services.postgres_service.docker_service.exec_in_managed_stdin",
        return_value={"ok": True},
    ) as execute:
        with pytest.raises(PostgresAgentError, match="rolled back"):
            restore_sql(
                admin_password="admin-secret",
                dumps={"msm_s1_db1": "SELECT 'new';", "msm_s1_db2": "\\! id"},
            )

    assert execute.call_count == 2
    assert execute.call_args_list[0].args[2] == "SELECT 'new';"
    assert execute.call_args_list[1].args[2] == "SELECT 'old';"
