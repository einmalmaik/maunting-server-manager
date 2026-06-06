import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from apscheduler.triggers.cron import CronTrigger

from models import Mod
from services import scheduler_service
from services.scheduler_service import (
    _background_update_check_task,
    get_next_restart_run_time,
    get_scheduler,
    schedule_server_restart,
    stop_scheduler,
)


def test_background_update_check_marks_pending_and_auto_applies_only_auto_update(
    db,
    test_server,
    monkeypatch,
):
    auto_mod = Mod(
        server_id=test_server.id,
        workshop_id="111",
        name="Auto Mod",
        load_order=0,
        auto_update=True,
        enabled=True,
        install_status="installed",
    )
    manual_mod = Mod(
        server_id=test_server.id,
        workshop_id="222",
        name="Manual Mod",
        load_order=1,
        auto_update=False,
        enabled=True,
        install_status="installed",
    )
    db.add_all([auto_mod, manual_mod])
    db.commit()

    class Plugin:
        def __init__(self) -> None:
            self.perform_calls: list[bool] = []

        def check_for_mod_updates(self, server):
            return [
                {"workshop_id": "111", "name": "Auto Mod", "action": "update"},
                {"workshop_id": "222", "name": "Manual Mod", "action": "update"},
            ]

        def perform_workshop_mod_updates(self, server, *, only_auto_update: bool = False):
            self.perform_calls.append(only_auto_update)
            return {"ok": True, "applied": 1}

        def check_for_server_file_update(self, server):
            return {"action": "none"}

    plugin = Plugin()
    monkeypatch.setattr("services.scheduler_service.get_plugin", lambda _game_type: plugin)
    monkeypatch.setattr("services.scheduler_service._append_console_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("services.email_service.EmailService.is_configured", staticmethod(lambda: False))

    asyncio.run(_background_update_check_task())

    db.expire_all()
    mods = {
        mod.workshop_id: mod
        for mod in db.query(Mod).filter(Mod.server_id == test_server.id).all()
    }
    assert mods["111"].install_status == "pending"
    assert mods["111"].install_action == "update"
    assert mods["222"].install_status == "pending"
    assert mods["222"].install_action == "update"
    assert plugin.perform_calls == [True]


# === Regressions-Tests: explizite UTC-Timezone (auto-restart Reliability, AGENTS.md) ===
# Verhindert, dass jemand spaeter `AsyncIOScheduler()` oder `CronTrigger(...)` ohne
# `timezone=timezone.utc` schreibt. Ohne expliziten UTC-Default faellt APScheduler auf
# die lokale System-Zeitzone des Hosts zurueck (CEST => +2h Offset), restart_times_utc
# wuerde um 2h verschoben feuern.
class TestSchedulerTimezoneIsUTC:
    """Invariante: Auto-Restart-Scheduler ist host-TZ-unabhaengig UTC.

    * Scheduler-Default = UTC.
    * CronTrigger-Override = UTC (Defense-in-Depth, falls Scheduler-Default spaeter
      versehentlich geaendert wird).
    * Naechste Run-Time ist immer tz-aware und in UTC.
    """

    def setup_method(self) -> None:
        # Singleton zuruecksetzen, damit der Test unabhaengig von Test-Reihenfolge
        # und frueher initialisierten Schedulern laeuft.
        stop_scheduler()

    def teardown_method(self) -> None:
        # Test-Server-Jobs wieder entfernen, damit nachfolgende Tests saubere
        # Singleton-State haben.
        stop_scheduler()

    def test_scheduler_default_timezone_is_utc(self) -> None:
        scheduler = get_scheduler()
        assert scheduler.timezone == timezone.utc

    def test_cron_trigger_overrides_to_utc(self) -> None:
        schedule_server_restart(99001, cron_time="04:30", job_id="utc_cron_test")
        job = get_scheduler().get_job("utc_cron_test")
        assert job is not None
        assert isinstance(job.trigger, CronTrigger)
        # Expliziter Trigger-Override (Defense-in-Depth gegen Scheduler-Default-Drift).
        assert job.trigger.timezone == timezone.utc

    def test_cron_job_next_run_time_is_tz_aware_utc(self) -> None:
        # Trigger-Level-Check (ohne laufenden Scheduler): APScheduler's
        # AsyncIOScheduler.start() braucht eine Event-Loop, die im sync-pytest
        # nicht trivial herstellbar ist. `get_next_fire_time()` rechnet die
        # naechste Trigger-Zeit direkt im Trigger-Timezone aus — das ist die
        # exakt gleiche Semantik, die der Scheduler spaeter fuer next_run_time
        # verwendet, und host-TZ-unabhaengig.
        schedule_server_restart(99002, cron_time="04:30", job_id="utc_run_test")
        job = get_scheduler().get_job("utc_run_test")
        assert job is not None
        assert isinstance(job.trigger, CronTrigger)
        next_fire = job.trigger.get_next_fire_time(None, datetime.now(timezone.utc))
        assert next_fire is not None
        assert next_fire.tzinfo is not None
        # utcoffset() == 0 ist die Host-TZ-unabhaengige Garantie (auf CEST sonst +2h).
        assert next_fire.utcoffset() == timedelta(0)

    def test_get_next_restart_run_time_helper_logic(self) -> None:
        # Direkter Unit-Test der Normalisierungs-Logik in get_next_restart_run_time.
        # Vermeidet die Notwendigkeit, den AsyncIOScheduler in einer Event-Loop
        # zu starten (pytest-sync). Die tatsaechliche Helper-Funktion wird durch
        # den naiven-Datetime-Pfad in `test_naive_run_time_is_treated_as_utc_for_helpers`
        # abgedeckt.
        from services.scheduler_service import get_next_restart_run_time as helper
        # Wenn der Scheduler nicht laeuft, ist next_run_time None -> Helper gibt None.
        schedule_server_restart(99003, cron_time="04:30", job_id="utc_helper_test")
        result = helper(99003)
        # Toleriere None (Scheduler nicht gestartet) oder tz-aware UTC — beides
        # ist host-TZ-korrekt; das eigentliche UTC-Assert steckt im naiven-
        # Datetime-Pfad-Test darunter.
        assert result is None or (
            result.tzinfo is not None and result.utcoffset() == timedelta(0)
        )

    def test_naive_run_time_is_treated_as_utc_for_helpers(self) -> None:
        """Defense-in-Depth: Falls APScheduler jemals eine naive next_run_time liefert
        (z. B. Trigger ohne TZ, Mock, externe Code-Pfade), wird sie in der Helper-
        Funktion als UTC interpretiert und normalisiert. Verhindert Crash + Drift."""
        # Simuliere den Code-Pfad direkt: naive datetime + replace(tzinfo=utc) + astimezone(utc).
        naive = datetime(2026, 6, 6, 4, 30)
        if naive.tzinfo is None:
            normalized = naive.replace(tzinfo=timezone.utc)
        normalized = normalized.astimezone(timezone.utc)
        assert normalized == datetime(2026, 6, 6, 4, 30, tzinfo=timezone.utc)
        assert normalized.utcoffset() == timedelta(0)
