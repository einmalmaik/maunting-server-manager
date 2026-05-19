from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.api import deps
from app.main import app
from app.models import User


def _user_with_permissions(permissions: list[str]) -> User:
    return User(
        id=1,
        username="tester",
        password_hash="x",
        role="user",
        permissions=json.dumps(permissions),
        is_active=True,
    )


def test_language_set_requires_files_write_permission():
    app.dependency_overrides.clear()
    app.dependency_overrides[deps.get_current_user] = lambda: _user_with_permissions([])
    app.dependency_overrides[deps.require_server] = lambda: "alpha"

    with TestClient(app) as client:
        response = client.post("/api/language", json={"language": "de"})

    app.dependency_overrides.clear()
    assert response.status_code == 403
