"""Zugang der Kamera-Sicherung ohne offene App (10/2026).

Gepruefte Grenzen: nur mit frischem Nachweis, nur Hochladen und Posteingang
schreiben, faellt mit der Sitzungsfamilie und dem Konto, gilt fuer einen
Bucket, steht in der Datenbank nur als Hash ohne Kontonummer.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from dependencies import get_current_user, get_db, session_familie, verify_csrf
from main import app
from models import RefreshToken
from services import vault_service
from services.auth_service import AuthService
from tests.test_vault_blobs import BUCKET, _schluessel, blob_dir, konten  # noqa: F401  (Fixtures)

FAMILIE = "fam-telefon-1"
PASSWORT = "Kamera-" + "Probe-2026"


def _familie_anlegen(db, user, familie: str = FAMILIE, abgelaufen: bool = False) -> None:
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=secrets.token_hex(32),
            family=familie,
            expires_at=datetime.now(timezone.utc) + (timedelta(days=-1) if abgelaufen else timedelta(days=30)),
        )
    )
    db.commit()


@pytest.fixture
def telefon(db, konten, blob_dir):
    """``telefon.app`` ist die angemeldete App (Sitzung mit Familie), ``telefon.worker`` der Hintergrund ohne Sitzung."""
    eins = konten[0]
    eins.password_hash = AuthService.hash_password(PASSWORT)
    eins.has_password = True
    db.commit()
    _familie_anlegen(db, eins)
    zustand = {"user": eins, "familie": FAMILIE}

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as worker, TestClient(app) as sitzung:

        class Telefon:
            pass

        t = Telefon()
        t.worker = worker
        t.zustand = zustand

        def app_client() -> TestClient:
            app.dependency_overrides[get_current_user] = lambda: zustand["user"]
            app.dependency_overrides[session_familie] = lambda: zustand["familie"]
            app.dependency_overrides[verify_csrf] = lambda: None
            sitzung.headers["X-MSM-Vault-Bucket"] = BUCKET[zustand["user"].id]
            return sitzung

        t.app = app_client

        def zugang(**nachweis) -> str:
            antwort = app_client().post("/api/vault/sicherung/zugang", json=nachweis or {"password": PASSWORT})
            assert antwort.status_code == 201, antwort.text
            return antwort.json()["zugang"]

        t.zugang = zugang
        yield t
    app.dependency_overrides.clear()


def _als_worker(client: TestClient, token: str) -> TestClient:
    # Der Worker hat keine Sitzung: ohne Ersatz fuer get_current_user waere jede Sitzungsroute 401.
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(verify_csrf, None)
    client.headers["X-MSM-Sicherung"] = token
    return client


def _blob_hochladen(client: TestClient) -> str:
    blob_id = secrets.token_hex(16)
    _, verifier = _schluessel()
    groesse = 65_536 + 28
    antwort = client.post(
        "/api/vault/sicherung/blobs",
        json={"id": blob_id, "chunk_count": 1, "bytes_total": groesse, "delete_verifier": verifier},
    )
    assert antwort.status_code == 201, antwort.text
    antwort = client.put(f"/api/vault/sicherung/blobs/{blob_id}/chunks/0", content=b"\x01" * groesse)
    assert antwort.status_code == 204, antwort.text
    assert client.get(f"/api/vault/sicherung/blobs/{blob_id}/status").json()["vorhanden"] == [0]
    assert client.post(f"/api/vault/sicherung/blobs/{blob_id}/fertig").status_code == 200
    return blob_id


def test_nur_mit_frischem_nachweis(telefon):
    assert telefon.app().post("/api/vault/sicherung/zugang", json={}).status_code == 403
    assert telefon.app().post("/api/vault/sicherung/zugang", json={"password": "falsch"}).status_code == 403
    token = telefon.zugang()
    assert token.startswith(f"msz1.1.{FAMILIE}.")


def test_ohne_sitzungsfamilie_kein_zugang(telefon):
    telefon.zustand["familie"] = None
    antwort = telefon.app().post("/api/vault/sicherung/zugang", json={"password": PASSWORT})
    assert antwort.status_code == 403


def test_hochladen_und_posteingang(telefon, db):
    worker = _als_worker(telefon.worker, telefon.zugang())
    _blob_hochladen(worker)
    eingang_id = str(uuid.uuid4())
    assert worker.post("/api/vault/sicherung/eingang", json={"id": eingang_id, "ciphertext": "umschlag"}).status_code == 201
    assert db.execute(text("SELECT bucket_id FROM vault_eingang")).scalar() == BUCKET[1]
    assert worker.delete(f"/api/vault/sicherung/eingang/{eingang_id}").status_code == 204


def test_blob_loeschen_nur_mit_loeschnachweis(telefon):
    worker = _als_worker(telefon.worker, telefon.zugang())
    blob_id = secrets.token_hex(16)
    schluessel, verifier = _schluessel()
    worker.post(
        "/api/vault/sicherung/blobs",
        json={"id": blob_id, "chunk_count": 1, "bytes_total": 65_564, "delete_verifier": verifier},
    )
    assert worker.request("DELETE", f"/api/vault/sicherung/blobs/{blob_id}", json={"schluessel": "0" * 64}).status_code == 403
    assert worker.request("DELETE", f"/api/vault/sicherung/blobs/{blob_id}", json={"schluessel": schluessel}).status_code == 200


@pytest.mark.parametrize(
    "methode, pfad",
    [
        ("GET", "/api/vault/eingang"),
        ("GET", "/api/vault/salt"),
        ("GET", f"/api/vault/blobs/{'c' * 32}/status"),
        ("POST", "/api/vault/blobs/klein"),
        ("GET", "/api/vault/speicher"),
        ("GET", f"/api/vault/blobs/{'c' * 32}/chunks/0"),
        ("POST", "/api/vault/blobs"),
        ("POST", "/api/vault/sicherung/zugang"),
    ],
)
def test_zugang_oeffnet_keine_andere_route(telefon, methode, pfad):
    worker = _als_worker(telefon.worker, telefon.zugang())
    worker.headers["X-MSM-Vault-Bucket"] = BUCKET[1]
    assert worker.request(methode, pfad, json={}).status_code in (401, 403)


def test_faellt_mit_der_sitzungsfamilie(telefon, db):
    worker = _als_worker(telefon.worker, telefon.zugang())
    _blob_hochladen(worker)
    AuthService.revoke_refresh_family(db, 1, FAMILIE)
    antwort = worker.post("/api/vault/sicherung/eingang", json={"id": str(uuid.uuid4()), "ciphertext": "x"})
    assert antwort.status_code == 401
    assert antwort.json()["detail"]["code"] == "VAULT_SICHERUNG_UNGUELTIG"


def test_faellt_wenn_die_sitzung_abgelaufen_ist(telefon, db):
    token = telefon.zugang()
    db.query(RefreshToken).update({RefreshToken.expires_at: datetime.now(timezone.utc) - timedelta(minutes=1)})
    db.commit()
    assert _als_worker(telefon.worker, token).get(f"/api/vault/sicherung/blobs/{'d' * 32}/status").status_code == 401


def test_faellt_mit_dem_konto(telefon, db, konten):
    token = telefon.zugang()
    konten[0].is_active = False
    db.commit()
    assert _als_worker(telefon.worker, token).get(f"/api/vault/sicherung/blobs/{'d' * 32}/status").status_code == 401


def test_ein_neuer_zugang_ersetzt_den_alten(telefon):
    alt = telefon.zugang()
    neu = telefon.zugang()
    worker = _als_worker(telefon.worker, alt)
    assert worker.get(f"/api/vault/sicherung/blobs/{'d' * 32}/status").status_code == 401
    worker.headers["X-MSM-Sicherung"] = neu
    assert worker.get(f"/api/vault/sicherung/blobs/{'d' * 32}/status").status_code == 404


@pytest.mark.parametrize("veraendern", ["konto", "familie", "geheimnis", "leer"])
def test_veraendertes_token_gilt_nicht(telefon, db, konten, veraendern):
    _familie_anlegen(db, konten[1], "fam-zwei")
    praefix, user_id, familie, geheim = telefon.zugang().split(".")
    token = {
        "konto": f"{praefix}.2.{familie}.{geheim}",
        "familie": f"{praefix}.{user_id}.fam-zwei.{geheim}",
        "geheimnis": f"{praefix}.{user_id}.{familie}.{geheim[:-2]}AA",
        "leer": "",
    }[veraendern]
    assert _als_worker(telefon.worker, token).get(f"/api/vault/sicherung/blobs/{'d' * 32}/status").status_code == 401


def test_nach_dem_zuruecksetzen_410_und_der_zugang_ist_weg(telefon, db, konten):
    worker = _als_worker(telefon.worker, telefon.zugang())
    vault_service.tresor_zuruecksetzen(db, vault_service.tresor_konto(konten[0].id))
    db.commit()
    assert db.execute(text("SELECT count(*) FROM vault_sicherungszugaenge")).scalar() == 0
    # Auch ein neuer Tresor desselben Kontos nimmt den alten Zugang nicht an.
    assert worker.get(f"/api/vault/sicherung/blobs/{'d' * 32}/status").status_code == 401


def test_bucket_gewechselt_410(telefon, db, konten):
    worker = _als_worker(telefon.worker, telefon.zugang())
    db.execute(
        text("UPDATE vault_user_settings SET bucket_id = :neu WHERE konto_index = :k"),
        {"neu": "e" * 64, "k": vault_service.tresor_konto(konten[0].id).index},
    )
    db.commit()
    antwort = worker.get(f"/api/vault/sicherung/blobs/{'d' * 32}/status")
    assert antwort.status_code == 410
    assert antwort.json()["detail"]["code"] == "VAULT_ZURUECKGESETZT"


def test_abmelden_der_app_nimmt_den_zugang_mit(telefon, db):
    token = telefon.zugang()
    assert telefon.app().delete("/api/vault/sicherung/zugang").status_code == 204
    assert _als_worker(telefon.worker, token).get(f"/api/vault/sicherung/blobs/{'d' * 32}/status").status_code == 401


def test_in_der_datenbank_nur_hash_und_index(telefon, db):
    token = telefon.zugang()
    zeile = db.execute(text("SELECT * FROM vault_sicherungszugaenge")).mappings().one()
    assert token not in str(dict(zeile))
    assert token.split(".")[3] not in str(dict(zeile))
    assert FAMILIE not in str(dict(zeile))
    pruefer = inspect(db.get_bind())
    assert "user_id" not in {s["name"] for s in pruefer.get_columns("vault_sicherungszugaenge")}
    assert pruefer.get_foreign_keys("vault_sicherungszugaenge") == []
