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
from main import app
from models import User, RefreshToken, Server, Permission
from services.auth_service import AuthService

# Create tables AFTER models are imported and registered in Base.metadata
db_module.Base.metadata.create_all(bind=db_module.engine)


@pytest.fixture(scope="function", autouse=True)
def clean_db():
    """Clean all tables and rate limit store before each test."""
    with db_module.engine.begin() as conn:
        for table in reversed(db_module.Base.metadata.sorted_tables):
            conn.execute(table.delete())
    # Reset rate limiting store between tests
    import middleware.rate_limit as rate_limit_module
    rate_limit_module._rate_limit_store.clear()
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
        linux_user="test_user",
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


@pytest.fixture
def user_permission(db: Session, regular_user: User, test_server: Server) -> Permission:
    perm = Permission(
        user_id=regular_user.id,
        server_id=test_server.id,
        can_view_console=True,
        can_view_logs=True,
        can_start=True,
        can_stop=True,
        can_restart=True,
        can_backup=True,
        can_restore=True,
        can_edit_config=True,
        can_manage_mods=True,
    )
    db.add(perm)
    db.commit()
    db.refresh(perm)
    return perm
