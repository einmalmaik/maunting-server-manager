from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import deps
from app.api import account as account_api
from app.api import auth as auth_api
from app.auth import hash_password
from app.database import Base
from app.models import BackupCode, User


def _make_session(tmp_path):
    db_path = tmp_path / "auth.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    return SessionLocal


def _request(ip: str = "127.0.0.1", session: dict | None = None):
    return SimpleNamespace(headers={}, client=SimpleNamespace(host=ip), session={} if session is None else session)


def test_login_rate_limit_blocks_after_repeated_failures(tmp_path):
    SessionLocal = _make_session(tmp_path)

    with SessionLocal() as db:
        request = _request()
        body = auth_api.LoginBody(username="owner", password="wrong-password")

        for _ in range(auth_api._MAX_LOGIN_ATTEMPTS):
            with pytest.raises(Exception) as exc:
                auth_api.login(body, request=request, db=db)
            assert getattr(exc.value, "status_code", None) == 401

        with pytest.raises(Exception) as exc:
            auth_api.login(body, request=request, db=db)

        assert getattr(exc.value, "status_code", None) == 429


def test_download_backup_codes_only_once_and_marks_them_available(tmp_path):
    SessionLocal = _make_session(tmp_path)

    with SessionLocal() as db:
        user = User(
            username="owner",
            password_hash=hash_password("password123"),
            role="owner",
            is_active=True,
            totp_secret="JBSWY3DPEHPK3PXP",
            totp_enabled=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        response = account_api.download_backup_codes(user=user, db=db)
        db.refresh(user)

        assert len(response["codes"]) == account_api._BACKUP_CODE_COUNT
        assert user.backup_codes_downloaded_at is not None
        assert db.query(BackupCode).filter(BackupCode.user_id == user.id).count() == account_api._BACKUP_CODE_COUNT

        with pytest.raises(Exception) as exc:
            account_api.download_backup_codes(user=user, db=db)

        assert getattr(exc.value, "status_code", None) == 409


def test_backup_code_can_be_used_once_for_login(tmp_path):
    SessionLocal = _make_session(tmp_path)

    with SessionLocal() as db:
        user = User(
            username="owner",
            password_hash=hash_password("password123"),
            role="owner",
            is_active=True,
            totp_secret="JBSWY3DPEHPK3PXP",
            totp_enabled=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        codes = account_api.download_backup_codes(user=user, db=db)["codes"]

        login_request = _request(session={})
        login_response = auth_api.login(
            auth_api.LoginBody(username="owner", password="password123"),
            request=login_request,
            db=db,
        )
        assert login_response == {"needs_2fa": True}

        verify_request = _request(session=login_request.session)
        verify_response = auth_api.verify_2fa(
            auth_api.TwoFABody(code=codes[0]),
            request=verify_request,
            db=db,
        )

        assert verify_response["user"]["username"] == "owner"

        second_request = _request(session={})
        auth_api.login(
            auth_api.LoginBody(username="owner", password="password123"),
            request=second_request,
            db=db,
        )
        with pytest.raises(Exception) as exc:
            auth_api.verify_2fa(
                auth_api.TwoFABody(code=codes[0]),
                request=_request(session=second_request.session),
                db=db,
            )

        assert getattr(exc.value, "status_code", None) == 401


def test_get_current_user_rejects_inactive_session_and_clears_session(tmp_path):
    SessionLocal = _make_session(tmp_path)

    with SessionLocal() as db:
        user = User(
            username="owner",
            password_hash=hash_password("password123"),
            role="owner",
            is_active=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        request = _request(session={"user_id": user.id})

        with pytest.raises(HTTPException) as exc:
            deps.get_current_user(request=request, db=db)

        assert exc.value.status_code == 401
        assert request.session == {}


def test_client_ip_uses_last_forwarded_hop_from_local_proxy():
    request = SimpleNamespace(
        headers={"x-forwarded-for": "203.0.113.5, 198.51.100.22"},
        client=SimpleNamespace(host="127.0.0.1"),
        session={},
    )

    assert auth_api._client_ip(request) == "198.51.100.22"


def test_client_ip_ignores_forwarded_header_for_direct_clients():
    request = SimpleNamespace(
        headers={"x-forwarded-for": "203.0.113.5"},
        client=SimpleNamespace(host="198.51.100.22"),
        session={},
    )

    assert auth_api._client_ip(request) == "198.51.100.22"
