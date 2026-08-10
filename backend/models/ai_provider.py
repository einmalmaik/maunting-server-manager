"""AI-Provider-Konfiguration — vollstaendig in der Hand des Betreibers."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AiProvider(Base):
    """Vom Betreiber freigegebener OpenAI-kompatibler Endpunkt.

    Ziel-URL, Modell und Schluessel legt der Betreiber fest; ein Benutzer waehlt
    nur noch unter dem aus, was freigegeben ist. Es gab hier einmal einen
    zweiten Weg — jeder Benutzer durfte einen eigenen API-Key mitbringen, und
    `resolve_api_key` nahm ihn **vor** dem des Betreibers. Fuer ein Panel, das
    ein Hoster betreibt, ist das der falsche Weg herum: der Kunde zahlt fuer den
    Dienst, und ein eigener Schluessel waere ein zweiter Abrechnungspfad neben
    dem kalkulierten.
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
