"""Tresor: Befunde der Gegenprobe vom 02.10.2026, je ein Test, der vorher rot war.

- Zwei Geraete setzen zugleich verschiedene Salze; vorher bekamen beide 200,
  und das erste leitete danach mit dem richtigen Passwort einen falschen
  Schluessel ab.
- Ein Salz aus Leerzeichen wurde als leeres Salz gespeichert.
- Die Kontoloeschung verbrauchte ohne Sidecar den Nachweis, wenn der
  Tresorindex schon im Prozess lag.
- Eine KI-Karte fuer eine Rolle mit Speicher schrieb nicht in einer
  Transaktion, und nicht endliche Speicherwerte warfen statt zu melden.
"""

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

import database as db_module
from dependencies import get_current_user, get_db, verify_csrf
from main import app
from models import LoginChallenge, User, VaultUserSetting
from models.user_passkey import UserPasskey
from services import login_challenge_service, vault_service
from services.dis_client import DisClient, DisSidecarError
from tests.test_ai_user_permission_tools import _bestaetigen, _vorschlag
from tests.test_vault_blobs import BUCKET, konten  # noqa: F401


def test_zwei_geraete_setzen_zugleich_verschiedene_salze(db: Session, konten):  # noqa: F811
    eins, _ = konten
    konto = vault_service.tresor_konto(eins.id)
    setting = db.scalar(select(VaultUserSetting).where(VaultUserSetting.konto_index == konto.index))
    assert setting.kdf_salt is None
    engine = create_engine(db_module.engine.url, poolclass=NullPool)
    sitzung = sessionmaker(bind=engine)
    barriere = threading.Barrier(2)
    echt = vault_service.einstellung

    def einstellung_mit_barriere(s, k):
        # Beide lesen ihre Zeile, bevor einer schreibt.
        zeile = echt(s, k)
        try:
            barriere.wait(timeout=3)
        except threading.BrokenBarrierError:
            pass
        return zeile

    ergebnisse: list[str] = []

    def lauf(salz: str) -> None:
        s = sitzung()
        try:
            vault_service.set_vault_salt(s, konto, salz, BUCKET[eins.id])
            ergebnisse.append("gesetzt")
        except vault_service.VaultSalzGesetzt:
            ergebnisse.append("409")
        except Exception as exc:  # noqa: BLE001
            ergebnisse.append(type(exc).__name__)
        finally:
            s.close()

    vault_service.einstellung = einstellung_mit_barriere
    try:
        threads = [threading.Thread(target=lauf, args=(salz,)) for salz in ("ab" * 16, "cd" * 16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    finally:
        vault_service.einstellung = echt
        engine.dispose()
    assert sorted(ergebnisse) == ["409", "gesetzt"], ergebnisse


@pytest.fixture
def salz_client(db: Session, konten):  # noqa: F811
    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[get_current_user] = lambda: konten[0]
    app.dependency_overrides[verify_csrf] = lambda: None
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.parametrize("salz", [" " * 16, " " * 10 + "ab" * 3, "ab<script>cdefgh12"])
def test_salz_ohne_gueltige_zeichen_wird_nicht_gesetzt(salz_client, konten, salz):  # noqa: F811
    antwort = salz_client.post("/api/vault/salt", json={"kdf_salt": salz, "bucket_id": BUCKET[konten[0].id]})
    assert antwort.status_code == 422, antwort.text


def test_loeschung_ohne_sidecar_verbraucht_den_nachweis_nicht(db: Session, monkeypatch) -> None:
    user = User(
        username="nur_sozial", email="nur_sozial@test.de", password_hash="x",
        has_password=False, is_active=True, email_verified=True, two_factor_enabled=True,
    )
    db.add(user)
    db.flush()
    passkey = UserPasskey(
        user_id=user.id, credential_id="cred-1", public_key="pk", algorithm=-7, rp_id="localhost", sign_count=0,
    )
    db.add(passkey)
    db.commit()
    vorgang = login_challenge_service.create_challenge(
        db, purpose="passkey_browser", user_id=user.id,
        payload={"zweck": "account_delete", "zahl": 11, "auswahl": [11, 22, 33], "bestaetigt": True,
                 "passkey_id": passkey.id},
    )
    db.commit()
    vault_service.tresor_konto(user.id)  # Index schon im Prozess

    def weg(*_a, **_k):
        raise DisSidecarError("weg")

    for name in ("decrypt", "decrypt_many", "encrypt", "blind_index", "verify_password", "hash_password"):
        monkeypatch.setattr(DisClient, name, staticmethod(weg))
    db.expire(user)

    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[verify_csrf] = lambda: None
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            antwort = client.request(
                "DELETE", "/api/auth/delete-account",
                json={"confirmation": "delete", "passkey": {"type": "browser", "vorgang": vorgang}},
            )
    finally:
        app.dependency_overrides.clear()
    monkeypatch.undo()
    db.rollback()
    db.expire_all()
    assert db.get(User, user.id) is not None
    zeile = db.query(LoginChallenge).filter(LoginChallenge.user_id == user.id).one()
    assert (antwort.status_code, zeile.consumed_at) == (503, None), antwort.text


def test_neue_rolle_mit_speicher_entsteht_nur_ganz(db: Session, owner_user: User, monkeypatch):
    from services import vault_blob_service
    from services.role_service import get_role_by_name

    vorschlag = _vorschlag(
        db, owner_user, "propose_role_set", name="tarif-halb-neu", permissions=["server.view"], vault_storage_gb=3,
    )

    def scheitert(*_a, **_k):
        raise RuntimeError("Speicher kaputt")

    monkeypatch.setattr(vault_blob_service, "rolle_speicher_setzen", scheitert)
    with pytest.raises(Exception):
        _bestaetigen(db, owner_user, vorschlag)
    db.rollback()
    assert get_role_by_name(db, "tarif-halb-neu") is None


def test_geaenderte_rolle_mit_speicher_aendert_sich_nur_ganz(db: Session, owner_user: User, monkeypatch):
    from services import rechtevergabe_service, role_service, vault_blob_service

    rolle = rechtevergabe_service.create_role(db, owner_user, "tarif-halb-zwei", None, ["server.view"])
    db.commit()
    vorschlag = _vorschlag(
        db, owner_user, "propose_role_set", role_id=rolle.id, description="neu",
        permissions=["server.view", "server.start"], vault_storage_gb=3,
    )

    def scheitert(*_a, **_k):
        raise RuntimeError("Rechte kaputt")

    monkeypatch.setattr(role_service, "_replace_role_permissions", scheitert)
    with pytest.raises(Exception):
        _bestaetigen(db, owner_user, vorschlag)
    db.rollback()
    db.expire_all()
    assert set(role_service.role_permission_keys(db, rolle.id)) == {"server.view"}
    assert vault_blob_service.rolle_speicher(db, rolle.id) is None
    assert db.get(type(rolle), rolle.id).description is None


@pytest.mark.parametrize("roh", ["nan", "inf", float("nan"), float("inf"), "1e400"])
def test_ki_speicher_ohne_endliche_zahl_ist_ein_validierungsfehler(roh):
    from services.ai_action_errors import AiActionValidationError
    from services.ai_proposals.user_proposals import _speicher

    with pytest.raises(AiActionValidationError):
        _speicher(roh)
