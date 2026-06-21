"""API: reinstall-all workshop mods."""

from models import Mod


class _ModsPlugin:
    supports_mods = True

    def cleanup_mod(self, server, workshop_id):
        return {"ok": True}

    def install_mods(self, server, workshop_ids):
        return {
            "ok": True,
            "items": {wid: {"ok": True} for wid in workshop_ids},
        }

    def update_modlist(self, server):
        return None


def test_reinstall_all_marks_pending_and_queues(client, db, test_server, owner_cookies, csrf_token, monkeypatch):
    monkeypatch.setattr("routers.mods.get_plugin", lambda _gt: _ModsPlugin())
    ran: list[tuple[int, list[str]]] = []

    def _fake_bg(server_id: int, workshop_ids: list[str]) -> None:
        ran.append((server_id, list(workshop_ids)))

    monkeypatch.setattr("routers.mods.reinstall_all_mods_bg", _fake_bg)

    for i, wid in enumerate(("111", "222")):
        db.add(
            Mod(
                server_id=test_server.id,
                workshop_id=wid,
                name=f"Mod {wid}",
                load_order=i,
                install_status="installed",
                install_progress=100,
                update_status="up_to_date",
            )
        )
    db.commit()

    headers = {"X-CSRF-Token": csrf_token or ""}
    response = client.post(
        f"/api/mods/{test_server.id}/reinstall-all",
        cookies=owner_cookies,
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert all(m["install_status"] == "pending" for m in payload)
    assert all(m["install_action"] == "reinstall" for m in payload)
    assert ran == [(test_server.id, ["111", "222"])]