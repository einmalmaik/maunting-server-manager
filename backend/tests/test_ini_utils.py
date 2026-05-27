"""Tests fuer den minimalen INI-Setter (games/ini_utils.py)."""

from __future__ import annotations

from pathlib import Path

from blueprints.schema import load_blueprint_file
from games.ini_utils import remove_ini_key, set_ini_value
from games.blueprint_plugin import BlueprintPlugin


def _conan_blueprint_plugin() -> BlueprintPlugin:
    path = Path(__file__).resolve().parents[1] / "blueprints" / "native" / "conan_exiles_ue5.blueprint.json"
    return BlueprintPlugin(load_blueprint_file(path))


class TestSetIniValue:
    def test_creates_file_with_section(self, tmp_path):
        f = tmp_path / "Engine.ini"
        set_ini_value(str(f), "URL", "Port", "27015")

        content = f.read_text()
        assert "[URL]" in content
        assert "Port=27015" in content

    def test_creates_parent_directories(self, tmp_path):
        f = tmp_path / "nested" / "subdir" / "Engine.ini"
        set_ini_value(str(f), "URL", "Port", "27015")
        assert f.exists()

    def test_overwrites_existing_key(self, tmp_path):
        f = tmp_path / "Engine.ini"
        f.write_text("[URL]\nPort=1111\n")

        set_ini_value(str(f), "URL", "Port", "27015")
        content = f.read_text()
        assert content.count("Port=") == 1
        assert "Port=27015" in content
        assert "Port=1111" not in content

    def test_adds_key_to_existing_section(self, tmp_path):
        f = tmp_path / "Engine.ini"
        f.write_text("[URL]\nMap=DefaultMap\n")

        set_ini_value(str(f), "URL", "Port", "27015")
        content = f.read_text()
        assert "Map=DefaultMap" in content
        assert "Port=27015" in content

    def test_appends_new_section(self, tmp_path):
        f = tmp_path / "Engine.ini"
        f.write_text("[URL]\nMap=DefaultMap\n")

        set_ini_value(str(f), "OnlineSubsystemNull", "GameServerQueryPort", "27016")
        content = f.read_text()
        # Existing section unchanged
        assert "[URL]" in content
        assert "Map=DefaultMap" in content
        # New section appended
        assert "[OnlineSubsystemNull]" in content
        assert "GameServerQueryPort=27016" in content

    def test_preserves_other_sections(self, tmp_path):
        f = tmp_path / "Engine.ini"
        f.write_text(
            "[A]\nKeyA=1\n\n[URL]\nPort=1111\n\n[B]\nKeyB=2\n"
        )

        set_ini_value(str(f), "URL", "Port", "27015")
        content = f.read_text()
        assert "KeyA=1" in content
        assert "KeyB=2" in content
        assert "Port=27015" in content

    def test_does_not_overwrite_same_key_in_other_section(self, tmp_path):
        f = tmp_path / "Game.ini"
        f.write_text("[A]\nPort=1111\n[B]\nPort=2222\n")

        set_ini_value(str(f), "B", "Port", "27015")
        content = f.read_text()
        # A.Port bleibt unangetastet
        assert "Port=1111" in content
        # B.Port wird ersetzt
        assert "Port=27015" in content
        assert "Port=2222" not in content


class TestRemoveIniKey:
    def test_removes_existing_key(self, tmp_path):
        f = tmp_path / "Engine.ini"
        f.write_text("[URL]\nPort=27015\nMap=DefaultMap\n")
        remove_ini_key(str(f), "URL", "Port")
        content = f.read_text()
        assert "Port=" not in content
        assert "Map=DefaultMap" in content

    def test_noop_on_missing_file(self, tmp_path):
        f = tmp_path / "nope.ini"
        # Soll nicht crashen
        remove_ini_key(str(f), "X", "Y")
        assert not f.exists()


class TestConanPrepareRuntime:
    """Integration-Tests fuer das Blueprint-getriebene Conan-Port-Mapping."""

    def test_writes_ports_to_engine_and_game_ini(self, tmp_path):
        class _Srv:
            def __init__(self, install_dir):
                self.id = 1
                self.install_dir = install_dir
                self.game_port = 27015
                self.query_port = 27016
                self.rcon_port = 27017

        srv = _Srv(str(tmp_path))
        plugin = _conan_blueprint_plugin()
        plugin.prepare_runtime(srv)

        engine_ini = (
            tmp_path / "ConanSandbox" / "Saved" / "Config" / "LinuxServer" / "Engine.ini"
        )
        game_ini = (
            tmp_path / "ConanSandbox" / "Saved" / "Config" / "LinuxServer" / "Game.ini"
        )

        assert engine_ini.exists()
        engine_content = engine_ini.read_text()
        assert "[URL]" in engine_content
        assert "Port=27015" in engine_content
        assert "[OnlineSubsystemNull]" in engine_content
        assert "GameServerQueryPort=27016" in engine_content

        assert game_ini.exists()
        game_content = game_ini.read_text()
        assert "[RconPlugin]" in game_content
        assert "RconPort=27017" in game_content
        assert "RconEnabled=True" in game_content

    def test_skips_unset_ports(self, tmp_path):
        class _Srv:
            def __init__(self, install_dir):
                self.id = 2
                self.install_dir = install_dir
                self.game_port = 27015
                self.query_port = None
                self.rcon_port = None

        srv = _Srv(str(tmp_path))
        plugin = _conan_blueprint_plugin()
        plugin.prepare_runtime(srv)

        engine_ini = (
            tmp_path / "ConanSandbox" / "Saved" / "Config" / "LinuxServer" / "Engine.ini"
        )
        game_ini = (
            tmp_path / "ConanSandbox" / "Saved" / "Config" / "LinuxServer" / "Game.ini"
        )

        # game_port wurde geschrieben
        assert "Port=27015" in engine_ini.read_text()
        # query_port wurde NICHT geschrieben (Section gar nicht angelegt)
        assert "GameServerQueryPort" not in engine_ini.read_text()
        # rcon_port wurde NICHT geschrieben
        assert not game_ini.exists() or "RconPort=" not in game_ini.read_text()

    def test_idempotent_on_repeat_call(self, tmp_path):
        class _Srv:
            def __init__(self, install_dir):
                self.id = 3
                self.install_dir = install_dir
                self.game_port = 27015
                self.query_port = 27016
                self.rcon_port = 27017

        srv = _Srv(str(tmp_path))
        plugin = _conan_blueprint_plugin()
        plugin.prepare_runtime(srv)
        first_content = (
            tmp_path / "ConanSandbox" / "Saved" / "Config" / "LinuxServer" / "Engine.ini"
        ).read_text()

        plugin.prepare_runtime(srv)
        second_content = (
            tmp_path / "ConanSandbox" / "Saved" / "Config" / "LinuxServer" / "Engine.ini"
        ).read_text()

        # Zweiter Lauf erzeugt KEINE Duplikate
        assert first_content == second_content
        # Genau ein "Port=27015" und ein "GameServerQueryPort=27016" — keine Duplikate
        assert second_content.count("Port=27015") == 1
        assert second_content.count("GameServerQueryPort=27016") == 1

    def test_preserves_existing_user_keys(self, tmp_path):
        """User-Edits an anderen Keys ueberleben einen Port-Patch."""
        config_dir = tmp_path / "ConanSandbox" / "Saved" / "Config" / "LinuxServer"
        config_dir.mkdir(parents=True)
        engine_ini = config_dir / "Engine.ini"
        engine_ini.write_text(
            "[URL]\nPort=1111\nMap=CustomMap\n\n"
            "[CustomSection]\nMyKey=MyValue\n"
        )

        class _Srv:
            def __init__(self):
                self.id = 4
                self.install_dir = str(tmp_path)
                self.game_port = 27015
                self.query_port = 27016
                self.rcon_port = 27017

        _conan_blueprint_plugin().prepare_runtime(_Srv())
        content = engine_ini.read_text()

        # Port ist gepatcht
        assert "Port=27015" in content
        # User-Keys sind unverändert
        assert "Map=CustomMap" in content
        assert "[CustomSection]" in content
        assert "MyKey=MyValue" in content
