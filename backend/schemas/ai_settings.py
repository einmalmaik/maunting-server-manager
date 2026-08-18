"""API-Verträge für rollenbasierte KI-Kontingente."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, SecretStr

from services.ai_limit_service import (
    CONCURRENT_OPERATIONS_MAX,
    MAX_MEMORY_ENTRIES_MAX,
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
#: Memory-Einträge je Bereich. ``None`` heißt hier als einzigem Feld dieser
#: Datei *nicht* unbegrenzt, sondern „nichts hinterlegt“; welche Zahl beim
#: Merken tatsächlich gilt, entscheidet allein
#: ``ai_limit_service.resolve_scope_memory_limit``. Die Obergrenze ist keine
#: Willkür und liegt deutlich unter den Kontingenten darüber — warum, steht bei
#: ``ai_limit_service.MAX_MEMORY_ENTRIES_MAX``.
MemoryLimit = Annotated[int | None, Field(ge=0, le=MAX_MEMORY_ENTRIES_MAX)]


class AiLimitsBase(BaseModel):
    """Vollständiges Limit-Set; ``None`` bedeutet bei den Kontingenten
    explizit unbegrenzt.

    Für ``max_memory_entries`` gilt dieser Satz nicht, und das ist am Vertrag
    nicht abzulesen: die Antwort gibt ein gesetztes ``null`` brav zurück, die
    Zahl, die beim Merken greift, steht nirgends darin. Wer gegen diesen
    Vertrag baut, liest die verbindliche Auflösung deshalb in
    ``ai_limit_service.resolve_scope_memory_limit`` — nicht hier.
    """

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
    # Ebenfalls kein Kontingent, sondern ein Vorrat: wieviele Memory-Einträge
    # ein Bereich fasst. Der Default steht hier aus demselben Grund wie eine
    # Zeile darüber — ``set_role_limit`` verlangt *alle* ``LIMIT_FIELDS``, also
    # muss auch ein Aufrufer ohne dieses Feld ein vollständiges Set liefern.
    max_memory_entries: MemoryLimit = None


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


class AiContextPolicyUpdate(BaseModel):
    """Ab wieviel Prozent des Kontextfensters zusammengefasst wird.

    Die Grenzen stehen auch im Service (`ai_context_window.set_schwelle_prozent`)
    und sind dort die verbindlichen. Hier wiederholt, damit ein Tippfehler eine
    422 mit Feldbezug ergibt statt einer Fehlermeldung ohne Ort.
    """

    compaction_percent: int = Field(ge=50, le=95)


class AiContextPolicyStatus(BaseModel):
    compaction_percent: int
    #: Die zulaessigen Grenzen, damit die Oberflaeche sie nicht selbst kennen
    #: muss. Unter 50 % faltet der Chat staendig und verliert mehr Verlauf, als
    #: er Kosten spart; ueber 95 % bleibt kein Platz mehr fuer die Antwort und
    #: fuer die Anfrage, die das Falten ausloest.
    min_percent: int
    max_percent: int


class AiWorkerPolicyUpdate(BaseModel):
    """Die Betreiber-Deckel der Worker (docs/agentic-framework.md, Abschnitt 5).

    Die Grenzen stehen verbindlich in `services/ai_worker_limits.py`; hier
    wiederholt, damit ein Tippfehler eine 422 mit Feldbezug ergibt statt einer
    Fehlermeldung ohne Ort.
    """

    max_parallel_workers: int = Field(ge=1, le=16)
    rounds_per_worker: int = Field(ge=4, le=48)


class AiWorkerPolicyStatus(BaseModel):
    max_parallel_workers: int
    rounds_per_worker: int
    #: Die zulaessigen Grenzen, damit die Oberflaeche sie nicht selbst kennen
    #: muss. Das Rundenmaximum ist die harte Code-Kappe je Lauf
    #: (`MAX_TOOL_ROUNDS`) — ein Betreiber kann Worker knapper halten als den
    #: Chat, nie grosszuegiger.
    min_workers: int
    max_workers: int
    min_rounds: int
    max_rounds: int


class AiCostPolicyUpdate(BaseModel):
    """Waehrung und Kurs fuer die **Anzeige**. Gebucht wird weiter in USD.

    ``usd_rate`` darf bei ``USD`` fehlen — dort gibt es keinen Kurs. Die
    verbindliche Pruefung steht in `services/ai_kosten.setzen`; hier nur die
    Form, damit ein Tippfehler eine 422 mit Feldbezug ergibt.
    """

    currency: str = Field(min_length=3, max_length=3)
    usd_rate: str | None = Field(default=None, max_length=16)


class AiCostPolicyStatus(BaseModel):
    """Wie Betraege angezeigt werden — die Antwort haengt an jeder Verbrauchszahl.

    Sie steht bewusst neben den Zahlen und nicht in einem eigenen Aufruf: eine
    Oberflaeche, die Betraege rendert, bevor sie die Waehrung kennt, zeigt fuer
    einen Wimpernschlag Dollar dort, wo Euro stehen — und beim Nachladen
    umgekehrt.
    """

    currency: str
    #: Als Zeichenkette, nicht als Fliesskommazahl: ein Kurs ist eine
    #: Dezimalzahl, und der Umweg ueber ``float`` waere genau die Ungenauigkeit,
    #: gegen die diese ganze Aenderung angeht.
    usd_rate: str
    available_currencies: list[str] = Field(default_factory=list)
    min_rate: str
    max_rate: str


class AiUsageEntry(BaseModel):
    """Der Verbrauch eines Benutzers, in denselben Zeitraeumen wie die Grenzen.

    Kosten stehen in **US-Cent-Microunits** (1 Cent = 10.000) — der Einheit, in
    der auch gebucht wird. Frueher standen hier ganze Cent, und das war fuer
    eine Einzelanfrage unbrauchbar: die meisten kosten weniger als einen Cent,
    und aufgerundet sah jede gleich teuer aus. Die Waehrung liefert
    ``AiCostPolicyStatus`` daneben; umgerechnet wird in der Oberflaeche.
    """

    user_id: int
    username: str
    tokens_today: int
    tokens_week: int
    tokens_month: int
    cost_month_micro_usd: int
    requests_month: int
    last_request_at: datetime | None = None


class AiUsageEventEntry(BaseModel):
    """Eine einzelne Anfrage, so wie der Anbieter sie gemeldet hat.

    Der Nachweis hinter den Summen. Er existiert, weil die Summen allein sich
    nicht gegen das Dashboard des Anbieters halten lassen — und weil eine
    geschaetzte Zahl darin bis dahin genauso aussah wie eine gemessene.

    Alle Tokenfelder ausser ``tokens`` sind optional: fuer Zeilen aus der Zeit
    vor der Aufschluesselung gibt es sie nicht, und eine erfundene Null waere
    dort eine Behauptung.
    """

    id: int
    created_at: datetime
    user_id: int
    username: str
    model: str | None = None
    #: Die verbuchte Gesamtzahl — die, an der die Kontingente haengen.
    tokens: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    #: Teilmenge von ``prompt_tokens``: aus dem Zwischenspeicher gelesen und
    #: deshalb rund ein Zehntel so teuer. Wer sie addiert, zaehlt doppelt.
    cached_tokens: int | None = None
    #: Die Gegenzahl dazu: was in den Zwischenspeicher geschrieben wurde. Warum
    #: es beide braucht, steht bei der Spalte in `models/ai_usage_event.py`.
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    #: Wieviele Anbieteranfragen in dieser Zeile stecken. Eine Chatnachricht ist
    #: nicht eine Anfrage: jede Werkzeugrunde ruft den Anbieter erneut.
    provider_requests: int | None = None
    cost_micro_usd: int
    #: 'provider' | 'estimate' | 'none' | None (Bestandszeile ohne Herkunft).
    cost_source: str | None = None


class AiUsageEvents(BaseModel):
    """Ein Ausschnitt der Einzelaufstellung, mit der Politik zum Formatieren."""

    entries: list[AiUsageEventEntry] = Field(default_factory=list)
    #: Ob hinter diesem Ausschnitt noch mehr liegt. Bewusst kein Gesamtzaehler:
    #: der kostet eine zweite Abfrage ueber dieselbe Tabelle fuer eine Zahl, die
    #: niemand braucht, um weiterzublaettern.
    has_more: bool = False
    cost_policy: AiCostPolicyStatus


class AiUsageOverview(BaseModel):
    """Alle Benutzer mit Verbrauch, plus die Summe darueber."""

    entries: list[AiUsageEntry] = Field(default_factory=list)
    total_tokens_month: int = 0
    total_cost_month_micro_usd: int = 0
    cost_policy: AiCostPolicyStatus


class AiUsageMine(AiUsageEntry):
    """Der eigene Verbrauch — mit den Grenzen daneben, gegen die er laeuft.

    Getrennt vom reinen Verbrauch, weil erst das Paar eine Aussage ergibt:
    50.000 Tokens sind viel oder wenig, je nachdem was erlaubt ist.
    """

    limits: EffectiveAiLimitsResponse
    cost_policy: AiCostPolicyStatus


class AiLearningPolicyStatus(BaseModel):
    policy: Literal["off", "review", "instant"]
    # Wie viele global gelernte Skills gerade auf Freigabe warten. Steht hier,
    # damit die Einstellungsseite den Handlungsbedarf zeigt, ohne dass die
    # Oberflaeche eine zweite Abfrage braucht.
    pending_count: int = 0
