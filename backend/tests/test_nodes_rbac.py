from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from models import Node, Role, RolePermission, User

@pytest.fixture()
def setup_rbac_user(db: Session, regular_user: User):
    def _setup(keys: list[str]) -> None:
        role = Role(name="TestRBACRole", description="Role for testing RBAC", is_system=False)
        db.add(role)
        db.commit()
        db.refresh(role)
        for k in keys:
            db.add(RolePermission(role_id=role.id, permission_key=k))
        db.commit()
        regular_user.role_id = role.id
        db.commit()
    return _setup

def test_list_nodes_rbac_denied(client: TestClient, user_cookies: dict):
    # Regular user has no permissions by default, should be denied
    r = client.get("/api/nodes", cookies=user_cookies)
    assert r.status_code == 403

def test_list_nodes_rbac_allowed_read(db: Session, client: TestClient, regular_user: User, user_cookies: dict, setup_rbac_user):
    setup_rbac_user(["nodes.read"])
    node = Node(name="Test Node", host="http://12.0.0.1:9000", auth_token_enc="enc", is_local=True, status="online")
    db.add(node)
    db.commit()

    with patch("services.node_service.NodeClient.from_node") as from_node:
        from_node.return_value.metrics.return_value = {}
        r = client.get("/api/nodes", cookies=user_cookies)
    assert r.status_code == 200
    db.delete(node)
    db.commit()

def test_create_node_rbac_denied_read_only(db: Session, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str, setup_rbac_user):
    setup_rbac_user(["nodes.read"]) # Read-only, cannot write
    r = client.post(
        "/api/nodes",
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token or ""},
        json={
            "name": "Node-X",
            "host": "https://10.0.0.6:9000",
            "auth_token": "super-secret-agent-token-32chars!!",
            "tls_fingerprint": "a" * 64,
        },
    )
    assert r.status_code == 403

def test_create_node_rbac_allowed_manage(db: Session, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str, setup_rbac_user):
    setup_rbac_user(["nodes.manage"])
    with patch("services.node_service.encrypt_node_token", return_value="enc-token"), \
         patch("routers.nodes.encrypt_node_token", return_value="enc-token"):
        r = client.post(
            "/api/nodes",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token or ""},
            json={
                "name": "Node-Y",
                "host": "https://10.0.0.7:9000",
                "auth_token": "super-secret-agent-token-32chars!!",
                "tls_fingerprint": "a" * 64,
            },
        )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Node-Y"
    # Clean up
    created_node = db.query(Node).filter(Node.name == "Node-Y").first()
    if created_node:
        db.delete(created_node)
        db.commit()

def test_delete_node_rbac_denied_read_only(db: Session, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str, setup_rbac_user):
    setup_rbac_user(["nodes.read"])
    node = Node(name="Test Node Delete", host="https://10.0.0.8:9000", auth_token_enc="enc", is_local=False)
    db.add(node)
    db.commit()
    db.refresh(node)

    r = client.delete(
        f"/api/nodes/{node.id}",
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token or ""},
    )
    assert r.status_code == 403
    db.delete(node)
    db.commit()

def test_delete_node_rbac_allowed_manage(db: Session, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str, setup_rbac_user):
    setup_rbac_user(["nodes.manage"])
    node = Node(name="Test Node Delete 2", host="https://10.0.0.9:9000", auth_token_enc="enc", is_local=False)
    db.add(node)
    db.commit()
    db.refresh(node)

    r = client.delete(
        f"/api/nodes/{node.id}",
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token or ""},
    )
    assert r.status_code == 200
    assert db.query(Node).filter(Node.id == node.id).first() is None
