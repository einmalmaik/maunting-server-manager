from database import SessionLocal
from models import PanelSetting


class PanelSettingsService:
    """Panel-Einstellungen aus der Datenbank mit In-Memory-Cache.

    DB-Werte haben Vorrang vor Umgebungsvariablen.
    """

    _cache: dict[str, str] = {}
    _cache_loaded: bool = False

    @classmethod
    def _load_cache(cls) -> None:
        if cls._cache_loaded:
            return
        db = SessionLocal()
        try:
            for row in db.query(PanelSetting).all():
                cls._cache[row.key] = row.value
        finally:
            db.close()
        cls._cache_loaded = True

    @classmethod
    def get(cls, key: str, default: str = "") -> str:
        cls._load_cache()
        return cls._cache.get(key, default)

    @classmethod
    def set(cls, key: str, value: str) -> None:
        db = SessionLocal()
        try:
            row = db.query(PanelSetting).filter_by(key=key).first()
            if row:
                row.value = value
            else:
                db.add(PanelSetting(key=key, value=value))
            db.commit()
        finally:
            db.close()
        cls._cache[key] = value

    @classmethod
    def get_all(cls) -> dict[str, str]:
        cls._load_cache()
        return dict(cls._cache)

    @classmethod
    def invalidate_cache(cls) -> None:
        cls._cache_loaded = False
        cls._cache.clear()
