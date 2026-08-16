"""Tests für CurseForge Router (Search, Popular, Details, RBAC & Error Contracts)."""

from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from models import Server, User
from services.curseforge_service import CurseForgeApiUnavailable, CurseForgeModInfo


def _mock_curseforge_plugin():
    plugin = MagicMock()
    plugin.supports_mods = True
    plugin.get_mod_support.return_value = {
        "provider": "curseforge",
        "curseforge_game_id": "83374",
        "curseforge_class_id": None,
    }
    return plugin


def test_curseforge_search_returns_structured_missing_key_error(
    client: TestClient,
    owner_user: User,
    owner_cookies: dict,
    test_server: Server,
) -> None:
    with (
        patch("routers.curseforge.get_plugin", return_value=_mock_curseforge_plugin()),
        patch(
            "routers.curseforge.get_curseforge_service",
            new=AsyncMock(side_effect=CurseForgeApiUnavailable("curseforge_api_key_missing")),
        ),
    ):
        response = client.get(
            f"/api/curseforge/search?server_id={test_server.id}&query=test",
            cookies=owner_cookies,
        )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "curseforge_api_key_missing",
        "message": "errors.curseforge_api_key_missing",
    }


def test_curseforge_popular_returns_results(
    client: TestClient,
    owner_user: User,
    owner_cookies: dict,
    test_server: Server,
) -> None:
    now = datetime.now(timezone.utc)
    mock_mod = CurseForgeModInfo(
        publishedfileid="927142",
        title="Super Spyglass Plus",
        description="Shows dino stats",
        creator="Author",
        file_size=10485760,
        created=now,
        updated=now,
        subscriptions=1542000,
        favorites=35000,
        tags=["Utility"],
        preview_url="https://example.com/logo.png",
        direct_url="https://curseforge.com/mod/927142",
    )

    mock_service = AsyncMock()
    mock_service.get_popular_mods = AsyncMock(return_value=[mock_mod])

    with (
        patch("routers.curseforge.get_plugin", return_value=_mock_curseforge_plugin()),
        patch(
            "routers.curseforge.get_curseforge_service",
            new=AsyncMock(return_value=mock_service),
        ),
    ):
        response = client.get(
            f"/api/curseforge/popular?server_id={test_server.id}",
            cookies=owner_cookies,
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["publishedfileid"] == "927142"
    assert data[0]["title"] == "Super Spyglass Plus"


def test_curseforge_details_returns_structured_unavailable_error(
    client: TestClient,
    owner_user: User,
    owner_cookies: dict,
    test_server: Server,
) -> None:
    with (
        patch("routers.curseforge.get_plugin", return_value=_mock_curseforge_plugin()),
        patch(
            "routers.curseforge.get_curseforge_service",
            new=AsyncMock(side_effect=CurseForgeApiUnavailable("curseforge_api_unavailable")),
        ),
    ):
        response = client.get(
            f"/api/curseforge/mod/927142?server_id={test_server.id}",
            cookies=owner_cookies,
        )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "curseforge_api_unavailable",
        "message": "errors.curseforge_api_unavailable",
    }


def test_curseforge_details_invalid_mod_id(
    client: TestClient,
    owner_user: User,
    owner_cookies: dict,
    test_server: Server,
) -> None:
    response = client.get(
        f"/api/curseforge/mod/invalid-alpha-id?server_id={test_server.id}",
        cookies=owner_cookies,
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_workshop_id"


def test_curseforge_server_not_found(
    client: TestClient,
    owner_user: User,
    owner_cookies: dict,
) -> None:
    with patch("dependencies.require_server_permission", return_value=None):
        response = client.get(
            "/api/curseforge/popular?server_id=999999",
            cookies=owner_cookies,
        )
    assert response.status_code == 404


def test_curseforge_game_without_mod_support(
    client: TestClient,
    owner_user: User,
    owner_cookies: dict,
    test_server: Server,
) -> None:
    no_mods_plugin = MagicMock()
    no_mods_plugin.supports_mods = False

    with patch("routers.curseforge.get_plugin", return_value=no_mods_plugin):
        response = client.get(
            f"/api/curseforge/popular?server_id={test_server.id}",
            cookies=owner_cookies,
        )
    assert response.status_code == 400
    assert "unterstützt keine Mods" in response.json()["detail"]


def test_curseforge_game_without_cf_config(
    client: TestClient,
    owner_user: User,
    owner_cookies: dict,
    test_server: Server,
) -> None:
    no_cf_plugin = MagicMock()
    no_cf_plugin.supports_mods = True
    no_cf_plugin.get_mod_support.return_value = {"provider": "steam", "curseforge_game_id": None}

    with patch("routers.curseforge.get_plugin", return_value=no_cf_plugin):
        response = client.get(
            f"/api/curseforge/popular?server_id={test_server.id}",
            cookies=owner_cookies,
        )
    assert response.status_code == 400
    assert "CurseForge für dieses Spiel nicht konfiguriert" in response.json()["detail"]


def test_curseforge_mod_not_found_404(
    client: TestClient,
    owner_user: User,
    owner_cookies: dict,
    test_server: Server,
) -> None:
    mock_service = AsyncMock()
    mock_service.get_mod_details = AsyncMock(return_value=None)

    with (
        patch("routers.curseforge.get_plugin", return_value=_mock_curseforge_plugin()),
        patch("routers.curseforge.get_curseforge_service", new=AsyncMock(return_value=mock_service)),
    ):
        response = client.get(
            f"/api/curseforge/mod/927142?server_id={test_server.id}",
            cookies=owner_cookies,
        )
    assert response.status_code == 404
