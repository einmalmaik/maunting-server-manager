"""Tresor-Cloud: Blob-Speicher (09/2026).

Der Server haelt nur Chiffrat in Chunks. Diese Tests pruefen, was er dabei
erzwingen muss: fremde Blobs sind unsichtbar, jeder Chunk hat genau die
angemeldete Laenge, die Quote haelt auch bei zwei Anfragen zugleich, Loeschen
braucht den Schluessel aus dem Tresor-Eintrag, und das Aufraeumen entfernt nur,
was faellig ist.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import struct
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

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
from services.vault_blob_service import CHUNK_CHIFFRAT, CHUNK_UEBERHANG, MAX_BLOB_BYTES, PLATTENRESERVE


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
    """``als(user)`` wechselt das angemeldete Konto des Test-Clients; der Client nennt dessen Bucket."""
    aktuell = {"user": konten[0]}

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: aktuell["user"]
    app.dependency_overrides[verify_csrf] = lambda: None
    with TestClient(app) as client:
        def wechseln(user: User) -> TestClient:
            aktuell["user"] = user
            client.headers["X-MSM-Vault-Bucket"] = BUCKET[user.id]
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
        # groesser als ein Blob sein darf, auch wenn Chunks und Bytes zusammenpassen
        (MAX_BLOB_BYTES // CHUNK_CHIFFRAT + 2, (MAX_BLOB_BYTES // CHUNK_CHIFFRAT + 1) * CHUNK_CHIFFRAT + CHUNK_UEBERHANG + 1),
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
    abgelehnt = client.request("DELETE", f"/api/vault/blobs/{blob_id}", json={"schluessel": falsch})
    assert abgelehnt.status_code == 403
    # Der Client unterscheidet daran „gilt nie“ von CSRF oder abgeschaltetem Tresor.
    assert abgelehnt.json()["detail"]["code"] == "VAULT_LOESCHNACHWEIS_FALSCH"
    assert client.get(f"/api/vault/blobs/{blob_id}/chunks/0").status_code == 200

    assert client.request("DELETE", f"/api/vault/blobs/{blob_id}", json={"schluessel": schluessel}).status_code == 200
    assert client.get(f"/api/vault/blobs/{blob_id}/chunks/0").status_code == 404
    # In der Loeschhaltung belegt er die Quote weiter.
    speicher = client.get("/api/vault/speicher").json()
    assert speicher["belegt"] == 100
    assert speicher["in_loeschung"] == 100


def test_schreibende_blob_routen_brauchen_csrf(db, konten, blob_dir):
    """Ein Browser schickt die Sitzung ungefragt mit. Ohne CSRF-Nachweis
    legt keine Route etwas an, schreibt, schliesst ab oder loescht."""
    eins, _ = konten
    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[get_current_user] = lambda: eins
    blob_id = _neue_id()
    try:
        with TestClient(app) as client:
            client.cookies.set("__Secure-access_token", "sitzung")
            client.headers["X-MSM-Vault-Bucket"] = BUCKET[eins.id]
            schluessel, verifier = _schluessel()
            antworten = [
                client.post(
                    "/api/vault/blobs",
                    json={"id": blob_id, "chunk_count": 1, "bytes_total": 100, "delete_verifier": verifier},
                ),
                _hochladen(client, blob_id, 0, b"x" * 100),
                client.post(f"/api/vault/blobs/{blob_id}/fertig"),
                client.post("/api/vault/blobs/klein", json={"ids": [blob_id]}),
                client.request("DELETE", f"/api/vault/blobs/{blob_id}", json={"schluessel": schluessel}),
            ]
    finally:
        app.dependency_overrides.clear()
    assert [a.status_code for a in antworten] == [403] * 5
    assert db.get(VaultBlob, blob_id) is None


def test_fremdes_konto_schliesst_keinen_upload_ab(als, konten, db):
    eins, zwei = konten
    blob_id, _, antwort = _anlegen(als(eins), 100)
    assert antwort.status_code == 201
    assert _hochladen(als(eins), blob_id, 0, b"x" * 100).status_code == 204

    assert als(zwei).post(f"/api/vault/blobs/{blob_id}/fertig").status_code == 404
    db.expire_all()
    assert db.get(VaultBlob, blob_id).state == "offen"


def test_quote_je_tresor(als, konten, db):
    eins, _ = konten
    _speicher_der_rolle(db, eins, 1000)
    client = als(eins)
    assert _anlegen(client, 600)[2].status_code == 201
    voll = _anlegen(client, 600)[2]
    assert voll.status_code == 507
    # Jeder Grund hat seinen Code: Löschen hilft nur beim Kontingent.
    assert voll.json()["detail"]["code"] == "VAULT_SPEICHER_VOLL"
    assert _anlegen(client, 400)[2].status_code == 201
    assert client.get("/api/vault/speicher").json() == {"belegt": 1000, "quote": 1000, "in_loeschung": 0, "blobs": 2}


def test_chunk_haelt_die_plattenreserve_frei(als, konten, blob_dir, monkeypatch):
    """Zwei Tresore reservieren je 1000 Bytes, frei sind 1500 ueber der
    Reserve. Beide bestehen die Pruefung beim Reservieren; erst beim Schreiben
    darf der zweite die Reserve nicht mehr anbrechen."""
    eins, zwei = konten
    frei = {"bytes": PLATTENRESERVE + 1500}
    monkeypatch.setattr(vault_blob_service.shutil, "disk_usage", lambda _pfad: SimpleNamespace(free=frei["bytes"]))
    erster, _, a = _anlegen(als(eins), 1000)
    zweiter, _, b = _anlegen(als(zwei), 1000)
    assert (a.status_code, b.status_code) == (201, 201)

    assert _hochladen(als(eins), erster, 0, b"x" * 1000).status_code == 204
    frei["bytes"] -= 1000
    antwort = _hochladen(als(zwei), zweiter, 0, b"y" * 1000)
    assert antwort.status_code == 507
    assert antwort.json()["detail"]["code"] == "VAULT_PLATTE_VOLL"
    assert not (blob_dir / zweiter[:2] / zweiter / "0").exists()


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


def test_chunk_upload_haelt_keine_datenbankverbindung_beim_lesen(als, konten, db):
    """Der Body eines Chunks kann langsam eintroepfeln. Bis 01.10.2026 hielt die
    Route waehrenddessen eine offene Transaktion: rund 30 langsame Uploads
    belegten den ganzen Pool, und das Panel stand."""
    client = als(konten[0])
    blob_id, _, _ = _anlegen(client, 1000)
    db.commit()
    waehrend: list[bool] = []

    def body():
        yield b"x" * 500
        waehrend.append(db.in_transaction())
        yield b"x" * 500

    antwort = client.put(
        f"/api/vault/blobs/{blob_id}/chunks/0", content=body(), headers={"Content-Type": "application/octet-stream"}
    )
    assert antwort.status_code == 204
    assert waehrend == [False]


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
    _altern(abgebrochen, timedelta(hours=25))
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
    _altern(von_zwei, timedelta(hours=25))

    ergebnis = vault_blob_service.aufraeumen(db)

    assert ergebnis == {"entfernt": 2, "verwaist": 1}
    vorhanden = {p.name for p in blob_dir.glob("*/*")}
    assert vorhanden == {bleibt, frisch_geloescht}
    assert {z.id for z in db.query(VaultBlob).all()} == {bleibt, frisch_geloescht}


def _altern(blob_id: str, wie_alt: timedelta) -> None:
    """Stellt Verzeichnis und Chunks eines Blobs so, als waere zuletzt vor ``wie_alt`` geschrieben worden."""
    verzeichnis = vault_blob_service.blob_verzeichnis(blob_id)
    zeit = (datetime.now(timezone.utc) - wie_alt).timestamp()
    for pfad in [*verzeichnis.iterdir(), verzeichnis]:
        os.utime(pfad, (zeit, zeit))


def test_offener_upload_gilt_ab_dem_letzten_chunk_als_abgebrochen(als, konten, db, blob_dir):
    """Ein grosser Upload, der nach 24 Stunden noch Chunks schreibt, laeuft noch.

    Bis 01.10.2026 zaehlte die Frist ab dem Anlegen, und das Aufraeumen nahm
    einen solchen Upload mitten im Hochladen weg.
    """
    eins, _ = konten
    client = als(eins)
    laeuft, _, _ = _anlegen(client, CHUNK_CHIFFRAT + 100, chunk_count=2)
    assert _hochladen(client, laeuft, 0, b"z" * CHUNK_CHIFFRAT).status_code == 204
    steht, _, _ = _anlegen(client, 100)
    assert _hochladen(client, steht, 0, b"z" * 100).status_code == 204
    nie_begonnen, _, _ = _anlegen(client, 100)
    jetzt = datetime.now(timezone.utc)
    for blob_id in (laeuft, steht, nie_begonnen):
        db.get(VaultBlob, blob_id).created_at = jetzt - timedelta(hours=30)
    db.commit()
    _altern(laeuft, timedelta(hours=26))
    # Der eben geschriebene zweite Chunk ist die juengste Aktivitaet.
    assert _hochladen(client, laeuft, 1, b"z" * 100).status_code == 204
    _altern(steht, timedelta(hours=25))

    ergebnis = vault_blob_service.aufraeumen(db)

    assert ergebnis == {"entfernt": 2, "verwaist": 0}
    assert {z.id for z in db.query(VaultBlob).all()} == {laeuft}
    assert sorted(p.name for p in vault_blob_service.blob_verzeichnis(laeuft).iterdir()) == ["0", "1"]
    assert client.post(f"/api/vault/blobs/{laeuft}/fertig").status_code == 200


def test_verzeichnis_ohne_zeile_bleibt_solange_geschrieben_wird(db, blob_dir):
    """Ein Chunk, dessen Zeile diese Sitzung (noch) nicht sieht, wird nicht sofort weggeraeumt.

    Bis 01.10.2026 entfernte das Aufraeumen jedes Verzeichnis ohne Zeile, auch
    eines, in das im selben Augenblick geschrieben wurde.
    """
    blob_id = _neue_id()
    vault_blob_service.chunk_schreiben(blob_id, 0, b"z" * 100)

    assert vault_blob_service.aufraeumen(db)["verwaist"] == 0
    assert vault_blob_service.blob_verzeichnis(blob_id).is_dir()

    _altern(blob_id, timedelta(hours=25))
    assert vault_blob_service.aufraeumen(db)["verwaist"] == 1
    assert not vault_blob_service.blob_verzeichnis(blob_id).exists()


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
        assert "vault_bucket_tombstones" not in pruefer.get_table_names()
        assert "vault_eingang" not in pruefer.get_table_names()
        assert "vault_sicherungszugaenge" not in pruefer.get_table_names()

        command.upgrade(config, "head")
        pruefer = inspect(engine)
        assert {"vault_blobs", "vault_bucket_formats", "role_vault_quotas", "vault_bucket_tombstones", "vault_eingang", "vault_sicherungszugaenge"} <= set(
            pruefer.get_table_names()
        )
        # Seit 20261001_01 haengt ein Blob am Bucket, nicht am Konto.
        assert pruefer.get_foreign_keys("vault_blobs") == []
        assert "user_id" not in {s["name"] for s in pruefer.get_columns("vault_blobs")}
    finally:
        engine.dispose()
        settings.database_url = vorher


@pytest.mark.parametrize("route", ["tresor_speicher", "blob_status", "blob_fertig", "blob_loeschen"])
def test_jede_blob_route_hat_eine_eigene_grenze(route):
    """Bis 01.10.2026 hingen diese vier Routen nur an der globalen Grenze des Panels."""
    from middleware.rate_limit import limiter

    grenzen = limiter._route_limits.get(f"routers.vault.{route}", [])
    assert [str(g.limit) for g in grenzen] in (["1200 per 1 minute"], ["120 per 1 minute"])


def test_speicher_grenze_greift(als, konten):
    client = als(konten[0])
    for _ in range(120):
        assert client.get("/api/vault/speicher").status_code == 200
    assert client.get("/api/vault/speicher").status_code == 429


# ─── Befunde vom 02.10.2026 ─────────────────────────────────────────────────


def test_alter_tresor_laedt_nicht_in_den_neuen(als, konten, db):
    """Geraet 1 setzt zurueck und richtet einen neuen Tresor ein; Geraet 2 hat
    noch den alten offen. Bis 02.10.2026 nahmen die Datei-Routen den Bucket des
    Kontos, und Geraet 2 lud in den neuen hoch: Dateien ohne Eintrag, die dessen
    Speicher belegten."""
    eins, _ = konten
    alt = BUCKET[eins.id]
    vault_service.tresor_zuruecksetzen(db, vault_service.tresor_konto(eins.id))
    neu = "9" * 64
    db.add(VaultUserSetting(konto_index=vault_service.tresor_konto(eins.id).index, bucket_id=neu))
    db.commit()

    client = als(eins)
    client.headers["X-MSM-Vault-Bucket"] = alt
    blob_id, _, antwort = _anlegen(client, 100)
    assert antwort.status_code == 410, antwort.text
    assert antwort.json()["detail"]["code"] == "VAULT_ZURUECKGESETZT"
    assert db.get(VaultBlob, blob_id) is None
    assert vault_blob_service.belegt(db, neu) == 0

    # Ein Bucket, der nicht (mehr) der des Kontos ist, gilt genauso.
    client.headers["X-MSM-Vault-Bucket"] = "7" * 64
    assert _anlegen(client, 100)[2].status_code == 410
    # Ohne genannten Bucket schreibt keine Route.
    del client.headers["X-MSM-Vault-Bucket"]
    assert _anlegen(client, 100)[2].status_code == 400

    client.headers["X-MSM-Vault-Bucket"] = neu
    assert _anlegen(client, 100)[2].status_code == 201


def test_anlegen_in_beerdigtem_bucket(db, konten, blob_dir):
    """Laeuft das Zuruecksetzen, waehrend ein Upload schon an der Route vorbei
    ist, prueft ``anlegen`` unter der Sperre selbst noch einmal."""
    from models import VaultBucketTombstone

    eins, _ = konten
    db.add(VaultBucketTombstone(bucket_id=BUCKET[eins.id]))
    db.commit()
    with pytest.raises(vault_blob_service.BlobFehler) as fehler:
        vault_blob_service.anlegen(db, eins, BUCKET[eins.id], _neue_id(), 1, 100, "0" * 64)
    assert (fehler.value.status_code, fehler.value.code) == (410, "VAULT_ZURUECKGESETZT")
    db.rollback()
    assert db.query(VaultBlob).count() == 0


def test_zuruecksetzen_und_anlegen_teilen_eine_sperre(db, konten, blob_dir):
    """Bis 02.10.2026 sperrten Zuruecksetzen (``sha256(bucket)``) und Anlegen
    (``vault-blob:`` + bucket) verschiedene Schluessel und liefen aneinander vorbei."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    eins, _ = konten
    engine = create_engine(db_module.engine.url, poolclass=NullPool)
    sitzung = sessionmaker(bind=engine)
    zuruecksetzen, upload = sitzung(), sitzung()
    try:
        # Das Zuruecksetzen haelt die Sperre bis zu seinem Commit.
        vault_service._bucket_entfernen(zuruecksetzen, BUCKET[eins.id])
        upload.execute(text("SET LOCAL lock_timeout = '300ms'"))
        with pytest.raises(OperationalError):
            vault_blob_service.anlegen(upload, upload.get(User, eins.id), BUCKET[eins.id], _neue_id(), 1, 100, "0" * 64)
    finally:
        upload.rollback()
        zuruecksetzen.rollback()
        upload.close()
        zuruecksetzen.close()
        engine.dispose()


def test_chunk_nach_fertig_ueberschreibt_nichts(als, konten, db, blob_dir):
    """Der Body eines Chunks kam an, nachdem der Blob fertig war. Bis 02.10.2026
    galt nur die Pruefung vor dem Body, und der Chunk eines fertigen Blobs wurde
    ueberschrieben."""
    eins, _ = konten
    client = als(eins)
    blob_id, _, _ = _anlegen(client, 100)
    assert _hochladen(client, blob_id, 0, b"a" * 100).status_code == 204

    def body():
        yield b"b" * 50
        vault_blob_service.fertigstellen(db, BUCKET[eins.id], blob_id)
        yield b"b" * 50

    antwort = client.put(
        f"/api/vault/blobs/{blob_id}/chunks/0", content=body(), headers={"Content-Type": "application/octet-stream"}
    )
    assert antwort.status_code == 409
    assert (blob_dir / blob_id[:2] / blob_id / "0").read_bytes() == b"a" * 100
    assert client.get(f"/api/vault/blobs/{blob_id}/chunks/0").content == b"a" * 100


def test_zu_viele_chunks_zugleich_503(als, konten, monkeypatch):
    """Jeder Chunk liegt bis zum Schreiben im Speicher. Bis 02.10.2026 gab es
    keine Grenze, wie viele das zugleich sein durften."""
    from routers import vault as vault_router

    client = als(konten[0])
    blob_id, _, _ = _anlegen(client, 100)
    monkeypatch.setattr(vault_router, "_chunks_laufend", getattr(vault_router, "CHUNKS_ZUGLEICH", 16), raising=False)
    antwort = _hochladen(client, blob_id, 0, b"a" * 100)
    assert antwort.status_code == 503
    assert antwort.headers.get("retry-after")
    assert client.get(f"/api/vault/blobs/{blob_id}/status").json()["vorhanden"] == []

    monkeypatch.setattr(vault_router, "_chunks_laufend", 0, raising=False)
    assert _hochladen(client, blob_id, 0, b"a" * 100).status_code == 204
    # Der Zaehler steht danach wieder, wo er war.
    assert vault_router._chunks_laufend == 0


def test_status_liest_das_verzeichnis_einmal(blob_dir, monkeypatch):
    """Ein Blob von 1 TiB hat rund 262.000 Chunks. Bis 02.10.2026 kostete jeder
    Status und jedes Fertigstellen ein ``stat`` je Index, auch fuer fehlende."""
    blob = VaultBlob(id=_neue_id(), bucket_id=BUCKET[1], chunk_count=50_000, bytes_total=50_000 * CHUNK_CHIFFRAT, state="offen")
    vault_blob_service.chunk_schreiben(blob.id, 3, b"x" * CHUNK_CHIFFRAT)
    vault_blob_service.chunk_schreiben(blob.id, 7, b"x" * 10)  # zu kurz: zaehlt nicht

    aufrufe = {"stat": 0}
    echt = os.stat

    def gezaehlt(*args, **kwargs):
        aufrufe["stat"] += 1
        return echt(*args, **kwargs)

    monkeypatch.setattr(os, "stat", gezaehlt)
    assert vault_blob_service.vorhandene_chunks(blob) == [3]
    assert aufrufe["stat"] < 10
