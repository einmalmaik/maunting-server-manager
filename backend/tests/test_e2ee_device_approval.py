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
    # Und das Telefon sich selbst — mit Unterschrift.
    assert e2ee_device_service.vergessen(
        db, user, "telefon-0001", "telefon-0001", _entfernen(tel_priv, user.id, "telefon-0001", RSA_A)
    ) is True


def _sitzung(db: Session, user: User):
    tokens = issue_session(Response(), db, user)
    familie = AuthService.decode_token(tokens.access_token)["familie"]
    return {"Authorization": f"Bearer {tokens.access_token}"}, familie


def test_entfernen_sperrt_das_geraet_sofort(client: TestClient, db: Session, clean_db):
    """Das Access-Token des entfernten Geraets faellt beim naechsten Aufruf, nicht nach 15 Minuten."""
    user = _konto(db, "freigabe7")
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
        "device_id": "laptop-00002",
    }).status_code == 403

    assert client.post("/api/social/e2ee/devices/self/remove", headers=tel_kopf, json={
        "device_id": "laptop-00002", "approver_device_id": "telefon-0001",
        "signature": _entfernen(tel_priv, user.id, "laptop-00002", RSA_B),
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
