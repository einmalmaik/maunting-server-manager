"""Idempotente KI-Kontingentreservierung vor einem Provider-Aufruf.

Die User-Zeile wird in PostgreSQL gesperrt, damit parallele Anfragen desselben
Benutzers nicht beide dieselbe freie Quote sehen. Die eindeutige Request-ID
verhindert zusätzlich Doppelzählung bei Retries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import AiMessage, AiUsageEvent, User
from services.ai_limit_service import (
    MONTHLY_COST_LIMIT_CENTS_MAX,
    TOKEN_LIMIT_MAX,
    resolve_effective_limits,
)


ACTIVE_STATUSES = ("reserved", "completed")
MICROUNITS_PER_CENT = 10_000


class AiQuotaExceeded(ValueError):
    """Eine konkrete, nicht-sensible KI-Grenze ist ausgeschöpft."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"KI-Kontingent ausgeschöpft: {reason}")


class AiUsageConflict(ValueError):
    """Eine Request-ID wurde mit widersprüchlichen Daten wiederverwendet."""


def _matches_reservation(
    event: AiUsageEvent,
    *,
    user_id: int,
    server_id: int | None,
    provider_id: int | None,
    model: str | None,
    estimated_tokens: int,
    estimated_cost_microunits: int,
) -> bool:
    """Vergleicht den unveränderlichen Idempotenzvertrag einer Reservierung."""
    return (
        event.user_id == user_id
        and event.server_id == server_id
        and event.provider_id == provider_id
        and event.model == model
        and event.reserved_tokens == estimated_tokens
        and event.reserved_cost_microunits == estimated_cost_microunits
    )


def _canonical_request_id(request_id: str | UUID) -> str:
    """Akzeptiert ausschließlich kanonische UUIDs als Idempotency-Key."""
    try:
        return str(UUID(str(request_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Ungültige AI-Request-ID") from exc


def _period_starts(now: datetime) -> tuple[datetime, datetime, datetime]:
    """Berechnet UTC-Tages-, ISO-Wochen- und Monatsanfang."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = day - timedelta(days=day.weekday())
    month = day.replace(day=1)
    return day, week, month


def _sum_since(db: Session, user_id: int, since: datetime, column) -> int:
    value = (
        db.query(func.coalesce(func.sum(column), 0))
        .filter(
            AiUsageEvent.user_id == user_id,
            AiUsageEvent.status.in_(ACTIVE_STATUSES),
            AiUsageEvent.created_at >= since,
        )
        .scalar()
    )
    return int(value or 0)


def _ensure_within(limit: int | None, current: int, requested: int, reason: str) -> None:
    """Prüft eine Grenze; ``None`` ist explizit unbegrenzt."""
    if limit is not None and current + requested > limit:
        raise AiQuotaExceeded(reason)


def reserve_ai_usage(
    db: Session,
    user: User,
    *,
    request_id: str | UUID,
    estimated_tokens: int,
    estimated_cost_microunits: int = 0,
    server_id: int | None = None,
    provider_id: int | None = None,
    model: str | None = None,
    now: datetime | None = None,
) -> AiUsageEvent:
    """Reserviert eine Anfrage atomar oder liefert dieselbe Reservierung erneut."""
    request_key = _canonical_request_id(request_id)
    if (
        estimated_tokens < 0
        or estimated_tokens > TOKEN_LIMIT_MAX
        or estimated_cost_microunits < 0
        or estimated_cost_microunits > MONTHLY_COST_LIMIT_CENTS_MAX * MICROUNITS_PER_CENT
    ):
        raise ValueError("Geschätzter AI-Verbrauch liegt außerhalb des erlaubten Bereichs")

    # Bestehende Reservierung zuerst pruefen: Dies verhindert nur eine zweite
    # Kontingentbuchung. Provider-Replays blockiert die Chat-Schicht anhand der
    # persistenten Assistant-Nachricht mit derselben Request-ID.
    existing = db.query(AiUsageEvent).filter(AiUsageEvent.request_id == request_key).first()
    if existing is not None:
        if not _matches_reservation(
            existing,
            user_id=user.id,
            server_id=server_id,
            provider_id=provider_id,
            model=model,
            estimated_tokens=estimated_tokens,
            estimated_cost_microunits=estimated_cost_microunits,
        ):
            raise AiUsageConflict("AI-Request-ID wurde mit anderen Daten wiederverwendet")
        return existing

    # PostgreSQL serialisiert dadurch alle Quotenentscheidungen pro User.
    db.query(User.id).filter(User.id == user.id).with_for_update().one()
    current_time = now or datetime.now(timezone.utc)
    day_start, week_start, month_start = _period_starts(current_time)
    limits = resolve_effective_limits(db, user)

    minute_count = (
        db.query(func.count(AiUsageEvent.id))
        .filter(
            AiUsageEvent.user_id == user.id,
            AiUsageEvent.status.in_(ACTIVE_STATUSES),
            AiUsageEvent.created_at >= current_time - timedelta(minutes=1),
        )
        .scalar()
        or 0
    )
    _ensure_within(limits.requests_per_minute, int(minute_count), 1, "requests_per_minute")

    concurrent = (
        db.query(func.count(AiUsageEvent.id))
        .filter(
            AiUsageEvent.user_id == user.id,
            AiUsageEvent.status == "reserved",
        )
        .scalar()
        or 0
    )
    _ensure_within(limits.concurrent_operations, int(concurrent), 1, "concurrent_operations")
    _ensure_within(
        limits.daily_token_limit,
        _sum_since(db, user.id, day_start, AiUsageEvent.accounted_tokens),
        estimated_tokens,
        "daily_token_limit",
    )
    _ensure_within(
        limits.weekly_token_limit,
        _sum_since(db, user.id, week_start, AiUsageEvent.accounted_tokens),
        estimated_tokens,
        "weekly_token_limit",
    )
    _ensure_within(
        limits.monthly_token_limit,
        _sum_since(db, user.id, month_start, AiUsageEvent.accounted_tokens),
        estimated_tokens,
        "monthly_token_limit",
    )
    _ensure_within(
        None if limits.monthly_cost_limit_cents is None else limits.monthly_cost_limit_cents * MICROUNITS_PER_CENT,
        _sum_since(db, user.id, month_start, AiUsageEvent.accounted_cost_microunits),
        estimated_cost_microunits,
        "monthly_cost_limit_cents",
    )

    event = AiUsageEvent(
        request_id=request_key,
        user_id=user.id,
        server_id=server_id,
        provider_id=provider_id,
        model=model,
        status="reserved",
        reserved_tokens=estimated_tokens,
        reserved_cost_microunits=estimated_cost_microunits,
        accounted_tokens=estimated_tokens,
        accounted_cost_microunits=estimated_cost_microunits,
        created_at=current_time,
    )
    try:
        with db.begin_nested():
            db.add(event)
            db.flush()
    except IntegrityError as exc:
        concurrent = db.query(AiUsageEvent).filter(AiUsageEvent.request_id == request_key).first()
        if concurrent is not None and _matches_reservation(
            concurrent,
            user_id=user.id,
            server_id=server_id,
            provider_id=provider_id,
            model=model,
            estimated_tokens=estimated_tokens,
            estimated_cost_microunits=estimated_cost_microunits,
        ):
            return concurrent
        raise AiUsageConflict("AI-Request-ID wurde gleichzeitig widersprüchlich reserviert") from exc
    return event


def complete_ai_usage(
    db: Session,
    event: AiUsageEvent,
    *,
    actual_tokens: int,
    actual_cost_microunits: int,
    now: datetime | None = None,
) -> AiUsageEvent:
    """Schließt eine Reservierung idempotent mit dem tatsächlichen Verbrauch ab."""
    if (
        actual_tokens < 0
        or actual_tokens > TOKEN_LIMIT_MAX
        or actual_cost_microunits < 0
        or actual_cost_microunits > MONTHLY_COST_LIMIT_CENTS_MAX * MICROUNITS_PER_CENT
    ):
        raise ValueError("Tatsächlicher AI-Verbrauch liegt außerhalb des erlaubten Bereichs")
    if event.status == "completed":
        if (
            event.accounted_tokens != actual_tokens
            or event.accounted_cost_microunits != actual_cost_microunits
        ):
            raise AiUsageConflict("Abgeschlossene AI-Anfrage hat abweichende Verbrauchsdaten")
        return event
    if event.status != "reserved":
        raise AiUsageConflict("Nur reservierte AI-Anfragen können abgeschlossen werden")
    event.status = "completed"
    event.accounted_tokens = actual_tokens
    event.accounted_cost_microunits = actual_cost_microunits
    event.completed_at = now or datetime.now(timezone.utc)
    db.flush()
    return event


def fail_ai_usage(db: Session, event: AiUsageEvent) -> AiUsageEvent:
    """Gibt eine fehlgeschlagene Reservierung frei, ohne Verbrauch zu erfinden."""
    if event.status == "failed":
        return event
    if event.status != "reserved":
        raise AiUsageConflict("Abgeschlossene AI-Anfragen können nicht fehlschlagen")
    event.status = "failed"
    event.accounted_tokens = 0
    event.accounted_cost_microunits = 0
    event.completed_at = datetime.now(timezone.utc)
    db.flush()
    return event


# ── Auswertung ────────────────────────────────────────────────────────
#
# Getrennt von der Reservierung, weil es eine andere Frage beantwortet: dort
# „darf diese Anfrage noch“, hier „wer hat wieviel verbraucht“. Gemeinsam ist
# beiden die Grundlage — dieselben Zeitraeume aus `_period_starts` und dieselben
# `ACTIVE_STATUSES`. Das ist keine Kosmetik: eine Ansicht, die andere Zahlen
# zeigt als die Sperre durchsetzt, ist schlimmer als gar keine. Wer sich fragt,
# warum jemand mit „noch 20 % frei“ abgewiesen wird, findet die Antwort dann
# nirgends.
#
# `failed` faellt damit heraus, und zwar richtig: `fail_ai_usage` setzt den
# verbuchten Verbrauch auf 0 zurueck: eine Anfrage, die nie beim Anbieter
# ankam, hat nichts gekostet.


@dataclass(frozen=True)
class AiUsageSummary:
    """Verbrauch eines Benutzers in den Zeitraeumen, die auch die Grenzen kennen.

    ``last_request_at`` ist die letzte Anfrage **innerhalb des ausgewerteten
    Fensters**, nicht die letzte ueberhaupt. Das Fenster ist genau der Zeitraum,
    den die Ansicht zeigt — ein Datum von ausserhalb waere dort nicht einzuordnen.
    """

    user_id: int
    username: str
    tokens_today: int
    tokens_week: int
    tokens_month: int
    cost_month_microunits: int
    requests_month: int
    last_request_at: datetime | None


def _since(column, threshold: datetime):
    """Summiert eine Spalte nur fuer Zeilen ab ``threshold``.

    Alle drei Zeitraeume in **einer** Abfrage statt in dreien je Benutzer. Bei
    einem Hoster mit einigen hundert Kunden waere die naheliegende Schleife
    dreihundert Abfragen fuer eine Tabelle.
    """
    return func.coalesce(func.sum(case((AiUsageEvent.created_at >= threshold, column), else_=0)), 0)


def _usage_rows(
    db: Session, *, user_id: int | None, now: datetime | None = None
) -> list[AiUsageSummary]:
    current_time = now or datetime.now(timezone.utc)
    day_start, week_start, month_start = _period_starts(current_time)
    # Die ISO-Woche kann vor dem Monatsanfang beginnen — am Ersten eines Monats
    # regelmaessig. Wer hier nur ab Monatsanfang laedt, zeigt in den ersten
    # Tagen eine zu niedrige Wochenzahl.
    earliest = min(week_start, month_start)

    query = (
        db.query(
            AiUsageEvent.user_id.label("user_id"),
            User.username.label("username"),
            _since(AiUsageEvent.accounted_tokens, day_start).label("tokens_today"),
            _since(AiUsageEvent.accounted_tokens, week_start).label("tokens_week"),
            _since(AiUsageEvent.accounted_tokens, month_start).label("tokens_month"),
            _since(AiUsageEvent.accounted_cost_microunits, month_start).label("cost_month"),
            _since(1, month_start).label("requests_month"),
            func.max(AiUsageEvent.created_at).label("last_request_at"),
        )
        .join(User, User.id == AiUsageEvent.user_id)
        .filter(
            AiUsageEvent.status.in_(ACTIVE_STATUSES),
            AiUsageEvent.created_at >= earliest,
        )
        .group_by(AiUsageEvent.user_id, User.username)
    )
    if user_id is not None:
        query = query.filter(AiUsageEvent.user_id == user_id)

    return [
        AiUsageSummary(
            user_id=int(row.user_id),
            username=row.username,
            tokens_today=int(row.tokens_today or 0),
            tokens_week=int(row.tokens_week or 0),
            tokens_month=int(row.tokens_month or 0),
            cost_month_microunits=int(row.cost_month or 0),
            requests_month=int(row.requests_month or 0),
            last_request_at=row.last_request_at,
        )
        for row in query.all()
    ]


def usage_overview(db: Session, *, now: datetime | None = None) -> list[AiUsageSummary]:
    """Der Verbrauch aller Benutzer, die im Zeitraum ueberhaupt etwas verbraucht haben.

    Wer nichts verbraucht hat, fehlt — bewusst. Die Frage hinter dieser Ansicht
    ist „wohin fliessen die Kosten“, und eine Liste, in der zweihundert Kunden
    mit lauter Nullen stehen, beantwortet sie schlechter als eine mit den zwoelf,
    die tatsaechlich etwas verbrauchen.

    Sortiert nach Monatsverbrauch: die teuerste Zeile steht oben, weil sie die
    ist, wegen der jemand diese Seite aufruft.
    """
    rows = _usage_rows(db, user_id=None, now=now)
    return sorted(rows, key=lambda row: (-row.tokens_month, row.username.lower()))


def usage_for_user(db: Session, user: User, *, now: datetime | None = None) -> AiUsageSummary:
    """Der eigene Verbrauch. Ohne Ereignisse eine Null-Zeile statt ``None``.

    Ein Benutzer, der die KI noch nie benutzt hat, soll „0 von 50.000“ sehen und
    nicht eine leere Ansicht, die nach einem Fehler aussieht.
    """
    rows = _usage_rows(db, user_id=user.id, now=now)
    if rows:
        return rows[0]
    return AiUsageSummary(
        user_id=user.id, username=user.username, tokens_today=0, tokens_week=0,
        tokens_month=0, cost_month_microunits=0, requests_month=0, last_request_at=None,
    )


def verwaiste_reservierungen_abgleichen(db) -> int:
    """Schliesst beim Panel-Start Reservierungen ohne zugehoerige Nachricht.

    Die Verdichtung ist der einzige Pfad, der Kontingent reserviert und sofort
    committet, **ohne** dazu eine `AiMessage` anzulegen — sie fasst ja nur
    vorhandene Nachrichten zusammen, sie schreibt keine. Der bestehende
    Wiederanlauf `reconcile_interrupted_ai_streams` haengt aber genau an einer
    Nachricht im Zustand `streaming` und findet diese Zeile deshalb nie.

    Stirbt der Prozess zwischen Reservierung und Abschluss — ein Kill loest
    `asyncio.CancelledError` aus, und das ist eine BaseException, die weder der
    Anbieterfehler-Zweig hier noch das `except Exception` des Aufrufers faengt —
    bleibt das Ereignis fuer immer auf `reserved`. Das ist nicht bloss
    Buchhaltung: der Nebenlaeufigkeitszaehler in `reserve_ai_usage` zaehlt
    reservierte Ereignisse **ohne Zeitfenster**. Bei `concurrent_operations = 2`
    genuegen zwei solcher Abbrueche, und der Benutzer bekommt dauerhaft
    AiQuotaExceeded, obwohl nichts laeuft — von selbst loest sich das nie.

    Abgerechnet wird konservativ mit dem reservierten Wert, aus demselben Grund
    wie beim Stream-Wiederanlauf: nach einem Abbruch ist unbekannt, wie viele
    Tokens der Anbieter bereits geliefert hat, und verschenkte Tokens sind das
    schlechtere Risiko als ein paar zu viel gebuchte.

    Die Funktion steht hier und nicht bei der Verdichtung, obwohl nur diese die
    verwaisten Zeilen erzeugt: sie fegt ueber *alle* offenen Reservierungen und
    kennt weder Unterhaltung noch Faltung. Wer spaeter sucht, warum eine
    Reservierung beim Start geschlossen wurde, sieht dort nach, wo sie angelegt
    wurde.
    """
    offen = db.query(AiUsageEvent).filter(AiUsageEvent.status == "reserved").all()
    jetzt = datetime.now(timezone.utc)
    geschlossen = 0
    for event in offen:
        hat_nachricht = (
            db.query(AiMessage.id)
            .filter(AiMessage.request_id == event.request_id)
            .first()
        )
        if hat_nachricht is not None:
            # Gibt es eine Nachricht, ist `reconcile_interrupted_ai_streams`
            # zustaendig. Der kennt zusaetzlich deren Zustand und darf eine
            # gerade erst begonnene Anfrage nicht faelschlich abschliessen.
            continue
        complete_ai_usage(
            db,
            event,
            actual_tokens=event.reserved_tokens,
            actual_cost_microunits=event.reserved_cost_microunits,
            now=jetzt,
        )
        geschlossen += 1
    if geschlossen:
        db.commit()
    return geschlossen
