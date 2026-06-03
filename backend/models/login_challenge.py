import hashlib
from datetime import datetime, timezone

from sqlalchemy import Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class LoginChallenge(Base):
    """Kurzlebiger In-flight-Login-State.

    Wird verwendet, wenn ein OAuth-Login zusaetzliche Bestaetigung braucht
    (z. B. 2FA beim zurueckkehrenden User). Der Client erhaelt einen
    zufaelligen opaque Token; serverseitig wird nur der Hash gespeichert.
    Single-use, kurze TTL (5 min).
    """

    __tablename__ = "login_challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # SHA-256 des opaque Tokens (Hex, 64 Zeichen)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    # Zweck, fuer spaetere Erweiterbarkeit (aktuell nur "oauth_2fa")
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)

    # User, fuer den die Challenge geloest werden muss. NULL = noch nicht resolved.
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # Beliebige zusaetzliche Daten als JSON (z. B. Provider-Slug, next-URL).
    # Niemals Secrets hier ablegen.
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


def hash_challenge_token(token: str) -> str:
    """SHA-256-Hash eines opaque Login-Challenge-Tokens (Hex)."""
    return hashlib.sha256(token.encode()).hexdigest()
