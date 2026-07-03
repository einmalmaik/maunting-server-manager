"""Pytest fixtures for MSM backend tests.

Patches in-memory SQLite with StaticPool BEFORE any app imports
so that all DB connections share the same database.
"""
import os

# Must set env BEFORE any module imports that read settings
os.environ["MSM_DATABASE_URL"] = "sqlite:///:memory:"
os.environ["MSM_SECRET_KEY"] = "test-secret-key-32-chars-long!!!"
os.environ["MSM_DEBUG"] = "true"
os.environ["MSM_PANEL_URL"] = "http://localhost:3000"
os.environ["MSM_ACCESS_TOKEN_EXPIRE_MINUTES"] = "15"

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

# Patch database engine BEFORE app imports anything
import database as db_module
db_module.engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
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
    return "test-enc-" + plaintext.encode().hex()

def _mock_decrypt(ciphertext: str, aad: str | None = None) -> str:
    if ciphertext.startswith("test-enc-"):
        return bytes.fromhex(ciphertext[9:]).decode()
    return ciphertext

def _mock_hash_password(password: str) -> str:
    return "msm-pw-v1:test:" + _hl.sha256(password.encode()).hexdigest()

def _mock_verify_password(password: str, stored_hash: str) -> bool:
    return stored_hash == _mock_hash_password(password)

def _mock_totp_verify(secret: str, code: str, window: int = 1) -> bool:
    from tests._totp import totp_now
    return totp_now(secret) == code.strip()

DisClient.encrypt = staticmethod(_mock_encrypt)
DisClient.decrypt = staticmethod(_mock_decrypt)
DisClient.hash_password = staticmethod(_mock_hash_password)
DisClient.verify_password = staticmethod(_mock_verify_password)
DisClient.is_dis_hash = staticmethod(lambda h: h.startswith("msm-pw-v1:"))
DisClient.generate_totp_secret = staticmethod(lambda: _b64.b32encode(_sec.token_bytes(20)).decode().rstrip("="))
DisClient.verify_totp = staticmethod(_mock_totp_verify)
DisClient.build_totp_uri = staticmethod(lambda issuer, label, secret: f"otpauth://totp/{issuer}:{label}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30")
DisClient.health_check = staticmethod(lambda: True)

from main import app
from models import User, RefreshToken, Server, Role, ServerPermission
from services.auth_service import AuthService
from services.role_service import ensure_system_roles
from services.permission_catalog import SERVER_KEYS

# Create tables AFTER models are imported and registered in Base.metadata
db_module.Base.metadata.create_all(bind=db_module.engine)


@pytest.fixture(scope="function", autouse=True)
def clean_db():
    """Clean all tables and rate limit store before each test."""
    with db_module.engine.begin() as conn:
        for table in reversed(db_module.Base.metadata.sorted_tables):
            conn.execute(table.delete())
    # Reset slowapi in-memory storage between tests
    from middleware.rate_limit import limiter
    limiter.reset()
    # Built-in Rollen (admin/user) bei jedem Test bereitstellen.
    from services.install_update_lock_service import reset_install_update_lock_for_tests
    from services.server_lifecycle_service import reset_lifecycle_jobs_for_tests
    from services.panel_settings_service import PanelSettingsService
    reset_install_update_lock_for_tests()
    reset_lifecycle_jobs_for_tests()
    # PanelSettingsService hat einen In-Memory-Cache — ohne invalidate_cache
    # leaken Werte zwischen Tests (z. B. oauth.allow_registration=true aus
    # einem frueheren Test).
    PanelSettingsService.invalidate_cache()
    session = db_module.SessionLocal()
    try:
        ensure_system_roles(session)
    finally:
        session.close()
    yield


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
    user = AuthService.create_owner(db, "owner", "owner@test.de", "OwnerPass123!")
    db.refresh(user)
    return user


@pytest.fixture
def regular_user(db: Session) -> User:
    user = AuthService.create_user(db, "user1", "user1@test.de", "UserPass123!")
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def inactive_user(db: Session) -> User:
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
    return perms
