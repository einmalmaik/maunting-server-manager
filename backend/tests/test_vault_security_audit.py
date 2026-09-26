"""Sicherheitsaudit des Zero-Knowledge Passwort-Managers.

Jeder Test hier war zuerst ein Exploit: er beschreibt einen Angriff, der vor dem
Audit vom 22.09.2026 funktioniert hat. Die Zusagen, die hier gepruefte werden,
stehen so in `docs/patchnotes/vault-password-manager.md` und in den
Klassen-Docstrings der Vault-Modelle — ein Bruch ist deshalb immer beides: eine
Luecke und eine gebrochene Zusage.

Die Leitregel, aus der fast alle Faelle folgen:

    Ein Tresor-Bucket, der bereits Daten traegt, darf niemals von einem neuen
    Prinzipal beansprucht werden — weder blind noch angemeldet.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from dependencies import get_current_user, get_db, verify_csrf
from main import app
from models import User, VaultEntry, VaultUserSetting
from models.vault_blind_bucket import VaultBlindBucket


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    opfer = User(
        id=1,
        username="opfer",
        email="opfer@example.com",
        password_hash="hash",
        is_active=True,
        is_owner=True,
    )
    angreifer = User(
        id=2,
        username="angreifer",
        email="angreifer@example.com",
        password_hash="hash",
        is_active=True,
        is_owner=False,
    )
    session.add_all([opfer, angreifer])
    session.commit()
    session.refresh(opfer)
    session.refresh(angreifer)

    yield session, opfer, angreifer
    session.close()


@pytest.fixture
def anonym(test_db):
    """Client ganz ohne Konto — so wie `/blind-sync` aufgerufen werden darf."""
    session, _, _ = test_db

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _als(session, user):
    """Baut einen angemeldeten Client fuer `user`."""

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[verify_csrf] = lambda: None
    return TestClient(app)


def _lege_fremden_tresor_an(session, besitzer, bucket, *, revision=5):
    """Ein bestehender, befuellter Tresor eines anderen Kontos."""
    session.add(VaultUserSetting(user_id=besitzer.id, bucket_id=bucket, kdf_salt="00" * 16))
    session.add(
        VaultEntry(
            id="geheim-1",
            bucket_id=bucket,
            ciphertext="sv-vault-v1:streng_geheimer_ciphertext",
            revision=revision,
            is_deleted=False,
        )
    )
    session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# V1 — Uebernahme eines fremden, befuellten Buckets ueber /blind-sync
# ─────────────────────────────────────────────────────────────────────────────


def test_blind_sync_uebernimmt_keinen_befuellten_fremdbucket(test_db, anonym):
    """Exploit: `/blind-sync` registrierte jeden noch unregistrierten Bucket per
    Trust-On-First-Use — auch einen, der laengst Daten trug.

    Ablauf des Angriffs (Server-Betreiber oder jeder mit DB-/Log-Lesezugriff,
    denn dort steht `bucket_id` im Klartext):
      1. `POST /api/vault/blind-sync` mit fremder `bucket_id` und selbst
         gewaehltem `auth_token` — ohne Cookie, ohne CSRF, ohne Konto.
      2. Der Server legt den Verifier des Angreifers an.
      3. Die Antwort enthaelt saemtliche Ciphertexte des Opfers.
      4. Das Opfer ist ab jetzt ausgesperrt: sein Token passt nicht mehr.

    Erwartung nach dem Fix: 401. Ein Bucket mit Daten ist kein Niemandsland.
    """
    session, opfer, _ = test_db
    bucket = "a" * 64
    _lege_fremden_tresor_an(session, opfer, bucket)

    res = anonym.post(
        "/api/vault/blind-sync",
        json={
            "bucket_id": bucket,
            "auth_token": "f" * 64,
            "since_revision": 0,
            "mutations": [],
        },
    )

    assert res.status_code == 401, "Befuellter Fremdbucket wurde blind uebernommen"
    assert session.get(VaultBlindBucket, bucket) is None


def test_blind_sync_entkoppelt_den_besitzer_nicht(test_db, anonym):
    """Exploit: die „sanfte Zero-Breakage-Migration" setzte
    `vault_user_settings.bucket_id = NULL` — ausgeloest von einem
    unauthentifizierten Request.

    Damit riss der blinde Pfad genau die Kopplung weg, auf die sich der
    Cookie-Pfad (`/sync`) als IDOR-Schutz verlaesst. Ein Angreifer brauchte
    dafuer nur die `bucket_id` zu kennen.
    """
    session, opfer, _ = test_db
    bucket = "b" * 64
    _lege_fremden_tresor_an(session, opfer, bucket)

    anonym.post(
        "/api/vault/blind-sync",
        json={"bucket_id": bucket, "auth_token": "e" * 64, "since_revision": 0, "mutations": []},
    )

    session.expire_all()
    assert session.get(VaultUserSetting, opfer.id).bucket_id == bucket, (
        "Die Besitzkopplung wurde durch einen unauthentifizierten Request geloest"
    )


def test_blind_sync_registriert_jungfraeulichen_bucket_weiterhin(test_db, anonym):
    """Gegenprobe: ein Bucket ohne jede Spur darf weiter per TOFU entstehen.

    Sonst koennte sich ein neues Geraet nie anmelden. Der Unterschied zum Fall
    oben ist genau einer: hier gibt es nichts zu stehlen.
    """
    bucket = "c" * 64
    res = anonym.post(
        "/api/vault/blind-sync",
        json={
            "bucket_id": bucket,
            "auth_token": "1" * 64,
            "since_revision": 0,
            "mutations": [
                {
                    "id": "neu-1",
                    "ciphertext": "sv-vault-v1:frisch",
                    "revision": 1,
                    "is_deleted": False,
                }
            ],
        },
    )
    assert res.status_code == 200
    assert res.json()["server_revision"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# V2 — Auth-Bypass zwischen den beiden Sync-Pfaden
# ─────────────────────────────────────────────────────────────────────────────


def test_sync_beansprucht_keinen_befuellten_herrenlosen_bucket(test_db):
    """Exploit: `/sync` prueft nur `vault_user_settings`. Stand dort nichts
    (etwa weil die alte Migration gerade entkoppelt hatte), nahm der Endpunkt
    den Bucket dem naechstbesten angemeldeten Konto einfach in Besitz — und
    lieferte ihm dessen Inhalt aus. Ohne `auth_token`.
    """
    session, _, angreifer = test_db
    bucket = "d" * 64
    session.add(
        VaultEntry(
            id="geheim-2",
            bucket_id=bucket,
            ciphertext="sv-vault-v1:herrenlos_aber_nicht_frei",
            revision=3,
            is_deleted=False,
        )
    )
    session.add(VaultBlindBucket(bucket_id=bucket, auth_verifier="0" * 64))
    session.commit()

    with _als(session, angreifer) as client:
        res = client.post(
            "/api/vault/sync",
            json={"bucket_id": bucket, "since_revision": 0, "mutations": []},
        )
    app.dependency_overrides.clear()

    assert res.status_code == 403, "Fremder befuellter Bucket ueber den Cookie-Pfad vereinnahmt"


def test_sync_laesst_den_eigenen_bucket_weiter_zu(test_db):
    """Gegenprobe: der eingetragene Besitzer kommt an seinen Bucket."""
    session, opfer, _ = test_db
    bucket = "0" * 64
    _lege_fremden_tresor_an(session, opfer, bucket, revision=2)

    with _als(session, opfer) as client:
        res = client.post(
            "/api/vault/sync",
            json={"bucket_id": bucket, "since_revision": 0, "mutations": []},
        )
    app.dependency_overrides.clear()

    assert res.status_code == 200
    assert len(res.json()["entries"]) == 1


def test_salt_beansprucht_keinen_blind_registrierten_fremdbucket(test_db, anonym):
    """Exploit bis 26.09.2026: `/salt` pruefte nur, ob ein anderes Konto den
    Bucket schon traegt. Ein blind registrierter Bucket ohne Kontobesitzer ging
    an den, der die Kennung kannte, und `/sync` lieferte ihm danach alles.
    """
    session, _, angreifer = test_db
    bucket = "e" * 64
    token = "7" * 64
    erster = anonym.post(
        "/api/vault/blind-sync",
        json={
            "bucket_id": bucket,
            "auth_token": token,
            "since_revision": 0,
            "mutations": [{"id": "blind-1", "ciphertext": "sv-vault-v1:nur_fuer_den_besitzer", "revision": 1, "is_deleted": False}],
        },
    )
    assert erster.status_code == 200

    with _als(session, angreifer) as client:
        salz = client.post("/api/vault/salt", json={"kdf_salt": "11" * 16, "bucket_id": bucket})
        lesen = client.post("/api/vault/sync", json={"bucket_id": bucket, "since_revision": 0, "mutations": []})
    app.dependency_overrides.clear()

    assert salz.status_code == 403
    assert lesen.status_code == 403
    assert session.get(VaultUserSetting, angreifer.id) is None

    # Wer den Besitznachweis hat, bindet den Bucket weiter an sein Konto.
    with _als(session, angreifer) as client:
        mit_nachweis = client.post(
            "/api/vault/salt",
            json={"kdf_salt": "11" * 16, "bucket_id": bucket, "auth_token": token},
        )
    app.dependency_overrides.clear()
    assert mit_nachweis.status_code == 200


def test_salt_beansprucht_keine_verwaisten_eintraege(test_db):
    """Das Konto des Besitzers ist geloescht, die Eintraege liegen noch da."""
    session, _, angreifer = test_db
    bucket = "9" * 64
    session.add(VaultEntry(id="waise-1", bucket_id=bucket, ciphertext="sv-vault-v1:waise", revision=1, is_deleted=False))
    session.commit()

    with _als(session, angreifer) as client:
        salz = client.post("/api/vault/salt", json={"kdf_salt": "11" * 16, "bucket_id": bucket})
    app.dependency_overrides.clear()
    assert salz.status_code == 403


def test_salt_fuer_einen_neuen_tresor_geht_weiter(test_db):
    """Gegenprobe: die Einrichtung schickt `/salt` zuerst, vor jedem Abgleich."""
    session, opfer, _ = test_db
    with _als(session, opfer) as client:
        salz = client.post("/api/vault/salt", json={"kdf_salt": "22" * 16, "bucket_id": "8" * 64})
        nochmal = client.post("/api/vault/salt", json={"kdf_salt": "22" * 16, "bucket_id": "8" * 64})
    app.dependency_overrides.clear()
    assert salz.status_code == 200
    assert nochmal.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# V3 — Authentifizierter Migrationspfad (Ersatz fuer die blinde Uebernahme)
# ─────────────────────────────────────────────────────────────────────────────


def test_blind_register_bindet_den_eigenen_bucket(test_db):
    """Der legitime Weg vom Cookie-Pfad zum blinden Pfad: angemeldet den
    eigenen Verifier hinterlegen. Danach traegt der blinde Sync."""
    session, opfer, _ = test_db
    bucket = "2" * 64
    _lege_fremden_tresor_an(session, opfer, bucket, revision=4)

    with _als(session, opfer) as client:
        res = client.post(
            "/api/vault/blind-register",
            json={"bucket_id": bucket, "auth_token": "3" * 64},
        )
    app.dependency_overrides.clear()
    assert res.status_code == 200

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as anonym_client:
        ok = anonym_client.post(
            "/api/vault/blind-sync",
            json={"bucket_id": bucket, "auth_token": "3" * 64, "since_revision": 0, "mutations": []},
        )
        falsch = anonym_client.post(
            "/api/vault/blind-sync",
            json={"bucket_id": bucket, "auth_token": "4" * 64, "since_revision": 0, "mutations": []},
        )
    app.dependency_overrides.clear()

    assert ok.status_code == 200
    assert len(ok.json()["entries"]) == 1
    assert falsch.status_code == 401


def test_blind_register_verweigert_fremden_bucket(test_db):
    """Ein angemeldetes Konto darf den Verifier nur fuer den eigenen Bucket
    setzen — sonst waere `/blind-register` dieselbe Uebernahme mit Cookie."""
    session, opfer, angreifer = test_db
    bucket = "5" * 64
    _lege_fremden_tresor_an(session, opfer, bucket)

    with _als(session, angreifer) as client:
        res = client.post(
            "/api/vault/blind-register",
            json={"bucket_id": bucket, "auth_token": "6" * 64},
        )
    app.dependency_overrides.clear()

    assert res.status_code == 403


def test_blind_register_aendert_bestehenden_verifier_nicht(test_db):
    """Ist der Bucket blind registriert, gilt der Besitznachweis — nicht das
    Konto. Sonst waere ein uebernommenes Panel-Konto ein Tresor-Schluessel."""
    session, opfer, _ = test_db
    bucket = "7" * 64
    _lege_fremden_tresor_an(session, opfer, bucket)
    session.add(VaultBlindBucket(bucket_id=bucket, auth_verifier="9" * 64))
    session.commit()

    with _als(session, opfer) as client:
        res = client.post(
            "/api/vault/blind-register",
            json={"bucket_id": bucket, "auth_token": "8" * 64},
        )
    app.dependency_overrides.clear()

    assert res.status_code == 409
    session.expire_all()
    assert session.get(VaultBlindBucket, bucket).auth_verifier == "9" * 64


# ─────────────────────────────────────────────────────────────────────────────
# V4 — Revisions-Wasserzeichen: kein Ueberspringen ungesehener Eintraege
# ─────────────────────────────────────────────────────────────────────────────


def test_server_revision_ueberspringt_keine_ungesehenen_eintraege(test_db, anonym):
    """Exploit (Race): `entries` und `max(revision)` waren zwei getrennte
    Abfragen. Committete ein zweites Geraet dazwischen, meldete der Server ein
    `server_revision`, zu dem er den passenden Eintrag nie ausgeliefert hatte.
    Der Client schreibt das als Wasserzeichen fort und fragt kuenftig nur noch
    `revision >` — der fremde Eintrag ist damit fuer dieses Geraet **fuer immer**
    unsichtbar. Stiller Datenverlust, kein Fehler, keine Meldung.

    Die Invariante, die das strukturell ausschliesst: `server_revision` ist nie
    hoeher als die hoechste tatsaechlich ausgelieferte Revision.
    """
    bucket = "e" * 64
    anonym.post(
        "/api/vault/blind-sync",
        json={"bucket_id": bucket, "auth_token": "2" * 64, "since_revision": 0, "mutations": []},
    )

    session, _, _ = test_db
    for rev, kennung in ((10, "alt-1"), (20, "alt-2")):
        session.add(
            VaultEntry(
                id=kennung,
                bucket_id=bucket,
                ciphertext=f"sv-vault-v1:{kennung}",
                revision=rev,
                is_deleted=False,
            )
        )
    session.commit()

    # Der Client kennt Stand 10 und holt ab dort.
    res = anonym.post(
        "/api/vault/blind-sync",
        json={"bucket_id": bucket, "auth_token": "2" * 64, "since_revision": 10, "mutations": []},
    )
    assert res.status_code == 200
    daten = res.json()
    geliefert = {e["revision"] for e in daten["entries"]}
    assert daten["server_revision"] <= max(geliefert, default=10), (
        "Wasserzeichen zeigt hinter Eintraege, die nie geliefert wurden"
    )

    # Und ohne Nachschub bleibt das Wasserzeichen stehen, statt zu wandern.
    leer = anonym.post(
        "/api/vault/blind-sync",
        json={"bucket_id": bucket, "auth_token": "2" * 64, "since_revision": 20, "mutations": []},
    )
    assert leer.json()["server_revision"] == 20


def test_revisionen_bleiben_innerhalb_eines_buckets_eindeutig(test_db, anonym):
    """Zwei Eintraege mit derselben Revision sind eine Luecke im Wasserzeichen:
    wer den einen sieht und fortschreibt, verliert den anderen."""
    bucket = "9" * 64
    anonym.post(
        "/api/vault/blind-sync",
        json={"bucket_id": bucket, "auth_token": "5" * 64, "since_revision": 0, "mutations": []},
    )
    for lauf in range(3):
        anonym.post(
            "/api/vault/blind-sync",
            json={
                "bucket_id": bucket,
                "auth_token": "5" * 64,
                "since_revision": 0,
                "mutations": [
                    {
                        "id": f"e-{lauf}-{i}",
                        "ciphertext": "sv-vault-v1:x",
                        "revision": 1,
                        "is_deleted": False,
                    }
                    for i in range(3)
                ],
            },
        )

    session, _, _ = test_db
    session.expire_all()
    revisionen = [e.revision for e in session.query(VaultEntry).filter_by(bucket_id=bucket).all()]
    assert len(revisionen) == len(set(revisionen)), f"Doppelte Revisionen: {sorted(revisionen)}"
