from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import Role, RolePermission, User


def _grant(db: Session, user: User, *keys: str) -> None:
    role = Role(name=f"role-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    user.role_id = role.id
    db.commit()


def test_owner_can_read_panel_database_stats(client: TestClient, owner_cookies: dict):
    with patch("routers.panel_database.panel_database_service.stats", return_value={
        "status": "healthy",
        "latency_ms": 1,
        "size_bytes": 1024,
        "table_count": 3,
        "active_connections": 1,
        "max_connections": 100,
        "database_name": "msm",
        "engine": "PostgreSQL",
    }):
        response = client.get("/api/panel/database/stats", cookies=owner_cookies)

    assert response.status_code == 200
    assert response.json()["database_name"] == "msm"


def test_user_without_panel_database_permission_is_forbidden(client: TestClient, user_cookies: dict):
    response = client.get("/api/panel/database/stats", cookies=user_cookies)

    assert response.status_code == 403


def test_panel_database_sql_requires_admin_permission_and_csrf(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
):
    _grant(db, regular_user, "panel.database.admin")
    with patch("routers.panel_database.panel_database_service.execute_sql", return_value={
        "statements": [],
        "total_duration_ms": 1,
        "statement_timeout_ms": 5000,
    }) as execute_sql:
        response = client.post(
            "/api/panel/database/sql",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token or ""},
            json={"database_id": 0, "sql": "SELECT 1", "limit": 500},
        )

    assert response.status_code == 200
    execute_sql.assert_called_once_with("SELECT 1", 500)


def test_panel_database_sql_rejects_missing_csrf(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
):
    _grant(db, regular_user, "panel.database.admin")

    response = client.post(
        "/api/panel/database/sql",
        cookies=user_cookies,
        json={"database_id": 0, "sql": "SELECT 1", "limit": 500},
    )

    assert response.status_code == 403
