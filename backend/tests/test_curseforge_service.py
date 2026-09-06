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

    assert normalize_game_id(None) is None
    assert normalize_game_id("") is None
    assert normalize_game_id("432") == 432
    assert normalize_game_id(432) == 432
    assert normalize_game_id("83262") == 83262
    assert normalize_game_id("invalid_text") is None
    assert normalize_game_id(None, default=83374) == 83374
    assert normalize_game_id("invalid_text", default=83374) == 83374


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


@pytest.mark.asyncio
async def test_search_mods_defaults_to_allowing_all_mods():
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
        ]
    }
    mock_resp = httpx.Response(status_code=200, json=mock_payload, request=httpx.Request("GET", "https://api.curseforge.com/v1/mods/search"))

    with patch("services.curseforge_api_key_service.resolve_key", return_value="TEST_API_KEY"):
        svc = CurseForgeService()
        with patch.object(svc.client, "get", new=AsyncMock(return_value=mock_resp)):
            mods = await svc.search_mods(game_id="83374", query="test")

        assert len(mods) == 2
        assert [m.publishedfileid for m in mods] == ["100", "200"]
        await svc.close()



@pytest.mark.asyncio
async def test_search_mods_passes_mod_loader_type_and_game_version():
    mock_resp = httpx.Response(status_code=200, json={"data": []}, request=httpx.Request("GET", "https://api.curseforge.com/v1/mods/search"))

    with patch("services.curseforge_api_key_service.resolve_key", return_value="TEST_API_KEY"):
        svc = CurseForgeService()
        mock_get = AsyncMock(return_value=mock_resp)
        with patch.object(svc.client, "get", new=mock_get):
            await svc.search_mods(
                game_id=432,
                query="cobblemon",
                mod_loader_type=4,
                game_version="1.20.1",
            )

        call_params = mock_get.call_args[1]["params"]
        assert call_params["gameId"] == 432
        assert call_params["searchFilter"] == "cobblemon"
        assert call_params["modLoaderType"] == 4
        assert call_params["gameVersion"] == "1.20.1"
        await svc.close()


def test_detect_minecraft_modloader_and_version():
    from routers.curseforge import _detect_minecraft_modloader_and_version
    from unittest.mock import MagicMock

    server = MagicMock()
    plugin = MagicMock()

    # Fabric
    server.game_type = "minecraft_fabric"
    plugin.blueprint.runtime.env = {"VERSION": "1.21.1"}
    loader, ver, cls = _detect_minecraft_modloader_and_version(server, plugin)
    assert loader == 4
    assert ver == "1.21.1"
    assert cls is None

    # Forge
    server.game_type = "minecraft_forge"
    plugin.blueprint.runtime.env = {"VERSION": "LATEST"}
    loader, ver, cls = _detect_minecraft_modloader_and_version(server, plugin)
    assert loader == 1
    assert ver is None  # LATEST ignored
    assert cls is None

    # NeoForge
    server.game_type = "minecraft_neoforge"
    loader, ver, cls = _detect_minecraft_modloader_and_version(server, plugin)
    assert loader == 6

    # Paper (Spigot/Bukkit plugins)
    server.game_type = "minecraft_paper"
    loader, ver, cls = _detect_minecraft_modloader_and_version(server, plugin)
    assert cls == "12"


@pytest.mark.asyncio
async def test_resolve_game_id_known_and_numeric():
    svc = CurseForgeService()
    assert await svc.resolve_game_id("minecraft") == 432
    assert await svc.resolve_game_id("Minecraft") == 432
    assert await svc.resolve_game_id("palworld") == 83262
    assert await svc.resolve_game_id("ark") == 83374
    assert await svc.resolve_game_id("83374") == 83374
    assert await svc.resolve_game_id(432) == 432
    await svc.close()


@pytest.mark.asyncio
async def test_get_categories_and_find_class_id():
    mock_payload = {
        "data": [
            {"id": 4471, "gameId": 432, "name": "Modpacks", "slug": "modpacks", "isClass": True},
            {"id": 6, "gameId": 432, "name": "Mods", "slug": "mc-mods", "isClass": True},
            {"id": 412, "gameId": 432, "name": "Technology", "slug": "technology", "classId": 6},
        ]
    }
    mock_resp = httpx.Response(200, json=mock_payload, request=httpx.Request("GET", "https://api.curseforge.com/v1/categories?gameId=432"))

    with patch("services.curseforge_api_key_service.resolve_key", return_value="TEST_KEY"):
        svc = CurseForgeService()
        with patch.object(svc.client, "get", new=AsyncMock(return_value=mock_resp)):
            categories = await svc.get_categories(432)
            assert len(categories) == 3
            modpack_class = await svc.find_class_id(432, "modpacks")
            assert modpack_class == 4471
            mods_class = await svc.find_class_id(432, "mods")
            assert mods_class == 6
        await svc.close()


@pytest.mark.asyncio
async def test_search_modpacks_generic_with_resolved_class():
    mock_cats_resp = httpx.Response(
        200,
        json={"data": [{"id": 9999, "gameId": 83374, "name": "Modpacks", "slug": "ark-modpacks", "isClass": True}]},
        request=httpx.Request("GET", "https://api.curseforge.com/v1/categories?gameId=83374"),
    )
    mock_search_resp = httpx.Response(
        200,
        json={"data": [{"id": 12345, "name": "ARK Overhaul Pack", "summary": "Mega Pack"}]},
        request=httpx.Request("GET", "https://api.curseforge.com/v1/mods/search"),
    )

    with patch("services.curseforge_api_key_service.resolve_key", return_value="TEST_KEY"):
        svc = CurseForgeService()

        async def _mock_get(url, params=None, **kwargs):
            if "categories" in url:
                return mock_cats_resp
            return mock_search_resp

        with patch.object(svc.client, "get", side_effect=_mock_get) as mock_get:
            res = await svc.search_modpacks(game_id="83374", query="overhaul")
            assert len(res) == 1
            assert res[0].title == "ARK Overhaul Pack"
            # Verify classId 9999 was passed
            call_params = mock_get.call_args[1]["params"]
            assert call_params["classId"] == 9999
            assert call_params["gameId"] == 83374
        await svc.close()


@pytest.mark.asyncio
async def test_find_class_id_non_minecraft():
    mock_payload = {
        "data": [
            {"id": 8888, "gameId": 83262, "name": "Palworld Mods", "slug": "pal-mods", "isClass": True},
            {"id": 8889, "gameId": 83262, "name": "Palworld Modpacks", "slug": "palworld-modpacks", "isClass": True},
        ]
    }
    mock_resp = httpx.Response(200, json=mock_payload, request=httpx.Request("GET", "https://api.curseforge.com/v1/categories?gameId=83262"))

    with patch("services.curseforge_api_key_service.resolve_key", return_value="TEST_KEY"):
        svc = CurseForgeService()
        with patch.object(svc.client, "get", new=AsyncMock(return_value=mock_resp)):
            mods_cls = await svc.find_class_id(83262, "mods")
            assert mods_cls == 8888
            packs_cls = await svc.find_class_id(83262, "modpacks")
            assert packs_cls == 8889
        await svc.close()


@pytest.mark.asyncio
async def test_search_mods_thematic_filler_cleaning():
    mock_first_empty = httpx.Response(200, json={"data": []}, request=httpx.Request("GET", "https://api.curseforge.com/v1/mods/search"))
    mock_second_hit = httpx.Response(
        200,
        json={"data": [{"id": 555, "name": "Global Economy Mod", "summary": "Economy for players"}]},
        request=httpx.Request("GET", "https://api.curseforge.com/v1/mods/search"),
    )

    with patch("services.curseforge_api_key_service.resolve_key", return_value="TEST_KEY"):
        svc = CurseForgeService()
        responses = [mock_first_empty, mock_second_hit]
        call_count = 0

        async def _mock_get(url, params=None, **kwargs):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        with patch.object(svc.client, "get", side_effect=_mock_get) as mock_get:
            res = await svc.search_mods(game_id=83374, query="Mods für Wirtschaft")
            assert len(res) == 1
            assert res[0].title == "Global Economy Mod"
            # Verify fallback searched for thematic English term "economy"
            assert mock_get.call_count == 2
            second_params = mock_get.call_args_list[1][1]["params"]
            assert second_params["searchFilter"] == "economy"
        await svc.close()


@pytest.mark.asyncio
async def test_search_mods_unresolvable_game_returns_empty():
    with patch("services.curseforge_api_key_service.resolve_key", return_value="TEST_KEY"):
        svc = CurseForgeService()
        res = await svc.search_mods(game_id="unknown_nonexistent_game_xyz", query="something")
        assert res == []
        await svc.close()


