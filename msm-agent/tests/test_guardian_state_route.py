"""GET /containers/{name}/guardian-state bei beschaedigter Zustandsdatei.

Bis 28.09.2026 lief CorruptedGuardianStateError ungefangen durch: 500, und
ein Vorfall entstand erst, wenn die Guardian-Schleife zufaellig vorher las.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config import settings
from services import guardian_service


@pytest.fixture()
def ruhige_schleife(servers_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Die Schleife liest die Datei sonst womoeglich vor der Route."""

    async def nichts() -> None:
        return None

    monkeypatch.setattr(guardian_service, "reconcile_all_servers", nichts)
    guardian_service.reset_guardian_service_for_tests()
    from main import app

    with TestClient(app) as client:
        yield client
    guardian_service.reset_guardian_service_for_tests()


def test_beschaedigter_sollzustand_ist_409_mit_vorfall(
    ruhige_schleife: TestClient, auth_headers: dict[str, str]
) -> None:
    server_dir = Path(settings.guardian_state_dir) / "7"
    server_dir.mkdir(parents=True, exist_ok=True)
    (server_dir / "desired-state.json").write_text("{kaputt", encoding="utf-8")

    res = ruhige_schleife.get("/containers/msm-srv-7/guardian-state", headers=auth_headers)

    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "stored_state_corrupted"
    assert not (server_dir / "desired-state.json").exists()
    beiseite = list(server_dir.glob("desired-state.json.corrupt-*"))
    assert len(beiseite) == 1

    vorfaelle = ruhige_schleife.get("/containers/msm-srv-7/incidents", headers=auth_headers).json()
    assert [v["type"] for v in vorfaelle] == ["guardian_state_corruption"]
    assert vorfaelle[0]["payload"]["retained_file"] == beiseite[0].name


def test_fehlender_sollzustand_bleibt_404(
    ruhige_schleife: TestClient, auth_headers: dict[str, str]
) -> None:
    res = ruhige_schleife.get("/containers/msm-srv-8/guardian-state", headers=auth_headers)

    assert res.status_code == 404
    assert res.json()["detail"]["code"] == "guardian_state_not_found"
    assert ruhige_schleife.get("/containers/msm-srv-8/incidents", headers=auth_headers).json() == []
