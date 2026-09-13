"""Endpoint-Tests fuer /api/blueprints — Listing, Template, Import, Delete."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from blueprints import reload_registry


@pytest.fixture
def patched_blueprints_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Patcht den community-Pfad auf ein leeres tmp-Verzeichnis."""
    from config import settings
    monkeypatch.setattr(settings, "blueprints_dir", str(tmp_path))
    reload_registry()
    return tmp_path


def _minimal_bp_payload(blueprint_id: str = "my_test_bp") -> dict:
    return {
        "version": 1,
        "meta": {
            "id": blueprint_id,
            "name": "My Test",
            "category": "non_steam_game",
            "author": "tester",
            "description": "",
        },
        "runtime": {
            "image": "alpine",
            "workdir": "/data",
            "env": {},
            "startup": "/data/server -port={GAME_PORT}",
        },
        "ports": [{"name": "game", "protocol": "udp"}],
        "source": {"type": "dockerOnly"},
        "mods": None,
    }


# ── Listing / Template ────────────────────────────────────────────────────


def test_list_requires_auth(client: TestClient) -> None:
    response = client.get("/api/blueprints")
    assert response.status_code == 401


def test_list_returns_native(client: TestClient, user_cookies: dict, patched_blueprints_dir: Path) -> None:
    response = client.get("/api/blueprints", cookies=user_cookies)
    assert response.status_code == 200
    ids = {bp["id"] for bp in response.json()["blueprints"]}
    assert "dayz" in ids
    assert "conan_exiles_ue5" in ids


def test_template_downloadable(client: TestClient, user_cookies: dict) -> None:
    response = client.get("/api/blueprints/template", cookies=user_cookies)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    from blueprints.schema import _strip_json_comments
    data = json.loads(_strip_json_comments(response.text))
    assert data["version"] == 1
    assert "meta" in data and "runtime" in data and "ports" in data
    assert "steam/http/github=checkBased" in response.text
    assert "github, dockerOnly, custom, manualUpload" in response.text
    assert "Dateiname muss <id>.blueprint.json sein" not in response.text


def test_template_requires_auth(client: TestClient) -> None:
    response = client.get("/api/blueprints/template")
    assert response.status_code == 401


def test_export_existing_blueprint(client: TestClient, user_cookies: dict, patched_blueprints_dir: Path) -> None:
    response = client.get("/api/blueprints/dayz", cookies=user_cookies)
    assert response.status_code == 200
    data = json.loads(response.text)
    assert data["meta"]["id"] == "dayz"


def test_export_unknown_returns_404(client: TestClient, user_cookies: dict, patched_blueprints_dir: Path) -> None:
    response = client.get("/api/blueprints/nonexistent_x", cookies=user_cookies)
    assert response.status_code == 404


# ── Import ────────────────────────────────────────────────────────────────


def test_import_requires_owner_perm(client: TestClient, user_cookies: dict, user_csrf_token: str, patched_blueprints_dir: Path) -> None:
    """Standard-User ohne panel.settings.write -> 403."""
    response = client.post(
        "/api/blueprints/import",
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
        json=_minimal_bp_payload("import_block_test"),
    )
    assert response.status_code == 403


def test_import_requires_csrf(client: TestClient, owner_cookies: dict, patched_blueprints_dir: Path) -> None:
    response = client.post(
        "/api/blueprints/import",
        cookies=owner_cookies,
        json=_minimal_bp_payload("import_csrf_test"),
    )
    assert response.status_code == 403


def test_import_creates_community_blueprint(
    client: TestClient,
    owner_cookies: dict,
    csrf_token: str,
    patched_blueprints_dir: Path,
) -> None:
    response = client.post(
        "/api/blueprints/import",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
        json=_minimal_bp_payload("import_happy"),
    )
    assert response.status_code == 201, response.text
    assert (patched_blueprints_dir / "import_happy.blueprint.json").exists()

    listing = client.get("/api/blueprints", cookies=owner_cookies)
    ids = {bp["id"] for bp in listing.json()["blueprints"]}
    assert "import_happy" in ids


def test_import_native_id_rejected(
    client: TestClient,
    owner_cookies: dict,
    csrf_token: str,
    patched_blueprints_dir: Path,
) -> None:
    """Native IDs duerfen nicht ueberschrieben werden."""
    response = client.post(
        "/api/blueprints/import",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
        json=_minimal_bp_payload("dayz"),
    )
    assert response.status_code == 409


def test_import_invalid_json_returns_400(
    client: TestClient,
    owner_cookies: dict,
    csrf_token: str,
    patched_blueprints_dir: Path,
) -> None:
    response = client.post(
        "/api/blueprints/import",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token, "Content-Type": "application/json"},
        content=b"not-json",
    )
    assert response.status_code == 400


def test_import_invalid_schema_returns_400(
    client: TestClient,
    owner_cookies: dict,
    csrf_token: str,
    patched_blueprints_dir: Path,
) -> None:
    payload = _minimal_bp_payload("bad_one")
    payload["runtime"]["startup"] = "/data/srv $(id)"  # forbidden
    response = client.post(
        "/api/blueprints/import",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
        json=payload,
    )
    assert response.status_code == 400
    body = response.json()
    assert "errors" in body["detail"]


# ── Delete ────────────────────────────────────────────────────────────────


def test_delete_native_rejected(
    client: TestClient,
    owner_cookies: dict,
    csrf_token: str,
    patched_blueprints_dir: Path,
) -> None:
    response = client.delete(
        "/api/blueprints/dayz",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 400


def test_delete_community(
    client: TestClient,
    owner_cookies: dict,
    csrf_token: str,
    patched_blueprints_dir: Path,
) -> None:
    # Erst importieren …
    client.post(
        "/api/blueprints/import",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
        json=_minimal_bp_payload("to_delete"),
    )
    # … dann loeschen.
    response = client.delete(
        "/api/blueprints/to_delete",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 204
    assert not (patched_blueprints_dir / "to_delete.blueprint.json").exists()


def test_delete_unknown_returns_404(
    client: TestClient,
    owner_cookies: dict,
    csrf_token: str,
    patched_blueprints_dir: Path,
) -> None:
    response = client.delete(
        "/api/blueprints/does_not_exist",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 404


def test_delete_requires_owner_perm(
    client: TestClient,
    user_cookies: dict,
    user_csrf_token: str,
    patched_blueprints_dir: Path,
) -> None:
    response = client.delete(
        "/api/blueprints/dayz",
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )
    assert response.status_code == 403


def test_import_handles_permission_denied_cleanly(
    client: TestClient,
    owner_cookies: dict,
    csrf_token: str,
    patched_blueprints_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pathlib import Path
    orig_write = Path.write_text

    def mock_write(self, *args, **kwargs):
        if "perm_fail" in str(self):
            raise PermissionError(13, "Permission denied")
        return orig_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", mock_write)
    payload = {
        "version": 1,
        "meta": {"id": "perm_fail", "name": "Perm Fail", "category": "bot"},
        "runtime": {"image": "alpine:latest", "startup": "echo 1"},
        "ports": [],
        "source": {"type": "dockerOnly"},
    }
    resp = client.post(
        "/api/blueprints/import",
        json=payload,
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 500
    assert "Permission denied" in resp.json()["detail"]


def test_delete_handles_permission_denied_cleanly(
    client: TestClient,
    owner_cookies: dict,
    csrf_token: str,
    patched_blueprints_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # First write a normal blueprint
    ziel = patched_blueprints_dir / "perm_del.blueprint.json"
    ziel.write_text('{"version": 1, "meta": {"id": "perm_del", "name": "Perm Del", "category": "bot"}, "runtime": {"image": "alpine", "startup": "echo 1"}, "ports": [], "source": {"type": "dockerOnly"}}', encoding="utf-8")
    from blueprints.registry import reload_registry
    reload_registry()

    from pathlib import Path
    def mock_unlink(self, *args, **kwargs):
        if "perm_del" in str(self):
            raise PermissionError(13, "Permission denied")
        return None

    monkeypatch.setattr(Path, "unlink", mock_unlink)
    resp = client.delete(
        "/api/blueprints/perm_del",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 500
    assert "Permission denied" in resp.json()["detail"]


def test_derive_payload_allows_github_and_steam_branch(
    client: TestClient,
    patched_blueprints_dir: Path,
    db: Session,
) -> None:
    from blueprints.registry import reload_registry
    from services.blueprint_service import derived_payload

    # Create base GitHub blueprint
    ziel = patched_blueprints_dir / "base_gh.blueprint.json"
    ziel.write_text(
        '{"version": 1, "meta": {"id": "base_gh", "name": "Base GH", "category": "bot"}, "runtime": {"image": "node:22", "startup": "node index.js"}, "ports": [], "source": {"type": "github", "github": {"repo": "owner/repo", "branch": "main", "setupCommands": []}}}',
        encoding="utf-8",
    )
    reload_registry()

    derived = derived_payload(
        "base_gh",
        new_id="derived_gh",
        changes={
            "source.github.branch": "dev",
            "source.github.repo": "owner/repo2",
            "source.github.subPath": "src",
        },
        db=db,
    )
    assert derived["meta"]["id"] == "derived_gh"
    assert derived["source"]["github"]["branch"] == "dev"
    assert derived["source"]["github"]["repo"] == "owner/repo2"
    assert derived["source"]["github"]["subPath"] == "src"


def test_import_overwrites_readonly_existing_blueprint(
    client: TestClient,
    owner_cookies: dict,
    csrf_token: str,
    patched_blueprints_dir: Path,
) -> None:
    ziel = patched_blueprints_dir / "readonly_bp.blueprint.json"
    ziel.write_text(
        '{"version": 1, "meta": {"id": "readonly_bp", "name": "Readonly Old", "category": "bot"}, "runtime": {"image": "alpine:latest", "startup": "echo 1"}, "ports": [], "source": {"type": "dockerOnly"}}',
        encoding="utf-8",
    )
    ziel.chmod(0o444)

    payload = {
        "version": 1,
        "meta": {"id": "readonly_bp", "name": "Readonly Updated", "category": "bot"},
        "runtime": {"image": "alpine:latest", "startup": "echo 2"},
        "ports": [],
        "source": {"type": "dockerOnly"},
    }
    resp = client.post(
        "/api/blueprints/import",
        json=payload,
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 201
    assert "Readonly Updated" in ziel.read_text(encoding="utf-8")

