"""Secret-minimierte API-Vertraege fuer AI-Provider."""

from datetime import datetime

from pydantic import BaseModel, Field, SecretStr


# Obergrenze fuer den gepflegten Preis: 1.000 USD je eine Million Tokens, in
# Microunits (1 US-Cent = 10.000). Das teuerste Modell bei OpenRouter liegt bei
# rund 75 USD — die Grenze faengt also Tippfehler ab, ohne einer echten
# Preisliste im Weg zu stehen.
MAX_TOKEN_PRICE_MICRO_USD = 1_000_000_000


class AiProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    # Schluessel aus `services/ai_provider_registry.ANBIETER`. Die Adresse
    # dahinter gehoert dem Programm — hier steht nur noch, *welcher* Anbieter.
    provider_kind: str = Field(min_length=1, max_length=32)
    default_model: str = Field(min_length=1, max_length=256)
    enabled: bool = True
    requires_api_key: bool = True
    # Rueckfallpreis je eine Million Tokens, in US-Cent-Microunits (1 Cent =
    # 10.000). Gebraucht wird er nur, wenn der Anbieter selbst keine Kosten
    # meldet; ``None`` heisst dann: keine belastbare Preisquelle, Kosten werden
    # mit null verbucht. Die Oberflaeche nimmt hier eine Dezimalzahl in der
    # Anzeigewaehrung entgegen und rechnet um — die Schnittstelle sieht nur die
    # ganze Zahl.
    token_price_micro_usd_per_million: int | None = Field(
        default=None, ge=0, le=MAX_TOKEN_PRICE_MICRO_USD
    )
    operator_api_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)


class AiProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    provider_kind: str | None = Field(default=None, min_length=1, max_length=32)
    default_model: str | None = Field(default=None, min_length=1, max_length=256)
    enabled: bool | None = None
    requires_api_key: bool | None = None
    # Siehe `AiProviderCreate`: Rueckfallpreis in US-Cent-Microunits je Million.
    token_price_micro_usd_per_million: int | None = Field(
        default=None, ge=0, le=MAX_TOKEN_PRICE_MICRO_USD
    )
    operator_api_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    clear_operator_api_key: bool = False


class AiProviderResponse(BaseModel):
    id: int
    name: str
    provider_kind: str
    # Aus der Registry abgeleitet, nicht gespeichert. Steht in der Antwort,
    # damit die Oberflaeche zeigen kann, wohin die Anfragen gehen.
    base_url: str
    default_model: str
    enabled: bool
    requires_api_key: bool
    operator_key_configured: bool
    operator_key_hint: str | None
    token_price_micro_usd_per_million: int | None
    updated_at: datetime


class AiProviderKindResponse(BaseModel):
    """Ein von MSM unterstuetzter Anbieter — die Auswahl im Einrichtungsformular."""

    kind: str
    label: str
    base_url: str
    key_url: str
    key_prefix: str | None


class AiCatalogModelResponse(BaseModel):
    """Ein Modell aus dem Katalog des Anbieters, mit seinen Denkfaehigkeiten.

    ``efforts`` sind die Stufen, die dieses Modell **wirklich** kennt — direkt
    aus dem Katalog. Eine leere Liste heisst nicht "denkt nicht", sondern "kennt
    keine Stufen": gemessen koennen 145 der 272 denkenden Modelle bei OpenRouter
    nur an oder aus.
    """

    model_id: str
    name: str
    reasoning: bool
    efforts: list[str]
    default_effort: str | None
    mandatory: bool


class AiProviderAvailableResponse(BaseModel):
    """Ein Provider, wie ihn der Chat zur Auswahl anbietet.

    Kein ``user_key_configured`` mehr: seit der Betreiber die Schluessel stellt,
    haengt ``available`` allein an seinem Schluessel, und der Benutzer hat
    nichts, was er hier beitragen koennte.

    Die Denkangaben sind bereits **auf die Rolle dieses Benutzers geklemmt**:
    ``efforts`` enthaelt nur, was er auch waehlen darf. Damit muss die
    Oberflaeche keine Rechte auswerten — sie zeigt eine Liste an. Die
    verbindliche Klemmung passiert trotzdem erneut serverseitig beim Senden;
    diese hier ist Darstellung, nicht Durchsetzung.
    """

    id: int
    name: str
    default_model: str
    requires_api_key: bool
    operator_key_available: bool
    available: bool
    #: Ob bei diesem Modell ueberhaupt nachgedacht werden kann.
    reasoning: bool = False
    #: Die waehlbaren Stufen, aufsteigend. Leer = nur an/aus.
    efforts: list[str] = []
    #: Ob "aus" eine gueltige Wahl ist. Bei 82 der 402 Modelle nicht.
    can_disable: bool = True
    #: Was gilt, wenn der Benutzer nichts waehlt.
    default_effort: str | None = None


class AiProviderTestResponse(BaseModel):
    """Ergebnis eines echten Testaufrufs gegen den konfigurierten Endpunkt."""

    ok: bool
    # Stabiler MSM-Code, z. B. AI_PROVIDER_AUTH_FAILED. Uebersetzbar.
    code: str | None = None
    # Gekuerzte, redigierte Klartextmeldung des Anbieters. Sie sagt, *was*
    # fehlt ("No endpoints found for openrouter-free") — genau das, was ein
    # uebersetzter Code allein nicht leisten kann.
    detail: str | None = None
