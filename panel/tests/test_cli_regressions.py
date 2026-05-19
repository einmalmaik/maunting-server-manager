from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import cli
from app.auth import verify_password
from app.database import Base
from app.models import User


def _make_session(tmp_path):
    db_path = tmp_path / "cli.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    return session_local


def test_create_admin_creates_owner_account_for_installer(tmp_path, monkeypatch):
    session_local = _make_session(tmp_path)
    monkeypatch.setattr(cli, "SessionLocal", session_local)

    assert cli.create_admin("owner", "password123", force=False) == 0

    with session_local() as db:
        user = db.scalar(select(User).where(User.username == "owner"))

    assert user is not None
    assert user.role == "owner"
    assert user.permissions is None
    assert verify_password(user.password_hash, "password123")


def test_create_admin_force_promotes_existing_user_to_owner(tmp_path, monkeypatch):
    session_local = _make_session(tmp_path)
    monkeypatch.setattr(cli, "SessionLocal", session_local)

    with session_local() as db:
        db.add(User(username="owner", password_hash="old", role="user", permissions='["files.read"]', is_active=False))
        db.commit()

    assert cli.create_admin("owner", "new-password123", force=True) == 0

    with session_local() as db:
        user = db.scalar(select(User).where(User.username == "owner"))

    assert user is not None
    assert user.role == "owner"
    assert user.permissions is None
    assert user.is_active is True
    assert verify_password(user.password_hash, "new-password123")
