"""Panel-to-agent integration contract for the complete Workshop mod lifecycle."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from blueprints.schema import (
    BlueprintModListContent,
    BlueprintMods,
    BlueprintSource,
    BlueprintSourceType,
    BlueprintSteamSource,
    BlueprintWorkshopFileAction,
)
from games.blueprint_plugin import BlueprintPlugin


def _plugin() -> BlueprintPlugin:
    mods = BlueprintMods(
        supportsMods=True,
        supportsSteamWorkshop=True,
        workshopAppId="67890",
        modInjection="file",
        modListFilePath="Runtime/modlist.txt",
        modListContent=BlueprintModListContent.POST_INSTALL_TARGET_BASENAMES,
        postInstall=[
            BlueprintWorkshopFileAction(
                operation="copy",
                source="steamapps/workshop/content/{WORKSHOP_APP_ID}/{WORKSHOP_ID}/*.pak",
                target="Runtime/Mods/{BASENAME}",
                required=True,
            )
        ],
    )
    blueprint = SimpleNamespace(
        meta=SimpleNamespace(id="conan_exiles_ue5", name="Synthetic", category="steam_game"),
        source=BlueprintSource(
            type=BlueprintSourceType.STEAM,
            steam=BlueprintSteamSource(appId="440900", platform="linux", validate_=True),
        ),
        runtime=SimpleNamespace(image="synthetic.invalid/runtime"),
        effective_mods=lambda: mods,
        ports=[],
    )
    return BlueprintPlugin(blueprint)


def _server():
    return SimpleNamespace(
        id=77,
        game_type="conan_exiles_ue5",
        install_dir="/opt/msm/servers/77",
        node=SimpleNamespace(id=2, is_local=False, status="online"),
    )


def test_remote_install_inspect_cleanup_and_modlist_stay_on_node() -> None:
    plugin = _plugin()
    server = _server()
    client = MagicMock()
    client.files_workshop.side_effect = [
        {"ok": True, "ready": True, "target_basenames": ["mod-a.pak"]},
        {"ok": True, "ready": True, "target_basenames": ["mod-a.pak"]},
        {"ok": True, "ready": True, "target_basenames": ["mod-a.pak"]},
        {"ok": True, "ready": True, "target_basenames": ["mod-a.pak"]},
    ]

    with patch("services.node_client.NodeClient.from_node", return_value=client):
        applied = plugin._run_workshop_post_install_actions(server, "12345")
        assert applied["ok"] is True
        assert plugin.workshop_runtime_targets_ready(server, "12345") is True
        lines = plugin.format_modlist_lines(
            server, [SimpleNamespace(workshop_id="12345")]
        )
        assert lines == ["*mod-a.pak"]
        plugin.update_modlist = MagicMock()
        cleaned = plugin.cleanup_mod(server, "12345")

    assert cleaned["ok"] is True
    modes = [call.args[1]["mode"] for call in client.files_workshop.call_args_list]
    assert modes == ["apply", "inspect", "inspect", "cleanup"]


def test_remote_modlist_is_written_through_agent() -> None:
    from games.base import write_workshop_modlist

    server = _server()
    client = MagicMock()
    with patch("services.node_client.NodeClient.from_node", return_value=client):
        write_workshop_modlist(server, "Runtime/modlist.txt", ["mod-a.pak", "mod-b.pak"])

    client.files_write.assert_called_once_with(
        77,
        "Runtime/modlist.txt",
        "*mod-a.pak\n*mod-b.pak\n",
    )
