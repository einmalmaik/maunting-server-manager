"""Der LiveKit-Dienst: Token, Adressprüfung, Umschaltung, Maskierung.

Reine Dienst-Tests ohne HTTP. Die Endpunkte liegen in `test_livekit_admin.py`
und `test_call_rooms.py`.
"""

from __future__ import annotations

import time

import pytest
from jose import jwt
from sqlalchemy.orm import Session

from services import livekit_service
from services.livekit_service import LivekitZielAbgelehnt
from services.panel_settings_service import PanelSettingsService


# ── Zugangstoken ────────────────────────────────────────────────────────────


def test_zugangstoken_traegt_genau_die_noetigen_rechte() -> None:
    token = livekit_service.zugangstoken(
        "raum-abc", "u7", "alice", api_key="APItest", api_secret="geheim"
    )
    anspruch = jwt.decode(token, "geheim", algorithms=["HS256"])

    assert anspruch["iss"] == "APItest"
    assert anspruch["sub"] == "u7"
    assert anspruch["name"] == "alice"
    video = anspruch["video"]
    assert video["room"] == "raum-abc"
    assert video["roomJoin"] is True
    assert video["canPublish"] is True
    assert video["canSubscribe"] is True
    # Kein Verwaltungsrecht: ein Teilnehmertoken darf keine fremden Raeume sehen
    # und niemanden hinauswerfen.
    assert "roomAdmin" not in video
    assert "roomList" not in video
    assert "roomCreate" not in video


def test_zugangstoken_laeuft_ab_und_haelt_die_ttl_ein() -> None:
    vorher = int(time.time())
    token = livekit_service.zugangstoken(
        "raum", "u1", "bob", api_key="k", api_secret="s", ttl_sekunden=90
    )
    anspruch = jwt.decode(token, "s", algorithms=["HS256"])
    assert anspruch["exp"] - anspruch["nbf"] == 90
    assert anspruch["nbf"] >= vorher


def test_zugangstoken_gilt_nur_mit_dem_richtigen_geheimnis() -> None:
    token = livekit_service.zugangstoken(
        "raum", "u1", "bob", api_key="k", api_secret="richtig"
    )
    with pytest.raises(Exception):
        jwt.decode(token, "falsch", algorithms=["HS256"])


def test_nur_lesetoken_fuer_die_room_api() -> None:
    """Das Backend fragt LiveKit nur ab, es tritt nie selbst bei."""
    anspruch = jwt.decode(
        livekit_service._verwaltungstoken("k", "s"), "s", algorithms=["HS256"]
    )
    assert anspruch["video"] == {"roomList": True}
    assert anspruch["exp"] - anspruch["nbf"] == 60


# ── Adressprüfung (SSRF) ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "adresse",
    [
        "wss://127.0.0.1:7880",
        "wss://localhost/livekit",
        "https://10.0.0.5",
        "https://192.168.1.10:7880",
        "wss://[::1]",
        "http://beispiel.de",
        "ftp://beispiel.de",
        "",
        "   ",
    ],
)
def test_externe_adresse_weist_nicht_oeffentliche_ziele_ab(adresse: str) -> None:
    with pytest.raises(LivekitZielAbgelehnt):
        livekit_service.normalisiere_externe_url(adresse)


@pytest.mark.parametrize(
    ("eingabe", "erwartet"),
    [
        ("wss://projekt.livekit.cloud", "wss://projekt.livekit.cloud"),
        ("https://projekt.livekit.cloud", "wss://projekt.livekit.cloud"),
        ("projekt.livekit.cloud", "wss://projekt.livekit.cloud"),
        ("  wss://projekt.livekit.cloud/  ", "wss://projekt.livekit.cloud"),
    ],
)
def test_externe_adresse_wird_vereinheitlicht(eingabe: str, erwartet: str) -> None:
    assert livekit_service.normalisiere_externe_url(eingabe) == erwartet


# ── Umschaltung und Geheimnis ───────────────────────────────────────────────


def test_externe_konfiguration_legt_das_geheimnis_verschluesselt_ab(db: Session) -> None:
    livekit_service.speichere_konfiguration(
        "extern",
        url="wss://projekt.livekit.cloud",
        api_key="APIabcdef123456",
        api_secret="sehr-geheim",
        db=db,
    )

    abgelegt = PanelSettingsService.get(livekit_service.SCHLUESSEL_SECRET_ENC, "", db)
    assert abgelegt
    assert "sehr-geheim" not in abgelegt

    konf = livekit_service.konfiguration(db)
    assert konf.modus == "extern"
    assert konf.client_url == "wss://projekt.livekit.cloud"
    assert konf.api_url == "https://projekt.livekit.cloud"
    assert konf.api_secret == "sehr-geheim"
    assert konf.konfiguriert


def test_leeres_geheimnis_laesst_das_gespeicherte_stehen(db: Session) -> None:
    livekit_service.speichere_konfiguration(
        "extern", "wss://a.livekit.cloud", "APIeins", "erstes-geheimnis", db=db
    )
    livekit_service.speichere_konfiguration(
        "extern", "wss://b.livekit.cloud", "APIzwei", None, db=db
    )

    konf = livekit_service.konfiguration(db)
    assert konf.client_url == "wss://b.livekit.cloud"
    assert konf.api_key == "APIzwei"
    assert konf.api_secret == "erstes-geheimnis"


def test_leerer_schluessel_laesst_den_gespeicherten_stehen(db: Session) -> None:
    """Die Oberflaeche zeigt den Schluessel nur maskiert.

    Schickt sie leer zurueck, muss der gespeicherte Wert stehen bleiben; sonst
    landete der Maskentext `****abcd` als echter Schluessel in der Datenbank.
    """
    livekit_service.speichere_konfiguration(
        "extern", "wss://a.livekit.cloud", "APIecht", "geheim", db=db
    )
    livekit_service.speichere_konfiguration(
        "extern", "wss://a.livekit.cloud", "", None, db=db
    )

    assert livekit_service.konfiguration(db).api_key == "APIecht"


def test_extern_ohne_jedes_geheimnis_wird_abgelehnt(db: Session) -> None:
    with pytest.raises(LivekitZielAbgelehnt):
        livekit_service.speichere_konfiguration(
            "extern", "wss://projekt.livekit.cloud", "APIkey", None, db=db
        )


def test_extern_ohne_schluessel_wird_abgelehnt(db: Session) -> None:
    with pytest.raises(LivekitZielAbgelehnt):
        livekit_service.speichere_konfiguration(
            "extern", "wss://projekt.livekit.cloud", None, "geheim", db=db
        )


def test_extern_ignoriert_den_sidecar_vollstaendig(db: Session, monkeypatch) -> None:
    """Im Externmodus wird der mitgelieferte Sidecar nirgends mehr angefasst.

    Weder seine Adresse noch sein Schluesselpaar. Ein Betreiber, der LiveKit
    Cloud nutzt, kann `msm-livekit` abschalten, ohne dass Anrufe leiden.
    """
    monkeypatch.setattr(livekit_service.settings, "livekit_api_key", "APIsidecar")
    monkeypatch.setattr(livekit_service.settings, "livekit_api_secret", "sidecar-geheim")
    monkeypatch.setattr(livekit_service.settings, "livekit_url", "wss://panel.test/livekit")

    livekit_service.speichere_konfiguration(
        "extern", "wss://projekt.livekit.cloud", "APIextern", "extern-geheim", db=db
    )
    konf = livekit_service.konfiguration(db)

    assert konf.client_url == "wss://projekt.livekit.cloud"
    assert konf.api_url == "https://projekt.livekit.cloud"
    assert konf.api_key == "APIextern"
    assert konf.api_secret == "extern-geheim"
    assert "127.0.0.1" not in konf.api_url
    assert "/livekit" not in konf.client_url

    # Und die Raumabfrage geht an denselben externen Endpunkt, nicht an 7880.
    gesehen: dict[str, str] = {}
    monkeypatch.setattr(
        livekit_service,
        "_twirp",
        lambda api_url, *a, **k: gesehen.setdefault("url", api_url) and None or {"participants": []},
    )
    livekit_service.raum_teilnehmer("irgendein-raum", db)
    assert gesehen["url"] == "https://projekt.livekit.cloud"


def test_zurueck_auf_lokal_laesst_die_externen_daten_unangetastet(db: Session) -> None:
    """Wer versehentlich umschaltet, soll nicht alles neu eintippen muessen."""
    livekit_service.speichere_konfiguration(
        "extern", "wss://projekt.livekit.cloud", "APIkey", "geheim", db=db
    )
    livekit_service.speichere_konfiguration("lokal", db=db)

    assert livekit_service.modus(db) == "lokal"
    assert PanelSettingsService.get(livekit_service.SCHLUESSEL_API_KEY, "", db) == "APIkey"

    livekit_service.speichere_konfiguration(
        "extern", "wss://projekt.livekit.cloud", None, None, db=db
    )
    assert livekit_service.konfiguration(db).api_secret == "geheim"


# ── CSP ─────────────────────────────────────────────────────────────────────


def test_csp_herkunft_ist_lokal_leer_und_extern_beide_schemata(db: Session) -> None:
    assert livekit_service.csp_origin(db) == ""

    livekit_service.speichere_konfiguration(
        "extern", "wss://projekt.livekit.cloud", "APIkey", "geheim", db=db
    )
    herkunft = livekit_service.csp_origin(db)
    assert "wss://projekt.livekit.cloud" in herkunft
    assert "https://projekt.livekit.cloud" in herkunft


def test_externe_herkunft_steht_im_ausgelieferten_connect_src(db: Session) -> None:
    """Ohne diesen Eintrag blockiert der Browser die Verbindung stillschweigend.

    Kein Fehler in der Konsole des Panels, keine Meldung im Anrufraum — der
    Anruf klingelt und kommt nie zustande. Deshalb haengt das hier an einem
    Test und nicht an der Aufmerksamkeit beim naechsten CSP-Umbau.
    """
    import main

    assert "livekit" not in main._csp_connect_src()

    livekit_service.speichere_konfiguration(
        "extern", "wss://projekt.livekit.cloud", "APIkey", "geheim", db=db
    )
    connect_src = main._csp_connect_src()

    assert "wss://projekt.livekit.cloud" in connect_src
    assert "https://projekt.livekit.cloud" in connect_src
    assert "'self'" in connect_src


def test_lokaler_modus_braucht_keine_zusaetzliche_csp_herkunft(db: Session) -> None:
    """Der Sidecar liegt hinter Caddy auf derselben Herkunft, `'self'` deckt ihn."""
    import main

    assert livekit_service.modus(db) == "lokal"
    assert main._csp_connect_src().strip() == "'self'"


# ── Maskierung und Status ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("wert", "erwartet"),
    [
        ("", ""),
        ("kurz", "****"),
        ("APIabcdefgh", "*******efgh"),
    ],
)
def test_maskierung_zeigt_hoechstens_die_letzten_vier(wert: str, erwartet: str) -> None:
    assert livekit_service._maskiere(wert) == erwartet


def test_status_nennt_das_geheimnis_nirgends(db: Session, monkeypatch) -> None:
    livekit_service.speichere_konfiguration(
        "extern", "wss://projekt.livekit.cloud", "APIabcdefgh", "streng-geheim", db=db
    )
    monkeypatch.setattr(
        livekit_service, "_twirp", lambda *a, **k: {"rooms": [{"name": "r1"}]}
    )

    bericht = livekit_service.status(db)

    assert bericht["modus"] == "extern"
    assert bericht["konfiguriert"] is True
    assert bericht["erreichbar"] is True
    assert bericht["raeume_aktiv"] == 1
    assert bericht["api_key_maskiert"] == "*******efgh"
    assert "streng-geheim" not in repr(bericht)
    assert "APIabcdefgh" not in repr(bericht)


def test_lokale_adresse_kommt_vom_api_host_nicht_vom_frontend(monkeypatch) -> None:
    """Caddy reicht `/livekit` dort durch, wo auch `/api` liegt.

    Bei getrenntem Frontend ist das der API-Host. Zeigte die Adresse auf
    `panel_url`, verbaende der Browser gegen eine Domain, die den Pfad gar
    nicht kennt.
    """
    monkeypatch.setattr(livekit_service.settings, "livekit_url", "")
    monkeypatch.setattr(livekit_service.settings, "panel_url", "https://msm.example.de")
    monkeypatch.setattr(livekit_service.settings, "api_url", "https://api.msm.example.de")

    assert livekit_service._lokale_client_url() == "wss://api.msm.example.de/livekit"

    # All-in-one: kein eigener API-Host, dann gilt panel_url.
    monkeypatch.setattr(livekit_service.settings, "api_url", "")
    assert livekit_service._lokale_client_url() == "wss://msm.example.de/livekit"

    # Ein ausdruecklich gesetztes MSM_LIVEKIT_URL schlaegt beides.
    monkeypatch.setattr(livekit_service.settings, "livekit_url", "wss://medien.example.de")
    assert livekit_service._lokale_client_url() == "wss://medien.example.de"


def test_lokale_fehlermeldung_nennt_die_wirklich_geprueft_adresse(
    db: Session, monkeypatch
) -> None:
    """Sonst sucht der Betreiber den Fehler bei seiner Domain statt beim Dienst."""
    monkeypatch.setattr(livekit_service.settings, "livekit_api_key", "APIlokal")
    monkeypatch.setattr(livekit_service.settings, "livekit_api_secret", "lokal-geheim")
    monkeypatch.setattr(livekit_service.settings, "livekit_url", "wss://msm.example.de/livekit")

    def _kaputt(*_a, **_k):
        raise __import__("httpx").ConnectError("connection refused")

    monkeypatch.setattr(livekit_service, "_twirp", _kaputt)

    bericht = livekit_service.status(db)

    assert bericht["erreichbar"] is False
    fehler = bericht["fehler"]
    assert livekit_service.LOKALE_HTTP_URL in fehler
    assert "msm.example.de" in fehler
    assert "msm-livekit" in fehler


def test_status_meldet_unerreichbar_statt_zu_scheitern(db: Session, monkeypatch) -> None:
    livekit_service.speichere_konfiguration(
        "extern", "wss://projekt.livekit.cloud", "APIkey", "geheim", db=db
    )

    def _kaputt(*_a, **_k):
        raise RuntimeError("Netz weg")

    monkeypatch.setattr(livekit_service, "_twirp", _kaputt)

    bericht = livekit_service.status(db)
    assert bericht["erreichbar"] is False
    assert bericht["fehler"]


def test_teilnehmerzahl_bleibt_bei_fehlern_null(db: Session, monkeypatch) -> None:
    """Eine Einladungskarte soll auch erscheinen, wenn LiveKit klemmt."""
    livekit_service.speichere_konfiguration(
        "extern", "wss://projekt.livekit.cloud", "APIkey", "geheim", db=db
    )

    def _kaputt(*_a, **_k):
        raise RuntimeError("Netz weg")

    monkeypatch.setattr(livekit_service, "_twirp", _kaputt)
    assert livekit_service.raum_teilnehmer("irgendein-raum", db) == 0


def test_teilnehmerzahl_ohne_konfiguration_ist_null(db: Session) -> None:
    assert livekit_service.raum_teilnehmer("", db) == 0


def test_verbindung_pruefen_verlangt_alle_drei_angaben() -> None:
    erreichbar, meldung, raeume = livekit_service.verbindung_pruefen(
        "wss://projekt.livekit.cloud", "APIkey", ""
    )
    assert erreichbar is False
    assert raeume == 0
    assert meldung
