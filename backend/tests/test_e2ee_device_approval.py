from __future__ import annotations

import base64
import json
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from dependencies import get_current_user, verify_csrf
from main import app
from models import User, UserE2eeDevice
from services import e2ee_device_service


def _valid_rsa_jwk(marker: str = "A") -> str:
    return json.dumps({
        "kty": "RSA",
        "n": marker * 350,
        "e": "AQAB",
        "alg": "RSA-OAEP-256",
        "use": "enc",
        "key_ops": ["encrypt"],
    })


def _create_real_ecdsa_keypair():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key()
    pub_nums = pub.public_numbers()

    def b64url(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

    jwk = json.dumps({
        "kty": "EC",
        "crv": "P-256",
        "x": b64url(pub_nums.x.to_bytes(32, "big")),
        "y": b64url(pub_nums.y.to_bytes(32, "big")),
        "use": "sig",
        "key_ops": ["verify"],
    })
    return priv, jwk


def _sign_device_approval(priv_key, user_id: int, target_device_id: str) -> str:
    msg = f"msm:device-approval:v1:{user_id}:{target_device_id}".encode("utf-8")
    der = priv_key.sign(msg, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return base64.b64encode(raw).decode("ascii")


DUMMY_RSA_KEY = _valid_rsa_jwk("A")
DUMMY_RSA_KEY_2 = _valid_rsa_jwk("B")


def test_erstes_geraet_wird_automatisch_freigegeben(db: Session, clean_db):
    user = User(username="user1", email="u1@msm.local", password_hash="pw", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    priv1, ecdsa1 = _create_real_ecdsa_keypair()
    _, ecdsa2 = _create_real_ecdsa_keypair()

    dev1 = e2ee_device_service.veroeffentlichen(
        db, user, "device00000001", DUMMY_RSA_KEY, "Erstes Geraet", ecdsa1
    )
    assert dev1.is_approved is True

    # Zweites Geraet registriert: da bereits ein bestaetigtes Geraet existiert,
    # muss dieses freigegeben werden (is_approved=False).
    dev2 = e2ee_device_service.veroeffentlichen(
        db, user, "device00000002", DUMMY_RSA_KEY_2, "Zweites Geraet", ecdsa2
    )
    assert dev2.is_approved is False

    # Fremde sehen nur bestaetigte Geraete
    fremde_sicht = e2ee_device_service.geraete(db, user.id, nur_bestaetigt=True)
    assert len(fremde_sicht) == 1
    assert fremde_sicht[0]["device_id"] == "device00000001"

    # Ausstehende Geraete abrufen
    ausstehend = e2ee_device_service.ausstehende_geraete(db, user)
    assert len(ausstehend) == 1
    assert ausstehend[0]["device_id"] == "device00000002"

    # Versuch ohne approver_device_id schlaegt fehl (Passwort-Schutz)
    assert e2ee_device_service.bestaetigen(db, user, "device00000002") is False

    # Versuch mit falscher Signatur schlaegt fehl
    assert (
        e2ee_device_service.bestaetigen(
            db,
            user,
            "device00000002",
            approver_device_id="device00000001",
            signature=base64.b64encode(b"0" * 64).decode("ascii"),
        )
        is False
    )

    # Zweites Geraet mit gueltiger Signatur des ersten Geraets bestaetigen
    sig = _sign_device_approval(priv1, user.id, "device00000002")
    ok = e2ee_device_service.bestaetigen(
        db, user, "device00000002", approver_device_id="device00000001", signature=sig
    )
    assert ok is True

    # Jetzt sehen auch Fremde beide Geraete
    fremde_sicht_neu = e2ee_device_service.geraete(db, user.id, nur_bestaetigt=True)
    assert len(fremde_sicht_neu) == 2


def test_api_geraetefreigabe_und_sicherheitsabfrage(client: TestClient, db: Session, clean_db):
    alice = User(username="alice", email="alice@msm.local", password_hash="pw", is_active=True)
    bob = User(username="bob", email="bob@msm.local", password_hash="pw", is_active=True)
    db.add_all([alice, bob])
    db.commit()
    db.refresh(alice)
    db.refresh(bob)

    app.dependency_overrides[verify_csrf] = lambda: None

    alice_phone_priv, alice_phone_ecdsa = _create_real_ecdsa_keypair()
    _, laptop_ecdsa = _create_real_ecdsa_keypair()

    # 1. Alice registriert erstes Geraet
    app.dependency_overrides[get_current_user] = lambda: alice
    res1 = client.put("/api/social/e2ee/devices/self", json={
        "device_id": "alice-phone01",
        "public_key": DUMMY_RSA_KEY,
        "signing_public_key": alice_phone_ecdsa,
        "label": "Alice Phone",
    })
    assert res1.status_code == 200
    assert res1.json()["is_approved"] is True

    # 2. Alice registriert zweites Geraet (z. B. Angreifer mit Passwort)
    res2 = client.put("/api/social/e2ee/devices/self", json={
        "device_id": "alice-laptop02",
        "public_key": DUMMY_RSA_KEY_2,
        "signing_public_key": laptop_ecdsa,
        "label": "Alice Laptop",
    })
    assert res2.status_code == 200
    assert res2.json()["is_approved"] is False

    # 3. Bob fragt Alice' Geraete ab: er darf NUR das bestaetigte Phone sehen!
    app.dependency_overrides[get_current_user] = lambda: bob
    res_bob = client.get(f"/api/social/e2ee/devices/{alice.id}")
    assert res_bob.status_code == 200
    bob_geraete = res_bob.json()
    assert len(bob_geraete) == 1
    assert bob_geraete[0]["device_id"] == "alice-phone01"

    # Bob versucht mit include_unapproved=true Alice' unbestaetigte Geraete zu sehen:
    # Der Server ignoriert das fuer Fremde!
    res_bob_hack = client.get(f"/api/social/e2ee/devices/{alice.id}?include_unapproved=true")
    assert res_bob_hack.status_code == 200
    assert len(res_bob_hack.json()) == 1
    assert res_bob_hack.json()[0]["device_id"] == "alice-phone01"

    # 4. Alice ruft eigene Geraete mit include_unapproved=true ab:
    app.dependency_overrides[get_current_user] = lambda: alice
    res_alice_all = client.get(f"/api/social/e2ee/devices/{alice.id}?include_unapproved=true")
    assert res_alice_all.status_code == 200
    assert len(res_alice_all.json()) == 2

    # 5. Alice ruft /pending ab
    res_pending = client.get("/api/social/e2ee/devices/self/pending")
    assert res_pending.status_code == 200
    assert len(res_pending.json()) == 1
    assert res_pending.json()[0]["device_id"] == "alice-laptop02"

    # 6. Angreifer mit Passwort versucht Freigabe ohne approver_device_id -> HTTP 400
    res_hack_no_approver = client.post("/api/social/e2ee/devices/self/approve", json={
        "device_id": "alice-laptop02"
    })
    assert res_hack_no_approver.status_code == 400

    # Angreifer versucht Freigabe mit gefaelschter Signatur -> HTTP 400
    res_hack_bad_sig = client.post("/api/social/e2ee/devices/self/approve", json={
        "device_id": "alice-laptop02",
        "approver_device_id": "alice-phone01",
        "signature": base64.b64encode(b"1" * 64).decode("ascii"),
    })
    assert res_hack_bad_sig.status_code == 400

    # Legitime Freigabe durch Alice' Phone mit Signatur
    sig = _sign_device_approval(alice_phone_priv, alice.id, "alice-laptop02")
    res_approve = client.post("/api/social/e2ee/devices/self/approve", json={
        "device_id": "alice-laptop02",
        "approver_device_id": "alice-phone01",
        "signature": sig,
    })
    assert res_approve.status_code == 200
    assert res_approve.json()["ok"] is True

    # 7. Bob sieht jetzt beide Geraete
    app.dependency_overrides[get_current_user] = lambda: bob
    res_bob_neu = client.get(f"/api/social/e2ee/devices/{alice.id}")
    assert len(res_bob_neu.json()) == 2

    # 8. Sofortiges Entfernen (Revocation)
    app.dependency_overrides[get_current_user] = lambda: alice
    res_del = client.delete("/api/social/e2ee/devices/self?device_id=alice-laptop02")
    assert res_del.status_code == 200

    # Bob sieht Laptop sofort nicht mehr
    app.dependency_overrides[get_current_user] = lambda: bob
    res_bob_nach_del = client.get(f"/api/social/e2ee/devices/{alice.id}")
    assert len(res_bob_nach_del.json()) == 1
    assert res_bob_nach_del.json()[0]["device_id"] == "alice-phone01"


def test_unbestaetigtes_geraet_kann_sich_nicht_selbst_freigeben(client: TestClient, db: Session, clean_db):
    user = User(username="victim", email="vic@msm.local", password_hash="pw", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    app.dependency_overrides[verify_csrf] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: user

    _, phone_ecdsa = _create_real_ecdsa_keypair()
    rogue_priv, rogue_ecdsa = _create_real_ecdsa_keypair()

    # Erstes Geraet (Phone)
    client.put("/api/social/e2ee/devices/self", json={
        "device_id": "phone-00000001",
        "public_key": DUMMY_RSA_KEY,
        "signing_public_key": phone_ecdsa,
        "label": "Phone",
    })

    # Angreifer registriert zweites Geraet (unbestaetigt)
    client.put("/api/social/e2ee/devices/self", json={
        "device_id": "rogue-00000002",
        "public_key": DUMMY_RSA_KEY_2,
        "signing_public_key": rogue_ecdsa,
        "label": "Rogue",
    })

    # Angreifer versucht sich selbst als approver_device_id einzutragen:
    rogue_sig = _sign_device_approval(rogue_priv, user.id, "rogue-00000002")
    res_self_app = client.post("/api/social/e2ee/devices/self/approve", json={
        "device_id": "rogue-00000002",
        "approver_device_id": "rogue-00000002",
        "signature": rogue_sig,
    })
    assert res_self_app.status_code == 400


def test_schluesselaenderung_auf_bestaetigtem_geraet_entzieht_freigabe_wenn_andere_existieren(db: Session, clean_db):
    user = User(username="user_key_change", email="kc@msm.local", password_hash="pw", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    priv1, ecdsa1 = _create_real_ecdsa_keypair()
    _, ecdsa2 = _create_real_ecdsa_keypair()

    # 2 Geraete freigegeben
    dev1 = e2ee_device_service.veroeffentlichen(db, user, "dev-phone", DUMMY_RSA_KEY, "Phone", ecdsa1)
    dev2 = e2ee_device_service.veroeffentlichen(db, user, "dev-laptop", DUMMY_RSA_KEY_2, "Laptop", ecdsa2)
    sig = _sign_device_approval(priv1, user.id, "dev-laptop")
    assert (
        e2ee_device_service.bestaetigen(
            db, user, "dev-laptop", approver_device_id="dev-phone", signature=sig
        )
        is True
    )

    assert dev1.is_approved is True
    assert dev2.is_approved is True

    # Ein Angreifer mit Passwort versucht dev-phone mit neuem Schluessel zu ueberschreiben:
    dev1_updated = e2ee_device_service.veroeffentlichen(
        db, user, "dev-phone", _valid_rsa_jwk("X"), "Phone Hijacked", ecdsa1
    )
    # Da dev-laptop existiert und bestaetigt ist, verliert dev-phone seine Freigabe!
    assert dev1_updated.is_approved is False
