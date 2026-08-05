"""Idempotente KI-Kontingentreservierung vor einem Provider-Aufruf.

Die User-Zeile wird in PostgreSQL gesperrt, damit parallele Anfragen desselben
Benutzers nicht beide dieselbe freie Quote sehen. Die eindeutige Request-ID
verhindert zusätzlich Doppelzählung bei Retries.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import AiUsageEvent, User
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
