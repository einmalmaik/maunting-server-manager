"""Tests fuer msm_admin Passwort-Rotation auf dem Agenten."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services.postgres_service import PostgresAgentError, rotate_admin_password


def test_rotate_admin_password_success():
    """Happy Path: ALTER ROLE mit altem Passwort, Verifikation mit neuem."""
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    verify = MagicMock()

    with patch("services.postgres_service.psycopg2.connect", side_effect=[conn, verify]) as connect:
        result = rotate_admin_password(
            admin_password="old-admin-secret-value",
            new_admin_password="new-admin-secret-value-32chars",
        )

    assert result["ok"] is True
    assert result["admin_user"] == "msm_admin"
    assert "password" not in result or result.get("password") in (None, "")
    # Erster Connect: altes Passwort
    assert connect.call_args_list[0].kwargs["password"] == "old-admin-secret-value"
    # Zweiter Connect: neues Passwort (Verify)
    assert connect.call_args_list[1].kwargs["password"] == "new-admin-secret-value-32chars"
    ddl = str(cur.execute.call_args.args[0])
    assert "ALTER ROLE" in ddl
    assert "PASSWORD" in ddl
    # Parametrisiert — Klartext nicht im SQL-String
    assert "new-admin-secret-value-32chars" not in ddl


def test_rotate_admin_rejects_empty_and_short():
    with pytest.raises(PostgresAgentError, match="admin_password is required"):
        rotate_admin_password(admin_password="", new_admin_password="x" * 20)
    with pytest.raises(PostgresAgentError, match="new_admin_password is required"):
        rotate_admin_password(admin_password="old", new_admin_password="")
    with pytest.raises(PostgresAgentError, match="at least 16"):
        rotate_admin_password(admin_password="old-secret", new_admin_password="short")
    with pytest.raises(PostgresAgentError, match="must differ"):
        rotate_admin_password(admin_password="same-password-here", new_admin_password="same-password-here")


def test_rotate_admin_unavailable_when_connect_fails():
    import psycopg2

    with patch(
        "services.postgres_service.psycopg2.connect",
        side_effect=psycopg2.OperationalError("refused"),
    ), patch("services.postgres_service.time.sleep"):
        with pytest.raises(PostgresAgentError, match="not available") as ei:
            rotate_admin_password(
                admin_password="old-admin-secret-value",
                new_admin_password="new-admin-secret-value-32chars",
            )
    assert ei.value.status_code == 503
