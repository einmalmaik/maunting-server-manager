"""Die Betreiber-Umschaltung unter „Messenger / Anrufe".

Geprueft wird, was hinausgeht und was hereinkommt: das API-Secret verlaesst den
Server nie, der Testknopf speichert nichts, und ohne `panel.settings.write`
aendert niemand etwas.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User
from services import livekit_service
from services.panel_settings_service import PanelSettingsService

EXTERN = "wss://projekt.livekit.cloud"


def _header(kekse: dict) -> dict[str, str]:
    return {"X-CSRF-Token": kekse.get("__Secure-csrf_token", "")}


def test_status_liefert_modus_und_maskierten_schluessel(
    client: TestClient, owner_cookies: dict, db: Session, monkeypatch
) -> None:
    monkeypatch.setattr(livekit_service, "_twirp", lambda *a, **k: {"rooms": []})
    livekit_service.speichere_konfiguration(
        "extern", EXTERN, "APIabcdefgh", "streng-geheim", db=db
    )

    antwort = client.get("/api/admin/messenger/livekit/status", cookies=owner_cookies)
    assert antwort.status_code == 200
    daten = antwort.json()

    assert daten["modus"] == "extern"
    assert daten["url"] == EXTERN
    assert daten["konfiguriert"] is True
    assert daten["api_key_maskiert"] == "*******efgh"
    assert "streng-geheim" not in antwort.text
    assert "APIabcdefgh" not in antwort.text


def test_umschalten_speichert_das_geheimnis_verschluesselt(
    client: TestClient, owner_cookies: dict, db: Session, monkeypatch
) -> None:
    monkeypatch.setattr(livekit_service, "_twirp", lambda *a, **k: {"rooms": []})

    antwort = client.put(
        "/api/admin/messenger/livekit/config",
        json={
            "modus": "extern",
            "url": EXTERN,
            "api_key": "APIabcdefgh",
            "api_secret": "streng-geheim",
        },
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 200
    assert antwort.json()["modus"] == "extern"
    assert "streng-geheim" not in antwort.text

    abgelegt = PanelSettingsService.get(livekit_service.SCHLUESSEL_SECRET_ENC, "", db)
    assert abgelegt and "streng-geheim" not in abgelegt


def test_erneutes_speichern_ohne_geheimnis_behaelt_es(
    client: TestClient, owner_cookies: dict, db: Session, monkeypatch
) -> None:
    """Die Oberflaeche schickt weder Schluessel noch Geheimnis zurueck.

    Das darf nicht heissen, dass beim naechsten Speichern beides verloren geht.
    """
    monkeypatch.setattr(livekit_service, "_twirp", lambda *a, **k: {"rooms": []})
    livekit_service.speichere_konfiguration("extern", EXTERN, "APIecht", "geheim", db=db)

    antwort = client.put(
        "/api/admin/messenger/livekit/config",
        json={"modus": "extern", "url": "wss://anderes.livekit.cloud"},
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 200

    konf = livekit_service.konfiguration(db)
    assert konf.client_url == "wss://anderes.livekit.cloud"
    assert konf.api_key == "APIecht"
    assert konf.api_secret == "geheim"


def test_lokaler_sidecar_wird_nicht_als_externes_ziel_akzeptiert(
    client: TestClient, owner_cookies: dict, db: Session
) -> None:
    """SSRF-Schranke: im Externmodus sind nur oeffentliche Ziele zulaessig."""
    for adresse in ("http://127.0.0.1:7880", "wss://192.168.1.10:7880", "wss://localhost"):
        antwort = client.put(
            "/api/admin/messenger/livekit/config",
            json={
                "modus": "extern",
                "url": adresse,
                "api_key": "APIkey",
                "api_secret": "geheim",
            },
            cookies=owner_cookies,
            headers=_header(owner_cookies),
        )
        assert antwort.status_code == 400, adresse

    assert livekit_service.modus(db) == "lokal"


def test_testknopf_speichert_nichts(
    client: TestClient, owner_cookies: dict, db: Session, monkeypatch
) -> None:
    """Ein falsches Geheimnis soll die Datenbank gar nicht erst erreichen."""
    monkeypatch.setattr(livekit_service, "_twirp", lambda *a, **k: {"rooms": [{"n": 1}]})

    antwort = client.post(
        "/api/admin/messenger/livekit/test",
        json={"url": EXTERN, "api_key": "APIprobe", "api_secret": "probe-geheim"},
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 200
    assert antwort.json()["erreichbar"] is True
    assert antwort.json()["raeume_aktiv"] == 1

    assert livekit_service.modus(db) == "lokal"
    assert PanelSettingsService.get(livekit_service.SCHLUESSEL_API_KEY, "", db) == ""


def test_testknopf_meldet_abgelehnte_zugangsdaten(
    client: TestClient, owner_cookies: dict, monkeypatch
) -> None:
    import httpx

    def _abgelehnt(*_a, **_k):
        raise httpx.HTTPStatusError(
            "nope",
            request=httpx.Request("POST", "https://x"),
            response=httpx.Response(401),
        )

    monkeypatch.setattr(livekit_service, "_twirp", _abgelehnt)

    antwort = client.post(
        "/api/admin/messenger/livekit/test",
        json={"url": EXTERN, "api_key": "APIfalsch", "api_secret": "falsch"},
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 200
    assert antwort.json()["erreichbar"] is False
    assert antwort.json()["meldung"]


def test_testknopf_weist_nicht_oeffentliche_adressen_ab(
    client: TestClient, owner_cookies: dict
) -> None:
    antwort = client.post(
        "/api/admin/messenger/livekit/test",
        json={"url": "http://127.0.0.1:7880", "api_key": "k", "api_secret": "s"},
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 200
    assert antwort.json()["erreichbar"] is False


def test_testknopf_nutzt_das_gespeicherte_geheimnis(
    client: TestClient, owner_cookies: dict, db: Session, monkeypatch
) -> None:
    """Leere Felder meinen den gespeicherten Stand — sonst waere ein erneuter
    Test nur nach vollstaendiger Neueingabe moeglich."""
    livekit_service.speichere_konfiguration("extern", EXTERN, "APIecht", "geheim", db=db)
    gesehen: dict[str, str] = {}

    def _merke(api_url, api_key, api_secret, *_a, **_k):
        gesehen["key"] = api_key
        gesehen["secret"] = api_secret
        return {"rooms": []}

    monkeypatch.setattr(livekit_service, "_twirp", _merke)

    antwort = client.post(
        "/api/admin/messenger/livekit/test",
        json={"url": EXTERN},
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 200
    assert antwort.json()["erreichbar"] is True
    assert gesehen == {"key": "APIecht", "secret": "geheim"}


def test_ohne_schreibrecht_aendert_niemand_etwas(
    client: TestClient, user_cookies: dict, regular_user: User, db: Session
) -> None:
    antwort = client.put(
        "/api/admin/messenger/livekit/config",
        json={
            "modus": "extern",
            "url": EXTERN,
            "api_key": "APIkey",
            "api_secret": "geheim",
        },
        cookies=user_cookies,
        headers=_header(user_cookies),
    )
    assert antwort.status_code == 403
    assert livekit_service.modus(db) == "lokal"


def test_ohne_leserecht_kein_status(client: TestClient, user_cookies: dict) -> None:
    antwort = client.get("/api/admin/messenger/livekit/status", cookies=user_cookies)
    assert antwort.status_code == 403


def test_ohne_anmeldung_kein_zugriff(client: TestClient) -> None:
    assert client.get("/api/admin/messenger/livekit/status").status_code == 401
