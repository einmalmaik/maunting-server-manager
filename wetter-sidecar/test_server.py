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
    monkeypatch.setattr(server, "plz_lader_starten", lambda: None)
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


def test_plz_suche_findet_standorte_weltweit_ohne_einwohner_leak(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_pfad = tmp_path / "plz.sqlite"
    monkeypatch.setattr(server, "PLZ_DB", db_pfad)

    import sqlite3
    conn = sqlite3.connect(db_pfad)
    server._plz_db_init(conn)
    test_eintraege = [
        ("DE", "10115", "Berlin", server._normal("Berlin"), "Berlin", 52.52, 13.405),
        ("US", "90210", "Beverly Hills", server._normal("Beverly Hills"), "California", 34.0901, -118.4065),
        ("FR", "75001", "Paris 01", server._normal("Paris 01"), "Île-de-France", 48.86, 2.34),
    ]
    conn.executemany(
        "INSERT INTO plz (land, code, name, ascii_name, bundesland, lat, lon) VALUES (?, ?, ?, ?, ?, ?, ?)",
        test_eintraege,
    )
    conn.commit()
    conn.close()

    # 1. Deutsche PLZ
    de_treffer = server.orte_suchen("10115")
    assert len(de_treffer) >= 1
    assert de_treffer[0]["land"] == "DE"
    assert "10115" in de_treffer[0]["name"]
    assert "Berlin" in de_treffer[0]["name"]
    assert "einwohner" not in de_treffer[0]
    assert set(de_treffer[0].keys()) == {"name", "land", "lat", "lon"}

    # 2. US ZIP Code
    us_treffer = server.orte_suchen("90210")
    assert len(us_treffer) >= 1
    assert us_treffer[0]["land"] == "US"
    assert "Beverly Hills" in us_treffer[0]["name"]
    assert "einwohner" not in us_treffer[0]

    # 3. Französische PLZ
    fr_treffer = server.orte_suchen("75001")
    assert len(fr_treffer) >= 1
    assert fr_treffer[0]["land"] == "FR"
    assert "Paris" in fr_treffer[0]["name"]

    # 4. Suche nach Name in der PLZ-Tabelle
    name_treffer = server.orte_suchen("Berlin")
    assert len(name_treffer) >= 1
    assert any("Berlin" in t["name"] for t in name_treffer)

    # 5. Suche kombiniert mit PLZ und Name
    kombi_treffer = server.orte_suchen("10115 Berlin")
    assert len(kombi_treffer) >= 1
    assert "Berlin" in kombi_treffer[0]["name"]


def test_kleinorte_ohne_einwohnerfilter_in_cities5000(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # PLZ DB leer / nicht vorhanden -> Fallback auf cities5000
    monkeypatch.setattr(server, "PLZ_DB", tmp_path / "nicht_vorhanden.sqlite")
    monkeypatch.setattr(server, "_orte", None)

    staedte_datei = tmp_path / "cities5000.txt"
    monkeypatch.setattr(server, "ORTE_DATEI", staedte_datei)

    # Ein Ort mit nur 4500 Einwohnern (wurde früher durch < 15000 gefiltert)
    zeile = "12345\tMusterdorf\tMusterdorf\t\t50.1\t10.2\tP\tPPL\tDE\t\t\t\t\t\t4500\t\t\tEurope/Berlin\t2026-01-01"
    staedte_datei.write_text(zeile + "\n", encoding="utf-8")

    treffer = server.orte_suchen("Musterdorf")
    assert len(treffer) == 1
    assert treffer[0]["name"] == "Musterdorf"
    assert treffer[0]["land"] == "DE"
    assert "einwohner" not in treffer[0]

