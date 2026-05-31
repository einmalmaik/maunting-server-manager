import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models import EmailVerification


class EmailVerificationService:
    @staticmethod
    def _hash_code(code: str) -> str:
        return hashlib.sha256(code.encode()).hexdigest()

    @staticmethod
    def generate_code() -> str:
        """Generiert einen 6-stelligen numerischen Code."""
        return secrets.randbelow(900000) + 100000  # 100000 - 999999

    @staticmethod
    def create_verification(db: Session, email: str, purpose: str) -> str:
        """Erstellt einen neuen Verifikations-Code. Loescht vorherige Codes fuer dieselbe Email+Purpose."""
        # Alte Codes loeschen
        db.query(EmailVerification).filter(
            EmailVerification.email == email,
            EmailVerification.purpose == purpose,
        ).delete(synchronize_session=False)

        plain_code = str(EmailVerificationService.generate_code())
        code_hash = EmailVerificationService._hash_code(plain_code)

        ev = EmailVerification(
            email=email,
            code_hash=code_hash,
            purpose=purpose,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        db.add(ev)
        db.commit()
        return plain_code

    @staticmethod
    def create_verification_if_needed(db: Session, email: str, purpose: str) -> str | None:
        """Erstellt nur dann einen Code, wenn kein gueltiger unverbrauchter Code existiert."""
        if EmailVerificationService.has_active_verification(db, email, [purpose]):
            return None

        return EmailVerificationService.create_verification(db, email, purpose)

    @staticmethod
    def has_active_verification(db: Session, email: str, purposes: list[str]) -> bool:
        """Prueft, ob fuer eine E-Mail ein gueltiger unverbrauchter Code existiert."""
        return db.query(EmailVerification).filter(
            EmailVerification.email == email,
            EmailVerification.purpose.in_(purposes),
            EmailVerification.verified == False,
            EmailVerification.expires_at > datetime.now(timezone.utc),
        ).first() is not None

    @staticmethod
    def verify_code_for_purposes(db: Session, email: str, purposes: list[str], code: str) -> bool:
        """Prueft einen Code gegen mehrere erlaubte Zwecke."""
        for purpose in purposes:
            if EmailVerificationService.verify_code(db, email, purpose, code):
                return True
        return False

    @staticmethod
    def verify_code(db: Session, email: str, purpose: str, code: str) -> bool:
        """Prueft einen Verifikations-Code. Gibt True zurueck wenn gueltig."""
        code_hash = EmailVerificationService._hash_code(code)
        ev = db.query(EmailVerification).filter(
            EmailVerification.email == email,
            EmailVerification.purpose == purpose,
            EmailVerification.code_hash == code_hash,
            EmailVerification.verified == False,
            EmailVerification.expires_at > datetime.now(timezone.utc),
        ).first()

        if not ev:
            return False

        ev.verified = True
        db.commit()
        return True

    @staticmethod
    def cleanup_expired(db: Session) -> int:
        """Entfernt abgelaufene Verifikations-Codes. Gibt Anzahl geloeschter zurueck."""
        result = db.query(EmailVerification).filter(
            EmailVerification.expires_at < datetime.now(timezone.utc),
        ).delete(synchronize_session=False)
        db.commit()
        return result
