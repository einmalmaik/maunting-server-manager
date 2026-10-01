"""Die Tresortabellen verraten nicht, welchem Konto ein Tresor gehoert.

Bis 01.10.2026 stand die `user_id` in `vault_user_settings`, `vault_hints` und
`vault_blobs`. Wer die Datenbank las (Admin im PostgreSQL-Studio, Dump),
sah, wem welcher Tresor gehoert und wie viele und wie grosse Dateien darin
liegen. Seither steht dort nur ein HMAC der Kontonummer aus dem DIS-Sidecar.

Invarianten:

1. Nach Einrichten, Hinweis und Upload steht in keiner Tresortabelle die
   Kontonummer, und keine hat einen Fremdschluessel auf `users` (Roh-SQL am
   ORM vorbei).
2. Altbestand mit `user_id` wird beim Lesen und beim Start umgestellt.
3. Ohne Zuordnung antworten die Datei-Routen 409 `VAULT_BUCKET_UNBEKANNT`;
   ohne Sidecar 503.
4. Eine Kontoloeschung raeumt den Tresor ab, ohne Sidecar loescht sie nichts.
5. Der Datenexport findet den Tresor ueber den Index.
"""

from __future__ import annotations

import hashlib
import json
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from config import settings
from models import User

BUCKET = "c" * 64
TABELLEN = ("vault_user_settings", "vault_hints", "vault_blobs")


@pytest.fixture(autouse=True)
def _blob_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "vault_blob_dir", str(tmp_path / "vault-blobs"))


def _kopf(cookies: dict) -> dict:
    marke = cookies.get("__Secure-csrf_token")
    return {"X-CSRF-Token": marke} if marke else {}


def _tresor_mit_datei(client: TestClient, cookies: dict) -> str:
    """Salz, Hinweis und eine fertige Datei, alles ueber die echten Routen."""
    kopf = _kopf(cookies)
    assert client.post(
        "/api/vault/salt", json={"kdf_salt": "ab" * 16, "bucket_id": BUCKET}, cookies=cookies, headers=kopf
    ).status_code == 200
    assert client.post("/api/vault/hint", json={"hint": "Hund und Jahr"}, cookies=cookies, headers=kopf).status_code == 200
    blob_id = secrets.token_hex(16)
    verifier = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
    antwort = client.post(
        "/api/vault/blobs",
        json={"id": blob_id, "chunk_count": 1, "bytes_total": 100, "delete_verifier": verifier},
        cookies=cookies,
        headers=kopf,
    )
    assert antwort.status_code == 201, antwort.text
    assert client.put(
        f"/api/vault/blobs/{blob_id}/chunks/0",
        content=b"x" * 100,
        cookies=cookies,
        headers={**kopf, "Content-Type": "application/octet-stream"},
    ).status_code == 204
    assert client.post(f"/api/vault/blobs/{blob_id}/fertig", cookies=cookies, headers=kopf).status_code == 200
    return blob_id


def test_keine_tresortabelle_nennt_das_konto(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    _tresor_mit_datei(client, owner_cookies)
    db.commit()

    pruefer = inspect(db.get_bind())
    for tabelle in TABELLEN:
        assert [fk for fk in pruefer.get_foreign_keys(tabelle) if fk["referred_table"] == "users"] == [], tabelle
        spalten = {s["name"] for s in pruefer.get_columns(tabelle)}
        if "user_id" in spalten:
            belegt = db.execute(text(f'SELECT count(*) FROM "{tabelle}" WHERE user_id IS NOT NULL')).scalar()
            assert belegt == 0, tabelle

    # Und die Zeilen gibt es wirklich: der Test prueft nicht leere Tabellen.
    for tabelle in TABELLEN:
        assert db.execute(text(f'SELECT count(*) FROM "{tabelle}"')).scalar() == 1, tabelle
    zeile = db.execute(text("SELECT konto_index, bucket_id FROM vault_user_settings")).one()
    assert zeile.bucket_id == BUCKET
    assert len(zeile.konto_index) == 64 and str(owner_user.id) != zeile.konto_index
    assert db.execute(text("SELECT bucket_id FROM vault_blobs")).scalar() == BUCKET


def test_altbestand_wird_beim_lesen_umgestellt(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    from services import vault_service

    db.execute(
        text("INSERT INTO vault_user_settings (user_id, bucket_id, kdf_salt, created_at, updated_at) "
             "VALUES (:u, :b, 'aa', now(), now())"),
        {"u": owner_user.id, "b": BUCKET},
    )
    db.commit()

    antwort = client.get("/api/vault/salt", cookies=owner_cookies)
    assert antwort.json() == {"kdf_salt": "aa", "bucket_id": BUCKET, "has_vault": True}
    zeile = db.execute(text("SELECT user_id, konto_index FROM vault_user_settings")).one()
    assert zeile.user_id is None
    assert zeile.konto_index == vault_service.tresor_konto(owner_user.id).index


def test_nachzug_beim_start_stellt_alles_um(db: Session, owner_user: User, regular_user: User):
    from services import vault_service

    for user, bucket in ((owner_user, "d" * 64), (regular_user, "e" * 64)):
        db.execute(
            text("INSERT INTO vault_user_settings (user_id, bucket_id, created_at, updated_at) "
                 "VALUES (:u, :b, now(), now())"),
            {"u": user.id, "b": bucket},
        )
    db.execute(
        text("INSERT INTO vault_hints (user_id, hint, created_at, updated_at) VALUES (:u, 'x', now(), now())"),
        {"u": regular_user.id},
    )
    db.commit()

    assert vault_service.kontoindex_nachziehen(db) == {"vault_user_settings", "vault_hints"}
    assert db.execute(text("SELECT count(*) FROM vault_user_settings WHERE user_id IS NOT NULL")).scalar() == 0
    assert db.execute(text("SELECT count(*) FROM vault_hints WHERE user_id IS NOT NULL")).scalar() == 0
    index = db.execute(text("SELECT konto_index FROM vault_hints")).scalar()
    assert index == vault_service.tresor_konto(regular_user.id).index
    # Ein zweiter Lauf findet nichts mehr und loest kein VACUUM aus.
    assert vault_service.kontoindex_nachziehen(db) == set()


def test_ohne_zuordnung_409_mit_code(client: TestClient, owner_cookies: dict):
    antwort = client.post(
        "/api/vault/blobs",
        json={"id": secrets.token_hex(16), "chunk_count": 1, "bytes_total": 100, "delete_verifier": "0" * 64},
        cookies=owner_cookies,
        headers=_kopf(owner_cookies),
    )
    assert antwort.status_code == 409
    assert antwort.json()["detail"]["code"] == "VAULT_BUCKET_UNBEKANNT"
    # Die Anzeige des Speichers geht auch ohne Tresor.
    speicher = client.get("/api/vault/speicher", cookies=owner_cookies).json()
    assert speicher["belegt"] == 0 and speicher["blobs"] == 0


def test_ohne_sidecar_503(client: TestClient, owner_cookies: dict, monkeypatch):
    from services import vault_service
    from services.dis_client import DisClient, DisSidecarError

    def kaputt(werte):
        raise DisSidecarError("weg")

    monkeypatch.setattr(vault_service, "_KONTO_INDEX", {})
    monkeypatch.setattr(DisClient, "blind_index", staticmethod(kaputt))
    assert client.get("/api/vault/salt", cookies=owner_cookies).status_code == 503


def test_kontoloeschung_raeumt_den_tresor_ab(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    from models import VaultBlindBucket, VaultBlob, VaultBucketFormat, VaultEntry
    from services.auth_service import AuthService

    blob_id = _tresor_mit_datei(client, owner_cookies)
    db.add(VaultEntry(id="e-1", bucket_id=BUCKET, ciphertext="sv-vault-v1:x", revision=1, is_deleted=False))
    db.add(VaultBlindBucket(bucket_id=BUCKET, auth_verifier="9" * 64))
    db.add(VaultBucketFormat(bucket_id=BUCKET, min_client_format=2))
    db.commit()

    AuthService.delete_account_atomically(db, db.get(User, owner_user.id))

    for tabelle in ("vault_user_settings", "vault_hints", "vault_entries", "vault_blind_buckets", "vault_bucket_formats"):
        assert db.execute(text(f'SELECT count(*) FROM "{tabelle}"')).scalar() == 0, tabelle
    blob = db.get(VaultBlob, blob_id)
    assert blob.state == "geloescht"
    from services import vault_blob_service

    assert vault_blob_service.aufraeumen(db)["entfernt"] == 1


def test_kontoloeschung_ohne_sidecar_loescht_nichts(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict, monkeypatch
):
    from fastapi import HTTPException

    from services import vault_service
    from services.auth_service import AuthService
    from services.dis_client import DisClient, DisSidecarError

    _tresor_mit_datei(client, owner_cookies)
    db.commit()

    def kaputt(werte):
        raise DisSidecarError("weg")

    monkeypatch.setattr(vault_service, "_KONTO_INDEX", {})
    monkeypatch.setattr(DisClient, "blind_index", staticmethod(kaputt))
    with pytest.raises(HTTPException) as fehler:
        AuthService.delete_account_atomically(db, db.get(User, owner_user.id))
    assert fehler.value.status_code == 503
    db.rollback()
    assert db.get(User, owner_user.id) is not None
    for tabelle in TABELLEN:
        assert db.execute(text(f'SELECT count(*) FROM "{tabelle}"')).scalar() == 1, tabelle


def test_export_findet_den_tresor(client: TestClient, db: Session, owner_user: User, owner_cookies: dict):
    _tresor_mit_datei(client, owner_cookies)
    db.commit()
    antwort = client.post(
        "/api/auth/data-export",
        json={"password": "OwnerPass123!"},
        cookies=owner_cookies,
        headers=_kopf(owner_cookies),
    )
    assert antwort.status_code == 200, antwort.text
    tabellen = antwort.json()["tabellen"]
    assert tabellen["vault_user_settings"][0]["bucket_id"] == BUCKET
    assert tabellen["vault_hints"][0]["hint"] == "Hund und Jahr"
    blob = tabellen["vault_blobs"][0]
    assert blob["bytes_total"] == 100 and "delete_verifier" not in blob
    assert "Hund und Jahr" in json.dumps(tabellen, ensure_ascii=False)


def test_fremdes_konto_bekommt_den_tresor_nicht_im_export(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict, regular_user: User
):
    from services import datenexport_service

    _tresor_mit_datei(client, owner_cookies)
    db.commit()
    paket = datenexport_service.exportieren(db, regular_user.id, mit_geheimnissen=False)
    for tabelle in TABELLEN:
        assert tabelle not in paket["tabellen"], tabelle


def test_dateien_gehoeren_dem_bucket(client: TestClient, db: Session, owner_cookies: dict):
    _tresor_mit_datei(client, owner_cookies)
    db.commit()
    from models import VaultBlob

    assert db.scalar(select(VaultBlob.bucket_id)) == BUCKET


def test_migration_hin_und_zurueck(pg_wegwerf) -> None:
    """Hin: Blobs bekommen den Bucket ihres Kontos, Blobs ohne Tresor fallen,
    die Kontonummer bleibt bis zum Nachzug stehen. Zurueck: laeuft."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    from database import Base

    db_url = pg_wegwerf("tresor_index")
    vorher = settings.database_url
    settings.database_url = db_url
    backend_dir = Path(__file__).resolve().parent.parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    engine = create_engine(db_url)
    try:
        Base.metadata.create_all(engine)
        command.stamp(config, "head")
        command.downgrade(config, "20260930_02")
        with engine.begin() as con:
            for uid in (1, 2):
                con.execute(text(
                    "INSERT INTO users (id, username, password_hash, is_owner, is_active, email_verified, "
                    "two_factor_enabled, email_notifications, ai_notifications, device_notifications, "
                    "location_sharing_enabled, ai_desktop_systembereich, created_at) VALUES "
                    "(:id, :n, 'x', false, true, true, false, true, true, true, false, 'lesen', '2026-09-01')"
                ), {"id": uid, "n": f"konto{uid}"})
            con.execute(text(
                "INSERT INTO vault_user_settings (user_id, bucket_id, created_at, updated_at) "
                "VALUES (1, :b, now(), now())"
            ), {"b": BUCKET})
            con.execute(text(
                "INSERT INTO vault_hints (user_id, hint, created_at, updated_at) VALUES (1, 'x', now(), now())"
            ))
            for blob_id, uid in (("1" * 32, 1), ("2" * 32, 2)):
                con.execute(text(
                    "INSERT INTO vault_blobs (id, user_id, chunk_count, bytes_total, delete_verifier, state, created_at) "
                    "VALUES (:id, :u, 1, 100, :v, 'fertig', now())"
                ), {"id": blob_id, "u": uid, "v": "0" * 64})

        command.upgrade(config, "head")
        with engine.connect() as con:
            assert con.execute(text("SELECT id, bucket_id FROM vault_blobs")).all() == [("1" * 32, BUCKET)]
            zeile = con.execute(text("SELECT id, user_id, konto_index FROM vault_user_settings")).one()
            assert zeile.user_id == 1 and zeile.konto_index is None and zeile.id is not None
        pruefer = inspect(engine)
        for tabelle in TABELLEN:
            assert pruefer.get_foreign_keys(tabelle) == [], tabelle

        command.downgrade(config, "20260930_02")
        with engine.connect() as con:
            assert con.execute(text("SELECT user_id FROM vault_blobs")).scalar() == 1
            assert con.execute(text("SELECT user_id FROM vault_hints")).scalar() == 1
        command.upgrade(config, "head")
    finally:
        engine.dispose()
        settings.database_url = vorher
