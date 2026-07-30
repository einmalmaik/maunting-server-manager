"""SaaS-Betrieb: Admin-Secret-Rotation, Audit-Schreiben, Audit-Liste/RBAC."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AuditLog, User
from services import audit_service, postgres_service
from services.postgres_service import PostgresServiceError, ADMIN_PASSWORD_KEY
from services.panel_settings_service import PanelSettingsService
from services.auth_service import AuthService


# ── Audit helper unit ──────────────────────────────────────────────────────


def test_sanitize_redacts_password_keys():
    """Secret-Keys duerfen nicht im Audit-Text landen."""
    out = audit_service.sanitize_audit_details(
        {"username": "msm_s1_u1", "password": "super-secret-value", "server_id": 1}
    )
    assert out is not None
    assert "super-secret-value" not in out
    assert "[redacted]" in out
    assert "msm_s1_u1" in out


def test_record_privileged_action_writes_row(db: Session, owner_user: User):
    entry = audit_service.record_privileged_action(
        db,
        user_id=owner_user.id,
        action="postgres.database.create",
        target_type="server",
        target_id=7,
        details={"password": "must-not-store", "database_name": "msm_s7_db1"},
        commit=True,
    )
    row = db.query(AuditLog).filter(AuditLog.id == entry.id).first()
    assert row is not None
    assert row.action == "postgres.database.create"
    assert row.target_id == 7
    assert row.details is not None
    assert "must-not-store" not in row.details
    assert "msm_s7_db1" in row.details


def test_record_rejects_empty_action(db: Session):
    with pytest.raises(ValueError, match="Ungueltiger Audit"):
        audit_service.record_privileged_action(db, user_id=1, action="")


# ── Cluster admin rotation (service) ───────────────────────────────────────


def _seed_node(db: Session, *, name: str = "n1", fp_byte: str = "aa") -> "Node":
    from models import Node
    import hashlib

    # Einzigartiger Fingerprint (UNIQUE constraint auf nodes.tls_fingerprint).
    digest = hashlib.sha256(f"{name}:{fp_byte}".encode()).hexdigest()
    node = Node(
        name=name,
        host="https://127.0.0.1:8443",
        auth_token_enc=AuthService.encrypt_secret("agent-token", aad="msm:node:auth_token"),
        tls_fingerprint=digest,
        is_local=False,
        status="online",
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def test_rotate_cluster_admin_password_stores_new_secret(db: Session):
    """Shipped entry: neues DIS-Secret, Agent bekommt altes+neues, Response ohne Passwort."""
    old_plain = "old-cluster-admin-password-aaaaaaaa"
    new_plain_holder: dict[str, str] = {}
    node = _seed_node(db, name="rotate-ok")

    enc = AuthService.encrypt_secret(old_plain, aad="msm:pg:admin")
    PanelSettingsService.set(ADMIN_PASSWORD_KEY, enc)

    mock_client = MagicMock()

    def _rotate_admin(*, admin_password: str, new_admin_password: str):
        assert admin_password == old_plain
        assert new_admin_password != old_plain
        assert len(new_admin_password) >= 16
        new_plain_holder["new"] = new_admin_password
        return {"ok": True, "admin_user": "msm_admin"}

    mock_client.postgres_rotate_admin.side_effect = _rotate_admin

    with patch("services.postgres_service.client_for_node", return_value=mock_client):
        result = postgres_service.rotate_cluster_admin_password(db)

    assert result["ok"] is True
    assert result["admin_user"] == "msm_admin"
    assert "password" not in result
    assert node.id in result["nodes_updated"]
    mock_client.postgres_rotate_admin.assert_called_once()

    stored = PanelSettingsService.get(ADMIN_PASSWORD_KEY, "")
    decrypted = AuthService.decrypt_secret(stored, aad="msm:pg:admin")
    assert decrypted == new_plain_holder["new"]
    assert decrypted != old_plain


def test_rotate_cluster_admin_aborts_on_hard_error_keeps_old_secret(db: Session):
    old_plain = "old-cluster-admin-password-bbbbbbbb"
    enc = AuthService.encrypt_secret(old_plain, aad="msm:pg:admin")
    PanelSettingsService.set(ADMIN_PASSWORD_KEY, enc)
    node = _seed_node(db, name="rotate-fail")

    from services.node_client import NodeClientError

    mock_client = MagicMock()
    mock_client.postgres_rotate_admin.side_effect = NodeClientError(
        "auth failed", status_code=401
    )

    with patch("services.postgres_service.client_for_node", return_value=mock_client):
        with pytest.raises(PostgresServiceError, match="abgebrochen"):
            postgres_service.rotate_cluster_admin_password(db)

    still = AuthService.decrypt_secret(
        PanelSettingsService.get(ADMIN_PASSWORD_KEY, ""), aad="msm:pg:admin"
    )
    assert still == old_plain
    assert node.id  # node exists; rotation aborted


def test_rotate_skips_node_without_postgres(db: Session):
    old_plain = "old-cluster-admin-password-cccccccc"
    enc = AuthService.encrypt_secret(old_plain, aad="msm:pg:admin")
    PanelSettingsService.set(ADMIN_PASSWORD_KEY, enc)
    node = _seed_node(db, name="rotate-skip")

    from services.node_client import NodeClientError

    mock_client = MagicMock()
    mock_client.postgres_rotate_admin.side_effect = NodeClientError(
        "Managed PostgreSQL is not available on this node", status_code=503
    )

    with patch("services.postgres_service.client_for_node", return_value=mock_client):
        result = postgres_service.rotate_cluster_admin_password(db)

    assert result["ok"] is True
    assert node.id in result["nodes_skipped"]
    new = AuthService.decrypt_secret(
        PanelSettingsService.get(ADMIN_PASSWORD_KEY, ""), aad="msm:pg:admin"
    )
    assert new != old_plain


def test_rotate_multi_node_partial_failure_rollback_keeps_old_secret(db: Session):
    """Node1 ok, Node2 hard-fail: erfolgreiche Nodes werden zurückgesetzt, Panel bleibt alt."""
    old_plain = "old-cluster-admin-password-partialrb"
    enc = AuthService.encrypt_secret(old_plain, aad="msm:pg:admin")
    PanelSettingsService.set(ADMIN_PASSWORD_KEY, enc)
    node_ok = _seed_node(db, name="partial-ok")
    node_fail = _seed_node(db, name="partial-fail")

    from services.node_client import NodeClientError

    client_ok = MagicMock()
    client_fail = MagicMock()
    applied_new: list[str] = []

    def rotate_ok(*, admin_password: str, new_admin_password: str):
        # Forward rotate uses old → new; rollback uses new → old.
        if admin_password == old_plain:
            applied_new.append(new_admin_password)
        return {"ok": True, "admin_user": "msm_admin"}

    client_ok.postgres_rotate_admin.side_effect = rotate_ok
    client_fail.postgres_rotate_admin.side_effect = NodeClientError(
        "auth failed", status_code=401
    )

    def _client_for(node, **_kwargs):
        if node.id == node_ok.id:
            return client_ok
        if node.id == node_fail.id:
            return client_fail
        return None

    with patch("services.postgres_service.client_for_node", side_effect=_client_for):
        with pytest.raises(PostgresServiceError, match="zurueckgesetzt"):
            postgres_service.rotate_cluster_admin_password(db)

    still = AuthService.decrypt_secret(
        PanelSettingsService.get(ADMIN_PASSWORD_KEY, ""), aad="msm:pg:admin"
    )
    assert still == old_plain
    # Forward + rollback on successful node
    assert client_ok.postgres_rotate_admin.call_count == 2
    # First call: old → new; second: new → old
    first = client_ok.postgres_rotate_admin.call_args_list[0].kwargs
    second = client_ok.postgres_rotate_admin.call_args_list[1].kwargs
    assert first["admin_password"] == old_plain
    assert second["new_admin_password"] == old_plain
    assert second["admin_password"] == first["new_admin_password"]


def test_rotate_multi_node_partial_failure_rollback_fail_stores_new_secret(db: Session):
    """Node1 ok, Node2 fail, Rollback von Node1 schlägt fehl → Panel + Node1 bleiben/NEU."""
    old_plain = "old-cluster-admin-password-partstore"
    enc = AuthService.encrypt_secret(old_plain, aad="msm:pg:admin")
    PanelSettingsService.set(ADMIN_PASSWORD_KEY, enc)
    node_ok = _seed_node(db, name="partial-store-ok")
    node_fail = _seed_node(db, name="partial-store-fail")

    from services.node_client import NodeClientError

    client_ok = MagicMock()
    client_fail = MagicMock()
    seen_new: dict[str, str] = {}
    # node_ok endet mit NEU (Rollback schlaegt fehl → kein Re-Forward noetig)
    node_secret: dict[int, str] = {node_ok.id: old_plain, node_fail.id: old_plain}

    def rotate_ok(*, admin_password: str, new_admin_password: str):
        if admin_password == old_plain and admin_password == node_secret[node_ok.id]:
            seen_new["pwd"] = new_admin_password
            node_secret[node_ok.id] = new_admin_password
            return {"ok": True, "admin_user": "msm_admin"}
        # Rollback attempt (new → old) fails → Node bleibt auf NEU
        raise NodeClientError("rollback refused", status_code=500)

    client_ok.postgres_rotate_admin.side_effect = rotate_ok
    client_fail.postgres_rotate_admin.side_effect = NodeClientError(
        "auth failed", status_code=401
    )

    def _client_for(node, **_kwargs):
        if node.id == node_ok.id:
            return client_ok
        if node.id == node_fail.id:
            return client_fail
        return None

    with patch("services.postgres_service.client_for_node", side_effect=_client_for):
        with pytest.raises(PostgresServiceError, match="Panel-Secret = neu"):
            postgres_service.rotate_cluster_admin_password(db)

    stored = AuthService.decrypt_secret(
        PanelSettingsService.get(ADMIN_PASSWORD_KEY, ""), aad="msm:pg:admin"
    )
    assert "pwd" in seen_new
    assert stored == seen_new["pwd"]
    assert stored != old_plain
    assert node_secret[node_ok.id] == stored


def test_rotate_three_node_mixed_rollback_reforward_aligns_all_forward_nodes(db: Session):
    """A+B forward ok, C hard-fail; A rollback ok, B rollback fail → re-forward A auf NEU; Panel=NEU.

    Alle vorwaerts-erfolgreichen Nodes (A,B) muessen am Ende dasselbe Secret wie das Panel haben.
    """
    old_plain = "old-cluster-admin-password-3node-mix"
    enc = AuthService.encrypt_secret(old_plain, aad="msm:pg:admin")
    PanelSettingsService.set(ADMIN_PASSWORD_KEY, enc)
    node_a = _seed_node(db, name="partial-3-a")
    node_b = _seed_node(db, name="partial-3-b")
    node_c = _seed_node(db, name="partial-3-c")

    from services.node_client import NodeClientError

    # Simuliertes Passwort pro Node (shipped code steuert die Reihenfolge der Calls).
    secrets_on_node: dict[int, str] = {
        node_a.id: old_plain,
        node_b.id: old_plain,
        node_c.id: old_plain,
    }
    captured_new: dict[str, str] = {}

    def make_rotate(node_id: int, *, rollback_fails: bool = False):
        def _rotate(*, admin_password: str, new_admin_password: str):
            current = secrets_on_node[node_id]
            if admin_password != current:
                raise NodeClientError(
                    f"auth mismatch on node {node_id}", status_code=401
                )
            # Forward: old → new
            if current == old_plain and new_admin_password != old_plain:
                captured_new["pwd"] = new_admin_password
                secrets_on_node[node_id] = new_admin_password
                return {"ok": True, "admin_user": "msm_admin"}
            # Rollback: new → old
            if current != old_plain and new_admin_password == old_plain:
                if rollback_fails:
                    raise NodeClientError("rollback refused", status_code=500)
                secrets_on_node[node_id] = old_plain
                return {"ok": True, "admin_user": "msm_admin"}
            # Re-forward after successful rollback: old → new
            if current == old_plain and new_admin_password == captured_new.get("pwd"):
                secrets_on_node[node_id] = new_admin_password
                return {"ok": True, "admin_user": "msm_admin"}
            raise NodeClientError("unexpected rotate path", status_code=500)

        return _rotate

    client_a = MagicMock()
    client_b = MagicMock()
    client_c = MagicMock()
    client_a.postgres_rotate_admin.side_effect = make_rotate(node_a.id, rollback_fails=False)
    client_b.postgres_rotate_admin.side_effect = make_rotate(node_b.id, rollback_fails=True)
    client_c.postgres_rotate_admin.side_effect = NodeClientError(
        "auth failed", status_code=401
    )

    def _client_for(node, **_kwargs):
        return {
            node_a.id: client_a,
            node_b.id: client_b,
            node_c.id: client_c,
        }.get(node.id)

    with patch("services.postgres_service.client_for_node", side_effect=_client_for):
        with pytest.raises(PostgresServiceError, match="Panel-Secret = neu"):
            postgres_service.rotate_cluster_admin_password(db)

    panel = AuthService.decrypt_secret(
        PanelSettingsService.get(ADMIN_PASSWORD_KEY, ""), aad="msm:pg:admin"
    )
    assert "pwd" in captured_new
    assert panel == captured_new["pwd"]
    # A wurde zurueckgerollt und dann re-forwarded → NEU
    assert secrets_on_node[node_a.id] == panel
    # B blieb auf NEU (Rollback fail)
    assert secrets_on_node[node_b.id] == panel
    # C nie forward-erfolgreich → ALT (bewusst inkonsistent, war hard-fail)
    assert secrets_on_node[node_c.id] == old_plain


# ── HTTP: audit list RBAC + rotation endpoint ──────────────────────────────


def test_audit_list_allowed_for_owner(client: TestClient, owner_cookies: dict, db: Session, owner_user: User):
    audit_service.record_privileged_action(
        db,
        user_id=owner_user.id,
        action="postgres.admin.rotate",
        target_type="managed_postgres",
        details={"nodes_updated": [1]},
        commit=True,
    )
    r = client.get("/api/admin/audit-logs?limit=10", cookies=owner_cookies)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert any(item["action"] == "postgres.admin.rotate" for item in data)
    # Keine Secrets in Response
    blob = r.text.lower()
    assert "password=" not in blob
    assert "token=" not in blob


def test_audit_list_forbidden_for_regular_user(client: TestClient, user_cookies: dict):
    """User ohne system.audit.read bekommt 403, kein stilles leeres OK."""
    r = client.get("/api/admin/audit-logs", cookies=user_cookies)
    assert r.status_code == 403, r.text
    detail = str(r.json().get("detail", "")).lower()
    assert "berechtigung" in detail or "keine" in detail


def test_rotate_admin_http_success(
    client: TestClient, owner_cookies: dict, csrf_token: str, db: Session, owner_user: User
):
    """HTTP-Entry ruft Service auf, speichert Audit, leakt kein Passwort."""
    service_result = {
        "ok": True,
        "admin_user": "msm_admin",
        "nodes_updated": [1],
        "nodes_skipped": [],
    }
    with patch(
        "routers.admin.postgres_service.rotate_cluster_admin_password",
        return_value=service_result,
    ) as mocked:
        r = client.post(
            "/api/admin/managed-postgres/rotate-admin",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["nodes_updated"] == [1]
    assert "password" not in body
    mocked.assert_called_once()
    logs = (
        db.query(AuditLog)
        .filter(AuditLog.action == "postgres.admin.rotate")
        .order_by(AuditLog.id.desc())
        .all()
    )
    assert logs
    assert logs[0].user_id == owner_user.id
    assert "password" not in (logs[0].details or "").lower() or "[redacted]" in (
        logs[0].details or ""
    )


def test_database_bootstrap_writes_audit(
    client: TestClient,
    owner_cookies: dict,
    csrf_token: str,
    db: Session,
    owner_user: User,
    test_server,
):
    fake_creds = [
        {
            "database_id": 1,
            "database_name": "msm_s1_db1",
            "username": "msm_s1_u1",
            "password": "ONE-TIME-SECRET-SHOULD-NOT-AUDIT",
            "host": "msm-postgres",
            "port": 5432,
            "is_power_user": False,
        }
    ]
    with patch(
        "routers.databases.postgres_service.provision_server_databases",
        return_value=fake_creds,
    ):
        r = client.post(
            f"/api/servers/{test_server.id}/databases/bootstrap",
            json={"database_count": 1},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
    assert r.status_code == 200, r.text
    row = (
        db.query(AuditLog)
        .filter(
            AuditLog.action == "postgres.database.provision",
            AuditLog.target_id == test_server.id,
        )
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert row is not None
    assert row.user_id == owner_user.id
    assert "ONE-TIME-SECRET-SHOULD-NOT-AUDIT" not in (row.details or "")
