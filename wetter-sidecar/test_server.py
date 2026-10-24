"""Negativfälle des Wetter-Sidecars.

Der Sidecar liest nur eigene Dateien und antwortet auf falsche Koordinaten,
einen fehlenden oder kaputten Modelllauf und unlesbare DWD-Daten mit einem
sauberen Fehler statt mit halben Werten.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("MSM_WETTER_DATEN", tempfile.mkdtemp(prefix="wetter-sidecar-test-"))

import server  # noqa: E402


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Der Lader und die DWD-Schleife würden sonst Netzzugriffe im Hintergrund
    # starten; die Tests prüfen nur die Antworten auf kaputte Eingaben.
    monkeypatch.setattr(server, "lader_starten", lambda: None)
    monkeypatch.setattr(server, "_warn_lader", lambda: None)
    return TestClient(server.app)


@pytest.fixture(autouse=True)
def leere_zeitraeume(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(server, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(server, "GFS", tmp_path / "gfs")
    monkeypatch.setattr(server, "_warnungen", [])


def test_unbrauchbare_koordinaten_und_suchbegriffe_werden_abgelehnt(client: TestClient) -> None:
    assert client.get("/wetter", params={"lat": 91, "lon": 0}).status_code == 422
    assert client.get("/wetter", params={"lat": -91, "lon": 0}).status_code == 422
    assert client.get("/wetter", params={"lat": 0, "lon": 181}).status_code == 422
    assert client.get("/wetter", params={"lat": 52.5}).status_code == 422
    assert client.get("/wetter").status_code == 422
    assert client.get("/orte").status_code == 422
    assert client.get("/orte", params={"q": "a"}).status_code == 422
    assert client.get("/orte", params={"q": "x" * 121}).status_code == 422


def test_ohne_modelllauf_meldet_der_sidecar_nicht_bereit(client: TestClient) -> None:
    antwort = client.get("/wetter", params={"lat": 52.5, "lon": 13.4})

    assert antwort.status_code == 503
    assert antwort.json()["detail"]["code"] == "WETTER_NICHT_BEREIT"

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["bereit"] is False


def test_kaputter_zustand_meldet_nicht_bereit(client: TestClient, tmp_path: Path) -> None:
    server.STATE.write_text("{kaputt", encoding="utf-8")

    antwort = client.get("/wetter", params={"lat": 52.5, "lon": 13.4})

    assert antwort.status_code == 503
    assert antwort.json()["detail"]["code"] == "WETTER_NICHT_BEREIT"


def test_zustand_ohne_brauchbare_schritte_meldet_nicht_bereit(client: TestClient) -> None:
    server.STATE.write_text(
        json.dumps(
            {
                "zyklus": "2026-10-22T06:00:00+00:00",
                "ordner": "run-leer",
                "dateien": [],
                "quelle": "NOAA GFS",
            }
        ),
        encoding="utf-8",
    )

    antwort = client.get("/wetter", params={"lat": 52.5, "lon": 13.4})

    assert antwort.status_code == 503


def test_dwd_antwort_in_keiner_bekannten_kodierung_wird_abgelehnt() -> None:
    with pytest.raises(ValueError):
        server._dwd_json(b"\xff\xfe\x00\x01")


def test_geometrie_ohne_punkte_und_unbekannter_typ_wird_nicht_gewertet() -> None:
    assert server._bbox({}) == (0.0, 0.0, 0.0, 0.0)
    assert server._in_geom(13.4, 52.5, {"type": "Point", "coordinates": [13.4, 52.5]}) is False
    assert server.warnungen_fuer(52.5, 13.4) == []


def test_warnung_ausserhalb_ihrer_bbox_wird_nicht_gewertet() -> None:
    warnung = {
        "titel": "Sturm",
        "text": "",
        "stufe": "Severe",
        "gruppe": "",
        "beginn": None,
        "ende": None,
        "bbox": (13.0, 52.0, 13.5, 52.5),
        "geom": {
            "type": "Polygon",
            "coordinates": [[[13.0, 52.0], [13.5, 52.0], [13.5, 52.5], [13.0, 52.5], [13.0, 52.0]]],
        },
    }
    server._warnungen = [warnung]

    assert server.warnungen_fuer(53.1, 14.2) == []
    assert [w["titel"] for w in server.warnungen_fuer(52.2, 13.2)] == ["Sturm"]


def test_zu_kurzer_ort_begriff_laedt_keine_liste() -> None:
    assert server.orte_suchen(" a ") == []


def test_zu_kleine_gfs_datei_wird_geloescht(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Antwort:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self, size: int):
            yield b"x" * 1000

    monkeypatch.setattr(server.httpx, "stream", lambda *a, **k: Antwort())
    ziel = tmp_path / "gfs" / "f000.grib2"

    with pytest.raises(RuntimeError, match="zu klein"):
        server._lade_datei("http://127.0.0.1/datei", ziel)

    assert not ziel.exists()


def test_wetter_url_enthaelt_lauf_und_stunde() -> None:
    zyklus = server.datetime(2026, 10, 22, 6, tzinfo=server.timezone.utc)

    url = server._gfs_url(zyklus, 3)

    assert "gfs.t06z.pgrb2full.0p50.f003" in url
    assert "gfs.20261022" in url
    assert "subregion=" in url
