"""Tresor-Cloud: Blob-Speicher (09/2026).

Der Server haelt nur Chiffrat in Chunks. Diese Tests pruefen, was er dabei
erzwingen muss: fremde Blobs sind unsichtbar, jeder Chunk hat genau die
angemeldete Laenge, die Quote haelt auch bei zwei Anfragen zugleich, Loeschen
braucht den Schluessel aus dem Tresor-Eintrag, und das Aufraeumen entfernt nur,
was faellig ist.
"""

from __future__ import annotations

import hashlib
import secrets
import struct
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import database as db_module
from config import settings
from database import Base
from dependencies import get_current_user, get_db, verify_csrf
from main import app
from models import Role, RoleVaultQuota, User, VaultBlob, VaultUserSetting
from services import vault_blob_service, vault_service
from services.vault_blob_service import CHUNK_CHIFFRAT, CHUNK_UEBERHANG


@pytest.fixture
def blob_dir(tmp_path, monkeypatch):
    ziel = tmp_path / "vault-blobs"
    monkeypatch.setattr(settings, "vault_blob_dir", str(ziel))
    return ziel


BUCKET = {1: "a" * 64, 2: "b" * 64}


def _tresor_anlegen(db, user: User) -> None:
    db.add(VaultUserSetting(konto_index=vault_service.tresor_konto(user.id).index, bucket_id=BUCKET[user.id]))


@pytest.fixture
def konten(db):
    """Zwei Konten mit je einem Tresor und einer gemeinsamen Rolle, die 10 GB Tresorspeicher gibt."""
    rolle = Role(name="tresor", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    db.add(RoleVaultQuota(role_id=rolle.id, quota_bytes=10 * 1024**3))
    eins = User(id=1, username="eins", email="eins@example.com", password_hash="x", is_active=True, role_id=rolle.id)
    zwei = User(id=2, username="zwei", email="zwei@example.com", password_hash="x", is_active=True, role_id=rolle.id)
    db.add_all([eins, zwei])
    db.flush()
    _tresor_anlegen(db, eins)
    _tresor_anlegen(db, zwei)
    db.commit()
    return eins, zwei


def _speicher_der_rolle(db, user: User, quota_bytes: int) -> None:
    db.get(RoleVaultQuota, user.role_id).quota_bytes = quota_bytes
    db.commit()


@pytest.fixture
def als(db, konten, blob_dir):
    """``als(user)`` wechselt das angemeldete Konto des Test-Clients."""
    aktuell = {"user": konten[0]}

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: aktuell["user"]
    app.dependency_overrides[verify_csrf] = lambda: None
    with TestClient(app) as client:
        def wechseln(user: User) -> TestClient:
            aktuell["user"] = user
            return client

        yield wechseln
    app.dependency_overrides.clear()


def _neue_id() -> str:
    return secrets.token_hex(16)


def _schluessel() -> tuple[str, str]:
    roh = secrets.token_bytes(32)
    return roh.hex(), hashlib.sha256(roh).hexdigest()


def _anlegen(client: TestClient, bytes_total: int, chunk_count: int = 1, blob_id: str | None = None):
    blob_id = blob_id or _neue_id()
    schluessel, verifier = _schluessel()
    antwort = client.post(
        "/api/vault/blobs",
        json={"id": blob_id, "chunk_count": chunk_count, "bytes_total": bytes_total, "delete_verifier": verifier},
    )
    return blob_id, schluessel, antwort


def _hochladen(client: TestClient, blob_id: str, index: int, daten: bytes):
    return client.put(
        f"/api/vault/blobs/{blob_id}/chunks/{index}",
        content=daten,
        headers={"Content-Type": "application/octet-stream"},
    )


def test_hochladen_fertigstellen_lesen(als, konten, blob_dir):
    client = als(konten[0])
    erster = secrets.token_bytes(CHUNK_CHIFFRAT)
    letzter = secrets.token_bytes(1000)
    blob_id, _, antwort = _anlegen(client, len(erster) + len(letzter), chunk_count=2)
    assert antwort.status_code == 201

    assert _hochladen(client, blob_id, 1, letzter).status_code == 204
    status = client.get(f"/api/vault/blobs/{blob_id}/status").json()
    assert status == {"state": "offen", "chunk_count": 2, "vorhanden": [1]}

    # Vor dem Fertigstellen gibt es nichts zu lesen, und ohne alle Chunks kein Fertig.
    assert client.get(f"/api/vault/blobs/{blob_id}/chunks/1").status_code == 404
    assert client.post(f"/api/vault/blobs/{blob_id}/fertig").status_code == 409

    assert _hochladen(client, blob_id, 0, erster).status_code == 204
    assert client.post(f"/api/vault/blobs/{blob_id}/fertig").status_code == 200
    assert client.post(f"/api/vault/blobs/{blob_id}/fertig").status_code == 200

    assert client.get(f"/api/vault/blobs/{blob_id}/chunks/0").content == erster
    assert client.get(f"/api/vault/blobs/{blob_id}/chunks/1").content == letzter
    # Nach dem Fertigstellen nimmt der Blob nichts mehr an.
    assert _hochladen(client, blob_id, 1, letzter).status_code == 409
    # Auf der Platte liegt genau das Chiffrat, unter der Kennung, ohne Namen.
    assert (blob_dir / blob_id[:2] / blob_id / "0").read_bytes() == erster


def test_chunk_mit_falscher_laenge(als, konten):
    client = als(konten[0])
    blob_id, _, _ = _anlegen(client, 500)
    assert _hochladen(client, blob_id, 0, b"x" * 499).status_code == 422
    assert _hochladen(client, blob_id, 0, b"x" * 501).status_code == 413
    assert _hochladen(client, blob_id, 1, b"x" * 500).status_code == 422
    assert client.get(f"/api/vault/blobs/{blob_id}/status").json()["vorhanden"] == []


@pytest.mark.parametrize(
    ("chunk_count", "bytes_total"),
    [
        (1, CHUNK_UEBERHANG),  # letzter Chunk ohne Klartext
        (1, CHUNK_CHIFFRAT + 1),
        (2, CHUNK_CHIFFRAT),
        (3, 2 * CHUNK_CHIFFRAT + CHUNK_UEBERHANG),
    ],
)
def test_unpassende_groesse_wird_abgelehnt(als, konten, chunk_count, bytes_total):
    _, _, antwort = _anlegen(als(konten[0]), bytes_total, chunk_count)
    assert antwort.status_code == 422


def test_fremder_blob_ist_unsichtbar(als, konten):
    eins, zwei = konten
    client = als(eins)
    blob_id, schluessel, _ = _anlegen(client, 100)
    _hochladen(client, blob_id, 0, b"a" * 100)
    client.post(f"/api/vault/blobs/{blob_id}/fertig")

    client = als(zwei)
    assert client.get(f"/api/vault/blobs/{blob_id}/status").status_code == 404
    assert client.get(f"/api/vault/blobs/{blob_id}/chunks/0").status_code == 404
    assert _hochladen(client, blob_id, 0, b"b" * 100).status_code == 404
    assert client.request("DELETE", f"/api/vault/blobs/{blob_id}", json={"schluessel": schluessel}).status_code == 404
    antwort = client.post("/api/vault/blobs/klein", json={"ids": [blob_id]})
    assert antwort.content == struct.pack(">I", 0)
    # Dieselbe Kennung ein zweites Mal anlegen geht nicht, auch nicht fremd.
    assert _anlegen(client, 100, blob_id=blob_id)[2].status_code == 409


def test_loeschen_braucht_den_schluessel(als, konten, db):
    client = als(konten[0])
    blob_id, schluessel, _ = _anlegen(client, 100)
    _hochladen(client, blob_id, 0, b"a" * 100)
    client.post(f"/api/vault/blobs/{blob_id}/fertig")

    falsch = secrets.token_hex(32)
    assert client.request("DELETE", f"/api/vault/blobs/{blob_id}", json={"schluessel": falsch}).status_code == 403
    assert client.get(f"/api/vault/blobs/{blob_id}/chunks/0").status_code == 200

    assert client.request("DELETE", f"/api/vault/blobs/{blob_id}", json={"schluessel": schluessel}).status_code == 200
    assert client.get(f"/api/vault/blobs/{blob_id}/chunks/0").status_code == 404
    # In der Loeschhaltung belegt er die Quote weiter.
    speicher = client.get("/api/vault/speicher").json()
    assert speicher["belegt"] == 100
    assert speicher["in_loeschung"] == 100


def test_quote_je_tresor(als, konten, db):
    eins, _ = konten
    _speicher_der_rolle(db, eins, 1000)
    client = als(eins)
    assert _anlegen(client, 600)[2].status_code == 201
    assert _anlegen(client, 600)[2].status_code == 507
    assert _anlegen(client, 400)[2].status_code == 201
    assert client.get("/api/vault/speicher").json() == {"belegt": 1000, "quote": 1000, "in_loeschung": 0, "blobs": 2}


def test_ohne_rolle_mit_speicher_kein_upload(als, konten, db):
    """Speicher kommt nur ueber eine Rolle. Ohne sie bleibt der Upload zu,
    und die Anzeige sagt 0 statt einer Vorgabe."""
    eins, _ = konten
    eins.role_id = None
    db.commit()
    client = als(eins)
    assert _anlegen(client, 100)[2].status_code == 507
    assert client.get("/api/vault/speicher").json()["quote"] == 0


def test_zuruecksetzen_gibt_alle_dateien_zum_entfernen_frei(als, konten, db, blob_dir):
    """Ohne das alte Master-Passwort liesse sich kein Blob mehr loeschen. Nach
    dem Zuruecksetzen gehoert der Bucket keinem Konto mehr, seine Blobs zaehlen
    gegen keinen Speicher; mit Loeschhaltung fuellte Hochladen und
    Zuruecksetzen die Platte. Das naechste Aufraeumen entfernt sie. Das fremde
    Konto bleibt unberuehrt."""
    eins, zwei = konten
    for user in (eins, zwei):
        client = als(user)
        for groesse in (100, 200):
            blob_id, _, _ = _anlegen(client, groesse)
            _hochladen(client, blob_id, 0, b"a" * groesse)
            client.post(f"/api/vault/blobs/{blob_id}/fertig")
    _anlegen(als(eins), 50)  # ein Upload, der nie fertig wurde

    client = als(eins)
    assert client.get("/api/vault/speicher").json()["blobs"] == 3

    vault_service.tresor_zuruecksetzen(db, vault_service.tresor_konto(eins.id))
    db.commit()

    assert client.get("/api/vault/speicher").json() == {"belegt": 0, "quote": 10 * 1024**3, "in_loeschung": 0, "blobs": 0}
    assert vault_blob_service.aufraeumen(db)["entfernt"] == 3
    uebrig = dict(db.execute(select(VaultBlob.bucket_id, func.count()).group_by(VaultBlob.bucket_id)).all())
    assert uebrig == {BUCKET[zwei.id]: 2}
    assert als(zwei).get("/api/vault/speicher").json()["blobs"] == 2


def test_kleine_blobs_in_einer_antwort(als, konten):
    client = als(konten[0])
    inhalte = {}
    for groesse in (50, 70):
        blob_id, _, _ = _anlegen(client, groesse)
        daten = secrets.token_bytes(groesse)
        _hochladen(client, blob_id, 0, daten)
        client.post(f"/api/vault/blobs/{blob_id}/fertig")
        inhalte[blob_id] = daten
    offen_id, _, _ = _anlegen(client, 30)
    fehlt = _neue_id()

    ids = [*inhalte, offen_id, fehlt, "../../etc/passwd"]
    antwort = client.post("/api/vault/blobs/klein", json={"ids": ids}).content

    gelesen = []
    pos = 0
    for _ in ids:
        (laenge,) = struct.unpack(">I", antwort[pos:pos + 4])
        gelesen.append(antwort[pos + 4:pos + 4 + laenge])
        pos += 4 + laenge
    assert pos == len(antwort)
    assert gelesen == [*inhalte.values(), b"", b"", b""]


def test_quote_haelt_bei_zwei_anfragen_zugleich(db, konten, blob_dir):
    """Zwei Uploads, die je die halbe Quote plus eins wollen: nur einer darf durch.

    Ohne Sperre lesen beide denselben belegten Stand und legen beide an.
    """
    eins, _ = konten
    _speicher_der_rolle(db, eins, 1000)

    engine = create_engine(db_module.engine.url, poolclass=NullPool)
    sitzung = sessionmaker(bind=engine)
    barriere = threading.Barrier(2)
    urspruenglich = vault_blob_service.belegt

    def belegt_mit_barriere(s, bucket_id):
        wert = urspruenglich(s, bucket_id)
        try:
            barriere.wait(timeout=3)
        except threading.BrokenBarrierError:
            pass
        return wert

    ergebnisse: list[str] = []

    def lauf() -> None:
        s = sitzung()
        try:
            user = s.get(User, eins.id)
            vault_blob_service.anlegen(s, user, BUCKET[eins.id], _neue_id(), 1, 501, "0" * 64)
            ergebnisse.append("angelegt")
        except vault_blob_service.SpeicherVoll:
            ergebnisse.append("voll")
        finally:
            s.close()

    vault_blob_service.belegt = belegt_mit_barriere
    try:
        threads = [threading.Thread(target=lauf) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    finally:
        vault_blob_service.belegt = urspruenglich
        engine.dispose()

    assert sorted(ergebnisse) == ["angelegt", "voll"]
    db.expire_all()
    assert vault_blob_service.belegt(db, BUCKET[eins.id]) == 501


def test_aufraeumen_entfernt_nur_faelliges(als, konten, db, blob_dir):
    eins, zwei = konten
    client = als(eins)

    def fertiger_blob() -> str:
        blob_id, _, _ = _anlegen(client, 100)
        _hochladen(client, blob_id, 0, b"z" * 100)
        client.post(f"/api/vault/blobs/{blob_id}/fertig")
        return blob_id

    bleibt = fertiger_blob()
    abgebrochen, _, _ = _anlegen(client, 100)
    _hochladen(client, abgebrochen, 0, b"z" * 100)
    frisch_geloescht = fertiger_blob()
    alt_geloescht = fertiger_blob()
    jetzt = datetime.now(timezone.utc)
    db.get(VaultBlob, abgebrochen).created_at = jetzt - timedelta(hours=25)
    for blob_id, wann in ((frisch_geloescht, jetzt - timedelta(days=1)), (alt_geloescht, jetzt - timedelta(days=8))):
        zeile = db.get(VaultBlob, blob_id)
        zeile.state = "geloescht"
        zeile.deleted_at = wann
    db.commit()

    # Ein Verzeichnis ohne Zeile (etwa nach der Umstellung auf Buckets, die
    # Blobs ohne Tresor entfernt) raeumt das Aufraeumen ab.
    als(zwei)
    von_zwei, _, _ = _anlegen(client, 100)
    _hochladen(client, von_zwei, 0, b"z" * 100)
    db.execute(VaultBlob.__table__.delete().where(VaultBlob.id == von_zwei))
    db.commit()

    ergebnis = vault_blob_service.aufraeumen(db)

    assert ergebnis == {"entfernt": 2, "verwaist": 1}
    vorhanden = {p.name for p in blob_dir.glob("*/*")}
    assert vorhanden == {bleibt, frisch_geloescht}
    assert {z.id for z in db.query(VaultBlob).all()} == {bleibt, frisch_geloescht}


def test_migration_hin_und_zurueck(tmp_path: Path, pg_wegwerf) -> None:
    db_url = pg_wegwerf("tresorcloud")
    vorher = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")
        command.downgrade(config, "20260929_02")
        pruefer = inspect(engine)
        assert "vault_blobs" not in pruefer.get_table_names()
        assert "vault_bucket_formats" not in pruefer.get_table_names()
        assert "role_vault_quotas" not in pruefer.get_table_names()

        command.upgrade(config, "head")
        pruefer = inspect(engine)
        assert {"vault_blobs", "vault_bucket_formats", "role_vault_quotas"} <= set(pruefer.get_table_names())
        # Seit 20261001_01 haengt ein Blob am Bucket, nicht am Konto.
        assert pruefer.get_foreign_keys("vault_blobs") == []
        assert "user_id" not in {s["name"] for s in pruefer.get_columns("vault_blobs")}
    finally:
        engine.dispose()
        settings.database_url = vorher
