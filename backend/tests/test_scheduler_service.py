import asyncio

from models import Mod
from services.scheduler_service import _background_update_check_task


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
