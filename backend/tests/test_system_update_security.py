from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import Role, RolePermission, User


def _grant(db: Session, user: User, *keys: str) -> None:
    role = Role(name="Synthetic update role", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    user.role_id = role.id
    db.commit()


def test_panel_update_requires_csrf(client: TestClient, owner_cookies: dict):
    with patch("services.update_service.trigger_panel_update") as trigger:
        response = client.post("/api/system/update/panel", cookies=owner_cookies)

    assert response.status_code == 403
    trigger.assert_not_called()


def test_node_update_requires_panel_write_and_nodes_manage(
    db: Session,
    regular_user: User,
    client: TestClient,
    user_cookies: dict,
    user_csrf_token: str,
):
    _grant(db, regular_user, "panel.settings.write")
    with patch("services.update_service.trigger_node_updates") as trigger:
        response = client.post(
            "/api/system/update/nodes",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )

    assert response.status_code == 403
    trigger.assert_not_called()


def test_node_update_succeeds_with_both_permissions_and_csrf(
    db: Session,
    regular_user: User,
    client: TestClient,
    user_cookies: dict,
    user_csrf_token: str,
):
    _grant(db, regular_user, "panel.settings.write", "nodes.manage")
    with patch(
        "services.update_service.trigger_node_updates",
        return_value={"ok": True, "results": [], "message": "done"},
    ) as trigger:
        response = client.post(
            "/api/system/update/nodes",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    trigger.assert_called_once()
