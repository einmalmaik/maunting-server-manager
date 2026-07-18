from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models import JwtBlacklist


# Fallback-Ablaufzeit fuer Blacklist-Eintraege, falls kein exp im Token vorhanden war.
_DEFAULT_BLACKLIST_TTL_DAYS = 7


def blacklist_jwt(db: Session, jti: str, user_id: int | None, expires_at: datetime | None) -> None:
    """Speichert einen JWT-JTI in der zentralen PostgreSQL-Blacklist.

    Falls expires_at None ist, wird ein Default-TTL verwendet, damit der
    Eintrag nicht fuer immer in der Datenbank verbleibt.
    """
    resolved_expires = expires_at or (datetime.now(timezone.utc) + timedelta(days=_DEFAULT_BLACKLIST_TTL_DAYS))
    entry = JwtBlacklist(jti=jti, user_id=user_id, expires_at=resolved_expires)
    db.add(entry)
    db.commit()


def is_jwt_blacklisted(db: Session, jti: str) -> bool:
    """Prueft ob ein JWT-JTI in der Blacklist vorhanden ist."""
    return db.query(JwtBlacklist).filter(JwtBlacklist.jti == jti).first() is not None


def cleanup_expired_blacklist(db: Session) -> int:
    """Loescht abgelaufene Blacklist-Eintraege und gibt die Anzahl zurueck."""
    now = datetime.now(timezone.utc)
    result = (
        db.query(JwtBlacklist)
        .filter(JwtBlacklist.expires_at < now)
        .delete(synchronize_session=False)
    )
    db.commit()
    return result
