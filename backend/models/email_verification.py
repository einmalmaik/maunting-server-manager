from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class EmailVerification(Base):
    """Temporaere Email-Verifikations-Codes fuer Setup und Registrierung."""
    __tablename__ = "email_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA256 des 6-stelligen Codes
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)  # "setup" oder "register"
    verified: Mapped[bool] = mapped_column(default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
