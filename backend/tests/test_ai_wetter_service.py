"""Fehlerpfade des Wetterdienstes zum eigenen Sidecar.

Der Browser spricht nur mit MSM; MSM darf bei einem kaputten, langsamen oder
falsch antwortenden Sidecar keine halben Daten weiterreichen und keine
Fehlerantwort in den Cache legen.
"""

from __future__ import annotations

import httpx
import pytest

from config import settings
from services import ai_wetter_service
from services.ai_wetter_service import WetterUnavailable

STAND = {
    "jetzt": {"temp": 18.0, "lage": "klar"},
    "stunden": [],
    "tage": [],
    "quelle": "NOAA GFS 0.50°",
    "stand": "2026-10-22T06:00:00+00:00",
}


class Antwort:
    def __init__(self, status: int = 200, daten: object = None, fehler: Exception | None = None) -> None:
        self.status_code = status
        self._daten = daten
        self._fehler = fehler

    def json(self) -> object:
        if self._fehler is not None:
            raise self._fehler
        return self._daten


class Klient:
    def __init__(self, *antworten: Antwort | Exception) -> None:
        self.antworten = list(antworten)
        self.aufrufe: list[tuple[str, dict | None]] = []

    def get(self, url: str, params: dict | None = None, **_):
        self.aufrufe.append((url, params))
        antwort = self.antworten.pop(0) if len(self.antworten) > 1 else self.antworten[0]
        if isinstance(antwort, Exception):
            raise antwort
        return antwort


@pytest.fixture(autouse=True)
def sauber(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_wetter_service, "_cache", {})
    monkeypatch.setattr(ai_wetter_service, "_status", None)
    monkeypatch.setattr(settings, "wetter_url", "http://127.0.0.1:8890/")


def _dienst(monkeypatch: pytest.MonkeyPatch, client: Klient) -> None:
    monkeypatch.setattr(ai_wetter_service, "is_sidecar_running", lambda timeout=0.2: True)
    monkeypatch.setattr(ai_wetter_service, "_http_client", lambda: client)


def test_ohne_laufenden_sidecar_keine_anfrage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_wetter_service, "is_sidecar_running", lambda timeout=0.2: False)
    monkeypatch.setattr(ai_wetter_service, "_http_client", lambda: pytest.fail("Sidecar darf nicht gefragt werden"))

    for aufruf in (
        lambda: ai_wetter_service.wetter_fuer_punkt(52.5, 13.4),
        lambda: ai_wetter_service.wetter_orte("berlin"),
    ):
        with pytest.raises(WetterUnavailable) as fehler:
            aufruf()
        assert fehler.value.code == "WETTER_SIDECAR_NICHT_ERREICHBAR"

    assert ai_wetter_service.wetter_konfig()["bereit"] is False
    assert ai_wetter_service.wetter_konfig()["sidecar_laeuft"] is False


def test_netzfehler_wird_zu_nicht_erreichbar(monkeypatch: pytest.MonkeyPatch) -> None:
    _dienst(monkeypatch, Klient(httpx.ConnectError("weg")))

    with pytest.raises(WetterUnavailable) as fehler:
        ai_wetter_service.wetter_fuer_punkt(52.5, 13.4)
    assert fehler.value.code == "WETTER_SIDECAR_NICHT_ERREICHBAR"

    with pytest.raises(WetterUnavailable):
        ai_wetter_service.wetter_orte("berlin")


def test_nicht_200_wird_abgelehnt(monkeypatch: pytest.MonkeyPatch) -> None:
    _dienst(monkeypatch, Klient(Antwort(status=500, daten=STAND)))

    with pytest.raises(WetterUnavailable):
        ai_wetter_service.wetter_fuer_punkt(52.5, 13.4)
    with pytest.raises(WetterUnavailable):
        ai_wetter_service.wetter_orte("berlin")


def test_kaputtes_json_wird_abgelehnt(monkeypatch: pytest.MonkeyPatch) -> None:
    _dienst(monkeypatch, Klient(Antwort(fehler=ValueError("kein JSON"))))

    with pytest.raises(WetterUnavailable):
        ai_wetter_service.wetter_fuer_punkt(52.5, 13.4)
    with pytest.raises(WetterUnavailable):
        ai_wetter_service.wetter_orte("berlin")


def test_antwort_ohne_jetzt_wird_nicht_als_stand_ausgegeben(monkeypatch: pytest.MonkeyPatch) -> None:
    for daten in ({}, {"stunden": []}, ["keine", "liste"], None):
        _dienst(monkeypatch, Klient(Antwort(daten=daten)))
        with pytest.raises(WetterUnavailable):
            ai_wetter_service.wetter_fuer_punkt(52.5, 13.4)


def test_orte_ohne_liste_werden_zu_leerer_liste(monkeypatch: pytest.MonkeyPatch) -> None:
    _dienst(monkeypatch, Klient(Antwort(daten={"orte": "kaputt"})))

    assert ai_wetter_service.wetter_orte("berlin") == []


def test_konfig_meldet_bei_kaputtem_health_nicht_bereit(monkeypatch: pytest.MonkeyPatch) -> None:
    for antwort in (Antwort(status=500, daten={}), Antwort(daten=["kein", "objekt"]), Antwort(fehler=ValueError("x"))):
        _dienst(monkeypatch, Klient(antwort))
        assert ai_wetter_service.wetter_konfig()["bereit"] is False


def test_konfig_meldet_nur_mit_bereit_true(monkeypatch: pytest.MonkeyPatch) -> None:
    _dienst(monkeypatch, Klient(Antwort(daten={"bereit": True, "orte": 3})))

    assert ai_wetter_service.wetter_konfig()["bereit"] is True


def test_erfolg_wird_gerundet_gecacht(monkeypatch: pytest.MonkeyPatch) -> None:
    client = Klient(Antwort(daten=STAND))
    _dienst(monkeypatch, client)

    assert ai_wetter_service.wetter_fuer_punkt(52.516, 13.377) == STAND
    assert ai_wetter_service.wetter_fuer_punkt(52.518, 13.382) == STAND

    assert len(client.aufrufe) == 1
    assert client.aufrufe[0][0] == "http://127.0.0.1:8890/wetter"
    assert client.aufrufe[0][1] == {"lat": 52.52, "lon": 13.38}


def test_fehler_wird_nicht_gecacht(monkeypatch: pytest.MonkeyPatch) -> None:
    client = Klient(httpx.ConnectError("weg"), Antwort(daten=STAND))
    _dienst(monkeypatch, client)

    with pytest.raises(WetterUnavailable):
        ai_wetter_service.wetter_fuer_punkt(52.5, 13.4)
    assert ai_wetter_service.wetter_fuer_punkt(52.5, 13.4) == STAND
    assert len(client.aufrufe) == 2


def test_sidecar_url_hat_keinen_doppelten_schraegstrich() -> None:
    assert ai_wetter_service.wetter_url() == "http://127.0.0.1:8890"
