from datetime import datetime, timezone

from sqlalchemy import Boolean, Integer, String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class OAuthProvider(Base):
    """OAuth/OIDC-Provider-Konfiguration.

    Pro Provider eine Zeile. Fuer die 7 festen Presets (google, discord, github,
    microsoft, twitter, custom_oidc, custom_oauth2) sind die Endpoints im Code
    hinterlegt. Bei custom_* duerfen sie ueberschrieben werden.

    Sicherheit: ``client_secret`` wird NIE im Klartext gespeichert. Bei
    ``set_secret`` wird DIS AES-256-GCM Encryption angewendet (Key abgeleitet aus
    ``settings.secret_key`` — identisches Pattern wie 2FA/Steam-Account).
    """

    __tablename__ = "oauth_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # Eindeutiger Identifier (URL-tauglich, lowercase)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    # Anzeigename im UI
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    # Preset-Key: "google" | "discord" | "github" | "microsoft" | "twitter"
    #            | "custom_oidc" | "custom_oauth2"
    preset: Mapped[str] = mapped_column(String(32), nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # OAuth/OIDC-Client-Konfiguration
    client_id: Mapped[str] = mapped_column(String(512), nullable=False)
    # DIS-encrypted; leer = kein Secret hinterlegt (z. B. PKCE-only Provider)
    client_secret_encrypted: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    # Maske des Klartext-Secrets (z. B. "********cdef") — wird bei
    # Create/Update berechnet und mit-geschrieben. Vermeidet, dass der
    # Listing-Pfad fuer jeden Provider einen DIS-Decrypt macht, nur um
    # die letzten 4 Zeichen anzuzeigen. KISS + Performance.
    client_secret_mask: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Optionale Overrides fuer custom_oidc/custom_oauth2 (sonst NULL = aus Preset)
    issuer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    authorization_endpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    token_endpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    userinfo_endpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Space-separated Scope-String (authlib normalisiert)
    scope: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Mapping von IdP-Claims auf MSM-Felder. Default = sinnvolle Heuristik pro Preset.
    # JSON-formatiert, z. B. {"id":"sub","email":"email","username":"preferred_username","verified":"email_verified"}
    claims_mapping_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Reihenfolge im Login-UI
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
