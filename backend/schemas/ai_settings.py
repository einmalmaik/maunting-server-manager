"""API-Verträge für rollenbasierte KI-Kontingente."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, SecretStr

from services.ai_limit_service import (
    CONCURRENT_OPERATIONS_MAX,
    MAX_REASONING_EFFORT_MAX,
    MONTHLY_COST_LIMIT_CENTS_MAX,
    REQUESTS_PER_MINUTE_MAX,
    TOKEN_LIMIT_MAX,
)


TokenLimit = Annotated[int | None, Field(ge=0, le=TOKEN_LIMIT_MAX)]
RequestLimit = Annotated[int | None, Field(ge=0, le=REQUESTS_PER_MINUTE_MAX)]
ConcurrencyLimit = Annotated[int | None, Field(ge=0, le=CONCURRENT_OPERATIONS_MAX)]
CostLimit = Annotated[int | None, Field(ge=0, le=MONTHLY_COST_LIMIT_CENTS_MAX)]
#: Denktiefe als Rang: 0 = gar nicht, 1 = minimal … 6 = max. ``None`` heißt
#: unbegrenzt — dieselbe Bedeutung wie bei den Kontingenten darüber.
ReasoningLimit = Annotated[int | None, Field(ge=0, le=MAX_REASONING_EFFORT_MAX)]


class AiLimitsBase(BaseModel):
    """Vollständiges Limit-Set; ``None`` bedeutet explizit unbegrenzt."""

    daily_token_limit: TokenLimit
    weekly_token_limit: TokenLimit
    monthly_token_limit: TokenLimit
    requests_per_minute: RequestLimit
    concurrent_operations: ConcurrencyLimit
    monthly_cost_limit_cents: CostLimit
    # Kein Kontingent, sondern eine Obergrenze. Steht trotzdem hier, weil der
    # Betreiber sie an derselben Stelle setzt und dieselbe Auflösung über
    # mehrere Rollen gilt.
    max_reasoning_effort: ReasoningLimit = None


class AiRoleLimitsUpdate(AiLimitsBase):
    """Ersetzt die KI-Limits genau einer Rolle."""


class AiRoleLimitsResponse(AiLimitsBase):
    """Konfiguration einer Rolle inklusive UI-Metadaten."""

    role_id: int
    role_name: str
    configured: bool
    updated_at: datetime | None = None


class EffectiveAiLimitsResponse(AiLimitsBase):
    """Backendseitig aufgelöste Grenzen des aktuellen Benutzers."""

    role_ids: list[int] = Field(default_factory=list)


class AiWebSearchKeyUpdate(BaseModel):
    """Suchschluessel setzen oder entfernen.

    ``SecretStr`` sorgt dafuer, dass der Wert in Logs und Fehlermeldungen als
    Platzhalter erscheint. Ein leerer Wert entfernt den Schluessel.
    """

    api_key: SecretStr | None = Field(default=None, max_length=512)


class AiWebSearchStatus(BaseModel):
    """Nur der Zustand — der Schluessel verlaesst das Backend nie."""

    configured: bool


class AiLearningPolicyUpdate(BaseModel):
    """Wie die KI global gueltige Skills anlegen darf.

    ``off`` = nur der Betreiber. ``review`` = Personal sofort, Kunden in die
    Warteschlange. ``instant`` = jedes Gespraech wirkt sofort panelweit.
    """

    policy: Literal["off", "review", "instant"]


class AiUsageEntry(BaseModel):
    """Der Verbrauch eines Benutzers, in denselben Zeitraeumen wie die Grenzen.

    Kosten stehen in **Cent**, nicht in den Mikroeinheiten der Datenbank. Die
    Grenze heisst ``monthly_cost_limit_cents`` und wird in Cent eingestellt;
    zwei Einheiten fuer dieselbe Groesse an der API waeren eine Einladung, sie
    in der Oberflaeche zu verwechseln.
    """

    user_id: int
    username: str
    tokens_today: int
    tokens_week: int
    tokens_month: int
    cost_month_cents: int
    requests_month: int
    last_request_at: datetime | None = None


class AiUsageOverview(BaseModel):
    """Alle Benutzer mit Verbrauch, plus die Summe darueber."""

    entries: list[AiUsageEntry] = Field(default_factory=list)
    total_tokens_month: int = 0
    total_cost_month_cents: int = 0


class AiUsageMine(AiUsageEntry):
    """Der eigene Verbrauch — mit den Grenzen daneben, gegen die er laeuft.

    Getrennt vom reinen Verbrauch, weil erst das Paar eine Aussage ergibt:
    50.000 Tokens sind viel oder wenig, je nachdem was erlaubt ist.
    """

    limits: EffectiveAiLimitsResponse


class AiLearningPolicyStatus(BaseModel):
    policy: Literal["off", "review", "instant"]
    # Wie viele global gelernte Skills gerade auf Freigabe warten. Steht hier,
    # damit die Einstellungsseite den Handlungsbedarf zeigt, ohne dass die
    # Oberflaeche eine zweite Abfrage braucht.
    pending_count: int = 0
