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


def test_curseforge_search_ark_sanitizes_minecraft_class_id(
    client: TestClient,
    owner_user: User,
    owner_cookies: dict,
    test_server: Server,
) -> None:
    mock_service = AsyncMock()
    mock_service.get_categories = AsyncMock(return_value=[{"id": 9999, "name": "ARK Mods", "isClass": True}])
    mock_service.search_mods = AsyncMock(return_value=[])

    with (
        patch("routers.curseforge.get_plugin", return_value=_mock_curseforge_plugin()),
        patch("routers.curseforge.get_curseforge_service", new=AsyncMock(return_value=mock_service)),
    ):
        response = client.get(
            f"/api/curseforge/search?server_id={test_server.id}&query=spyglass&class_id=6",
            cookies=owner_cookies,
        )

    assert response.status_code == 200
    mock_service.search_mods.assert_awaited_once()
    call_kwargs = mock_service.search_mods.call_args.kwargs
    assert call_kwargs["game_id"] == "83374"
    assert call_kwargs["class_id"] is None
    assert call_kwargs["only_distributable"] is False


def test_curseforge_popular_ark_sanitizes_minecraft_class_id(
    client: TestClient,
    owner_user: User,
    owner_cookies: dict,
    test_server: Server,
) -> None:
    mock_service = AsyncMock()
    mock_service.get_categories = AsyncMock(return_value=[{"id": 9999, "name": "ARK Mods", "isClass": True}])
    mock_service.get_popular_mods = AsyncMock(return_value=[])

    with (
        patch("routers.curseforge.get_plugin", return_value=_mock_curseforge_plugin()),
        patch("routers.curseforge.get_curseforge_service", new=AsyncMock(return_value=mock_service)),
    ):
        response = client.get(
            f"/api/curseforge/popular?server_id={test_server.id}&class_id=4471",
            cookies=owner_cookies,
        )

    assert response.status_code == 200
    mock_service.get_popular_mods.assert_awaited_once()
    call_kwargs = mock_service.get_popular_mods.call_args.kwargs
    assert call_kwargs["game_id"] == "83374"
    assert call_kwargs["class_id"] is None
    assert call_kwargs["only_distributable"] is False


def test_curseforge_search_keeps_valid_game_class_id(
    client: TestClient,
    owner_user: User,
    owner_cookies: dict,
    test_server: Server,
) -> None:
    mock_service = AsyncMock()
    mock_service.get_categories = AsyncMock(return_value=[{"id": 9999, "name": "ARK Mods", "isClass": True}])
    mock_service.search_mods = AsyncMock(return_value=[])

    with (
        patch("routers.curseforge.get_plugin", return_value=_mock_curseforge_plugin()),
        patch("routers.curseforge.get_curseforge_service", new=AsyncMock(return_value=mock_service)),
    ):
        response = client.get(
            f"/api/curseforge/search?server_id={test_server.id}&query=spyglass&class_id=9999",
            cookies=owner_cookies,
        )

    assert response.status_code == 200
    mock_service.search_mods.assert_awaited_once()
    call_kwargs = mock_service.search_mods.call_args.kwargs
    assert call_kwargs["game_id"] == "83374"
    assert call_kwargs["class_id"] == 9999


def test_curseforge_search_numeric_query_falls_back_to_get_mod_details(
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
        subscriptions=5000,
        favorites=120,
        tags=["Utility"],
        game_id="83374",
    )
    mock_service = AsyncMock()
    mock_service.search_mods = AsyncMock(return_value=[])
    mock_service.get_mod_details = AsyncMock(return_value=mock_mod)

    with (
        patch("routers.curseforge.get_plugin", return_value=_mock_curseforge_plugin()),
        patch("routers.curseforge.get_curseforge_service", new=AsyncMock(return_value=mock_service)),
    ):
        response = client.get(
            f"/api/curseforge/search?server_id={test_server.id}&query=927142",
            cookies=owner_cookies,
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["publishedfileid"] == "927142"
    assert data[0]["title"] == "Super Spyglass Plus"
    mock_service.get_mod_details.assert_awaited_once_with("927142")


def test_curseforge_search_numeric_query_ignores_mod_from_different_game(
    client: TestClient,
    owner_user: User,
    owner_cookies: dict,
    test_server: Server,
) -> None:
    now = datetime.now(timezone.utc)
    # Mod belongs to Minecraft (432), server is ARK (83374)
    mock_mod = CurseForgeModInfo(
        publishedfileid="238222",
        title="JEI",
        description="Just Enough Items",
        creator="mezz",
        file_size=1048576,
        created=now,
        updated=now,
        subscriptions=500000,
        favorites=12000,
        tags=["Utility"],
        game_id="432",
    )
    mock_service = AsyncMock()
    mock_service.search_mods = AsyncMock(return_value=[])
    mock_service.get_mod_details = AsyncMock(return_value=mock_mod)

    with (
        patch("routers.curseforge.get_plugin", return_value=_mock_curseforge_plugin()),
        patch("routers.curseforge.get_curseforge_service", new=AsyncMock(return_value=mock_service)),
    ):
        response = client.get(
            f"/api/curseforge/search?server_id={test_server.id}&query=238222",
            cookies=owner_cookies,
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 0


def test_curseforge_categories_endpoint(
    client: TestClient,
    owner_user: User,
    owner_cookies: dict,
    test_server: Server,
) -> None:
    mock_service = AsyncMock()
    mock_service.get_categories = AsyncMock(
        return_value=[
            {"id": 1, "name": "Modpacks", "isClass": True},
            {"id": 2, "name": "Creatures", "classId": 1},
        ]
    )

    with (
        patch("routers.curseforge.get_plugin", return_value=_mock_curseforge_plugin()),
        patch("routers.curseforge.get_curseforge_service", new=AsyncMock(return_value=mock_service)),
    ):
        response = client.get(
            f"/api/curseforge/categories?server_id={test_server.id}",
            cookies=owner_cookies,
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["name"] == "Modpacks"
    mock_service.get_categories.assert_awaited_once_with("83374")


def test_curseforge_search_with_semantic_modpacks_class(
    client: TestClient,
    owner_user: User,
    owner_cookies: dict,
    test_server: Server,
) -> None:
    mock_service = AsyncMock()
    mock_service.search_mods = AsyncMock(return_value=[])
    mock_service.find_class_id = AsyncMock(return_value=7777)

    with (
        patch("routers.curseforge.get_plugin", return_value=_mock_curseforge_plugin()),
        patch("routers.curseforge.get_curseforge_service", new=AsyncMock(return_value=mock_service)),
    ):
        response = client.get(
            f"/api/curseforge/search?server_id={test_server.id}&query=pack&class_id=modpacks",
            cookies=owner_cookies,
        )

    assert response.status_code == 200
    mock_service.search_mods.assert_awaited_once()
    assert mock_service.search_mods.call_args[1]["class_id"] == 7777



