"""Tests fuer Workshop-Batch-Chunking in BlueprintPlugin.install_mods.

Provider-neutral: bestaetigt, dass Server mit > 25 Mods in Chunks aufgeteilt
werden, das Batch-Limit von SteamCMD respektiert wird, und Ergebnisse korrekt
aggregiert werden.
"""

from types import SimpleNamespace
from unittest.mock import patch

from games.blueprint_plugin import WORKSHOP_BATCH_SIZE, BlueprintPlugin
from blueprints.schema import (
    BlueprintSource,
    BlueprintSourceType,
    BlueprintSteamSource,
    BlueprintWorkshopFileAction,
    BlueprintMods,
)


def _build_blueprint():
    """Minimale Blueprint mit aktiviertem Steam-Workshop."""
    source = BlueprintSource(
        type=BlueprintSourceType.STEAM,
        steam=BlueprintSteamSource(
            appId="12345",
            platform="linux",
        ),
    )
    mods = BlueprintMods(
        supportsMods=True,
        supportsSteamWorkshop=True,
        workshopAppId="67890",
        modInjection="file",
        modListFilePath="Mods/modlist.txt",
        modListContent="workshopIds",
        postInstall=[
            BlueprintWorkshopFileAction(
                operation="copy",
                source="{WORKSHOP_ID}",
                target="Mods/{WORKSHOP_ID}",
                required=False,
            ),
        ],
    )
    return SimpleNamespace(
        meta=SimpleNamespace(id="test", name="Test", category="steam_game"),
        source=source,
        runtime=SimpleNamespace(image="stub"),
        effective_mods=lambda: mods,
        ports=[],
    )


def _build_server():
    server = SimpleNamespace(
        id=1,
        name="Test",
        game_type="test",
        install_dir="/tmp/test",
    )
    server.ports = []
    return server


def test_install_mods_chunks_large_workshop_batches():
    """> 25 Mods werden in Chunks aufgeteilt; jeder Chunk erzeugt einen
    eigenen SteamCMD-Aufruf; applied/errors/items werden aggregiert."""
    blueprint = _build_blueprint()
    plugin = BlueprintPlugin(blueprint)
    server = _build_server()

    workshop_ids = [str(i) for i in range(30)]

    batch_call_count = 0
    captured_chunks: list[list[str]] = []

    def _fake_batch(*, server_id, install_dir, workshop_app_id, workshop_item_ids, **kwargs):
        nonlocal batch_call_count
        batch_call_count += 1
        captured_chunks.append(list(workshop_item_ids))
        return {
            "ok": True,
            "items": {wid: {"ok": True} for wid in workshop_item_ids},
        }

    with patch.object(plugin, "_run_workshop_post_install_actions", return_value={}), \
         patch.object(plugin, "update_modlist"), \
         patch(
             "games.blueprint_plugin.run_steamcmd_workshop_download_batch",
             side_effect=_fake_batch,
         ):
        result = plugin.install_mods(server, workshop_ids)

    expected_chunks = (len(workshop_ids) + WORKSHOP_BATCH_SIZE - 1) // WORKSHOP_BATCH_SIZE
    assert batch_call_count == expected_chunks
    assert len(captured_chunks[0]) == WORKSHOP_BATCH_SIZE
    assert len(captured_chunks[-1]) == len(workshop_ids) - WORKSHOP_BATCH_SIZE
    flat = [wid for chunk in captured_chunks for wid in chunk]
    assert flat == workshop_ids
    assert result["ok"] is True
    assert result["applied"] == len(workshop_ids)


def test_install_mods_aggregates_errors_across_chunks():
    """Errors aus Chunk 1 und Chunk 2 werden zusammengefuehrt."""
    blueprint = _build_blueprint()
    plugin = BlueprintPlugin(blueprint)
    server = _build_server()

    workshop_ids = [str(i) for i in range(30)]

    def _fake_batch_chunked(*, workshop_item_ids, **kwargs):
        if workshop_item_ids[0] == "0":
            return {
                "ok": True,
                "items": {wid: {"ok": True} for wid in workshop_item_ids},
            }
        return {
            "ok": False,
            "error": "Workshop-Download fehlgeschlagen",
            "items": {wid: {"ok": False, "error": "batch_fail"} for wid in workshop_item_ids},
        }

    with patch.object(plugin, "_run_workshop_post_install_actions", return_value={}), \
         patch.object(plugin, "update_modlist"), \
         patch(
             "games.blueprint_plugin.run_steamcmd_workshop_download_batch",
             side_effect=_fake_batch_chunked,
         ):
        result = plugin.install_mods(server, workshop_ids)

    assert result["ok"] is False
    assert result["applied"] == WORKSHOP_BATCH_SIZE
    assert len(result["errors"]) == (len(workshop_ids) - WORKSHOP_BATCH_SIZE)


def test_install_mods_single_chunk_under_batch_size():
    """< 25 Mods loesen genau einen Batch-Call aus."""
    blueprint = _build_blueprint()
    plugin = BlueprintPlugin(blueprint)
    server = _build_server()

    workshop_ids = [str(i) for i in range(10)]

    with patch.object(plugin, "_run_workshop_post_install_actions", return_value={}), \
         patch.object(plugin, "update_modlist"), \
         patch(
             "games.blueprint_plugin.run_steamcmd_workshop_download_batch",
             return_value={"ok": True, "items": {wid: {"ok": True} for wid in workshop_ids}},
         ) as mock_batch:
        result = plugin.install_mods(server, workshop_ids)

    assert mock_batch.call_count == 1
    assert result["ok"] is True
    assert result["applied"] == 10


def test_workshop_batch_size_constant_is_documented():
    import games.blueprint_plugin as bp_mod

    assert WORKSHOP_BATCH_SIZE == 25
    src = open(bp_mod.__file__, encoding="utf-8").read()
    assert "WORKSHOP_BATCH_SIZE" in src
    assert "SteamCMD" in src
    assert "Batch" in src or "batch" in src
