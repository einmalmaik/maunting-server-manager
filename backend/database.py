import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from config import settings
from database_policy import validate_panel_database_url

_DB_URL = validate_panel_database_url(
    settings.database_url,
    testing=os.getenv("MSM_TESTING", "").lower() == "true",
)
_engine_kwargs: dict = {
    "pool_pre_ping": True,
}
if _DB_URL.startswith("sqlite"):
    # SQLite uses SingletonThreadPool — extra pool kwargs crash. Concurrency is
    # serialised by the GIL + a per-call session, which is fine for tests/CLI.
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update(pool_size=10, max_overflow=20, pool_timeout=60)

engine = create_engine(_DB_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
