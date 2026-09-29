import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models import BackupCode


class BackupCodeService:
    _CODE_COUNT = 5
    _CODE_LENGTH = 8

    @staticmethod
    def _hash_code(code: str) -> str:
        return hashlib.sha256(code.encode()).hexdigest()

    @staticmethod
    def _generate_plain_code() -> str:
        """Generiert einen 8-stelligen alphanumerischen Code im Format XXXX-XXXX."""
        chars = secrets.token_urlsafe(16)
        # Nur alphanumerisch, kein ambiguous (0, O, 1, I, l)
        allowed = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
        code = "".join(secrets.choice(allowed) for _ in range(8))
        return f"{code[:4]}-{code[4:]}"

    @staticmethod
    def generate_backup_codes(db: Session, user_id: int) -> list[str]:
        """Erzeugt 5 neue Backup-Codes. Loescht vorher alle alten Codes."""
        db.query(BackupCode).filter(BackupCode.user_id == user_id).delete(synchronize_session=False)
        db.commit()

        plain_codes = []
        for _ in range(BackupCodeService._CODE_COUNT):
            plain = BackupCodeService._generate_plain_code()
            code_hash = BackupCodeService._hash_code(plain.upper().replace("-", ""))
            bc = BackupCode(user_id=user_id, code_hash=code_hash)
            db.add(bc)
            plain_codes.append(plain)

        db.commit()
        return plain_codes

    @staticmethod
    def validate_backup_code(db: Session, user_id: int, code: str) -> bool:
        """Verbraucht einen unbenutzten Backup-Code; ``True`` nur fuer die eine Anfrage, die ihn bekommt.

        Bis 5.0.1 las die Pruefung den Code und markierte ihn danach. Zwei
        Anfragen zugleich lasen ihn beide als unbenutzt, und beide kamen durch.
        Jetzt markiert ein bedingtes UPDATE; nur wer die Zeile umschreibt, hat
        den Code (AGENTS.md Punkt 43).
        """
        code_hash = BackupCodeService._hash_code(code.upper().replace("-", ""))
        verbraucht = (
            db.query(BackupCode)
            .filter(
                BackupCode.user_id == user_id,
                BackupCode.code_hash == code_hash,
                BackupCode.used_at.is_(None),
            )
            .update({BackupCode.used_at: datetime.now(timezone.utc)}, synchronize_session=False)
        )
        db.commit()
        return verbraucht > 0

    @staticmethod
    def get_remaining_count(db: Session, user_id: int) -> int:
        return db.query(BackupCode).filter(
            BackupCode.user_id == user_id,
            BackupCode.used_at.is_(None),
        ).count()

    @staticmethod
    def clear_all_backup_codes(db: Session, user_id: int) -> None:
        db.query(BackupCode).filter(BackupCode.user_id == user_id).delete(synchronize_session=False)
        db.commit()
