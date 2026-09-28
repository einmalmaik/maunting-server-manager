"""Panel-Datenbank im PostgreSQL-Studio (``/api/panel/database/studio``).

Dieselben Endpunkte wie das Studio eines Servers; hier zählt, was das Ziel
Panel anders macht: globale Rechte, keine Strukturänderung (die gehört den
Migrationen), keine Sicherung (die macht „Panel-Backups“), Audit auch für
Daten und SQL. Ausgeführt wird nichts — ``panel_database_service.run`` ist
ersetzt, geprüft wird nur, was vor der Ausführung liegt.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AuditLog, Role, RolePermission, User

BASE = "/api/panel/database/studio"


def _grant(db: Session, user: User, *keys: str) -> None:
    role = Role(name=f"role-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    user.role_id = role.id
    db.commit()


def _ergebnis(statements, **_kw):
    return {
        "results": [
            {"columns": [], "rows": [], "row_count": 0, "status": "OK", "truncated": False, "duration_ms": 0}
            for _ in statements
        ],
        "notices": [],
        "duration_ms": 1,
    }


@pytest.fixture
def panel_run():
    with (
        patch("services.panel_database_service.database_name", return_value="msm"),
        patch("services.panel_database_service.run", side_effect=_ergebnis) as run,
    ):
        yield run


def _csrf(token: str | None) -> dict:
    return {"X-CSRF-Token": token or ""}


def test_without_panel_database_permission_is_forbidden(client: TestClient, user_cookies: dict, panel_run):
    assert client.get(BASE, cookies=user_cookies).status_code == 403
    panel_run.assert_not_called()


def test_read_permission_opens_the_studio_as_panel(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, panel_run
):
    _grant(db, regular_user, "panel.database.read")

    response = client.get(BASE, cookies=user_cookies)

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "panel"
    assert body["permissions"] == {"read": True, "write": False, "admin": False}
    assert panel_run.call_args.kwargs["mode"] == "read"


def test_read_permission_does_not_change_data_or_run_sql(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, user_csrf_token: str, panel_run
):
    _grant(db, regular_user, "panel.database.read")

    sql = client.post(f"{BASE}/sql", cookies=user_cookies, headers=_csrf(user_csrf_token), json={"sql": "SELECT 1"})
    update = client.post(
        f"{BASE}/rows/update",
        cookies=user_cookies,
        headers=_csrf(user_csrf_token),
        json={"table": "users", "key": {"values": {"id": 1}}, "changes": {"username": "x"}},
    )
    vacuum = client.post(
        f"{BASE}/execute", cookies=user_cookies, headers=_csrf(user_csrf_token), json={"operation": {"op": "vacuum"}}
    )

    assert (sql.status_code, update.status_code, vacuum.status_code) == (403, 403, 403)
    panel_run.assert_not_called()


def test_admin_runs_sql_with_csrf_and_it_is_audited_without_the_text(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, user_csrf_token: str, panel_run
):
    _grant(db, regular_user, "panel.database.read", "panel.database.admin")

    response = client.post(
        f"{BASE}/sql", cookies=user_cookies, headers=_csrf(user_csrf_token), json={"sql": "SELECT 1; SELECT 2"}
    )

    assert response.status_code == 200
    assert panel_run.call_args.kwargs["mode"] == "tx"
    entry = db.query(AuditLog).filter(AuditLog.action == "postgres.studio.sql").one()
    assert entry.target_type == "panel_database"
    assert entry.user_id == regular_user.id
    assert "SELECT" not in (entry.details or "")


def test_sql_without_csrf_is_rejected(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, panel_run
):
    _grant(db, regular_user, "panel.database.admin")

    response = client.post(f"{BASE}/sql", cookies=user_cookies, json={"sql": "SELECT 1"})

    assert response.status_code == 403
    panel_run.assert_not_called()


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "create_schema", "name": "extra"},
        {"op": "drop_table", "schema_name": "public", "name": "users"},
        {"op": "create_extension", "name": "pgcrypto"},
    ],
)
def test_structure_belongs_to_the_migrations(
    client: TestClient, owner_cookies: dict, csrf_token: str, panel_run, operation
):
    for route in ("preview", "execute"):
        response = client.post(
            f"{BASE}/{route}", cookies=owner_cookies, headers=_csrf(csrf_token), json={"operation": operation}
        )
        assert response.status_code == 400, (route, response.text)
        assert "Migrationen" in response.json()["detail"]
    panel_run.assert_not_called()


def test_admin_runs_maintenance(client: TestClient, owner_cookies: dict, csrf_token: str, panel_run):
    response = client.post(
        f"{BASE}/execute",
        cookies=owner_cookies,
        headers=_csrf(csrf_token),
        json={"operation": {"op": "vacuum", "schema_name": "public", "table": "users"}},
    )

    assert response.status_code == 200, response.text
    assert panel_run.call_args.kwargs["mode"] == "autocommit"
    assert "VACUUM" in panel_run.call_args.args[0][0][0]


def test_backup_is_not_the_studios_job(client: TestClient, owner_cookies: dict, csrf_token: str, panel_run):
    dump = client.post(f"{BASE}/backup/dump", cookies=owner_cookies, headers=_csrf(csrf_token), json={})
    pending = client.get(f"{BASE}/backup/pending", cookies=owner_cookies)

    assert (dump.status_code, pending.status_code) == (400, 400)
    assert "Panel-Backups" in dump.json()["detail"]
    panel_run.assert_not_called()


def test_the_old_panel_database_routes_are_gone(client: TestClient, owner_cookies: dict):
    assert client.get("/api/panel/database/stats", cookies=owner_cookies).status_code == 404
