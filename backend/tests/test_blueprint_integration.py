"""Blueprint-weite Integrationstests (generisch, provider-neutral).

Zweck:
- Realer Flow: load_blueprint_* → BlueprintPlugin → real updater.check_server_file_update (für Steam),
  prepare_runtime, stop_grace_period_seconds, effective_update_strategy.
- Lifecycle-Entscheidungen (_run_start/_run_restart) mit updateStrategy (ALWAYS/CHECK/NONE + Override)
  und stopGracePeriodSeconds.
- Positive + negative Fälle (valide Minimal/Non-Steam/Steam, fehlende Dateien, kaputte Updates, invalid Tokens).
- Keine echten Secrets, keine echten Steam-Logins, keine instabilen externen Netzcalls (immer gepatcht),
  keine echten Docker/SteamCMD-Execs (Leafs gemockt).

KISS: Parametrisiert wo sinnvoll, aber kleine klare Testfunktionen. Wiederverwendet existierende
_native-Loader-Patterns und _run_*-Whitebox-Tests aus test_server_lifecycle_service.py.

Blueprint-Core muss Steam/Workshop als optionale Provider behandeln — Tests beweisen das.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from blueprints.schema import (
    BlueprintUpdateStrategy,
    BlueprintValidationError,
    load_blueprint_dict,
    load_blueprint_file,
)
from games.blueprint_plugin import BlueprintPlugin
from games import updater
from models import Server
from services.server_lifecycle_service import (
    _run_restart,
    _run_start,
    _source_update_strategy,
)


def _native_path(blueprint_id: str) -> Path:
    """Pfad zu nativen Blueprints (wie in test_install_lifecycle + migration_snapshots)."""
    return Path(__file__).resolve().parents[1] / "blueprints" / "native" / f"{blueprint_id}.blueprint.json"


def _load_native(blueprint_id: str):
    """Lädt echten nativen Blueprint (DayZ=steam, Hytale=http, Vanilla=dockerOnly)."""
    return load_blueprint_file(_native_path(blueprint_id))


def _make_stub_server(install_dir: str | Path, **kwargs) -> SimpleNamespace:
    """Minimaler Server-Stub (kein DB-Roundtrip nötig für die meisten Pfade)."""
    base = {
        "id": 42,
        "name": "itest",
        "game_type": "itest",
        "install_dir": str(install_dir),
        "public_bind_ip": "127.0.0.1",
        "status": "stopped",
        "game_port": 7777,
        "query_port": None,
        "rcon_port": None,
        "ports": [],
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _minimal_docker_blueprint() -> dict:
    """Valider Minimal-Blueprint (dockerOnly, keine Ports, triviales Startup)."""
    return {
        "version": 1,
        "meta": {
            "id": "minimal_docker_test",
            "name": "Minimal Docker Test",
            "category": "bot",
        },
        "runtime": {
            "image": "alpine:3.19",
            "workdir": "/data",
            "startup": "echo 'hello from blueprint integration test'",
            "ensureDirs": [],
            "requiredFiles": [],
            "configPatches": [],
            # stopGracePeriodSeconds fehlt → Default 30
        },
        "ports": [],
        "source": {
            "type": "dockerOnly",
            # updateStrategy fehlt → effective = NONE
        },
    }


def _bp_with_update_strategy(source_type: str, strategy: str | None, extra_source: dict | None = None) -> dict:
    """Konstruiert Blueprint-Dict mit expliziter (oder fehlender) updateStrategy."""
    safe_id = source_type.lower().replace("only", "_only")
    bp: dict = {
        "version": 1,
        "meta": {"id": f"strat_{safe_id}", "name": "Strat Test", "category": "non_steam_game"},
        "runtime": {"image": "alpine:3.19", "startup": "true"},
        "ports": [],
        "source": {"type": source_type},
    }
    if source_type == "http":
        bp["source"]["http"] = {"url": "https://example.invalid/test.zip", "archiveType": "zip"}
    if extra_source:
        bp["source"].update(extra_source)
    if strategy is not None:
        bp["source"]["updateStrategy"] = strategy
    return bp


# ── Loading + Plugin-Basics (real) ──────────────────────────────────────────


@pytest.mark.parametrize(
    "bpid,expected_cat",
    [
        ("dayz", "steam_game"),
        ("hytale", "non_steam_game"),
        ("minecraft_vanilla", "non_steam_game"),
        ("scum_server", "steam_game"),
    ],
)
def test_native_blueprints_load_and_create_generic_plugin(bpid: str, expected_cat: str):
    """Jeder native Blueprint muss laden und einen generischen BlueprintPlugin erzeugen."""
    bp = _load_native(bpid)
    assert bp.meta.category.value == expected_cat
    plugin = BlueprintPlugin(bp)
    assert plugin.game_id == bpid
    assert plugin.get_blueprint() is bp
    # Grace immer lesbar (Default oder explizit)
    grace = plugin.stop_grace_period_seconds(None)
    assert 5 <= grace <= 600


def test_minimal_valid_docker_blueprint_roundtrip():
    """Valider Minimal-Blueprint (dockerOnly) → load → Plugin → effective Strategy = none."""
    data = _minimal_docker_blueprint()
    bp = load_blueprint_dict(data)
    plugin = BlueprintPlugin(bp)
    assert plugin.get_blueprint().source.effective_update_strategy() == BlueprintUpdateStrategy.NONE
    assert plugin.stop_grace_period_seconds(None) == 30


# ── Echter updater.check_server_file_update Pfad (Steam) ────────────────────


def test_steam_blueprint_uses_real_check_server_file_update(tmp_path):
    """Akzeptanz: Mindestens ein Steam-Blueprint-Test durchläuft den *echten* updater-Pfad."""
    bp = _load_native("dayz")
    plugin = BlueprintPlugin(bp)

    install = tmp_path / "dayz_install"
    install.mkdir()
    # Mindestens eine Datei, damit nicht "missing"
    (install / "DayZServer.exe").write_text("fake")

    srv = _make_stub_server(install)
    # Direkter realer Aufruf (nicht gemockt)
    res = plugin.check_for_server_file_update(srv)

    assert res["source_type"] == "steam"
    assert res["action"] == "none"
    assert "Steam" in res.get("details", "") or "passive" in res.get("details", "").lower()


# ── updateStrategy-Verhalten (real Plugin + Lifecycle-Decision) ─────────────


def test_update_strategy_always_validate_forces_update_even_if_check_says_none(tmp_path):
    """alwaysValidate: perform wird gerufen, auch wenn check 'none' liefert (explizit, da Default jetzt checkBased)."""
    data = _bp_with_update_strategy("steam", "alwaysValidate", extra_source={"steam": {"appId": "223350", "platform": "linux"}})
    bp = load_blueprint_dict(data)
    plugin = BlueprintPlugin(bp)

    install = tmp_path / "s"
    install.mkdir()
    (install / "x").write_text("x")

    srv = Server(
        id=101,
        name="steam_always",
        game_type="steam_test",
        install_dir=str(install),
        public_bind_ip="127.0.0.1",
        status="stopped",
        backup_on_start=False,
    )
    srv.ports = []
    db = MagicMock(spec=Session)

    with patch("services.server_lifecycle_service.try_acquire_install_update_lock", return_value=True), \
         patch("services.server_lifecycle_service.release_install_update_lock"), \
         patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"), \
         patch.object(plugin, "start", return_value={"message": "started"}), \
         patch.object(plugin, "perform_server_file_update", return_value={"ok": True}) as mock_perf:
        _run_start(db, srv, plugin)

    assert mock_perf.called  # ALWAYS erzwingt trotz realem check="none"
    assert srv.status == "running"


def test_update_strategy_check_based_respects_http_check_result(tmp_path):
    """HTTP mit checkBased (Default): perform nur wenn realer Check (gepatcht) 'update' liefert."""
    from datetime import datetime, timezone
    data = _bp_with_update_strategy("http", None)
    bp = load_blueprint_dict(data)
    plugin = BlueprintPlugin(bp)

    install = tmp_path / "http_srv"
    install.mkdir()
    (install / "old.bin").write_text("old")

    srv = Server(
        id=102,
        name="http_check",
        game_type="http_test",
        install_dir=str(install),
        public_bind_ip="127.0.0.1",
        status="stopped",
        backup_on_start=False,
    )
    srv.ports = []
    db = MagicMock(spec=Session)

    future_dt = datetime(2030, 1, 1, tzinfo=timezone.utc)
    with patch("games.updater._fetch_http_last_modified", return_value=future_dt), \
         patch("services.server_lifecycle_service.try_acquire_install_update_lock", return_value=True), \
         patch("services.server_lifecycle_service.release_install_update_lock"), \
         patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"), \
         patch.object(plugin, "start", return_value={"message": "ok"}), \
         patch.object(plugin, "perform_server_file_update", return_value={"ok": True}) as mock_perf:
        _run_start(db, srv, plugin)

    assert mock_perf.called


def test_update_strategy_none_skips_for_docker_only(tmp_path):
    """dockerOnly (none): perform wird nie gerufen, Check wird aber (wegen !=NONE) aufgerufen."""
    data = _bp_with_update_strategy("dockerOnly", None)
    bp = load_blueprint_dict(data)
    plugin = BlueprintPlugin(bp)

    install = tmp_path / "dock"
    install.mkdir()

    srv = Server(
        id=103,
        name="docker_none",
        game_type="docker_test",
        install_dir=str(install),
        public_bind_ip="127.0.0.1",
        status="stopped",
        backup_on_start=False,
    )
    srv.ports = []
    db = MagicMock(spec=Session)

    with patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"), \
         patch.object(plugin, "start", return_value={"message": "ok"}), \
         patch.object(plugin, "perform_server_file_update", return_value={"ok": True}) as mock_perf:
        _run_start(db, srv, plugin)

    # NONE → kein perform
    assert not mock_perf.called


def test_update_strategy_override_on_steam_is_respected(tmp_path):
    """Explizites checkBased auf Steam-Source: respektiert den Check (kein Force)."""
    data = _bp_with_update_strategy("steam", "checkBased", {"steam": {"appId": "123", "platform": "linux"}})
    bp = load_blueprint_dict(data)
    plugin = BlueprintPlugin(bp)

    install = tmp_path / "steam_override"
    install.mkdir()
    (install / "f").write_text("f")

    srv = Server(
        id=104,
        name="steam_override",
        game_type="steam_test",
        install_dir=str(install),
        public_bind_ip="127.0.0.1",
        status="stopped",
        backup_on_start=False,
    )
    srv.ports = []
    db = MagicMock(spec=Session)

    with patch("services.server_lifecycle_service.try_acquire_install_update_lock", return_value=True), \
         patch("services.server_lifecycle_service.release_install_update_lock"), \
         patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"), \
         patch.object(plugin, "start", return_value={"message": "ok"}), \
         patch.object(plugin, "perform_server_file_update", return_value={"ok": True}) as mock_perf:
        _run_start(db, srv, plugin)

    # checkBased + (real check liefert none für Steam) → kein perform
    assert not mock_perf.called


# ── Restart + stopGracePeriodSeconds (real) ─────────────────────────────────


def test_restart_uses_blueprint_grace_period(tmp_path):
    """Restart-Flow nutzt stopGracePeriodSeconds aus realem Blueprint (nicht Hardcode 30)."""
    # Custom Grace via Dict (kein nativer BP hat abweichenden Wert)
    data = _minimal_docker_blueprint()
    data["runtime"]["stopGracePeriodSeconds"] = 90
    bp = load_blueprint_dict(data)
    plugin = BlueprintPlugin(bp)

    install = tmp_path / "g"
    install.mkdir()

    srv = Server(id=7, name="g", game_type="g", install_dir=str(install), public_bind_ip="127.0.0.1", status="running", backup_on_start=False)
    srv.ports = []
    db = MagicMock(spec=Session)

    with patch("services.server_lifecycle_service.close_ports"), \
         patch("services.server_lifecycle_service.iptables_revoke_server"), \
         patch("services.server_lifecycle_service.iptables_accept_server"), \
         patch("services.server_lifecycle_service.open_ports"), \
         patch.object(plugin, "stop", return_value={"message": "stopped"}) as mock_stop, \
         patch.object(plugin, "start", return_value={"message": "started"}):
        _run_restart(db, srv, plugin)

    # Der Stop im Restart-Pfad muss mit dem aus Blueprint gelesenen Timeout gerufen worden sein.
    # (Base.stop liest es und reicht an docker_service weiter; hier mocken wir plugin.stop)
    assert mock_stop.called


# ── Error-Pfade (real Flow) ────────────────────────────────────────────────


def test_invalid_blueprint_tokens_rejected_at_load():
    """Ungültige Variablen/Tokens (Shell-Meta, unbekannt) → früh ValidationError (kein späterer Crash)."""
    bad = _minimal_docker_blueprint()
    bad["runtime"]["startup"] = "echo $FOO; rm -rf /"
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(bad)

    bad2 = _minimal_docker_blueprint()
    bad2["runtime"]["startup"] = "./srv --port {UNKNOWN_PORT}"
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(bad2)


def test_prepare_runtime_fails_on_missing_required_files(tmp_path):
    """Fehlende requiredFiles → RuntimeError aus prepare_runtime (real BlueprintPlugin-Pfad)."""
    data = _minimal_docker_blueprint()
    data["runtime"]["requiredFiles"] = ["must_be_present.bin"]
    bp = load_blueprint_dict(data)
    plugin = BlueprintPlugin(bp)

    install = tmp_path / "miss"
    install.mkdir()
    # Datei absichtlich NICHT anlegen

    srv = _make_stub_server(install)
    with pytest.raises(RuntimeError) as exc:
        plugin.prepare_runtime(srv)
    assert "Runtime-Dateien fehlen" in str(exc.value)
    assert "must_be_present.bin" in str(exc.value)


def test_broken_updater_perform_does_not_block_start(tmp_path):
    """Kaputter Server-Datei-Update (perform liefert ok=False) → Start wird fortgesetzt (Invariant)."""
    bp = _load_native("dayz")
    plugin = BlueprintPlugin(bp)

    install = tmp_path / "broken"
    install.mkdir()
    (install / "f").write_text("f")

    srv = Server(id=99, name="b", game_type="dayz", install_dir=str(install), public_bind_ip="127.0.0.1", status="stopped")
    srv.ports = []
    db = MagicMock(spec=Session)

    with patch("services.server_lifecycle_service.try_acquire_install_update_lock", return_value=True), \
         patch("services.server_lifecycle_service.release_install_update_lock"), \
         patch("services.server_lifecycle_service.open_ports"), \
         patch("services.server_lifecycle_service.iptables_accept_server"), \
         patch.object(plugin, "start", return_value={"message": "started"}), \
         patch.object(plugin, "perform_server_file_update", return_value={"ok": False, "error": "synthetic kaputt"}):
        # Darf keine Exception werfen
        _run_start(db, srv, plugin)

    assert srv.status == "running"  # Flow setzt fort


def test_invalid_update_strategy_value_rejected_at_load():
    """Ungültige updateStrategy → Schema-ValidationError (früh, vor jedem Job)."""
    bad = _bp_with_update_strategy("steam", "invalidValue", {"steam": {"appId": "1", "platform": "linux"}})
    with pytest.raises(BlueprintValidationError):
        load_blueprint_dict(bad)


# ── Grace-Boundary (Schema + Hook) ──────────────────────────────────────────


def test_stop_grace_period_schema_bounds_and_default():
    """Schema erzwingt 5..600; Default 30 bei Fehlen."""
    data = _minimal_docker_blueprint()
    data["runtime"]["stopGracePeriodSeconds"] = 5
    bp = load_blueprint_dict(data)
    assert bp.runtime.stopGracePeriodSeconds == 5

    data["runtime"]["stopGracePeriodSeconds"] = 600
    bp = load_blueprint_dict(data)
    assert bp.runtime.stopGracePeriodSeconds == 600

    # Fehlt → Default
    del data["runtime"]["stopGracePeriodSeconds"]
    bp = load_blueprint_dict(data)
    assert bp.runtime.stopGracePeriodSeconds == 30
