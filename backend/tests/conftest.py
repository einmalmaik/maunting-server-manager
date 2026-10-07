"""Pytest fixtures for MSM backend tests.

Die Suite laeuft auf PostgreSQL — derselben Datenbank wie der Betrieb. Jeder
xdist-Worker legt sich eine eigene, leere Datenbank an, bevor irgendein Modul
des Panels geladen wird, und alle Sitzungen eines Workers teilen sich ueber
``StaticPool`` **eine** Verbindung dorthin: Anfragen des TestClient,
Hintergrundthreads und der Lifespan sehen damit dieselben Zeilen wie der Test.
"""
import atexit
import os
import re
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool, StaticPool

# ── Test-Datenbank ─────────────────────────────────────────────────────
# `MSM_TEST_DATABASE_URL` zeigt auf einen PostgreSQL-Server, auf dem die Suite
# Datenbanken anlegen darf (Recht CREATEDB) — nie auf die Panel-Datenbank.
# Angelegt und geloescht werden ausschliesslich Datenbanken `msm_test_…`; in
# die Datenbank der URL selbst wird nichts geschrieben. Es gibt bewusst keinen
# Rueckfall: eine Suite, die ohne PostgreSQL still auf etwas anderes auswiche,
# pruefte wieder gegen eine Datenbank, die der Betrieb nicht hat.
_ADMIN_URL = os.environ.get("MSM_TEST_DATABASE_URL", "").strip()
if not _ADMIN_URL.startswith(("postgresql://", "postgresql+psycopg2://")):
    pytest.exit(
        "MSM_TEST_DATABASE_URL fehlt oder ist keine PostgreSQL-URL. Die Tests "
        "brauchen einen PostgreSQL-Server mit dem Recht CREATEDB, z. B. "
        "postgresql://postgres@127.0.0.1:15499/postgres — scripts/test-postgres.sh "
        "startet einen passenden Wegwerf-Container.",
        returncode=4,
    )

_admin_engine = create_engine(_ADMIN_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
_LAUF = (os.environ.get("PYTEST_XDIST_TESTRUNUID") or uuid.uuid4().hex)[:8].lower()
_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "main").lower()


def _testdatenbank_name(*teile: str) -> str:
    """Der Schutzriegel: nur Namen `msm_test_…` gehen je an CREATE/DROP."""
    name = "msm_test_" + "_".join(teile)
    if not re.fullmatch(r"msm_test_[a-z0-9_]{1,54}", name):
        raise RuntimeError(f"Unzulaessiger Name fuer eine Test-Datenbank: {name!r}")
    return name


def testdatenbank_anlegen(*teile: str) -> tuple[str, str]:
    """Legt eine leere Test-Datenbank an und gibt ``(name, url)`` zurueck."""
    name = _testdatenbank_name(_LAUF, _WORKER, *teile)
    with _admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(_ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    return name, url


def testdatenbank_loeschen(name: str) -> None:
    name = _testdatenbank_name(name.removeprefix("msm_test_"))
    with _admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


_TESTDB_NAME, _TESTDB_URL = testdatenbank_anlegen()
atexit.register(testdatenbank_loeschen, _TESTDB_NAME)

# Must set env BEFORE any module imports that read settings
os.environ["MSM_DATABASE_URL"] = _TESTDB_URL
os.environ["MSM_SECRET_KEY"] = "test-secret-key-32-chars-long!!!"
os.environ["MSM_DEBUG"] = "true"
os.environ["MSM_TESTING"] = "true"
# AWS-Test-Credentials fuer moto (S3-Mocking)
os.environ["AWS_ACCESS_KEY_ID"] = "AKIAIOSFODNN7EXAMPLE"
os.environ["AWS_SECRET_ACCESS_KEY"] = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["MSM_PANEL_URL"] = "http://localhost:3000"
os.environ["MSM_ACCESS_TOKEN_EXPIRE_MINUTES"] = "15"

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# Patch database engine BEFORE app imports anything
import database as db_module
from config import settings as _settings_fuer_db

# Eine `backend/.env` darf die Test-Datenbank nicht ueberstimmen: wer die URL
# aus den Einstellungen liest (Backup, Alembic), muss hier landen.
_settings_fuer_db.database_url = _TESTDB_URL
db_module.engine = create_engine(
    _TESTDB_URL,
    poolclass=StaticPool,
    # Zeitstempel kommen im Betrieb als UTC zurueck; der Test soll nicht von
    # der Zeitzone des Rechners abhaengen, auf dem der Server laeuft.
    connect_args={"options": "-c timezone=UTC"},
)
db_module.SessionLocal = db_module.sessionmaker(
    autocommit=False, autoflush=False, bind=db_module.engine
)

# ── DIS Sidecar mock (tests use local crypto, no Node required) ────────
# Production code calls DisClient for all crypto. In tests we patch the
# static methods with simple reversible operations so no Node sidecar is
# needed. TOTP uses a standard-library implementation (tests/_totp.py).
import base64 as _b64
import hashlib as _hl
import secrets as _sec

from services.dis_client import DisClient

def _mock_encrypt(plaintext: str, aad: str | None = None) -> str:
    aad_str = aad if aad is not None else ""
    return "test-enc-v1:" + aad_str.encode().hex() + ":" + plaintext.encode().hex()

def _mock_decrypt(ciphertext: str, aad: str | None = None) -> str:
    from services.dis_client import DisDecryptionError
    if ciphertext.startswith("test-enc-v1:"):
        parts = ciphertext.split(":")
        if len(parts) == 3:
            expected_aad = aad if aad is not None else ""
            try:
                stored_aad = bytes.fromhex(parts[1]).decode()
            except Exception:
                raise DisDecryptionError("DIS Decryption failed")
            if stored_aad != expected_aad:
                raise DisDecryptionError("DIS Decryption AAD mismatch")
            try:
                return bytes.fromhex(parts[2]).decode()
            except Exception:
                raise DisDecryptionError("DIS Decryption failed")
        raise DisDecryptionError("DIS Decryption invalid format")
    if ciphertext.startswith("test-enc-"):
        try:
            return bytes.fromhex(ciphertext[9:]).decode()
        except Exception:
            raise DisDecryptionError("DIS Decryption failed")
    raise DisDecryptionError("DIS Decryption failed")

def _mock_hash_password(password: str) -> str:
    return "msm-pw-v1:test:" + _hl.sha256(password.encode()).hexdigest() + ":v2"

def _mock_wrap_legacy_password(passlib_hash: str) -> str:
    # Wie der Sidecar: nur passlib-Argon2id, sonst DisKeinAltHash.
    from services.dis_client import DisKeinAltHash

    if not passlib_hash.startswith("$argon2id$v=19$"):
        raise DisKeinAltHash("Kein passlib-Argon2id-Hash")
    return "msm-pw-v1:test-alt:" + passlib_hash.replace(":", "") + ":alt.test"

def _mock_verify_password(password: str, stored_hash: str) -> bool:
    if stored_hash.startswith("msm-pw-v1:test-alt:"):
        from passlib.context import CryptContext

        alt = stored_hash[len("msm-pw-v1:test-alt:"):-len(":alt.test")]
        return CryptContext(schemes=["argon2"]).verify(password, alt)
    return stored_hash == _mock_hash_password(password)

def _mock_totp_schritt(secret: str, code: str) -> int | None:
    """Wie `/totp/verify` im Sidecar: der getroffene Schritt (aktueller ±1) oder None."""
    from tests._totp import totp_schritt
    return totp_schritt(secret, code)

# Die echte Methode, fuer Tests, die ihre Pruefung des Sidecar-Ergebnisses brauchen.
ECHTES_ENCRYPT = DisClient.encrypt
DisClient.encrypt = staticmethod(_mock_encrypt)
DisClient.decrypt = staticmethod(_mock_decrypt)
# Woran `DisText` Chiffrat von Altbestand unterscheidet: die Form von
# `_mock_encrypt`, nicht die echte `msm-dis-v1:`.
DisClient.PRAEFIX = "test-enc-v1:"


def _mock_decrypt_many(items):
    from services.dis_client import DisDecryptionError

    ergebnis = []
    for ciphertext, aad in items:
        try:
            ergebnis.append(_mock_decrypt(ciphertext, aad or None))
        except DisDecryptionError:
            ergebnis.append(None)
    return ergebnis


DisClient.decrypt_many = staticmethod(_mock_decrypt_many)
DisClient.blind_index = staticmethod(
    lambda werte: [_hl.sha256(b"test-index:" + w.encode()).hexdigest() for w in werte]
)
DisClient.hash_password = staticmethod(_mock_hash_password)
DisClient.verify_password = staticmethod(_mock_verify_password)
DisClient.wrap_legacy_password = staticmethod(_mock_wrap_legacy_password)
DisClient.is_dis_hash = staticmethod(lambda h: h.startswith("msm-pw-v1:"))
DisClient.generate_totp_secret = staticmethod(lambda: _b64.b32encode(_sec.token_bytes(20)).decode().rstrip("="))
DisClient.totp_schritt = staticmethod(_mock_totp_schritt)
DisClient.build_totp_uri = staticmethod(lambda issuer, label, secret: f"otpauth://totp/{issuer}:{label}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30")
DisClient.health_check = staticmethod(lambda: True)

# ── DIS Streaming Crypto mock (backup init-key / encrypt / decrypt / invalidate) ──
# BackupCryptoService ruft die DIS-Streaming-Endpunkte direkt via httpx auf.
# In Tests mocken wir httpx.post (fuer init-key/invalidate-key) und httpx.stream
# (fuer encrypt-stream/decrypt-stream), sodass kein Node-Sidecar noetig ist.
# Die Mock-Operationen sind reversibel (XOR 0x42) und nutzen das echte Frame-Format,
# sodass Round-Trip-Tests (VAL-DIS-022) den Frame-Parser mitueben.
import struct as _struct
import uuid as _uuid

import httpx as _httpx

_dis_streaming_keys: set[str] = set()
_MOCK_XOR_BYTE = 0x42  # simpler reversibler Mock (keine echte Krypto in Tests)
_MOCK_NONCE = b"\x00" * 12  # 12-Byte Nonce (Mock)
_MOCK_FRAME_LEN = 4  # 4-Byte BE uint32 Laengenfeld
_MOCK_TAG_LEN = 4  # 4-Byte Mock-Auth-Tag (echtes DIS: 16 Byte GCM tag)
_MOCK_STREAM_CHUNK = 64 * 1024  # 64 KiB wie echtes DIS


def _mock_encrypt_frames(plaintext: bytes) -> bytes:
    """Produziert Frames im DIS-Format aus Plaintext (Mock: XOR +Checksum-Tag)."""
    out = bytearray()
    for i in range(0, len(plaintext), _MOCK_STREAM_CHUNK):
        chunk = plaintext[i:i + _MOCK_STREAM_CHUNK]
        ct = bytes(b ^ _MOCK_XOR_BYTE for b in chunk)
        tag = _hl.sha256(chunk).digest()[:_MOCK_TAG_LEN]  # Mock-Auth-Tag
        frame_len = 12 + len(ct) + _MOCK_TAG_LEN
        out += _struct.pack(">I", frame_len)
        out += _MOCK_NONCE
        out += ct
        out += tag
    return bytes(out)


def _mock_decrypt_frames(encrypted: bytes) -> bytes:
    """Parst DIS-Frames und gibt Plaintext zurueck (Mock: XOR +Tag-Verifikation)."""
    out = bytearray()
    off = 0
    while off < len(encrypted):
        if off + _MOCK_FRAME_LEN > len(encrypted):
            raise ValueError("TruncatedFrameLength")
        frame_len = _struct.unpack(">I", encrypted[off:off + _MOCK_FRAME_LEN])[0]
        off += _MOCK_FRAME_LEN
        if frame_len < 12 + _MOCK_TAG_LEN:
            raise ValueError("InvalidFrameLength")
        if off + frame_len > len(encrypted):
            raise ValueError("TruncatedFrame")
        ct = encrypted[off + 12:off + frame_len - _MOCK_TAG_LEN]
        tag = encrypted[off + frame_len - _MOCK_TAG_LEN:off + frame_len]
        off += frame_len
        pt = bytes(b ^ _MOCK_XOR_BYTE for b in ct)
        expected_tag = _hl.sha256(pt).digest()[:_MOCK_TAG_LEN]
        if tag != expected_tag:
            raise ValueError("AuthTagMismatch")  # Tamper erkannt
        out += pt
    return bytes(out)


def _extract_body(obj) -> bytes:
    """Extrahiert Bytes aus file-like, bytes oder iterable."""
    if obj is None:
        return b""
    if hasattr(obj, "read"):
        return obj.read()
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj)
    if hasattr(obj, "__iter__"):
        return b"".join(obj)
    return b""


class _MockStreamResponse:
    """Mock httpx StreamingResponse mit iter_bytes und raise_for_status."""

    def __init__(self, status_code: int, body: bytes, content_type: str = "application/octet-stream"):
        self.status_code = status_code
        self._body = body
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _httpx.HTTPStatusError(
                "DIS mock error", request=_httpx.Request("POST", "http://mock"), response=_httpx.Response(self.status_code)
            )

    def iter_bytes(self, chunk_size: int = 1024 * 64):
        if self._body:
            yield self._body

    @property
    def content(self):
        return self._body


class _MockStreamCM:
    """Context-Manager fuer httpx.stream Mock."""

    def __init__(self, response: _MockStreamResponse):
        self._response = response

    def __enter__(self):
        return self._response

    def __exit__(self, *exc):
        return False


_original_httpx_post = _httpx.post
_original_httpx_stream = _httpx.stream


def _mock_httpx_post(url: str, *args, **kwargs):
    """Intercept DIS /backup/init-key und /backup/invalidate-key."""
    if "/backup/init-key" in url:
        import json as _json
        body = kwargs.get("json") or {}
        password = body.get("password")
        salt = body.get("salt")
        if not password or not salt:
            return _httpx.Response(400, json={"error": "MissingPassword"})
        key_id = str(_uuid.uuid4())
        _dis_streaming_keys.add(key_id)
        return _httpx.Response(200, json={"key_id": key_id})
    if "/backup/invalidate-key" in url:
        body = kwargs.get("json") or {}
        key_id = body.get("key_id")
        if not key_id:
            return _httpx.Response(400, json={"error": "MissingKeyId"})
        _dis_streaming_keys.discard(key_id)
        return _httpx.Response(200, json={"ok": True})
    return _original_httpx_post(url, *args, **kwargs)


def _mock_httpx_stream(method: str, url: str, *args, **kwargs):
    """Intercept DIS /backup/encrypt-stream und /backup/decrypt-stream."""
    if "/backup/encrypt-stream" in url:
        headers = kwargs.get("headers") or {}
        key_id = headers.get("X-Backup-Key-Id") or headers.get("x-backup-key-id")
        if not key_id or key_id not in _dis_streaming_keys:
            return _MockStreamCM(_MockStreamResponse(400, b'{"error":"KeyNotFound"}', "application/json"))
        plaintext = _extract_body(kwargs.get("content") or kwargs.get("data"))
        encrypted = _mock_encrypt_frames(plaintext)
        return _MockStreamCM(_MockStreamResponse(200, encrypted))
    if "/backup/decrypt-stream" in url:
        headers = kwargs.get("headers") or {}
        key_id = headers.get("X-Backup-Key-Id") or headers.get("x-backup-key-id")
        if not key_id or key_id not in _dis_streaming_keys:
            return _MockStreamCM(_MockStreamResponse(400, b'{"error":"KeyNotFound"}', "application/json"))
        encrypted = _extract_body(kwargs.get("content") or kwargs.get("data"))
        try:
            plaintext = _mock_decrypt_frames(encrypted)
        except Exception:
            return _MockStreamCM(_MockStreamResponse(400, b'{"error":"DecryptionFailed"}', "application/json"))
        return _MockStreamCM(_MockStreamResponse(200, plaintext))
    return _original_httpx_stream(method, url, *args, **kwargs)


_httpx.post = _mock_httpx_post
_httpx.stream = _mock_httpx_stream


@pytest.fixture(autouse=True)
def _reset_dis_streaming_keys():
    """Reset DIS-Streaming-Mock Keys vor jedem Test."""
    _dis_streaming_keys.clear()
    yield


@pytest.fixture(scope="session", autouse=True)
def _versionsspeicher_umlenken(tmp_path_factory):
    """Der Dateiversionsspeicher darf nie im Arbeitsverzeichnis landen.

    `file_history_service._root()` baut seinen Pfad aus
    `settings.panel_config_dir`, und der zeigt im Test auf `backend/`. Jeder
    Test, der ueber `write_server_text`/`delete_server_text` oder ueber einen
    ausgefuehrten KI-Vorschlag einen Schnappschuss anlegt, schrieb damit nach
    `backend/.msm-file-history/` — in **echte, versionierte** Daten hinein.
    Beobachtet: ein Testlauf legte drei Versionen zu Server 1 an und
    verdraengte dabei die eine, die im Repository liegt (`git status` meldete
    sie danach als geloescht).

    Einzeln gefahren faellt das niemandem auf. Der Schaden entsteht still und
    wird erst beim naechsten Commit sichtbar — oder gar nicht.

    Deshalb sitzt die Umlenkung hier und nicht in den einzelnen Dateien: sie
    muss auch fuer Tests gelten, die von diesem Weg gar nichts wissen. Wer
    den Speicher selbst pruefen will, lenkt in seiner eigenen Fixture erneut
    um (`test_file_history_service.py` tut das) — das gewinnt, weil es spaeter
    greift.
    """
    from config import settings as _settings

    _settings.panel_config_dir = str(tmp_path_factory.mktemp("panel-config"))
    yield


from main import app
from models import User, RefreshToken, Server, Role, ServerPermission
from services.auth_service import AuthService
from services.role_service import ensure_system_roles
from services.permission_catalog import SERVER_KEYS

# Create tables AFTER models are imported and registered in Base.metadata
db_module.Base.metadata.create_all(bind=db_module.engine)

# Das Leeren zwischen den Tests laeuft als eine Funktion in der Datenbank:
# ein Aufruf statt einer Anweisung je Tabelle. `TRUNCATE` ueber alle Tabellen
# kostete gemessen 248 ms je Test (es legt jede Tabelle und jeden Index neu
# an), diese Funktion rund 6 ms. Kinder vor Eltern, damit kein Fremdschluessel
# im Weg steht; die Sequenzen gehen auf 1 zurueck wie nach `RESTART IDENTITY`,
# aber nur die, die seit dem letzten Leeren benutzt wurden. Kommt doch ein
# Fremdschluessel quer, faellt sie auf das langsame `TRUNCATE` zurueck.
_TABELLEN_KINDER_ZUERST = [t.name for t in reversed(db_module.Base.metadata.sorted_tables)]
# Roh ueber den Treiber: ohne Parameter formatiert psycopg2 die `%I` nicht um.
_roh = db_module.engine.raw_connection()
with _roh.driver_connection.cursor() as _cursor:
    _cursor.execute(
        """
        CREATE FUNCTION msm_test_leeren(tabellen text[]) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE
            tabelle text;
            sequenz record;
        BEGIN
            BEGIN
                FOREACH tabelle IN ARRAY tabellen LOOP
                    EXECUTE format('DELETE FROM %I', tabelle);
                END LOOP;
            EXCEPTION WHEN foreign_key_violation THEN
                EXECUTE 'TRUNCATE ' || (
                    SELECT string_agg(format('%I', t), ', ') FROM unnest(tabellen) AS t
                ) || ' CASCADE';
            END;
            FOR sequenz IN
                SELECT schemaname, sequencename FROM pg_sequences WHERE last_value IS NOT NULL
            LOOP
                PERFORM setval(format('%I.%I', sequenz.schemaname, sequenz.sequencename), 1, false);
            END LOOP;
        END $$;
        """
    )
_roh.driver_connection.commit()
_roh.close()


@pytest.fixture(scope="session", autouse=True)
def _testdatenbank_aufraeumen():
    """Die Worker-Datenbank ueberlebt den Lauf nicht (`atexit` ist der Rueckfall)."""
    yield
    db_module.engine.dispose()
    testdatenbank_loeschen(_TESTDB_NAME)


@pytest.fixture
def pg_wegwerf():
    """Leere Wegwerf-Datenbanken fuer Tests, die ein eigenes Schema bauen.

    Migrationstests brauchen eine Datenbank, die nur ihnen gehoert: sie legen
    das Schema mit `create_all` an, stempeln, fahren Alembic ab- und aufwaerts.
    ``pg_wegwerf("kette")`` gibt die URL einer frischen Datenbank zurueck; nach
    dem Test wird jede so angelegte wieder geloescht, auch wenn der Test noch
    Verbindungen offen haelt.
    """
    angelegt: list[str] = []

    def anlegen(zweck: str = "db") -> str:
        name, url = testdatenbank_anlegen(re.sub(r"[^a-z0-9]+", "_", zweck.lower())[:16], uuid.uuid4().hex[:8])
        angelegt.append(name)
        return url

    yield anlegen
    for name in angelegt:
        testdatenbank_loeschen(name)


@pytest.fixture(autouse=True)
def _eine_verbindung_kein_nebenlauf(request, monkeypatch):
    """Die Suite teilt je Worker eine Verbindung — Nebenlauf darauf ist keiner.

    Im Betrieb holt sich jeder Laufbeginn und jedes Lesewerkzeug eine eigene
    Verbindung aus dem Pool und darf deshalb zu acht nebeneinander laufen. Hier
    teilen sich alle Sitzungen eine Verbindung (`StaticPool`); zwei Transaktionen
    darauf waeren kein Nebenlauf, sondern ein Datenfehler. Tests, die genau die
    Breite pruefen, tragen `@pytest.mark.echte_nebenlaeufigkeit`.
    """
    if request.node.get_closest_marker("echte_nebenlaeufigkeit"):
        return
    # Ueber den Wrapper: er setzt Paket und eigene Kopie zugleich.
    from services import ai_stream_service
    from services.ai_voice import realtime_session

    monkeypatch.setattr(ai_stream_service, "_anlauf_nebenlaeufigkeit", lambda: 1)
    monkeypatch.setattr(ai_stream_service, "_werkzeug_nebenlaeufigkeit", lambda: 1)
    monkeypatch.setattr(realtime_session, "_werkzeug_nebenlaeufigkeit", lambda: 1)


@pytest.fixture(scope="function", autouse=True)
def clean_db():
    """Clean all tables and rate limit store before each test."""
    from sqlalchemy.orm import close_all_sessions
    close_all_sessions()
    # Alle Tabellen leeren und die Sequenzen zuruecksetzen, damit jeder Test
    # bei denselben IDs beginnt, egal was vor ihm lief (`msm_test_leeren`
    # oben). Das `rollback()` davor raeumt eine Transaktion ab, die ein
    # vorheriger Test nach einem SQL-Fehler abgebrochen liegen liess — auf der
    # geteilten Verbindung wuerde sonst jede weitere Anweisung scheitern.
    raw_conn = db_module.engine.raw_connection()
    try:
        raw_dbapi = raw_conn.driver_connection
        raw_dbapi.rollback()
        with raw_dbapi.cursor() as cursor:
            cursor.execute("SELECT msm_test_leeren(%s)", (_TABELLEN_KINDER_ZUERST,))
        raw_dbapi.commit()
    finally:
        raw_conn.close()
    # Reset slowapi in-memory storage between tests
    from middleware.rate_limit import limiter
    limiter.reset()
    # Built-in Rollen (admin/user) bei jedem Test bereitstellen.
    from services.install_update_lock_service import reset_install_update_lock_for_tests
    from services.server_lifecycle_service import reset_lifecycle_jobs_for_tests
    from services.panel_settings_service import PanelSettingsService
    from services.port_check_service import reset_port_cache_for_tests
    reset_install_update_lock_for_tests()
    reset_lifecycle_jobs_for_tests()
    # Der Listener-Schnappschuss lebt eine Sekunde — laenger als mancher Test.
    # Ohne das Verwerfen entschiede die Laufzeit des vorigen Tests mit.
    reset_port_cache_for_tests()
    # PanelSettingsService hat einen In-Memory-Cache — ohne invalidate_cache
    # leaken Werte zwischen Tests (z. B. oauth.allow_registration=true aus
    # einem frueheren Test).
    PanelSettingsService.invalidate_cache()
    # Der Vektorspeicher des Gedächtnisabrufs hält Bereiche über Tests hinweg;
    # die Tabellen sind geleert, dieselben Kennungen kommen wieder.
    from services import ai_gedaechtnis_abruf
    ai_gedaechtnis_abruf.leeren()
    PanelSettingsService.set("captcha_enabled", "false")
    session = db_module.SessionLocal()
    try:
        ensure_system_roles(session)
    finally:
        session.close()
    yield
    close_all_sessions()


@pytest.fixture
def db() -> Session:
    """Yield a fresh DB session, rolled back after each test."""
    session = db_module.SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client(db: Session) -> TestClient:
    """FastAPI test client with DB override."""
    def override_get_db():
        yield db

    app.dependency_overrides["get_db"] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── User fixtures ──

@pytest.fixture
def owner_user(db: Session) -> User:
    existing = db.query(User).filter(User.username == "owner").first()
    if existing:
        db.refresh(existing)
        return existing
    user = AuthService.create_owner(db, "owner", "owner@test.de", "OwnerPass123!")
    db.refresh(user)
    return user


@pytest.fixture
def regular_user(db: Session) -> User:
    existing = db.query(User).filter(User.username == "user1").first()
    if existing:
        db.refresh(existing)
        return existing
    user = AuthService.create_user(db, "user1", "user1@test.de", "UserPass123!")
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def inactive_user(db: Session) -> User:
    existing = db.query(User).filter(User.username == "inactive").first()
    if existing:
        db.refresh(existing)
        return existing
    user = AuthService.create_user(db, "inactive", "inactive@test.de", "Inactive123!")
    user.is_active = False
    db.commit()
    db.refresh(user)
    return user


# ── Auth fixtures ──

@pytest.fixture
def owner_cookies(client: TestClient, owner_user: User) -> dict:
    """Login as owner and return cookies."""
    response = client.post("/api/auth/login", json={
        "username": "owner",
        "password": "OwnerPass123!",
        "otp_code": None,
    })
    assert response.status_code == 200
    return dict(response.cookies)


@pytest.fixture
def user_cookies(client: TestClient, regular_user: User) -> dict:
    """Login as regular user and return cookies."""
    response = client.post("/api/auth/login", json={
        "username": "user1",
        "password": "UserPass123!",
        "otp_code": None,
    })
    assert response.status_code == 200
    return dict(response.cookies)


@pytest.fixture
def csrf_token(owner_cookies: dict) -> str | None:
    return owner_cookies.get("__Secure-csrf_token")


@pytest.fixture
def user_csrf_token(user_cookies: dict) -> str | None:
    return user_cookies.get("__Secure-csrf_token")


# ── Server fixture ──

@pytest.fixture
def test_server(db: Session, owner_user: User) -> Server:
    """Create a test server."""
    server = Server(
        name="Test Server",
        game_type="dayz",
        install_dir="/tmp/test_server",
        container_name="msm-srv-test",
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


@pytest.fixture
def user_permission(db: Session, regular_user: User, test_server: Server) -> list[ServerPermission]:
    """Delegiert dem regular_user alle server-scoped Permissions auf test_server.

    Bewusst breit, damit bestehende Tests ("User mit Permission darf X")
    weiterhin funktionieren — wir geben einfach den vollen server.*-Satz.
    """
    perms = [
        ServerPermission(
            user_id=regular_user.id,
            server_id=test_server.id,
            permission_key=key,
        )
        for key in sorted(SERVER_KEYS)
    ]
    for p in perms:
        db.add(p)
    db.commit()
    for p in perms:
        db.refresh(p)
    try:
        yield perms
    finally:
        for p in perms:
            db.delete(p)
        db.commit()
