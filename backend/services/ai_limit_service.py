"""Auflösung und Persistenz rollenbasierter KI-Limits.

Die Regeln sind absichtlich klein und deterministisch:
- fehlende Rollenkonfiguration trägt 0 bei (sicherer Default),
- der höchste endliche Wert gewinnt,
- ein explizites ``None`` gewinnt als „unbegrenzt“.

Verbrauch wird erst an den späteren Provider-/Chat-Endpunkten gezählt. Dieses
Modul stellt dafür die zentrale, backendseitige Grenzauflösung bereit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models import Role, RoleAiLimit, User
from services.role_service import effective_user_role_ids


TOKEN_LIMIT_MAX = 1_000_000_000_000
REQUESTS_PER_MINUTE_MAX = 10_000
CONCURRENT_OPERATIONS_MAX = 100
MONTHLY_COST_LIMIT_CENTS_MAX = 1_000_000_000

LIMIT_FIELDS = (
    "daily_token_limit",
    "weekly_token_limit",
    "monthly_token_limit",
    "requests_per_minute",
    "concurrent_operations",
    "monthly_cost_limit_cents",
)
LIMIT_MAXIMA = {
    "daily_token_limit": TOKEN_LIMIT_MAX,
    "weekly_token_limit": TOKEN_LIMIT_MAX,
    "monthly_token_limit": TOKEN_LIMIT_MAX,
    "requests_per_minute": REQUESTS_PER_MINUTE_MAX,
    "concurrent_operations": CONCURRENT_OPERATIONS_MAX,
    "monthly_cost_limit_cents": MONTHLY_COST_LIMIT_CENTS_MAX,
}


@dataclass(frozen=True)
class EffectiveAiLimits:
    """Unveränderliche effektive KI-Grenzen eines Benutzers."""

    daily_token_limit: int | None
    weekly_token_limit: int | None
    monthly_token_limit: int | None
    requests_per_minute: int | None
    concurrent_operations: int | None
    monthly_cost_limit_cents: int | None


def get_role_limit(db: Session, role_id: int) -> RoleAiLimit | None:
    """Liest eine explizite Rollenkonfiguration oder ``None``."""
    return db.query(RoleAiLimit).filter(RoleAiLimit.role_id == role_id).first()


def set_role_limit(
    db: Session,
    role_id: int,
    values: dict[str, int | None],
) -> RoleAiLimit:
    """Ersetzt alle KI-Limits einer existierenden Rolle in der offenen Transaktion."""
    if db.query(Role.id).filter(Role.id == role_id).first() is None:
        raise ValueError("Rolle nicht gefunden")
    if set(values) != set(LIMIT_FIELDS):
        raise ValueError("Unvollständige KI-Limit-Konfiguration")
    for field, maximum in LIMIT_MAXIMA.items():
        value = values[field]
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
            raise ValueError(f"Ungültiger Wert für {field}")

    row = get_role_limit(db, role_id)
    if row is None:
        row = RoleAiLimit(role_id=role_id, **values)
        db.add(row)
    else:
        for field in LIMIT_FIELDS:
            setattr(row, field, values[field])
        row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row


def _resolve_field(rows: list[RoleAiLimit], field: str) -> int | None:
    """Löst ein Feld nach „unbegrenzt vor Maximum vor sicherem Nullwert“ auf."""
    configured = [getattr(row, field) for row in rows]
    if any(value is None for value in configured):
        return None
    return max((int(value) for value in configured), default=0)


def resolve_effective_limits(db: Session, user: User) -> EffectiveAiLimits:
    """Vereinigt Limits aller effektiven Rollen, ohne eine Anfrage zu zählen."""
    role_ids = effective_user_role_ids(db, user)
    rows = (
        db.query(RoleAiLimit).filter(RoleAiLimit.role_id.in_(role_ids)).all()
        if role_ids
        else []
    )
    return EffectiveAiLimits(
        **{field: _resolve_field(rows, field) for field in LIMIT_FIELDS}
    )
