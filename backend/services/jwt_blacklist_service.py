from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models import JwtBlacklist


def blacklist_jwt(db: Session, jti: str, user_id: int, expires_at: datetime) -> None:
    """Speichert einen JWT-JTI in der SQLite-Blacklist."""
    entry = JwtBlacklist(jti=jti, user_id=user_id, expires_at=expires_at)
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
