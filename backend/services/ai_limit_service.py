"""Auflösung und Persistenz rollenbasierter KI-Limits.

Die Regeln sind absichtlich klein und deterministisch:
- hat *keine* Rolle des Benutzers eine Konfiguration, gilt „unbegrenzt“,
- unter den konfigurierten Rollen gewinnt der höchste endliche Wert,
- ein explizites ``None`` gewinnt als „unbegrenzt“.

Die erste Regel ist bewusst so und war früher anders: eine leere Zeilenmenge
ergab über ``max(..., default=0)`` ein effektives Limit von **0** und damit eine
KI, die auf jeder frischen Installation jede Anfrage mit „Kontingent
ausgeschöpft“ abwies — auch für Owner und Admin. Das war kein sicherer Default,
sondern ein stiller Totalausfall. Die Zugangsgrenze zur KI ist ``ai.chat.use``;
die Limits hier sind Kostensteuerung. Solange der Betreiber dazu gar nichts
hinterlegt hat, darf MSM ihm keine Politik unterstellen.

Sobald **mindestens eine** Rolle des Benutzers konfiguriert ist, gilt wieder die
alte Auflösung: unkonfigurierte Rollen tragen nichts bei, der höchste Wert der
konfigurierten gewinnt. Eine zusätzliche, privilegierte Rolle erhöht damit das
Kontingent (Zielpunkt 6.1) und eine bewusst auf 0 gesetzte Rolle sperrt, solange
keine andere Rolle mehr erlaubt.

Verbrauch wird erst an den späteren Provider-/Chat-Endpunkten gezählt. Dieses
Modul stellt dafür die zentrale, backendseitige Grenzauflösung bereit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models import Role, RoleAiLimit, User
from services.role_service import effective_user_role_ids


# Genau die Breite von PostgreSQL INTEGER (2^31-1). Die drei Tokenspalten in
# `models/role_ai_limit.py` sind INTEGER; eine hoehere Obergrenze hier haette die
# Oberflaeche Werte anbieten lassen, die beim Speichern in einen
# NumericValueOutOfRange laufen — den der Router als „gleichzeitige Aenderung“
# (HTTP 409) meldet, also mit einer Ursache, die es gar nicht gibt.
TOKEN_LIMIT_MAX = 2_147_483_647
REQUESTS_PER_MINUTE_MAX = 10_000
CONCURRENT_OPERATIONS_MAX = 100
MONTHLY_COST_LIMIT_CENTS_MAX = 1_000_000_000
# Hoechster Rang aus `ai_reasoning.RANGFOLGE` (minimal..max). Bewusst als Zahl
# hier statt als Import: dieses Modul soll nicht von der Denklogik abhaengen,
# und `test_ai_reasoning_limits.py` sichert zu, dass beide Werte gleich bleiben.
MAX_REASONING_EFFORT_MAX = 6

LIMIT_FIELDS = (
    "daily_token_limit",
    "weekly_token_limit",
    "monthly_token_limit",
    "requests_per_minute",
    "concurrent_operations",
    "monthly_cost_limit_cents",
    # Kein Kontingent, sondern eine Obergrenze fuer die Denktiefe — passt aber
    # in genau dieselbe Aufloesung: "None heisst unbegrenzt", "der hoechste
    # Wert der konfigurierten Rollen gewinnt", "keine Rolle konfiguriert heisst
    # unbegrenzt". Eine zweite Aufloesung daneben waere eine zweite Wahrheit.
    "max_reasoning_effort",
)
LIMIT_MAXIMA = {
    "daily_token_limit": TOKEN_LIMIT_MAX,
    "weekly_token_limit": TOKEN_LIMIT_MAX,
    "monthly_token_limit": TOKEN_LIMIT_MAX,
    "requests_per_minute": REQUESTS_PER_MINUTE_MAX,
    "concurrent_operations": CONCURRENT_OPERATIONS_MAX,
    "monthly_cost_limit_cents": MONTHLY_COST_LIMIT_CENTS_MAX,
    "max_reasoning_effort": MAX_REASONING_EFFORT_MAX,
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
    #: Hoechste erlaubte Denkstufe als Rang; ``None`` heisst unbegrenzt.
    max_reasoning_effort: int | None


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
    """Löst ein Feld unter den *konfigurierten* Rollen auf.

    ``rows`` ist hier garantiert nicht leer — den leeren Fall behandelt
    ``resolve_effective_limits`` vorher, weil er eine andere Bedeutung hat
    („gar keine Politik hinterlegt“ statt „auf 0 gesetzt“).
    """
    configured = [getattr(row, field) for row in rows]
    if any(value is None for value in configured):
        return None
    return max(int(value) for value in configured)


UNLIMITED_AI_LIMITS = EffectiveAiLimits(**{field: None for field in LIMIT_FIELDS})


def resolve_effective_limits(db: Session, user: User) -> EffectiveAiLimits:
    """Vereinigt Limits aller effektiven Rollen, ohne eine Anfrage zu zählen."""
    role_ids = effective_user_role_ids(db, user)
    rows = (
        db.query(RoleAiLimit).filter(RoleAiLimit.role_id.in_(role_ids)).all()
        if role_ids
        else []
    )
    if not rows:
        # Keine einzige Rolle des Benutzers hat ein KI-Kontingent hinterlegt.
        # Siehe Modul-Docstring: das ist „nicht konfiguriert“, nicht „gesperrt“.
        return UNLIMITED_AI_LIMITS
    return EffectiveAiLimits(
        **{field: _resolve_field(rows, field) for field in LIMIT_FIELDS}
    )
