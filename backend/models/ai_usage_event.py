"""Idempotente Reservierungen für backendseitig erzwungene KI-Kontingente."""

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AiUsageEvent(Base):
    """Zählt eine logische KI-Anfrage dank eindeutiger Request-ID genau einmal."""

    __tablename__ = "ai_usage_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('reserved', 'completed', 'failed')",
            name="ck_ai_usage_events_status",
        ),
        CheckConstraint("accounted_tokens >= 0", name="ck_ai_usage_events_tokens"),
        CheckConstraint("reserved_tokens >= 0", name="ck_ai_usage_events_reserved_tokens"),
        CheckConstraint(
            "accounted_cost_microunits >= 0",
            name="ck_ai_usage_events_cost",
        ),
        CheckConstraint(
            "reserved_cost_microunits >= 0",
            name="ck_ai_usage_events_reserved_cost",
        ),
        # NULL ist ausdruecklich erlaubt: Bestandszeilen aus der Zeit vor der
        # Aufschluesselung tragen keine Herkunft, und eine erfundene waere
        # schlimmer als eine fehlende.
        CheckConstraint(
            "cost_source IS NULL OR cost_source IN ('provider', 'estimate', 'none')",
            name="ck_ai_usage_events_cost_source",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    server_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("servers.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    provider_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "ai_providers.id",
            name="fk_ai_usage_events_provider_id_ai_providers",
            ondelete="SET NULL",
        ),
        index=True,
        nullable=True,
    )
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="reserved", index=True, nullable=False)
    # Bleibt für Idempotenzprüfungen unverändert, auch wenn die tatsächliche
    # Abrechnung nach dem Provider-Aufruf niedriger oder höher ausfällt.
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_cost_microunits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    accounted_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # In **US-Cent-Microunits** (1 Cent = 10.000, siehe
    # `ai_usage_service.MICROUNITS_PER_CENT`). Die Waehrung stand hier lange
    # nirgends und war deshalb die des zuletzt gepflegten Preises — bei zwei
    # Anbietern mit unterschiedlicher Waehrung addierte die Uebersicht Aepfel
    # und Birnen. Sie ist jetzt festgelegt: der Anbieter rechnet in USD ab, und
    # eine Umrechnung *vor* der Buchung waere eine zweite Fehlerquelle. In die
    # Anzeigewaehrung geht es erst in der Oberflaeche (`services/ai_kosten.py`).
    accounted_cost_microunits: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # ── Aufschluesselung ──────────────────────────────────────────────
    #
    # Was der Anbieter gemeldet hat, so wie er es gemeldet hat. `accounted_tokens`
    # bleibt die Zahl, an der die Kontingente haengen; das hier ist der Nachweis
    # daneben. Alles nullable, weil "nicht gemeldet" nicht "null" heisst — ein
    # stummer Anbieter hat nicht null Tokens verbraucht.
    prompt_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Teilmengen der beiden obigen. Wer sie addiert, zaehlt doppelt.
    cached_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Die Gegenzahl zu `cached_tokens`: was in den Zwischenspeicher geschrieben
    # wurde. Ohne sie ist eine Null bei den gelesenen Tokens nicht deutbar —
    # entweder wurde nie etwas angelegt (die Marke geht ins Leere) oder es wurde
    # angelegt und nie wiedergefunden (die Reihenfolge der Nachrichten stimmt
    # nicht). Das sind zwei verschiedene Fehler mit zwei verschiedenen
    # Behebungen, und sie sahen bisher gleich aus.
    cache_write_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    realtime_text_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    realtime_text_output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    realtime_audio_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    realtime_audio_output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Dauer der Diktat- bzw. STT-Transkriptionsaufnahme in Sekunden (provider-neutral).
    dictation_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Wieviele Anbieteranfragen in dieser Zeile stecken. Eine Chatnachricht ist
    # nicht eine Anfrage: jede Werkzeugrunde ruft den Anbieter erneut und
    # schickt den gewachsenen Verlauf komplett mit. Ohne diese Zahl sieht eine
    # ehrliche Summe von 360.000 Tokens fuer eine Frage nach einem Fehler aus.
    provider_requests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 'provider' — der Anbieter hat den Betrag gemeldet, er ist gebucht wie er kam.
    # 'estimate' — der Anbieter schwieg, gerechnet wurde mit dem gepflegten Preis.
    # 'none'     — kein Preis hinterlegt, die Kosten stehen ehrlich auf null.
    cost_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
