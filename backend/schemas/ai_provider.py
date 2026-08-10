"""Secret-minimierte API-Vertraege fuer AI-Provider."""

from datetime import datetime

from pydantic import BaseModel, Field, SecretStr


class AiProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=8, max_length=1024)
    default_model: str = Field(min_length=1, max_length=256)
    enabled: bool = True
    requires_api_key: bool = True
    allow_private_network: bool = False
    # Preis in Cent je eine Million Tokens. ``None`` bedeutet: keine
    # belastbare Preisquelle, Kosten werden mit null verbucht.
    token_price_cents_per_million: int | None = Field(default=None, ge=0, le=10_000_000)
    operator_api_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)


class AiProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=8, max_length=1024)
    default_model: str | None = Field(default=None, min_length=1, max_length=256)
    enabled: bool | None = None
    requires_api_key: bool | None = None
    allow_private_network: bool | None = None
    # Preis in Cent je eine Million Tokens. ``None`` bedeutet: keine
    # belastbare Preisquelle, Kosten werden mit null verbucht.
    token_price_cents_per_million: int | None = Field(default=None, ge=0, le=10_000_000)
    operator_api_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    clear_operator_api_key: bool = False


class AiProviderResponse(BaseModel):
    id: int
    name: str
    base_url: str
    default_model: str
    enabled: bool
    requires_api_key: bool
    allow_private_network: bool
    operator_key_configured: bool
    operator_key_hint: str | None
    token_price_cents_per_million: int | None
    updated_at: datetime


class AiProviderAvailableResponse(BaseModel):
    """Ein Provider, wie ihn der Chat zur Auswahl anbietet.

    Kein ``user_key_configured`` mehr: seit der Betreiber die Schluessel stellt,
    haengt ``available`` allein an seinem Schluessel, und der Benutzer hat
    nichts, was er hier beitragen koennte.
    """

    id: int
    name: str
    default_model: str
    requires_api_key: bool
    operator_key_available: bool
    available: bool


class AiProviderTestResponse(BaseModel):
    """Ergebnis eines echten Testaufrufs gegen den konfigurierten Endpunkt."""

    ok: bool
    # Stabiler MSM-Code, z. B. AI_PROVIDER_AUTH_FAILED. Uebersetzbar.
    code: str | None = None
    # Gekuerzte, redigierte Klartextmeldung des Anbieters. Sie sagt, *was*
    # fehlt ("No endpoints found for openrouter-free") — genau das, was ein
    # uebersetzter Code allein nicht leisten kann.
    detail: str | None = None
