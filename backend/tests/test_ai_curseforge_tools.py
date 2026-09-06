"""Tests für die generischen CurseForge KI-Werkzeuge (search_curseforge_mods, search_curseforge_modpacks)."""

from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from models import User
from services import ai_action_service
from services.curseforge_service import CurseForgeModInfo


def test_ai_search_curseforge_mods_generic(db: Session, owner_user: User) -> None:
    now = datetime.now(timezone.utc)
    mock_mod = CurseForgeModInfo(
        publishedfileid="927142",
        title="Super Spyglass Plus",
        description="Shows dino stats",
        creator="Author",
        file_size=10485760,
        created=now,
        updated=now,
        subscriptions=15000,
        favorites=500,
        tags=["Creatures"],
        game_id="83374",
    )

    mock_service = AsyncMock()
    mock_service.resolve_game_id = AsyncMock(return_value=83374)
    mock_service.search_mods = AsyncMock(return_value=[mock_mod])

    with (
        patch("services.curseforge_api_key_service.resolve_key", return_value="TEST_API_KEY"),
        patch("services.curseforge_service.get_curseforge_service", new=AsyncMock(return_value=mock_service)),
    ):
        result = ai_action_service.execute_read_tool(
            db,
            user=owner_user,
            tool_name="search_curseforge_mods",
            arguments={"game": "ARK", "query": "spyglass"},
        )

    assert "mods" in result
    assert len(result["mods"]) == 1
    assert result["mods"][0]["name"] == "Super Spyglass Plus"
    mock_service.search_mods.assert_awaited_once()
    assert mock_service.search_mods.call_args[1]["query"] == "spyglass"
    assert mock_service.search_mods.call_args[1]["game_id"] == 83374


def test_ai_search_curseforge_modpacks_generic(db: Session, owner_user: User) -> None:
    now = datetime.now(timezone.utc)
    mock_pack = CurseForgeModInfo(
        publishedfileid="123456",
        title="Palworld Mega Pack",
        description="All-in-one overhaul for Palworld",
        creator="Modder",
        file_size=50000000,
        created=now,
        updated=now,
        subscriptions=3000,
        favorites=200,
        tags=["Overhaul"],
        game_id="83262",
    )

    mock_service = AsyncMock()
    mock_service.resolve_game_id = AsyncMock(return_value=83262)
    mock_service.search_modpacks = AsyncMock(return_value=[mock_pack])

    with (
        patch("services.curseforge_api_key_service.resolve_key", return_value="TEST_API_KEY"),
        patch("services.curseforge_service.get_curseforge_service", new=AsyncMock(return_value=mock_service)),
    ):
        result = ai_action_service.execute_read_tool(
            db,
            user=owner_user,
            tool_name="search_curseforge_modpacks",
            arguments={"game": "palworld", "query": "mega"},
        )

    assert "mods" in result
    assert len(result["mods"]) == 1
    assert result["mods"][0]["name"] == "Palworld Mega Pack"
    mock_service.search_modpacks.assert_awaited_once()
    assert mock_service.search_modpacks.call_args[1]["game_id"] == 83262
    assert mock_service.search_modpacks.call_args[1]["query"] == "mega"


def test_ai_search_curseforge_mods_resolves_game_from_query(db: Session, owner_user: User) -> None:
    mock_service = AsyncMock()
    mock_service.resolve_game_id = AsyncMock(return_value=83374)
    mock_service.search_mods = AsyncMock(return_value=[])

    with (
        patch("services.curseforge_api_key_service.resolve_key", return_value="TEST_API_KEY"),
        patch("services.curseforge_service.get_curseforge_service", new=AsyncMock(return_value=mock_service)),
    ):
        result = ai_action_service.execute_read_tool(
            db,
            user=owner_user,
            tool_name="search_curseforge_mods",
            arguments={"query": "ark dinos"},
        )

    assert "mods" in result
    mock_service.search_mods.assert_awaited_once()
    assert mock_service.search_mods.call_args[1]["game_id"] == 83374


def test_ai_search_curseforge_mods_no_game_returns_game_required(db: Session, owner_user: User) -> None:
    mock_service = AsyncMock()
    mock_service.resolve_game_id = AsyncMock(return_value=None)
    mock_service.get_games = AsyncMock(return_value=[])

    with (
        patch("services.curseforge_api_key_service.resolve_key", return_value="TEST_API_KEY"),
        patch("services.curseforge_service.get_curseforge_service", new=AsyncMock(return_value=mock_service)),
    ):
        result = ai_action_service.execute_read_tool(
            db,
            user=owner_user,
            tool_name="search_curseforge_mods",
            arguments={"query": "mysterious unknown item"},
        )

    assert "error" in result
    assert result["error"] == "game_required"


def test_propose_mod_install_accepts_mod_id_alias(db: Session, owner_user: User) -> None:
    from models import Server
    from services.ai_proposals.server_proposals import _mod_install_payload
    from unittest.mock import MagicMock

    server = Server(
        id=999,
        name="Test Server",
        game_type="minecraft_fabric",
        status="running",
    )
    mock_plugin = MagicMock()
    mock_plugin.supports_mods = True

    with patch("games.get_plugin", return_value=mock_plugin):
        payload, preview = _mod_install_payload(
            db,
            server,
            {"mod_id": "927142", "action": "install", "name": "Super Mod"},
        )

    assert payload["workshop_id"] == "927142"
    assert payload["name"] == "Super Mod"
    assert preview["workshop_id"] == "927142"


def test_ai_search_curseforge_mods_no_false_positive_substring_match(db: Session, owner_user: User) -> None:
    """Wortgrenzen-Test: 'Dark' darf nicht fälschlicherweise auf ARK (83374) matchen."""
    mock_service = AsyncMock()
    mock_service.resolve_game_id = AsyncMock(return_value=None)
    mock_service.get_games = AsyncMock(return_value=[])

    with (
        patch("services.curseforge_api_key_service.resolve_key", return_value="TEST_API_KEY"),
        patch("services.curseforge_service.get_curseforge_service", new=AsyncMock(return_value=mock_service)),
    ):
        result = ai_action_service.execute_read_tool(
            db,
            user=owner_user,
            tool_name="search_curseforge_mods",
            arguments={"query": "Dark Fantasy Modpack"},
        )

    # Ohne Server oder Spielangabe darf 'Dark' nicht als 'ark' gewertet werden
    assert result.get("error") == "game_required"


def test_ai_search_curseforge_mods_matches_server_name_in_query(db: Session, owner_user: User) -> None:
    """Wenn im Suchtext der Servername vorkommt, wird dessen Spiel-ID ermittelt."""
    from models import Server
    from unittest.mock import MagicMock

    srv = Server(
        name="Jurassic Dino World",
        game_type="ark_ascended",
        install_dir="/tmp/ark",
        status="running",
    )
    db.add(srv)
    db.commit()
    db.refresh(srv)

    mock_plugin = MagicMock()
    mock_plugin.get_mod_support.return_value = {
        "curseforge_game_id": 83374,
        "curseforge_class_id": None,
    }

    mock_service = AsyncMock()
    mock_service.search_mods = AsyncMock(return_value=[])

    with (
        patch("games.get_plugin", return_value=mock_plugin),
        patch("services.curseforge_api_key_service.resolve_key", return_value="TEST_API_KEY"),
        patch("services.curseforge_service.get_curseforge_service", new=AsyncMock(return_value=mock_service)),
    ):
        result = ai_action_service.execute_read_tool(
            db,
            user=owner_user,
            tool_name="search_curseforge_mods",
            arguments={"query": "mods für jurassic dino world"},
        )

    assert "mods" in result
    mock_service.search_mods.assert_awaited_once()
    assert mock_service.search_mods.call_args[1]["game_id"] == 83374


def test_detect_game_modloader_non_minecraft_does_not_set_loader() -> None:
    """Non-Minecraft Spiele dürfen trotz 'forge' im Namen keinen Minecraft-Modloader (1) erhalten."""
    from routers.curseforge import _detect_game_modloader_and_version
    from unittest.mock import MagicMock

    server = MagicMock()
    server.game_type = "dark_forge_tactics"
    plugin = MagicMock()
    plugin.blueprint.runtime.env = {"VERSION": "1.0.5"}

    loader, ver, cls = _detect_game_modloader_and_version(server, plugin, 83374)
    assert loader is None
    assert ver == "1.0.5"
    assert cls is None


