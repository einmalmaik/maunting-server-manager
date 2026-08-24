from sqlalchemy.orm import Session

from database import SessionLocal
from models import PanelSetting


class PanelSettingsService:
    """Panel-Einstellungen aus der Datenbank mit In-Memory-Cache.

    DB-Werte haben Vorrang vor Umgebungsvariablen.
    """

    _cache: dict[str, str] = {}
    _cache_loaded: bool = False

    @classmethod
    def _load_cache(cls, db: Session | None = None) -> None:
        if cls._cache_loaded:
            return
        if db is not None:
            for row in db.query(PanelSetting).all():
                cls._cache[row.key] = row.value
            cls._cache_loaded = True
            return
        db_session = SessionLocal()
        try:
            for row in db_session.query(PanelSetting).all():
                cls._cache[row.key] = row.value
            cls._cache_loaded = True
        finally:
            db_session.close()

    @classmethod
    def get(cls, key: str, default: str = "", db: Session | None = None) -> str:
        cls._load_cache(db)
        return cls._cache.get(key, default)

    @classmethod
    def set(cls, key: str, value: str, db: Session | None = None) -> None:
        cls._cache[key] = value
        if db is not None:
            row = db.query(PanelSetting).filter_by(key=key).first()
            if row:
                row.value = value
            else:
                db.add(PanelSetting(key=key, value=value))
            return
        db_session = SessionLocal()
        try:
            row = db_session.query(PanelSetting).filter_by(key=key).first()
            if row:
                row.value = value
            else:
                db_session.add(PanelSetting(key=key, value=value))
            db_session.commit()
        finally:
            db_session.close()

    @classmethod
    def get_all(cls, db: Session | None = None) -> dict[str, str]:
        cls._load_cache(db)
        return dict(cls._cache)

    @classmethod
    def invalidate_cache(cls) -> None:
        cls._cache_loaded = False
        cls._cache.clear()

