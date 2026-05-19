from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import setup as setup_api
from app.database import Base
from app.models import PanelSetting


def _make_session(tmp_path):
    db_path = tmp_path / "setup.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    return SessionLocal


def test_setup_status_reports_completed_when_owner_lock_exists(tmp_path):
    SessionLocal = _make_session(tmp_path)

    with SessionLocal() as db:
        db.add(PanelSetting(key=setup_api._SETUP_OWNER_LOCK_KEY, value="owner"))
        db.commit()

        response = setup_api.setup_status(db=db)

        assert response == {"needs_setup": False}


def test_create_owner_rejects_when_owner_lock_exists(tmp_path):
    SessionLocal = _make_session(tmp_path)

    with SessionLocal() as db:
        db.add(PanelSetting(key=setup_api._SETUP_OWNER_LOCK_KEY, value="owner"))
        db.commit()

        request = SimpleNamespace(session={})

        with pytest.raises(Exception) as exc:
            setup_api.create_owner(
                setup_api.SetupBody(username="owner", password="password123"),
                request=request,
                db=db,
            )

        assert getattr(exc.value, "status_code", None) == 403
        assert getattr(exc.value, "detail", None) == "Setup already completed."
