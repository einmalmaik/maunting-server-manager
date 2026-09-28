"""Passkey-Bestaetigung im Browser fuer die Desktop-App.

Die App laeuft unter `tauri.localhost`, der Passkey gilt fuer die Adresse des
Panels. Bis 28.09.2026 konnte ein Passkey-Konto in der App deshalb nichts
bestaetigen (Datenexport, E2EE-Neustart). Jetzt legt die App einen Vorgang an,
der Browser bestaetigt ihn mit dem Passkey, die App loest ihn ein.

Invarianten:

1. Eingeloest wird nur ein bestaetigter Vorgang, nur vom eigenen Konto, nur
   fuer seinen Zweck und nur einmal, auch bei zwei gleichzeitigen Anfragen.
2. Die falsche Zahl verbraucht den Vorgang (Schutz gegen zugeschickte Links).
3. Login und OAuth lassen sich so nicht bestaetigen.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AuditLog, User
from services import passkey_service
from tests.test_passkey_2fa import HERKUNFT, _kopf, _passkey_einrichten


def _anlegen(client: TestClient, cookies: dict, zweck: str = "data_export"):
    return client.post(
        "/api/auth/passkey/browser", headers=_kopf(cookies), cookies=cookies, json={"zweck": zweck}
    )


def _im_browser(client: TestClient, geraet, vorgang: str, zahl: int):
    optionen = client.post(
        "/api/auth/passkey/browser/optionen", headers={"Origin": HERKUNFT}, json={"vorgang": vorgang}
    )
    assert optionen.status_code == 200, optionen.text
    daten = optionen.json()
    return daten, client.post(
        "/api/auth/passkey/browser/bestaetigen",
        headers={"Origin": HERKUNFT},
        json={"vorgang": vorgang, "zahl": zahl, "passkey": geraet.bestaetigen(daten["optionen"])},
    )


def _stand(client: TestClient, cookies: dict, vorgang: str) -> str:
    res = client.post(
        "/api/auth/passkey/browser/stand", headers=_kopf(cookies), cookies=cookies, json={"vorgang": vorgang}
    )
    assert res.status_code == 200, res.text
    return res.json()["stand"]


def _export(client: TestClient, cookies: dict, vorgang: str):
    return client.post(
        "/api/auth/data-export", headers=_kopf(cookies), cookies=cookies,
        json={"passkey": {"type": "browser", "vorgang": vorgang}},
    )


def test_export_aus_der_app_mit_bestaetigung_im_browser(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
):
    geraet = _passkey_einrichten(client, owner_cookies)
    vorgang = _anlegen(client, owner_cookies).json()
    assert vorgang["url"].endswith(f"/bestaetigen#{vorgang['vorgang']}")
    assert _stand(client, owner_cookies, vorgang["vorgang"]) == "offen"

    # Vor der Bestaetigung nimmt der Export den Vorgang nicht.
    assert _export(client, owner_cookies, vorgang["vorgang"]).status_code == 403

    daten, bestaetigt = _im_browser(client, geraet, vorgang["vorgang"], vorgang["zahl"])
    assert bestaetigt.status_code == 200, bestaetigt.text
    assert daten["zweck"] == "data_export"
    assert vorgang["zahl"] in daten["auswahl"] and len(daten["auswahl"]) == 3
    assert _stand(client, owner_cookies, vorgang["vorgang"]) == "bestaetigt"

    antwort = _export(client, owner_cookies, vorgang["vorgang"])
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["manifest"]["zugangsdaten_enthalten"] is True

    # Einmal eingeloest ist verbraucht.
    assert _stand(client, owner_cookies, vorgang["vorgang"]) == "verfallen"
    assert _export(client, owner_cookies, vorgang["vorgang"]).status_code == 403
    assert db.query(AuditLog).filter(AuditLog.action == "auth.data_export").count() == 1


def test_falsche_zahl_verbraucht_den_vorgang(client: TestClient, owner_user: User, owner_cookies: dict):
    geraet = _passkey_einrichten(client, owner_cookies)
    vorgang = _anlegen(client, owner_cookies).json()
    falsch = next(z for z in range(10, 100) if z != vorgang["zahl"])

    _, res = _im_browser(client, geraet, vorgang["vorgang"], falsch)
    assert res.status_code == 403
    assert _stand(client, owner_cookies, vorgang["vorgang"]) == "verfallen"
    nochmal = client.post(
        "/api/auth/passkey/browser/optionen", headers={"Origin": HERKUNFT}, json={"vorgang": vorgang["vorgang"]}
    )
    assert nochmal.status_code == 400


def test_gilt_nur_fuer_seinen_zweck(client: TestClient, owner_user: User, owner_cookies: dict):
    geraet = _passkey_einrichten(client, owner_cookies)
    vorgang = _anlegen(client, owner_cookies, zweck="device_pairing").json()
    _, res = _im_browser(client, geraet, vorgang["vorgang"], vorgang["zahl"])
    assert res.status_code == 200, res.text

    assert _export(client, owner_cookies, vorgang["vorgang"]).status_code == 403


def test_fremdes_konto_loest_nicht_ein(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict, regular_user: User
):
    geraet = _passkey_einrichten(client, owner_cookies)
    vorgang = _anlegen(client, owner_cookies).json()
    _im_browser(client, geraet, vorgang["vorgang"], vorgang["zahl"])

    try:
        passkey_service.bestaetigen(
            db, regular_user, {"type": "browser", "vorgang": vorgang["vorgang"]}, "data_export"
        )
        raise AssertionError("fremdes Konto hat eingeloest")
    except passkey_service.PasskeyFehler:
        pass
    assert _stand(client, owner_cookies, vorgang["vorgang"]) == "bestaetigt"


def test_login_ist_nicht_bestaetigbar(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    _passkey_einrichten(client, owner_cookies)
    assert _anlegen(client, owner_cookies, zweck="login").status_code == 400
    assert _anlegen(client, owner_cookies, zweck="oauth_2fa").status_code == 400


def test_nur_fuer_passkey_konten(client: TestClient, owner_user: User, owner_cookies: dict):
    assert _anlegen(client, owner_cookies).status_code == 400


def test_zwei_gleichzeitige_einloesungen_nur_eine_gilt(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict, monkeypatch
):
    """Lesen, pruefen, verbrauchen in einem bedingten UPDATE (AGENTS.md 43).

    Die zweite Einloesung laeuft in eigener Sitzung, waehrend die erste den
    Vorgang schon gelesen, aber noch nicht verbraucht hat.
    """
    import database as db_module

    geraet = _passkey_einrichten(client, owner_cookies)
    vorgang = _anlegen(client, owner_cookies).json()
    _im_browser(client, geraet, vorgang["vorgang"], vorgang["zahl"])

    echt = passkey_service._vorgang_umschreiben
    zweite: list[str] = []

    def dazwischen(sitzung, row, werte):
        if not zweite:
            zweite.append("laeuft")
            andere = db_module.SessionLocal()
            try:
                konto = andere.get(User, owner_user.id)
                passkey_service._browser_einloesen(andere, konto, vorgang["vorgang"], "data_export")
                zweite[0] = "eingeloest"
            except passkey_service.PasskeyFehler:
                zweite[0] = "abgelehnt"
            finally:
                andere.close()
        return echt(sitzung, row, werte)

    monkeypatch.setattr(passkey_service, "_vorgang_umschreiben", dazwischen)
    erste = _export(client, owner_cookies, vorgang["vorgang"])

    assert zweite == ["eingeloest"]
    assert erste.status_code == 403


def test_abgelaufen_wird_abgelehnt(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    from datetime import datetime, timedelta, timezone

    from models import LoginChallenge

    geraet = _passkey_einrichten(client, owner_cookies)
    vorgang = _anlegen(client, owner_cookies).json()
    _im_browser(client, geraet, vorgang["vorgang"], vorgang["zahl"])

    db.query(LoginChallenge).filter(LoginChallenge.purpose == "passkey_browser").update(
        {LoginChallenge.expires_at: datetime.now(timezone.utc) - timedelta(seconds=1)}
    )
    db.commit()

    assert _stand(client, owner_cookies, vorgang["vorgang"]) == "verfallen"
    assert _export(client, owner_cookies, vorgang["vorgang"]).status_code == 403


def test_warten_verbraucht_nicht_die_anmeldegrenze(client: TestClient, owner_user: User, owner_cookies: dict):
    """Die App fragt alle 2 s nach. Hing das an `auth_rate_limit` (10 je Minute
    und IP), sperrte das Warten nach 20 s die Bestaetigung im Browser gleich mit
    (Laufzeitprobe 28.09.2026: 429 auf der Seite und in der App)."""
    geraet = _passkey_einrichten(client, owner_cookies)
    vorgang = _anlegen(client, owner_cookies).json()
    for _ in range(15):
        assert _stand(client, owner_cookies, vorgang["vorgang"]) == "offen"

    _, bestaetigt = _im_browser(client, geraet, vorgang["vorgang"], vorgang["zahl"])
    assert bestaetigt.status_code == 200, bestaetigt.text
    assert _export(client, owner_cookies, vorgang["vorgang"]).status_code == 200
