"""Tests for Steam Workshop router error contracts."""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from models import Server, User
from services.steam_service import SteamApiUnavailable


def test_workshop_search_returns_structured_missing_key_error(
    client: TestClient,
    owner_user: User,
    owner_cookies: dict,
    test_server: Server,
) -> None:
    with patch(
        "routers.steam.get_steam_service",
        new=AsyncMock(side_effect=SteamApiUnavailable("steam_api_key_missing")),
    ):
        response = client.get(
            f"/api/steam/workshop/search?server_id={test_server.id}&query=test",
            cookies=owner_cookies,
        )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "steam_api_key_missing",
        "message": "errors.steam_api_key_missing",
    }


def test_workshop_popular_returns_structured_missing_key_error(
    client: TestClient,
    owner_user: User,
    owner_cookies: dict,
    test_server: Server,
) -> None:
    with patch(
        "routers.steam.get_steam_service",
        new=AsyncMock(side_effect=SteamApiUnavailable("steam_api_key_missing")),
    ):
        response = client.get(
            f"/api/steam/workshop/popular?server_id={test_server.id}",
            cookies=owner_cookies,
        )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "steam_api_key_missing",
        "message": "errors.steam_api_key_missing",
    }


def test_workshop_details_returns_structured_unavailable_error(
    client: TestClient,
    owner_user: User,
    owner_cookies: dict,
    test_server: Server,
) -> None:
    with patch(
        "routers.steam.get_steam_service",
        new=AsyncMock(side_effect=SteamApiUnavailable("steam_api_unavailable")),
    ):
        response = client.get(
            f"/api/steam/workshop/mod/12345?server_id={test_server.id}",
            cookies=owner_cookies,
        )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "steam_api_unavailable",
        "message": "errors.steam_api_unavailable",
    }
