"""Der QR-Code zur 2FA-Einrichtung darf das Geheimnis nicht aus dem Haus tragen.

Bis zum 11.08.2026 liess die Oberflaeche das Bild von api.qrserver.com holen und
schickte die vollstaendige `otpauth://`-URI als Query-Parameter mit — das
Base32-Geheimnis und die Kennung des Benutzers landeten damit im Zugriffslog
eines Fremden. Wer es liest, erzeugt gueltige Codes; `verify_totp` prueft reines
RFC 6238 ohne serverseitigen Zusatzfaktor.

Die Tests halten drei Zusagen fest: der Code entsteht im Panel, er ist von der
eigenen CSP gedeckt, und die Einrichtung ueberlebt es, wenn er einmal nicht
entsteht.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User
from services import totp_qr


def _setup(client: TestClient, cookies: dict) -> dict:
    res = client.post(
        "/api/auth/2fa/setup",
        cookies=cookies,
        headers={"X-CSRF-Token": cookies.get("__Secure-csrf_token")},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_the_setup_answer_carries_the_picture_itself(
    client: TestClient, owner_cookies: dict
) -> None:
    """Kein fremder Host mehr — das Bild steckt in der Antwort."""
    daten = _setup(client, owner_cookies)

    assert daten["qr_data_uri"], "Ohne Bild faellt die Oberflaeche auf Abtippen zurueck"
    assert daten["qr_data_uri"].startswith("data:image/svg+xml")

    # Die eigentliche Zusage: in der ganzen Antwort steht keine fremde Adresse.
    # Genau das war der Fehler — eine http(s)-URL zu einem Bilddienst, an den die
    # otpauth-URI als Query-Parameter ging.
    volltext = str(daten)
    assert "qrserver" not in volltext
    assert not re.search(r"https?://", volltext), volltext[:400]


def test_the_picture_is_covered_by_our_own_content_security_policy(
    client: TestClient, owner_cookies: dict
) -> None:
    """Ein Bild, das die eigene CSP verbietet, ist ein leerer Kasten.

    Der alte Weg scheiterte genau daran — allerdings nur dort, wo FastAPI das
    SPA-Dokument selbst ausliefert (Kubernetes); hinter Caddy lag ueberhaupt
    keine CSP auf dem Dokument, weshalb das Leck dort jahrelang unbemerkt lief.
    Dieser Test prueft beide Haelften zusammen: was der Endpunkt liefert, muss
    das erlauben, was die Kopfzeile zulaesst.
    """
    csp = client.get("/api/health").headers["Content-Security-Policy"]
    img_src = next(
        teil.strip() for teil in csp.split(";") if teil.strip().startswith("img-src")
    )
    assert "data:" in img_src, img_src

    daten = _setup(client, owner_cookies)
    schema = daten["qr_data_uri"].split(":", 1)[0] + ":"
    assert schema in img_src, f"{schema} steht nicht in {img_src}"


def test_the_picture_belongs_to_this_very_secret(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    """Ein konstantes oder veraltetes Bild waere schlimmer als gar keines.

    Wer einen fremden Code scannt, richtet einen zweiten Faktor ein, der nicht
    zu seinem Konto gehoert — und merkt es erst, wenn er sich aussperrt.
    """
    erste = _setup(client, owner_cookies)

    # Fuer einen zweiten Durchlauf muss 2FA wieder aus sein; `setup` verweigert
    # sonst mit 400, und das aus gutem Grund.
    owner_user.two_factor_enabled = False
    db.commit()
    zweite = _setup(client, owner_cookies)

    assert erste["secret"] != zweite["secret"]
    assert erste["qr_data_uri"] != zweite["qr_data_uri"]

    # Und das Bild gehoert zur URI derselben Antwort, nicht zu einer aelteren.
    # Das prueft die Verdrahtung, nicht die Bibliothek: dass der Endpunkt seine
    # eigene, frisch gebaute URI kodiert und nicht irgendeine.
    assert zweite["qr_data_uri"] == totp_qr.qr_datenuri(zweite["uri"])


def test_a_missing_drawing_library_does_not_take_the_panel_down(
    client: TestClient, owner_cookies: dict, monkeypatch
) -> None:
    """Eine fehlende Zeichenbibliothek kostet das Bild, nicht das Panel.

    Am 11.08.2026 war der Import hart. `routers/auth.py` laedt dieses Modul
    beim Start, `routers/__init__.py` laedt `auth`, `main.py` laedt `routers` —
    aus dem fehlenden `segno` wurde damit ein ModuleNotFoundError im
    Anwendungsimport: kein Login, keine Server, nichts, und systemd in einer
    Neustartschleife (Restart-Zaehler 43). Der Docstring des Moduls hatte zu
    dem Zeitpunkt bereits versprochen, ein Wegfall koste nur das Bild.

    Der Test stellt den Zustand nach: `segno is None` ist genau das, was der
    Rueckfall im Modulkopf hinterlaesst.
    """
    monkeypatch.setattr(totp_qr, "segno", None)

    assert totp_qr.qr_datenuri("otpauth://totp/x?secret=A") is None

    # Und der Weg zum zweiten Faktor bleibt vollstaendig begehbar.
    daten = _setup(client, owner_cookies)
    assert daten["qr_data_uri"] is None
    assert daten["secret"]
    assert daten["uri"].startswith("otpauth://")


def test_a_picture_that_cannot_be_drawn_does_not_block_the_second_factor(
    client: TestClient, owner_cookies: dict, monkeypatch
) -> None:
    """Das Bild ist eine Beigabe. Geheimnis und Link tragen allein."""
    assert totp_qr.qr_datenuri("") is None

    def explodiert(*_args, **_kwargs):
        raise RuntimeError("kein Bild heute")

    monkeypatch.setattr(totp_qr.segno, "make", explodiert)

    daten = _setup(client, owner_cookies)
    assert daten["qr_data_uri"] is None
    assert daten["secret"]
    assert daten["uri"].startswith("otpauth://")
