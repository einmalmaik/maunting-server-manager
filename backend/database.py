from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from config import settings
from database_policy import validate_panel_database_url

engine = create_engine(
    validate_panel_database_url(settings.database_url),
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=60,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
