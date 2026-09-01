"""Tests für CurseForgeService (v1 Client, Parsing, TTL Caching, Fehlerbehandlung)."""

from unittest.mock import AsyncMock, patch
import httpx
import pytest
import pytest_asyncio

from services.curseforge_service import (
    CurseForgeService,
    CurseForgeApiUnavailable,
    close_curseforge_service,
)


@pytest_asyncio.fixture(autouse=True)
async def _cleanup():
    yield
    await close_curseforge_service()


@pytest.mark.asyncio
async def test_missing_api_key_raises_error():
    with patch("services.curseforge_api_key_service.resolve_key", return_value=""):
        svc = CurseForgeService()
        with pytest.raises(CurseForgeApiUnavailable) as exc_info:
            await svc.search_mods(game_id="83374", query="test")
        assert exc_info.value.code == "curseforge_api_key_missing"
        await svc.close()


@pytest.mark.asyncio
async def test_search_mods_parsing():
    mock_payload = {
        "data": [
            {
                "id": 927142,
                "name": "Super Spyglass Plus",
                "summary": "Shows dino stats and torpor",
                "downloadCount": 1542000,
                "thumbsUpCount": 35000,
                "dateModified": "2024-03-01T12:00:00Z",
                "logo": {"url": "https://example.com/logo.png"},
                "links": {"websiteUrl": "https://curseforge.com/ark-survival-ascended/mods/super-spyglass-plus"},
                "latestFiles": [
                    {
                        "id": 5123456,
                        "displayName": "Super Spyglass Plus v1.2",
                        "fileName": "SuperSpyglassPlus.zip",
                        "fileLength": 10485760,  # 10 MB
                        "downloadUrl": "https://edge.forgecdn.net/files/5123/456/SuperSpyglassPlus.zip",
                        "isAvailable": True,
                    }
                ],
            }
        ]
    }

    mock_resp = httpx.Response(status_code=200, json=mock_payload, request=httpx.Request("GET", "https://api.curseforge.com/v1/mods/search"))

    with patch("services.curseforge_api_key_service.resolve_key", return_value="TEST_API_KEY"):
        svc = CurseForgeService()
        with patch.object(svc.client, "get", new=AsyncMock(return_value=mock_resp)):
            results = await svc.search_mods(game_id="83374", query="spyglass", page=1, per_page=20)

        assert len(results) == 1
        mod = results[0]
        assert mod.publishedfileid == "927142"
        assert mod.title == "Super Spyglass Plus"
        assert mod.description == "Shows dino stats and torpor"
        assert mod.subscriptions == 1542000
        assert mod.preview_url == "https://example.com/logo.png"

        # Test TTL Cache Hit
        cached_results = await svc.search_mods(game_id="83374", query="spyglass", page=1, per_page=20)
        assert len(cached_results) == 1
        await svc.close()


@pytest.mark.asyncio
async def test_search_mods_401_403_raises_invalid_key():
    mock_resp = httpx.Response(status_code=403, json={"error": "Forbidden"}, request=httpx.Request("GET", "https://api.curseforge.com/v1/mods/search"))

    with patch("services.curseforge_api_key_service.resolve_key", return_value="INVALID_KEY"):
        svc = CurseForgeService()
        with patch.object(svc.client, "get", new=AsyncMock(return_value=mock_resp)):
            with pytest.raises(CurseForgeApiUnavailable) as exc_info:
                await svc.search_mods(game_id="83374", query="test")
            assert exc_info.value.code == "curseforge_api_key_invalid"
        await svc.close()


@pytest.mark.asyncio
async def test_get_mod_details():
    mock_payload = {
        "data": {
            "id": 927142,
            "name": "Super Spyglass Plus",
            "summary": "Detailed info",
            "downloadCount": 1542000,
            "thumbsUpCount": 35000,
            "dateModified": "2024-03-01T12:00:00Z",
            "logo": {"url": "https://example.com/logo.png"},
            "links": {"websiteUrl": "https://curseforge.com/mod/927142"},
            "latestFiles": [
                {
                    "id": 5123456,
                    "displayName": "Super Spyglass Plus v1.2",
                    "fileName": "SuperSpyglassPlus.zip",
                    "fileLength": 5242880,  # 5 MB
                    "downloadUrl": "https://edge.forgecdn.net/files/5123/456/SuperSpyglassPlus.zip",
                    "isAvailable": True,
                }
            ],
        }
    }

    mock_resp = httpx.Response(status_code=200, json=mock_payload, request=httpx.Request("GET", "https://api.curseforge.com/v1/mods/927142"))

    with patch("services.curseforge_api_key_service.resolve_key", return_value="TEST_API_KEY"):
        svc = CurseForgeService()
        with patch.object(svc.client, "get", new=AsyncMock(return_value=mock_resp)):
            mod = await svc.get_mod_details("927142")

        assert mod is not None
        assert mod.publishedfileid == "927142"
        assert mod.title == "Super Spyglass Plus"
        await svc.close()


@pytest.mark.asyncio
async def test_get_mod_details_404_returns_none():
    mock_resp = httpx.Response(status_code=404, json={"error": "Not Found"}, request=httpx.Request("GET", "https://api.curseforge.com/v1/mods/999999"))

    with patch("services.curseforge_api_key_service.resolve_key", return_value="TEST_API_KEY"):
        svc = CurseForgeService()
        with patch.object(svc.client, "get", new=AsyncMock(return_value=mock_resp)):
            mod = await svc.get_mod_details("999999")

        assert mod is None
        await svc.close()


@pytest.mark.asyncio
async def test_get_mod_files_and_download_url():
    files_payload = {
        "data": [
            {
                "id": 5123456,
                "displayName": "Super Spyglass Plus v1.2",
                "fileName": "SuperSpyglassPlus.zip",
                "downloadUrl": "https://edge.forgecdn.net/files/5123/456/SuperSpyglassPlus.zip",
            }
        ]
    }
    url_payload = {"data": "https://edge.forgecdn.net/files/5123/456/SuperSpyglassPlus.zip"}

    mock_files_resp = httpx.Response(status_code=200, json=files_payload, request=httpx.Request("GET", "https://api.curseforge.com/v1/mods/927142/files"))
    mock_url_resp = httpx.Response(status_code=200, json=url_payload, request=httpx.Request("GET", "https://api.curseforge.com/v1/mods/927142/files/5123456/download-url"))

    with patch("services.curseforge_api_key_service.resolve_key", return_value="TEST_API_KEY"):
        svc = CurseForgeService()
        with patch.object(svc.client, "get", new=AsyncMock(side_effect=[mock_files_resp, mock_url_resp])):
            files = await svc.get_mod_files("927142")
            assert len(files) == 1
            assert files[0]["id"] == 5123456

            dl_url = await svc.get_file_download_url("927142", "5123456")
            assert dl_url == "https://edge.forgecdn.net/files/5123/456/SuperSpyglassPlus.zip"
        await svc.close()


@pytest.mark.asyncio
async def test_test_connection():
    mock_resp = httpx.Response(status_code=200, json={"data": [{"id": 432, "name": "Minecraft"}]}, request=httpx.Request("GET", "https://api.curseforge.com/v1/games"))

    with patch("services.curseforge_api_key_service.resolve_key", return_value="VALID_KEY"):
        svc = CurseForgeService()
        with patch.object(svc.client, "get", new=AsyncMock(return_value=mock_resp)):
            res = await svc.test_connection()

        assert res["ok"] is True
        await svc.close()


@pytest.mark.asyncio
async def test_test_connection_invalid_key():
    mock_resp = httpx.Response(status_code=401, json={"error": "Unauthorized"}, request=httpx.Request("GET", "https://api.curseforge.com/v1/games"))

    with patch("services.curseforge_api_key_service.resolve_key", return_value="BAD_KEY"):
        svc = CurseForgeService()
        with patch.object(svc.client, "get", new=AsyncMock(return_value=mock_resp)):
            res = await svc.test_connection()

        assert res["ok"] is False
        assert res["error"] == "curseforge_api_key_invalid"
        await svc.close()


def test_normalize_game_id():
    from services.curseforge_service import normalize_game_id

    assert normalize_game_id(None) == 432
    assert normalize_game_id("") == 432
    assert normalize_game_id("432") == 432
    assert normalize_game_id(432) == 432
    assert normalize_game_id("83262") == 83262
    assert normalize_game_id("invalid_text") == 432


@pytest.mark.asyncio
async def test_search_mods_filters_non_distributable_mods():
    mock_payload = {
        "data": [
            {
                "id": 100,
                "name": "Allowed Mod",
                "allowModDistribution": True,
            },
            {
                "id": 200,
                "name": "Blocked Mod",
                "allowModDistribution": False,
            },
            {
                "id": 300,
                "name": "Default Mod",
            },
        ]
    }
    mock_resp = httpx.Response(status_code=200, json=mock_payload, request=httpx.Request("GET", "https://api.curseforge.com/v1/mods/search"))

    with patch("services.curseforge_api_key_service.resolve_key", return_value="TEST_API_KEY"):
        svc = CurseForgeService()
        with patch.object(svc.client, "get", new=AsyncMock(return_value=mock_resp)):
            mods = await svc.search_mods(game_id=432, query="test", only_distributable=True)

        assert len(mods) == 2
        assert [m.publishedfileid for m in mods] == ["100", "300"]
        await svc.close()

