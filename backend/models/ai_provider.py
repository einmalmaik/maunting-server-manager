"""AI-Provider-Konfiguration und benutzereigene Credentials."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AiProvider(Base):
    """Vom Betreiber freigegebener OpenAI-kompatibler Endpunkt.

    Benutzer koennen die Ziel-URL nicht veraendern. Dadurch bleibt BYOK ein
    Credential-Flow und wird nicht zu einem frei steuerbaren SSRF-Kanal.
    """

    __tablename__ = "ai_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    default_model: Mapped[str] = mapped_column(String(256), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requires_api_key: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_private_network: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    operator_api_key_encrypted: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    operator_api_key_hint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Vom Betreiber gepflegter Preis in Cent je eine Million Tokens. MSM raet
    # keinen Preis: ohne diesen Wert bleiben die Kosten bei null und das
    # rollenbasierte Kostenlimit greift nicht (die Oberflaeche weist darauf hin).
    token_price_cents_per_million: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class AiUserCredential(Base):
    """DIS-verschluesselter Benutzer-Key fuer genau einen Provider."""

    __tablename__ = "ai_user_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "provider_id", name="uq_ai_user_credentials_user_provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ai_providers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    api_key_encrypted: Mapped[str] = mapped_column(String(4096), nullable=False)
    api_key_hint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
