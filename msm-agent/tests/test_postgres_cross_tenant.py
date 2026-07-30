"""Cross-tenant isolation on the shipped managed-Postgres provision path.

Two databases on one shared cluster: role A must never receive CONNECT on B.
Drives real ``provision`` / ``promote_owner`` (not a hand-rolled ACL simulator).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from services.postgres_service import promote_owner, provision


def _sql_text(arg: object) -> str:
    return str(arg)


def _capture_admin_executes():
    """Return (side_effect for _admin_connect, list that collects every execute SQL)."""
    statements: list[str] = []

    def _make_conn() -> MagicMock:
        conn = MagicMock()
        cur = MagicMock()

        def execute(query, params=None):  # noqa: ANN001
            statements.append(_sql_text(query))

        cur.execute.side_effect = execute
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return conn

    def side_effect(*_args, **_kwargs):
        return _make_conn()

    return side_effect, statements


def test_two_tenant_provision_never_grants_cross_connect():
    """Tenant A (power_user) and tenant B: each CONNECT only to own DB; PUBLIC revoked."""
    side_effect, statements = _capture_admin_executes()

    with patch("services.postgres_service._admin_connect", side_effect=side_effect):
        a = provision(
            admin_password="admin",
            db_name="msm_s1_db1",
            owner_role="msm_s1_o1",
            owner_password="owner-a",
            user_name="msm_s1_u1",
            user_password="user-a",
            power_user=True,
        )
        b = provision(
            admin_password="admin",
            db_name="msm_s2_db1",
            owner_role="msm_s2_o1",
            owner_password="owner-b",
            user_name="msm_s2_u1",
            user_password="user-b",
            power_user=False,
        )

    assert a["power_user"] is True
    assert a["cluster_superuser"] is False
    assert b["power_user"] is False
    assert b["cluster_superuser"] is False

    joined = "\n".join(statements)
    # Isolation primitives from shipped provision DDL
    assert "REVOKE ALL ON DATABASE" in joined
    assert "NOSUPERUSER" in joined
    assert "NOCREATEDB" in joined
    assert "NOCREATEROLE" in joined
    # Never cluster SUPERUSER attribute
    assert " SUPERUSER" not in joined.replace("NOSUPERUSER", "")

    # CONNECT only to own database for each app user (observe grant targets).
    grant_connect = [s for s in statements if "GRANT CONNECT ON DATABASE" in s]
    assert len(grant_connect) == 2
    assert any(
        "msm_s1_db1" in s and "msm_s1_u1" in s for s in grant_connect
    ), grant_connect
    assert any(
        "msm_s2_db1" in s and "msm_s2_u1" in s for s in grant_connect
    ), grant_connect
    # Cross grants must not exist
    assert not any("msm_s1_db1" in s and "msm_s2" in s for s in grant_connect)
    assert not any("msm_s2_db1" in s and "msm_s1" in s for s in grant_connect)

    revoke_public = [s for s in statements if "REVOKE ALL ON DATABASE" in s and "PUBLIC" in s]
    assert any("msm_s1_db1" in s for s in revoke_public)
    assert any("msm_s2_db1" in s for s in revoke_public)

    # Role A never granted CONNECT (or any) on database B
    for s in statements:
        if "msm_s2_db1" in s and ("GRANT" in s):
            assert "msm_s1_u1" not in s
            assert "msm_s1_o1" not in s or "OWNER" in s or "CREATE DATABASE" in s
        if "msm_s1_db1" in s and ("GRANT" in s):
            assert "msm_s2_u1" not in s
            assert "msm_s2_o1" not in s or "OWNER" in s or "CREATE DATABASE" in s


def test_power_user_promote_stays_database_scoped_and_not_cross_tenant():
    """promote_owner keeps NOSUPERUSER; no DDL touches another tenant's database."""
    side_effect, statements = _capture_admin_executes()

    with patch("services.postgres_service._admin_connect", side_effect=side_effect):
        result = promote_owner(
            admin_password="admin",
            owner_role="msm_s1_o1",
            new_password="rotated-a",
        )

    assert result["scope"] == "database"
    assert result["username"] == "msm_s1_o1"
    joined = "\n".join(statements)
    assert "NOSUPERUSER" in joined
    assert "NOCREATEDB" in joined
    assert "NOCREATEROLE" in joined
    assert " SUPERUSER" not in joined.replace("NOSUPERUSER", "")
    assert "msm_s2" not in joined
    assert "CREATE DATABASE" not in joined
    assert "GRANT CONNECT" not in joined


def test_postgres_connect_semantics_documented_for_shipped_grants():
    """Postgres enforces CONNECT at connection time for non-owners without GRANT.

    Shipped provision: REVOKE ALL … FROM PUBLIC + GRANT CONNECT only to the
    tenant's app role on its own database. Without CONNECT (and without being
    the database owner or a superuser), PostgreSQL rejects the session before
    any SQL runs — so the grant set above is the isolation boundary under test.
    """
    # Structural lock: provision source must still emit the two isolation primitives.
    import inspect

    from services import postgres_service as mod

    src = inspect.getsource(mod.provision)
    assert "REVOKE ALL ON DATABASE" in src
    assert "FROM PUBLIC" in src
    assert "GRANT CONNECT ON DATABASE" in src
    assert "NOSUPERUSER" in src
    assert "NOCREATEDB" in src
    assert "NOCREATEROLE" in src
