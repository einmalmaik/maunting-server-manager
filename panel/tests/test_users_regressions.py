from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import users as users_api
from app.database import Base
from app.models import User
from app.permissions import P_USERS_VIEW


def _make_session(tmp_path):
    db_path = tmp_path / "users.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    return SessionLocal


def test_non_owner_cannot_change_user_permissions(tmp_path):
    SessionLocal = _make_session(tmp_path)

    with SessionLocal() as db:
        admin = User(
            username="admin",
            email="admin@example.com",
            password_hash="x",
            role="admin",
            permissions=None,
            is_active=True,
        )
        target = User(
            username="target",
            email="target@example.com",
            password_hash="x",
            role="user",
            permissions=None,
            is_active=True,
        )
        db.add_all([admin, target])
        db.commit()
        db.refresh(admin)
        db.refresh(target)

        with pytest.raises(HTTPException) as exc:
            users_api.update_user(
                target.id,
                users_api.UpdateUserBody(permissions=[P_USERS_VIEW]),
                current_user=admin,
                db=db,
            )

        assert exc.value.status_code == 403
        assert exc.value.detail == "Only the owner can change permissions."


def test_non_owner_cannot_create_user_with_custom_permissions(tmp_path):
    SessionLocal = _make_session(tmp_path)

    with SessionLocal() as db:
        admin = User(
            username="admin",
            email="admin@example.com",
            password_hash="x",
            role="admin",
            permissions=None,
            is_active=True,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        with pytest.raises(HTTPException) as exc:
            users_api.create_user(
                users_api.CreateUserBody(
                    username="new-user",
                    email="new-user@example.com",
                    password="password123",
                    role="user",
                    permissions=[P_USERS_VIEW],
                ),
                current_user=admin,
                db=db,
            )

        assert exc.value.status_code == 403
        assert exc.value.detail == "Only the owner can set custom permissions."


def test_admin_accounts_ignore_custom_permission_payload_and_use_role_defaults(tmp_path):
    SessionLocal = _make_session(tmp_path)

    with SessionLocal() as db:
        owner = User(
            username="owner",
            email="owner@example.com",
            password_hash="x",
            role="owner",
            permissions=None,
            is_active=True,
        )
        db.add(owner)
        db.commit()
        db.refresh(owner)

        response = users_api.create_user(
            users_api.CreateUserBody(
                username="admin-two",
                email="admin-two@example.com",
                password="password123",
                role="admin",
                permissions=[P_USERS_VIEW],
            ),
            current_user=owner,
            db=db,
        )
        created_user = db.query(User).filter(User.username == "admin-two").one()

        assert response["user"]["role"] == "admin"
        assert created_user.permissions is None
