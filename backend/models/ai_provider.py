"""AI-Provider-Konfiguration — vollstaendig in der Hand des Betreibers."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AiProvider(Base):
    """Ein vom Betreiber eingerichteter Zugang zu einem unterstützten Anbieter.

    Ziel-Anbieter, Modell und Schlüssel legt der Betreiber fest; ein Benutzer
    wählt nur noch unter dem aus, was freigegeben ist. Es gab hier einmal einen
    zweiten Weg — jeder Benutzer durfte einen eigenen API-Key mitbringen, und
    `resolve_api_key` nahm ihn **vor** dem des Betreibers. Für ein Panel, das
    ein Hoster betreibt, ist das der falsche Weg herum: der Kunde zahlt für den
    Dienst, und ein eigener Schlüssel wäre ein zweiter Abrechnungspfad neben
    dem kalkulierten.

    **Die frei eintragbare Basis-URL ist entfallen** (`provider_kind` statt
    `base_url`). Sie war flexibel und teuer zugleich: MSM wusste nichts über
    das Ziel, brauchte deshalb eine SSRF-Prüfung mit IP-Pinning, und konnte
    über das Modell dahinter keine Aussage treffen — welche Denkstufen es
    kennt, ob es abschaltbar ist, ob es überhaupt nachdenkt. Genau das braucht
    ein Panel, das Denktiefe je Rolle begrenzen will.

    Mit einem Anbieter aus `ai_provider_registry` gehört die Adresse dem
    Programm, und mit ihr kommt der Modellkatalog. Was ein Modell kann, steht
    deshalb **nicht** in dieser Tabelle: es kommt bei Bedarf aus
    `ai_model_catalog` und wäre hier eine Kopie, die still veraltet.
    """

    __tablename__ = "ai_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    # Schlüssel aus `ai_provider_registry.ANBIETER`. Bestimmt Adresse und
    # Katalog; beides gehört damit nicht mehr in die Eingabe.
    provider_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="openrouter"
    )
    default_model: Mapped[str] = mapped_column(String(256), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requires_api_key: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
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
