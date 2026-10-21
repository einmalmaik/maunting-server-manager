"""Geraetefreigabe: wer nur das Passwort hat, liest nicht mit.

Die Freigabe ist eine Unterschrift eines freigegebenen Geraets ueber beide
Schluessel des neuen (`freigabe_daten`). Entfernen verlangt dieselbe Art
Unterschrift und sperrt die Sitzung des Geraets sofort. Die Clients pruefen die
Unterschrift selbst — das steht in `e2eeGeraet.test.ts`; hier steht, was der
Server durchsetzt.
"""

from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.responses import Response

from dependencies import get_current_user, verify_csrf
from main import app
from models import User
from services import e2ee_device_service
from services.auth_service import AuthService
from services.session_service import issue_session


def _rsa_jwk(marker: str = "A") -> str:
    return json.dumps({
        "kty": "RSA",
        "n": marker * 350,
        "e": "AQAB",
        "alg": "RSA-OAEP-256",
        "use": "enc",
        "key_ops": ["encrypt"],
    })


def _ecdsa_paar():
    priv = ec.generate_private_key(ec.SECP256R1())
    zahlen = priv.public_key().public_numbers()

    def b64url(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

    jwk = json.dumps({
        "kty": "EC",
        "crv": "P-256",
        "x": b64url(zahlen.x.to_bytes(32, "big")),
        "y": b64url(zahlen.y.to_bytes(32, "big")),
        "use": "sig",
        "key_ops": ["verify"],
    })
    return priv, jwk


def _unterschreibe(priv, daten: str) -> str:
    der = priv.sign(daten.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode("ascii")


def _freigabe(priv, user_id: int, device_id: str, rsa: str, ecdsa: str) -> str:
    return _unterschreibe(priv, e2ee_device_service.freigabe_daten(user_id, device_id, rsa, ecdsa))


def _entfernen(priv, user_id: int, device_id: str, rsa: str) -> str:
    return _unterschreibe(priv, e2ee_device_service.entfernen_daten(user_id, device_id, rsa))


RSA_A = _rsa_jwk("A")
RSA_B = _rsa_jwk("B")
RSA_X = _rsa_jwk("X")


def _konto(db: Session, name: str) -> User:
    user = User(username=name, email=f"{name}@msm.local", password_hash="pw", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _zwei_geraete(db: Session, user: User):
    """Telefon (erstes, freigegeben) und Laptop (wartend)."""
    tel_priv, tel_ecdsa = _ecdsa_paar()
    lap_priv, lap_ecdsa = _ecdsa_paar()
    e2ee_device_service.veroeffentlichen(db, user, "telefon-0001", RSA_A, "Telefon", tel_ecdsa, familie="fam-tel")
    e2ee_device_service.veroeffentlichen(db, user, "laptop-00002", RSA_B, "Laptop", lap_ecdsa, familie="fam-lap")
    return tel_priv, tel_ecdsa, lap_priv, lap_ecdsa


def test_erstes_geraet_frei_weiteres_wartet_bis_zur_unterschrift(db: Session, clean_db):
    user = _konto(db, "freigabe1")
    tel_priv, _, _, lap_ecdsa = _zwei_geraete(db, user)

    assert [g["device_id"] for g in e2ee_device_service.geraete(db, user.id, nur_bestaetigt=True)] == ["telefon-0001"]
    assert [g["device_id"] for g in e2ee_device_service.ausstehende_geraete(db, user)] == ["laptop-00002"]

    # Ohne Unterzeichner, mit Unsinn, mit der alten v1-Unterschrift: nichts.
    assert e2ee_device_service.bestaetigen(db, user, "laptop-00002") is False
    assert e2ee_device_service.bestaetigen(
        db, user, "laptop-00002", "telefon-0001", base64.b64encode(b"0" * 64).decode()
    ) is False
    v1 = _unterschreibe(tel_priv, f"msm:device-approval:v1:{user.id}:laptop-00002")
    assert e2ee_device_service.bestaetigen(db, user, "laptop-00002", "telefon-0001", v1) is False

    sig = _freigabe(tel_priv, user.id, "laptop-00002", RSA_B, lap_ecdsa)
    assert e2ee_device_service.bestaetigen(db, user, "laptop-00002", "telefon-0001", sig) is True

    laptop = next(g for g in e2ee_device_service.geraete(db, user.id, nur_bestaetigt=True) if g["device_id"] == "laptop-00002")
    # Der Beleg geht mit hinaus — die Gegenueber pruefen ihn selbst.
    assert laptop["approved_by"] == "telefon-0001"
    assert laptop["approval_signature"] == sig


def test_freigabe_deckt_nur_die_unterschriebenen_schluessel(db: Session, clean_db):
    """Eine Unterschrift ueber andere Schluessel gibt dieses Geraet nicht frei."""
    user = _konto(db, "freigabe2")
    tel_priv, _, _, lap_ecdsa = _zwei_geraete(db, user)
    fremd = _freigabe(tel_priv, user.id, "laptop-00002", RSA_X, lap_ecdsa)
    assert e2ee_device_service.bestaetigen(db, user, "laptop-00002", "telefon-0001", fremd) is False


def test_geraet_ohne_signaturschluessel_gibt_nichts_frei(db: Session, clean_db):
    user = _konto(db, "freigabe3")
    e2ee_device_service.veroeffentlichen(db, user, "altgeraet-01", RSA_A, "Alt", "")
    _, neu_ecdsa = _ecdsa_paar()
    e2ee_device_service.veroeffentlichen(db, user, "neugeraet-02", RSA_B, "Neu", neu_ecdsa)
    assert e2ee_device_service.bestaetigen(db, user, "neugeraet-02", "altgeraet-01", "x" * 88) is False


def test_schluesselwechsel_nimmt_freigabe_und_beleg(db: Session, clean_db):
    user = _konto(db, "freigabe4")
    tel_priv, tel_ecdsa, _, lap_ecdsa = _zwei_geraete(db, user)
    sig = _freigabe(tel_priv, user.id, "laptop-00002", RSA_B, lap_ecdsa)
    assert e2ee_device_service.bestaetigen(db, user, "laptop-00002", "telefon-0001", sig)

    neu = e2ee_device_service.veroeffentlichen(db, user, "laptop-00002", RSA_X, "Laptop", lap_ecdsa, familie="fam-lap")
    assert neu.is_approved is False
    assert neu.approved_by is None and neu.approval_signature is None

    # Ein nachgereichter Signaturschluessel ist ebenfalls ein neuer Schluessel.
    user2 = _konto(db, "freigabe4b")
    e2ee_device_service.veroeffentlichen(db, user2, "erstes-00001", RSA_A, "Eins", tel_ecdsa)
    e2ee_device_service.veroeffentlichen(db, user2, "zweites-0002", RSA_B, "Zwei", "")
    e2ee_device_service.bestaetigen(db, user2, "zweites-0002")  # scheitert: Unterzeichner fehlt
    assert e2ee_device_service.ausstehende_geraete(db, user2)


def test_fremde_sitzung_ueberschreibt_kein_geraet(db: Session, clean_db):
    """Unter einer bekannten Kennung legt nur die eigene Sitzung neue Schluessel ab."""
    user = _konto(db, "freigabe5")
    _, tel_ecdsa, _, _ = _zwei_geraete(db, user)
    try:
        e2ee_device_service.veroeffentlichen(db, user, "telefon-0001", RSA_X, "Dieb", tel_ecdsa, familie="fam-dieb")
    except e2ee_device_service.FremdeSitzungError:
        pass
    else:
        raise AssertionError("fremde Sitzung durfte den Schluessel ersetzen")
    tel = next(g for g in e2ee_device_service.geraete(db, user.id) if g["device_id"] == "telefon-0001")
    assert tel["public_key"] == RSA_A and tel["is_approved"] is True

    # Dasselbe Geraet nach neuer Anmeldung (neue Familie, gleiche Schluessel) ist willkommen.
    e2ee_device_service.veroeffentlichen(db, user, "telefon-0001", RSA_A, "Telefon", tel_ecdsa, familie="fam-neu")


def test_geraet_ohne_familie_nimmt_keinen_fremden_schluessel(db: Session, clean_db):
    """Unbekannte Familie heisst fremd: kein Schluesselwechsel, keine geerbte Freigabe.

    Altbestand vor `20260924_01` und Zeilen aus Tokens ohne Familie haben keine
    `auth_family`. Ist so ein Geraet das einzige freigegebene, behielt ein
    fremder Schluessel darunter bis 29.09.2026 die Freigabe.
    """
    user = _konto(db, "freigabe5b")
    _, tel_ecdsa = _ecdsa_paar()
    _, dieb_ecdsa = _ecdsa_paar()
    e2ee_device_service.veroeffentlichen(db, user, "altgeraet-01", RSA_A, "Telefon", tel_ecdsa)
    alt = e2ee_device_service._geraet(db, user.id, "altgeraet-01")
    assert alt.is_approved is True and alt.auth_family is None

    versuche = (
        (RSA_X, tel_ecdsa, "fam-dieb"),  # neuer RSA-Schluessel
        (RSA_A, dieb_ecdsa, "fam-dieb"),  # neuer Signaturschluessel
        (RSA_X, tel_ecdsa, None),  # Token ohne Familie
    )
    for rsa, ecdsa, familie in versuche:
        try:
            e2ee_device_service.veroeffentlichen(db, user, "altgeraet-01", rsa, "Dieb", ecdsa, familie=familie)
        except e2ee_device_service.FremdeSitzungError:
            pass
        else:
            raise AssertionError(f"Wechsel ohne bekannte Familie durchgelassen: {familie}")
        db.expire_all()
        alt = e2ee_device_service._geraet(db, user.id, "altgeraet-01")
        assert alt.public_key_jwk == RSA_A and alt.signing_public_key_jwk == tel_ecdsa
        assert alt.auth_family is None and alt.is_approved is True

    # Das echte Geraet meldet sich mit seinen Schluesseln und bindet seine Familie.
    echt = e2ee_device_service.veroeffentlichen(db, user, "altgeraet-01", RSA_A, "Telefon", tel_ecdsa, familie="fam-echt")
    assert echt.auth_family == "fam-echt" and echt.is_approved is True

    # Gebundene Familie, Anfrage ohne Familie: ebenfalls kein Wechsel.
    try:
        e2ee_device_service.veroeffentlichen(db, user, "altgeraet-01", RSA_X, "Dieb", tel_ecdsa, familie=None)
    except e2ee_device_service.FremdeSitzungError:
        pass
    else:
        raise AssertionError("Wechsel aus einem Token ohne Familie durchgelassen")


def test_freigegebenes_geraet_entfernt_nur_ein_freigegebenes(db: Session, clean_db):
    user = _konto(db, "freigabe6")
    tel_priv, _, lap_priv, _ = _zwei_geraete(db, user)

    # Das wartende Geraet (der Dieb) kann das Telefon nicht entfernen —
    # sonst waere es danach das erste und damit frei.
    try:
        e2ee_device_service.vergessen(
            db, user, "telefon-0001", "laptop-00002", _entfernen(lap_priv, user.id, "telefon-0001", RSA_A)
        )
    except e2ee_device_service.GeraeteaenderungAbgelehntError:
        pass
    else:
        raise AssertionError("wartendes Geraet durfte ein freigegebenes entfernen")
    try:
        e2ee_device_service.vergessen(db, user, "telefon-0001")
    except e2ee_device_service.GeraeteaenderungAbgelehntError:
        pass
    else:
        raise AssertionError("Entfernen ohne Unterschrift ging durch")

    # Das wartende Geraet selbst darf jeder Teil des Kontos entfernen.
    assert e2ee_device_service.vergessen(db, user, "laptop-00002") is True

    # Das eigene Geraet entfernt sich nicht selbst — auch nicht mit eigener
    # Unterschrift. Bis 10/2026 ging genau das durch: ein Geraet warf sich
    # hinaus und hinterliess ein Verzeichnis, in dem es fehlte.
    try:
        e2ee_device_service.vergessen(
            db, user, "telefon-0001", "telefon-0001", _entfernen(tel_priv, user.id, "telefon-0001", RSA_A)
        )
    except e2ee_device_service.GeraeteaenderungAbgelehntError:
        pass
    else:
        raise AssertionError("Geraet durfte sich selbst entfernen")
    assert [g["device_id"] for g in e2ee_device_service.geraete(db, user.id)] == ["telefon-0001"]


def _sitzung(db: Session, user: User):
    tokens = issue_session(Response(), db, user)
    familie = AuthService.decode_token(tokens.access_token)["familie"]
    return {"Authorization": f"Bearer {tokens.access_token}"}, familie


def test_entfernen_sperrt_das_geraet_sofort(client: TestClient, db: Session, clean_db):
    """Das Access-Token des entfernten Geraets faellt beim naechsten Aufruf, nicht nach 15 Minuten."""
    user = _konto(db, "freigabe7")
    user.password_hash = AuthService.hash_password("richtig-genug-7")
    db.commit()
    tel_kopf, _ = _sitzung(db, user)
    lap_kopf, _ = _sitzung(db, user)
    tel_priv, tel_ecdsa = _ecdsa_paar()
    lap_priv, lap_ecdsa = _ecdsa_paar()

    assert client.put("/api/social/e2ee/devices/self", headers=tel_kopf, json={
        "device_id": "telefon-0001", "public_key": RSA_A, "signing_public_key": tel_ecdsa,
    }).json()["is_approved"] is True
    assert client.put("/api/social/e2ee/devices/self", headers=lap_kopf, json={
        "device_id": "laptop-00002", "public_key": RSA_B, "signing_public_key": lap_ecdsa,
    }).json()["is_approved"] is False
    assert client.post("/api/social/e2ee/devices/self/approve", headers=tel_kopf, json={
        "device_id": "laptop-00002", "approver_device_id": "telefon-0001",
        "signature": _freigabe(tel_priv, user.id, "laptop-00002", RSA_B, lap_ecdsa),
    }).status_code == 200
    assert client.get(f"/api/social/e2ee/devices/{user.id}", headers=lap_kopf).status_code == 200

    # Ohne Unterschrift: 403, der Laptop bleibt.
    assert client.post("/api/social/e2ee/devices/self/remove", headers=tel_kopf, json={
        "device_id": "laptop-00002", "password": "richtig-genug-7",
    }).status_code == 403

    # Ohne Nachweis ebenfalls: die Unterschrift allein oeffnet den Weg nicht.
    assert client.post("/api/social/e2ee/devices/self/remove", headers=tel_kopf, json={
        "device_id": "laptop-00002", "approver_device_id": "telefon-0001",
        "signature": _entfernen(tel_priv, user.id, "laptop-00002", RSA_B),
    }).status_code == 403

    assert client.post("/api/social/e2ee/devices/self/remove", headers=tel_kopf, json={
        "device_id": "laptop-00002", "approver_device_id": "telefon-0001",
        "signature": _entfernen(tel_priv, user.id, "laptop-00002", RSA_B),
        "password": "richtig-genug-7",
    }).json() == {"ok": True}

    assert client.get(f"/api/social/e2ee/devices/{user.id}", headers=lap_kopf).status_code == 401
    geraete = client.get(f"/api/social/e2ee/devices/{user.id}", headers=tel_kopf)
    assert geraete.status_code == 200
    assert [g["device_id"] for g in geraete.json()] == ["telefon-0001"]


def test_dritte_sehen_nur_freigegebene(client: TestClient, db: Session, clean_db):
    alice = _konto(db, "alice_fg")
    bob = _konto(db, "bob_fg")
    _zwei_geraete(db, alice)
    app.dependency_overrides[verify_csrf] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: bob
    for pfad in (f"/api/social/e2ee/devices/{alice.id}", f"/api/social/e2ee/devices/{alice.id}?include_unapproved=true"):
        assert [g["device_id"] for g in client.get(pfad).json()] == ["telefon-0001"]
    app.dependency_overrides[get_current_user] = lambda: alice
    assert len(client.get(f"/api/social/e2ee/devices/{alice.id}?include_unapproved=true").json()) == 2


def test_neustart_nur_mit_passwort_und_sperrt_die_anderen(client: TestClient, db: Session, clean_db):
    user = _konto(db, "freigabe8")
    user.password_hash = AuthService.hash_password("richtig-genug-1")
    db.commit()
    alt_kopf, alt_familie = _sitzung(db, user)
    neu_kopf, neu_familie = _sitzung(db, user)
    _, alt_ecdsa = _ecdsa_paar()
    e2ee_device_service.veroeffentlichen(db, user, "altgeraet-01", RSA_A, "Alt", alt_ecdsa, familie=alt_familie)

    assert client.post("/api/social/e2ee/devices/self/reset", headers=neu_kopf, json={"password": "falsch"}).status_code == 403
    assert e2ee_device_service.geraete(db, user.id)

    antwort = client.post("/api/social/e2ee/devices/self/reset", headers=neu_kopf, json={"password": "richtig-genug-1"})
    assert antwort.status_code == 200 and antwort.json()["removed"] == 1
    assert e2ee_device_service.geraete(db, user.id) == []
    assert client.get(f"/api/social/e2ee/devices/{user.id}", headers=alt_kopf).status_code == 401

    # Das naechste Geraet ist wieder das erste.
    _, neu_ecdsa = _ecdsa_paar()
    assert client.put("/api/social/e2ee/devices/self", headers=neu_kopf, json={
        "device_id": "neugeraet-02", "public_key": RSA_B, "signing_public_key": neu_ecdsa,
    }).json()["is_approved"] is True


def test_neustart_social_login_mit_reset_bestaetigung(client: TestClient, db: Session, clean_db):
    from models import OAuthProvider, OAuthUserLink
    user = _konto(db, "social_user")
    user.has_password = False  # Social-Login-Konto: nie ein eigenes Passwort gesetzt
    db.commit()
    provider = OAuthProvider(
        name="Google",
        slug="google",
        preset="google",
        client_id="cid",
        client_secret_encrypted="sec",
        enabled=True,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)

    link = OAuthUserLink(
        provider_id=provider.id,
        user_id=user.id,
        subject=OAuthUserLink._hash_subject("sub-12345"),
    )
    db.add(link)
    db.commit()

    neu_kopf, neu_familie = _sitzung(db, user)
    _, ecdsa = _ecdsa_paar()
    e2ee_device_service.veroeffentlichen(db, user, "alt-dev-1", RSA_A, "Alt", ecdsa, familie=neu_familie)

    # Ohne Bestätigungswort -> 400
    res = client.post("/api/social/e2ee/devices/self/reset", headers=neu_kopf, json={})
    assert res.status_code == 400

    # Falsches Bestätigungswort -> 400
    res = client.post("/api/social/e2ee/devices/self/reset", headers=neu_kopf, json={"confirmation": "NEIN"})
    assert res.status_code == 400

    # Gültiges RESET -> 200
    res = client.post("/api/social/e2ee/devices/self/reset", headers=neu_kopf, json={"confirmation": "RESET"})
    assert res.status_code == 200
    assert res.json()["removed"] == 1
    assert e2ee_device_service.geraete(db, user.id) == []


def test_neustart_mit_2fa_nur_mit_dem_eingerichteten_faktor(client: TestClient, db: Session, clean_db):
    from services.dis_client import DisClient
    user = _konto(db, "twofa_user")
    user.two_factor_enabled = True
    secret = DisClient.generate_totp_secret()
    user.two_factor_secret_encrypted = AuthService.encrypt_secret(secret, aad=f"msm:user:{user.id}:2fa")
    db.commit()

    neu_kopf, neu_familie = _sitzung(db, user)
    _, ecdsa = _ecdsa_paar()
    e2ee_device_service.veroeffentlichen(db, user, "alt-dev-2fa", RSA_A, "Alt", ecdsa, familie=neu_familie)

    # Ohne 2FA-Nachweis -> 403
    res = client.post("/api/social/e2ee/devices/self/reset", headers=neu_kopf, json={})
    assert res.status_code == 403

    # Falscher OTP -> 403
    res = client.post("/api/social/e2ee/devices/self/reset", headers=neu_kopf, json={"otp_code": "000000"})
    assert res.status_code == 403

    # Das alte Feld `passkey_verified` oeffnete bis 09/2026 jedes Konto -> 403
    res = client.post("/api/social/e2ee/devices/self/reset", headers=neu_kopf, json={"passkey_verified": True})
    assert res.status_code == 403

    # Aktueller Code des eingerichteten Faktors -> 200
    from tests._totp import totp_now
    res = client.post("/api/social/e2ee/devices/self/reset", headers=neu_kopf, json={"otp_code": totp_now(secret)})
    assert res.status_code == 200
    assert res.json()["removed"] == 1
    assert e2ee_device_service.geraete(db, user.id) == []


def test_verzeichnis_meldet_jede_aenderung_in_echtzeit(db: Session, clean_db, monkeypatch):
    """Melden, Freigeben, Entfernen: jede offene Geraeteliste des Kontos erfaehrt es.

    Bis 5.0.3 lud die Liste im Profil nur beim Oeffnen. Ein frisch gekoppeltes
    Geraet erschien erst nach dem Neuladen der Seite zum Freigeben.
    """
    from services.sync_event_service import SyncEventService

    gemeldet: list[tuple[dict, int | None]] = []
    monkeypatch.setattr(
        SyncEventService, "publish", lambda ereignis, **kw: gemeldet.append((ereignis, kw.get("user_id")))
    )
    user = _konto(db, "echtzeit1")
    tel_priv, _, _, lap_ecdsa = _zwei_geraete(db, user)
    assert [(e["entity"], u) for e, u in gemeldet] == [("e2ee_devices", user.id)] * 2

    # Ein blosses Wiedermelden (jeder Start) ist keine Aenderung.
    gemeldet.clear()
    e2ee_device_service.veroeffentlichen(db, user, "laptop-00002", RSA_B, "", lap_ecdsa, familie="fam-lap")
    assert gemeldet == []

    sig = _freigabe(tel_priv, user.id, "laptop-00002", RSA_B, lap_ecdsa)
    assert e2ee_device_service.bestaetigen(db, user, "laptop-00002", "telefon-0001", sig)
    assert [e["entity"] for e, _ in gemeldet] == ["e2ee_devices"]

    gemeldet.clear()
    weg = _entfernen(tel_priv, user.id, "laptop-00002", RSA_B)
    assert e2ee_device_service.vergessen(db, user, "laptop-00002", "telefon-0001", weg)
    assert [e["entity"] for e, _ in gemeldet] == ["e2ee_devices"]

    gemeldet.clear()
    e2ee_device_service.zuruecksetzen(db, user, None)
    assert [e["entity"] for e, _ in gemeldet] == ["e2ee_devices"]


def test_gekoppeltes_geraet_ohne_namen_heisst_wie_die_einladung(db: Session, clean_db):
    """Ohne eigenen Namen stand in der Liste „Unbenanntes Geraet".

    Die Einladung im Panel hat einen Namen, und ihre Sitzungskette ist die, mit
    der sich das Geraet meldet.
    """
    from datetime import datetime, timedelta, timezone

    from models.device_pairing import DevicePairing

    user = _konto(db, "benennung1")
    jetzt = datetime.now(timezone.utc)
    db.add(DevicePairing(
        user_id=user.id, code_hash="h" * 64, label="Arbeitsrechner", family="fam-mss",
        expires_at=jetzt + timedelta(minutes=10), redeemed_at=jetzt,
    ))
    db.commit()
    _, ecdsa = _ecdsa_paar()
    neu = e2ee_device_service.veroeffentlichen(db, user, "mss-geraet-01", RSA_A, "", ecdsa, familie="fam-mss")
    assert neu.label == "Arbeitsrechner"

    # Ein selbst gewaehlter Name bleibt; ein Browser ohne Einladung heisst
    # nicht „unbenannt", sondern wie das Produkt (`STANDARD_BEZEICHNUNG`).
    _, ecdsa2 = _ecdsa_paar()
    eigen = e2ee_device_service.veroeffentlichen(db, user, "mss-geraet-02", RSA_B, "Laptop", ecdsa2, familie="fam-mss")
    assert eigen.label == "Laptop"
    _, ecdsa3 = _ecdsa_paar()
    browser = e2ee_device_service.veroeffentlichen(db, user, "browser-0003", RSA_X, "", ecdsa3, familie="fam-web")
    assert browser.label == e2ee_device_service.STANDARD_BEZEICHNUNG == "Web-Frontend"
    assert e2ee_device_service.eintrag_als_dict(browser)["label"] == "Web-Frontend"


def test_wiedergemeldetes_geraet_ohne_namen_bekommt_den_der_einladung(db: Session, clean_db):
    """Bestand von vor 5.0.4: der Eintrag hat keinen Namen, die Einladung schon."""
    from datetime import datetime, timedelta, timezone

    from models.device_pairing import DevicePairing

    user = _konto(db, "benennung2")
    _, ecdsa = _ecdsa_paar()
    e2ee_device_service.veroeffentlichen(db, user, "mss-geraet-01", RSA_A, "", ecdsa, familie="fam-mss")
    jetzt = datetime.now(timezone.utc)
    db.add(DevicePairing(
        user_id=user.id, code_hash="k" * 64, label="Arbeitsrechner", family="fam-mss",
        expires_at=jetzt + timedelta(minutes=10), redeemed_at=jetzt,
    ))
    db.commit()
    wieder = e2ee_device_service.veroeffentlichen(db, user, "mss-geraet-01", RSA_A, "", ecdsa, familie="fam-mss")
    assert wieder.label == "Arbeitsrechner"


def test_schluesselwechsel_beim_einzigen_geraet_laesst_es_warten(db: Session, clean_db):
    """Der Widerspruch aus dem Fehlerbericht: „freigegeben" ohne Unterschrift.

    Bis 10/2026 blieb `is_approved` beim einzigen Geraet stehen, waehrend
    `approved_by` und `approval_signature` geloescht wurden. Die Liste zeigte
    „freigegeben", die Signaturkette fand nie wieder eine gueltige Freigabe,
    und jedes Gegenueber warnte: „es bekommt nichts".
    """
    user = _konto(db, "freigabe9")
    _, ecdsa = _ecdsa_paar()
    erst = e2ee_device_service.veroeffentlichen(
        db, user, "einziges-0001", RSA_A, "Telefon", ecdsa, familie="fam-eins"
    )
    assert erst.is_approved is True

    # Neuer Primaerschluessel, dieselbe Sitzung: das Geraet wartet jetzt —
    # und der Zustand sagt die Wahrheit ueber die fehlende Unterschrift.
    neu = e2ee_device_service.veroeffentlichen(
        db, user, "einziges-0001", RSA_X, "Telefon", ecdsa, familie="fam-eins"
    )
    assert neu.is_approved is False
    assert neu.approved_by is None and neu.approval_signature is None
    assert [g["device_id"] for g in e2ee_device_service.ausstehende_geraete(db, user)] == [
        "einziges-0001"
    ]
    assert e2ee_device_service.geraete(db, user.id, nur_bestaetigt=True) == []


def test_nachgereichter_signaturschluessel_ist_ein_wechsel(db: Session, clean_db):
    """Auch der erste Signaturschluessel nimmt die Freigabe.

    Ein Altbestand ohne Signaturschluessel kam einmal ohne Freigabe davon. Das
    war der bequeme Weg, sich Freigaberecht zu verschaffen: wer das Token
    dieses Geraets hat, legt darunter seinen eigenen Signaturschluessel ab,
    bleibt freigegeben — und unterschreibt damit Freigaben fuer weitere
    Geraete. Ein abgegriffenes Token wurde so zur Freigabeinstanz.

    Der Preis ist eine Nachbestaetigung: `wieder_freigeben` mit frischem
    Nachweis, oder die Unterschrift eines freigegebenen Geraets.
    """
    user = _konto(db, "freigabe10")
    e2ee_device_service.veroeffentlichen(db, user, "altgeraet-01", RSA_A, "Telefon", "", familie="fam-alt")
    _, ecdsa = _ecdsa_paar()

    nach = e2ee_device_service.veroeffentlichen(
        db, user, "altgeraet-01", RSA_A, "Telefon", ecdsa, familie="fam-alt"
    )
    assert nach.is_approved is False
    assert nach.signing_public_key_jwk == ecdsa
    assert nach.approved_by is None and nach.approval_signature is None
    # Und der Weg zurueck steht offen, weil es das einzige Geraet ist.
    assert e2ee_device_service.wieder_freigeben(db, user, "altgeraet-01") is True
    assert e2ee_device_service.geraete(db, user.id, nur_bestaetigt=True)[0]["device_id"] == "altgeraet-01"


def test_wieder_freigeben_nur_ohne_anderes_freigegebenes_geraet(db: Session, clean_db):
    """Sonst waere es ein Weg an der Unterschrift vorbei."""
    user = _konto(db, "freigabe11")
    _zwei_geraete(db, user)
    try:
        e2ee_device_service.wieder_freigeben(db, user, "laptop-00002")
    except e2ee_device_service.GeraeteaenderungAbgelehntError:
        pass
    else:
        raise AssertionError("Freigabe am freigegebenen Geraet vorbei")

    lap = next(g for g in e2ee_device_service.ausstehende_geraete(db, user))
    assert lap["device_id"] == "laptop-00002"


def test_wieder_freigeben_verlangt_frischen_nachweis(client: TestClient, db: Session, clean_db):
    """Nur das Access-Token reicht nicht — sonst haette jeder Token wieder Zutritt."""
    user = _konto(db, "freigabe12")
    user.password_hash = AuthService.hash_password("richtig-genug-2")
    db.commit()
    kopf, familie = _sitzung(db, user)
    _, ecdsa = _ecdsa_paar()
    e2ee_device_service.veroeffentlichen(db, user, "einziges-0001", RSA_A, "Telefon", ecdsa, familie=familie)
    # Schluesselwechsel: das Geraet wartet.
    e2ee_device_service.veroeffentlichen(db, user, "einziges-0001", RSA_X, "Telefon", ecdsa, familie=familie)
    assert e2ee_device_service.geraete(db, user.id, nur_bestaetigt=True) == []

    pfad = "/api/social/e2ee/devices/self/reapprove"
    assert client.post(pfad, headers=kopf, json={"device_id": "einziges-0001"}).status_code == 403
    assert client.post(
        pfad, headers=kopf, json={"device_id": "einziges-0001", "password": "falsch"}
    ).status_code == 403
    assert e2ee_device_service.geraete(db, user.id, nur_bestaetigt=True) == []

    antwort = client.post(
        pfad, headers=kopf, json={"device_id": "einziges-0001", "password": "richtig-genug-2"}
    )
    assert antwort.status_code == 200
    eintrag = next(g for g in e2ee_device_service.geraete(db, user.id, nur_bestaetigt=True))
    assert eintrag["device_id"] == "einziges-0001"
    # Freigegeben heisst nicht beglaubigt: niemand hat den Schluessel unterschrieben.
    assert eintrag["approved_by"] == "" and eintrag["approval_signature"] == ""


def test_zwei_wartende_geraete_geben_sich_nicht_gegenseitig_frei(clean_db):
    """Gleichzeitige Selbstfreigaben: genau eine kommt durch.

    Ohne Sperre lesen beide „kein freigegebenes Geraet" und schreiben beide
    `is_approved = true` — die Unterschrift des ersten waere umgangen, und das
    Konto haette zwei Geraete, von denen keines die andere bestaetigt hat.

    Zwei eigene Verbindungen, Start an einer Barriere: auf der geteilten
    Verbindung der Suite gaebe es keinen Nebenlauf (AGENTS.md 43).
    """
    import threading

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    import database as db_module
    from models import UserE2eeDevice

    aufbau = db_module.SessionLocal()
    try:
        user = _konto(aufbau, "freigabe14")
        _, ecdsa_a = _ecdsa_paar()
        _, ecdsa_b = _ecdsa_paar()
        # Beide warten: keine Unterschrift, keine Freigabe.
        e2ee_device_service.veroeffentlichen(
            aufbau, user, "geraet-00001", RSA_A, "Telefon", ecdsa_a, familie="fam-a"
        )
        e2ee_device_service.veroeffentlichen(
            aufbau, user, "geraet-00002", RSA_B, "Laptop", ecdsa_b, familie="fam-b"
        )
        aufbau.query(UserE2eeDevice).filter(UserE2eeDevice.user_id == user.id).update(
            {"is_approved": False, "approved_by": None, "approval_signature": None}
        )
        aufbau.commit()
        konto_id = user.id
    finally:
        aufbau.close()

    engine = create_engine(db_module.engine.url, poolclass=NullPool)
    sitzung = sessionmaker(bind=engine)
    barriere = threading.Barrier(2)
    ergebnis: dict[str, object] = {}
    fehler: list[BaseException] = []

    def lauf(kennung: str) -> None:
        s = sitzung()
        try:
            konto = s.get(User, konto_id)
            try:
                barriere.wait(timeout=5)
            except threading.BrokenBarrierError:
                pass
            ergebnis[kennung] = e2ee_device_service.wieder_freigeben(s, konto, kennung)
        except e2ee_device_service.GeraeteaenderungAbgelehntError:
            ergebnis[kennung] = "abgelehnt"
        except BaseException as e:  # noqa: BLE001 - im Test sichtbar machen
            fehler.append(e)
        finally:
            s.close()

    threads = [threading.Thread(target=lauf, args=(k,)) for k in ("geraet-00001", "geraet-00002")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    engine.dispose()

    assert not fehler, fehler
    assert sorted(str(v) for v in ergebnis.values()) == ["True", "abgelehnt"]

    kontrolle = db_module.SessionLocal()
    try:
        freigegeben = (
            kontrolle.query(UserE2eeDevice)
            .filter(UserE2eeDevice.user_id == konto_id, UserE2eeDevice.is_approved.is_(True))
            .count()
        )
    finally:
        kontrolle.close()
    assert freigegeben == 1


def test_abgelehnte_freigabe_verbraucht_keinen_code(client: TestClient, db: Session, clean_db):
    """Der Nachweis gilt genau einmal — er darf nicht an einer 403 verfallen.

    Stand die Pruefung „es gibt noch ein freigegebenes Geraet" hinter dem
    Nachweis, verbrannte ein gueltiger Code fuer eine Antwort, die ohnehin 403
    lautet. Danach war derselbe Code nach dem Entfernen des anderen Geraets
    nicht mehr zu gebrauchen.
    """
    from services.dis_client import DisClient
    from tests._totp import totp_now

    user = _konto(db, "freigabe15")
    user.two_factor_enabled = True
    secret = DisClient.generate_totp_secret()
    user.two_factor_secret_encrypted = AuthService.encrypt_secret(secret, aad=f"msm:user:{user.id}:2fa")
    db.commit()
    kopf, _ = _sitzung(db, user)
    _, ecdsa = _ecdsa_paar()
    # Eigene Familien: das Entfernen des ersten Geraets sperrt dessen Sitzung,
    # nicht die des Testclients.
    e2ee_device_service.veroeffentlichen(db, user, "erstes-0001", RSA_A, "Telefon", ecdsa, familie="fam-erstes")
    e2ee_device_service.veroeffentlichen(db, user, "zweites-0002", RSA_B, "Laptop", ecdsa, familie="fam-zweites")

    pdaf = "/api/social/e2ee/devices/self/reapprove"
    code = totp_now(secret)
    assert client.post(
        pdaf, headers=kopf, json={"device_id": "zweites-0002", "otp_code": code}
    ).status_code == 403

    # Erstes Geraet weg: jetzt ist das zweite das einzige und darf zurueck.
    zusammen = e2ee_device_service.veroeffentlichen(
        db, user, "erstes-0001", RSA_X, "Telefon", ecdsa, familie="fam-erstes"
    )
    assert zusammen.is_approved is False
    assert e2ee_device_service.vergessen(db, user, "erstes-0001") is True

    antwort = client.post(pdaf, headers=kopf, json={"device_id": "zweites-0002", "otp_code": code})
    assert antwort.status_code == 200, antwort.text


def test_wieder_freigeben_mit_zweitem_faktor(client: TestClient, db: Session, clean_db):
    """Bei 2FA zaehlt nur der eingerichtete Faktor, nie das Passwort allein."""
    from services.dis_client import DisClient
    from tests._totp import totp_now

    user = _konto(db, "freigabe13")
    user.password_hash = AuthService.hash_password("richtig-genug-3")
    user.two_factor_enabled = True
    secret = DisClient.generate_totp_secret()
    user.two_factor_secret_encrypted = AuthService.encrypt_secret(secret, aad=f"msm:user:{user.id}:2fa")
    db.commit()
    kopf, familie = _sitzung(db, user)
    _, ecdsa = _ecdsa_paar()
    e2ee_device_service.veroeffentlichen(db, user, "einziges-0001", RSA_A, "Telefon", ecdsa, familie=familie)
    e2ee_device_service.veroeffentlichen(db, user, "einziges-0001", RSA_X, "Telefon", ecdsa, familie=familie)

    pfad = "/api/social/e2ee/devices/self/reapprove"
    assert client.post(pfad, headers=kopf, json={"device_id": "einziges-0001"}).status_code == 403
    # Das Passwort allein oeffnet ein 2FA-Konto nicht.
    assert client.post(
        pfad, headers=kopf, json={"device_id": "einziges-0001", "password": "richtig-genug-3"}
    ).status_code == 403
    assert client.post(
        pfad, headers=kopf, json={"device_id": "einziges-0001", "otp_code": "000000"}
    ).status_code == 403
    assert client.post(
        pfad, headers=kopf, json={"device_id": "einziges-0001", "otp_code": totp_now(secret)}
    ).status_code == 200
    assert len(e2ee_device_service.geraete(db, user.id, nur_bestaetigt=True)) == 1

def test_bestandsgeraet_ohne_namen_wird_als_web_frontend_ausgeliefert(db: Session, clean_db):
    """Ein Eintrag von vor 10/2026 stand auf jedem anderen Geraet als „Unbenanntes Geraet".

    Der Client von damals meldete ohne Label, und `_auffrischen` ueberschreibt
    ein leeres Feld nie — es blieb fuer immer leer. Gelesen wird es jetzt als
    Standardname, ohne dass die Zeile angefasst werden muss.
    """
    from models import UserE2eeDevice

    user = _konto(db, "bestandslabel")
    _, ecdsa = _ecdsa_paar()
    eintrag = e2ee_device_service.veroeffentlichen(db, user, "altbestand-01", RSA_A, "", ecdsa)
    # Zustand von vor dem Fix nachstellen: Spalte leer.
    db.query(UserE2eeDevice).filter(UserE2eeDevice.id == eintrag.id).update(
        {UserE2eeDevice.label: ""}, synchronize_session=False
    )
    db.commit()

    liste = e2ee_device_service.geraete(db, user.id)
    assert liste[0]["label"] == e2ee_device_service.STANDARD_BEZEICHNUNG
    assert e2ee_device_service.ausstehende_geraete(db, user) == []

    # Und die Anzeige eint ein Browser, der sich ohne Namen meldet.
    _, ecdsa2 = _ecdsa_paar()
    frisch = e2ee_device_service.veroeffentlichen(db, user, "browser-0002", RSA_B, "", ecdsa2)
    assert frisch.label == "Web-Frontend"


def test_erstes_geraet_beglaubigt_sich_selbst(db: Session, clean_db):
    """Fall A: das erste Geraet gibt sich frei — mit Unterschrift, nicht nur mit einem Wort.

    Bis 10/2026 blieb `approval_signature` leer. `is_approved` sagte „ja", die
    Signaturkette fand nichts, und jedes andere Geraet zeigte „Ohne gueltige
    Freigabe" neben einem Eintrag, den der Server freigegeben nannte.
    """
    user = _konto(db, "selbstsig1")
    priv, ecdsa = _ecdsa_paar()
    signatur = _freigabe(priv, user.id, "erstgeraet-1", RSA_A, ecdsa)

    erst = e2ee_device_service.veroeffentlichen(
        db, user, "erstgeraet-1", RSA_A, "Web-Frontend", ecdsa, self_signature=signatur
    )
    assert erst.is_approved is True
    assert erst.approved_by == "erstgeraet-1"
    assert erst.approval_signature == signatur
    assert e2ee_device_service.eintrag_als_dict(erst)["approval_signature"] == signatur

    # Eine Unterschrift ueber andere Schluessel bleibt draussen.
    fremd_priv, _ = _ecdsa_paar()
    falsch = _freigabe(fremd_priv, user.id, "erstgeraet-1", RSA_A, ecdsa)
    wieder = e2ee_device_service.veroeffentlichen(
        db, user, "erstgeraet-1", RSA_A, "", ecdsa, self_signature=falsch
    )
    assert wieder.approval_signature == signatur


def test_freigabe_wird_fuer_ein_freigegebenes_geraet_nachgezogen(db: Session, clean_db):
    """Ein freigegebenes Geraet ohne Unterschrift laesst sich von einem anderen beglaubigen.

    Der Fall aus dem Fehlerbericht: auf dem Telefon stand das Web-Frontend als
    freigegeben, aber „ohne gueltige Freigabe" — der Knopf daneben fehlte, und
    `bestaetigen` haette ohnehin nichts getan (`is_approved` war ja wahr).
    """
    from models import UserE2eeDevice

    user = _konto(db, "nachziehen1")
    tel_priv, _, _, lap_ecdsa = _zwei_geraete(db, user)
    assert e2ee_device_service.bestaetigen(
        db,
        user,
        "laptop-00002",
        "telefon-0001",
        _freigabe(tel_priv, user.id, "laptop-00002", RSA_B, lap_ecdsa),
    )

    # Zustand „freigegeben, aber ohne Unterschrift" — so kommt es aus
    # `wieder_freigeben` fuer ein Geraet ohne andere freigegebene.
    db.query(UserE2eeDevice).filter(UserE2eeDevice.device_id == "laptop-00002").update(
        {UserE2eeDevice.approved_by: None, UserE2eeDevice.approval_signature: None},
        synchronize_session=False,
    )
    db.commit()

    assert e2ee_device_service.bestaetigen(
        db,
        user,
        "laptop-00002",
        "telefon-0001",
        _freigabe(tel_priv, user.id, "laptop-00002", RSA_B, lap_ecdsa),
    )
    nachgezogen = [g for g in e2ee_device_service.geraete(db, user.id) if g["device_id"] == "laptop-00002"][0]
    assert nachgezogen["is_approved"] is True
    assert nachgezogen["approved_by"] == "telefon-0001"
    assert nachgezogen["approval_signature"]

    # Und eine falsche Unterschrift aendert nichts: das Geraet ist beglaubigt,
    # eine ungueltige Unterschrift daneben ist kein Grund, das zu verwerfen.
    fremd_priv, _ = _ecdsa_paar()
    assert e2ee_device_service.bestaetigen(
        db,
        user,
        "laptop-00002",
        "telefon-0001",
        _freigabe(fremd_priv, user.id, "laptop-00002", RSA_B, lap_ecdsa),
    )
    unveraendert = [g for g in e2ee_device_service.geraete(db, user.id) if g["device_id"] == "laptop-00002"][0]
    assert unveraendert["approval_signature"] == nachgezogen["approval_signature"]


def test_wieder_freigeben_traegt_die_eigene_unterschrift_ein(db: Session, clean_db):
    """Fall C: die Selbstfreigabe traegt die eigene Unterschrift — unbelegt bleibt sie erklaert."""
    user = _konto(db, "selbstsig2")
    priv, ecdsa = _ecdsa_paar()
    e2ee_device_service.veroeffentlichen(db, user, "einziges-0001", RSA_A, "Telefon", ecdsa, familie="fam-1")
    # Schluesselwechsel bei der Sitzung selbst: das Geraet wartet jetzt, und
    # `wieder_freigeben` ist sein Weg zurueck.
    e2ee_device_service.veroeffentlichen(db, user, "einziges-0001", RSA_X, "Telefon", ecdsa, familie="fam-1")

    signatur = _freigabe(priv, user.id, "einziges-0001", RSA_X, ecdsa)
    assert e2ee_device_service.wieder_freigeben(db, user, "einziges-0001", signatur=signatur)
    eintrag = e2ee_device_service.geraete(db, user.id)[0]
    assert eintrag["is_approved"] is True
    assert eintrag["approved_by"] == "einziges-0001"
    assert eintrag["approval_signature"] == signatur

    # Ohne Unterschrift bleibt der Zustand wie vorher: freigegeben, nicht
    # beglaubigt — aeltere Clients kennen den neuen Parameter nicht.
    e2ee_device_service.veroeffentlichen(db, user, "einziges-0001", RSA_A, "Telefon", ecdsa, familie="fam-1")
    assert e2ee_device_service.wieder_freigeben(db, user, "einziges-0001")
    eintrag = e2ee_device_service.geraete(db, user.id)[0]
    assert eintrag["is_approved"] is True
    assert eintrag["approved_by"] == ""
    assert eintrag["approval_signature"] == ""


def test_entfernen_verlangt_den_nachweis_des_kontos(client: TestClient, db: Session, clean_db):
    """Die Kaskade vor dem Entfernen: zweiter Faktor, sonst Passwort, sonst Sitzung."""
    from services.dis_client import DisClient
    from tests._totp import totp_now

    user = _konto(db, "entfernen1")
    user.password_hash = AuthService.hash_password("richtig-genug-8")
    user.two_factor_enabled = True
    secret = DisClient.generate_totp_secret()
    user.two_factor_secret_encrypted = AuthService.encrypt_secret(secret, aad=f"msm:user:{user.id}:2fa")
    db.commit()

    kopf, familie = _sitzung(db, user)
    tel_priv, tel_ecdsa = _ecdsa_paar()
    _, lap_ecdsa = _ecdsa_paar()
    e2ee_device_service.veroeffentlichen(db, user, "telefon-0001", RSA_A, "Telefon", tel_ecdsa, familie=familie)
    _, fremd_familie = _sitzung(db, user)
    e2ee_device_service.veroeffentlichen(db, user, "laptop-00002", RSA_B, "Laptop", lap_ecdsa, familie=fremd_familie)
    # Erst freigeben: nur ein freigegebenes Geraet bekommt Nachrichten — und
    # nur bei ihm haengt der Nachweis davor.
    assert e2ee_device_service.bestaetigen(
        db, user, "laptop-00002", "telefon-0001", _freigabe(tel_priv, user.id, "laptop-00002", RSA_B, lap_ecdsa)
    )

    pfad = "/api/social/e2ee/devices/self/remove"
    rumpf = {
        "device_id": "laptop-00002",
        "approver_device_id": "telefon-0001",
        "signature": _entfernen(tel_priv, user.id, "laptop-00002", RSA_B),
    }

    # Stufe 1/2: das Konto hat 2FA, das Passwort allein oeffnet nicht.
    assert client.post(pfad, headers=kopf, json={**rumpf, "password": "richtig-genug-8"}).status_code == 403
    assert client.post(pfad, headers=kopf, json={**rumpf, "otp_code": "000000"}).status_code == 403
    # Ein erfundenes Feld oeffnet nichts (Punkt 41 der Projektregeln).
    assert client.post(pfad, headers=kopf, json={**rumpf, "passkey_verified": True}).status_code == 403
    assert len(e2ee_device_service.geraete(db, user.id)) == 2

    # Ein falscher Beleg verbraucht den Code nicht: die Unterschrift wird
    # vorher geprueft. Sonst waere der gueltige Code fuer eine 403 weg.
    code = totp_now(secret)
    falsch = {**rumpf, "signature": _entfernen(tel_priv, user.id, "drittes-0003", RSA_X), "otp_code": code}
    assert client.post(pfad, headers=kopf, json=falsch).status_code == 403

    assert client.post(pfad, headers=kopf, json={**rumpf, "otp_code": code}).status_code == 200
    assert [g["device_id"] for g in e2ee_device_service.geraete(db, user.id)] == ["telefon-0001"]

    # Ein wartendes Geraet braucht keinen Nachweis: es hat nie etwas bekommen,
    # und die Kopplung mit falscher Sicherheitsnummer muss sofort abbrechbar
    # bleiben (`AiDevicePairingCard`).
    _, drittes_ecdsa = _ecdsa_paar()
    e2ee_device_service.veroeffentlichen(db, user, "drittes-0003", RSA_X, "Drittes", drittes_ecdsa)
    assert client.post(
        pfad, headers=kopf, json={"device_id": "drittes-0003"}
    ).status_code == 200
    assert [g["device_id"] for g in e2ee_device_service.geraete(db, user.id)] == ["telefon-0001"]


def test_entfernen_ohne_passwort_und_ohne_faktor_kommt_mit_der_sitzung_aus(
    client: TestClient, db: Session, clean_db
):
    """Stufe 4: ein Social-Login-Konto ohne Passwort und ohne zweiten Faktor.

    Mehr hat dieses Konto nicht herzugeben. Ein Feld zu verlangen, das es nicht
    gibt, waere eine Sackgasse — und der Nutzer koennte ein fremdes Geraet nie
    entfernen.
    """
    user = _konto(db, "entfernen2")
    user.has_password = False
    user.password_hash = ""
    db.commit()

    kopf, familie = _sitzung(db, user)
    tel_priv, tel_ecdsa = _ecdsa_paar()
    _, lap_ecdsa = _ecdsa_paar()
    e2ee_device_service.veroeffentlichen(db, user, "telefon-0001", RSA_A, "Telefon", tel_ecdsa, familie=familie)
    _, fremd_familie = _sitzung(db, user)
    e2ee_device_service.veroeffentlichen(db, user, "laptop-00002", RSA_B, "Laptop", lap_ecdsa, familie=fremd_familie)

    antwort = client.post(
        "/api/social/e2ee/devices/self/remove",
        headers=kopf,
        json={
            "device_id": "laptop-00002",
            "approver_device_id": "telefon-0001",
            "signature": _entfernen(tel_priv, user.id, "laptop-00002", RSA_B),
        },
    )
    assert antwort.status_code == 200, antwort.text
    assert [g["device_id"] for g in e2ee_device_service.geraete(db, user.id)] == ["telefon-0001"]


def test_eigenes_geraet_entfernt_sich_nicht(client: TestClient, db: Session, clean_db):
    """Auch ueber die Sitzung erkannt: das eigene Geraet ist kein Ziel.

    Der Nachweis darf dabei nicht verbraucht werden — geprueft wird vorher
    (`entfernen_hindernis`), sonst waere ein Einmalcode fuer eine 403 weg.
    """
    user = _konto(db, "entfernen3")
    user.password_hash = AuthService.hash_password("richtig-genug-9")
    db.commit()
    kopf, familie = _sitzung(db, user)
    _, ecdsa = _ecdsa_paar()
    e2ee_device_service.veroeffentlichen(db, user, "einziges-0001", RSA_A, "Telefon", ecdsa, familie=familie)

    antwort = client.post(
        "/api/social/e2ee/devices/self/remove",
        headers=kopf,
        json={"device_id": "einziges-0001", "password": "richtig-genug-9"},
    )
    assert antwort.status_code == 403
    assert "selbst" in antwort.json()["detail"]
    assert [g["device_id"] for g in e2ee_device_service.geraete(db, user.id)] == ["einziges-0001"]
