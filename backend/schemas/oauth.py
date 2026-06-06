"""Pydantic-Schemas fuer OAuth-Provider-Konfiguration.

Sicherheits-Invarianten:
- Response-Modelle geben Client-Secrets NUR maskiert zurueck (max. 4 Zeichen Sicht).
- Create/Update akzeptieren Client-Secrets im Klartext und speichern sie
  Fernet-encrypted (im Service-Layer, nicht hier).
- ``client_secret`` ist im Create-Body OPTIONAL — einige Provider (z. B. PKCE-only)
  benoetigen keins.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# ── Anzeige-Presets (fuer UI-Dropdowns) ────────────────────────────────

OAUTH_PRESETS: tuple[str, ...] = (
    "google",
    "discord",
    "github",
    "microsoft",
    "twitter",
    "custom_oidc",
    "custom_oauth2",
)


# ── Create ─────────────────────────────────────────────────────────────

class OAuthProviderCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=128)
    preset: str
    enabled: bool = True
    client_id: str = Field(min_length=1, max_length=512)
    # Optional: leeres/fehlendes Secret bedeutet "kein Secret hinterlegt".
    client_secret: str | None = Field(default=None, max_length=4096)

    # Optionale Overrides fuer custom_*-Presets
    issuer: str | None = Field(default=None, max_length=512)
    authorization_endpoint: str | None = Field(default=None, max_length=512)
    token_endpoint: str | None = Field(default=None, max_length=512)
    userinfo_endpoint: str | None = Field(default=None, max_length=512)
    scope: str | None = Field(default=None, max_length=512)
    claims_mapping_json: str | None = None
    position: int = 0


# ── Update ─────────────────────────────────────────────────────────────

class OAuthProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool | None = None
    client_id: str | None = Field(default=None, min_length=1, max_length=512)
    # Falls None/"" → Secret bleibt unveraendert. "" wird als "Secret loeschen" interpretiert.
    # Frontend MUSS maskierten Wert (****1234) zurueckschicken, wenn das Secret
    # erhalten bleiben soll — der Service prueft das Praefix und ueberspringt.
    client_secret: str | None = Field(default=None, max_length=4096)
    issuer: str | None = Field(default=None, max_length=512)
    authorization_endpoint: str | None = Field(default=None, max_length=512)
    token_endpoint: str | None = Field(default=None, max_length=512)
    userinfo_endpoint: str | None = Field(default=None, max_length=512)
    scope: str | None = Field(default=None, max_length=512)
    claims_mapping_json: str | None = None
    position: int | None = None


# ── Response ───────────────────────────────────────────────────────────

class OAuthProviderResponse(BaseModel):
    id: int
    slug: str
    name: str
    preset: str
    enabled: bool
    client_id: str
    # Maskiert, s. _mask_secret
    client_secret: str
    issuer: str | None = None
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    userinfo_endpoint: str | None = None
    scope: str | None = None
    claims_mapping_json: str | None = None
    position: int
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class OAuthProviderPublic(BaseModel):
    """Schlanke Public-Variante fuer das Login-UI (keine Client-IDs noetig)."""

    slug: str
    name: str
    preset: str
    position: int


# ── Test-Verbindung ────────────────────────────────────────────────────

class OAuthTestResult(BaseModel):
    ok: bool
    message: str
