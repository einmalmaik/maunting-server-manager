import pytest
from sqlalchemy.orm import Session
from database import SessionLocal
from services.email_verification_service import EmailVerificationService
from models import EmailVerification


def test_email_verification_lifecycle():
    db = SessionLocal()
    email = "verify-test@maunting.de"
    purpose = "register"

    try:
        # 1. Erstellen des Codes
        code = EmailVerificationService.create_verification(db, email, purpose)
        assert len(code) == 6
        assert code.isdigit()

        # 2. In DB überprüfen, ob E-Mail-Adresse gehasht ist und NICHT im Klartext vorliegt
        email_h = EmailVerificationService._email_hash(email)
        records = db.query(EmailVerification).all()
        for r in records:
            assert r.email_hash == email_h
            # Klartext darf nicht vorkommen
            assert "verify-test" not in str(r.__dict__)

        # 3. Code überprüfen
        assert EmailVerificationService.has_active_verification(db, email, [purpose]) is True
        
        # Falscher Code schlägt fehl
        assert EmailVerificationService.verify_code(db, email, purpose, "000000") is False

        # Richtiger Code funktioniert
        assert EmailVerificationService.verify_code(db, email, purpose, code) is True

        # Mehrmaliges Verifizieren schlägt fehl (da verbraucht)
        assert EmailVerificationService.verify_code(db, email, purpose, code) is False
        assert EmailVerificationService.has_active_verification(db, email, [purpose]) is False

    finally:
        db.close()
