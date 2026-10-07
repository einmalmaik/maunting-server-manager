"""KI-Limits je Rolle: weniger Felder, Echtzeit in Minuten.

- Entfallen: ``requests_per_minute``, ``concurrent_operations``,
  ``monthly_cost_limit_cents``. Die Tokenlimits je Tag, Woche und Monat decken
  die Kosten ab; Anfragen pro Minute begrenzt das Panel ohnehin je IP.
- ``monthly_realtime_cost_limit_cents`` wird zu
  ``monthly_realtime_minutes_limit``. Ein Kostenbetrag lässt sich nicht in
  Minuten umrechnen, ohne einen Preis zu raten: ``0`` (gesperrt) bleibt ``0``,
  jeder andere Betrag wird „unbegrenzt“ — die Tokenlimits gelten weiter auch
  für Sprachsitzungen.
- ``ai_usage_events.realtime_seconds``: wie lange eine Sprachsitzung lief.

Revision ID: 20261008_03
Revises: 20261008_02
Create Date: 2026-10-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261008_03"
down_revision: Union[str, None] = "20261008_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ENTFALLEN = ("requests_per_minute", "concurrent_operations", "monthly_cost_limit_cents")


def _spalten(tabelle: str) -> set[str]:
    return {s["name"] for s in sa.inspect(op.get_bind()).get_columns(tabelle)}


def upgrade() -> None:
    limits = _spalten("role_ai_limits")
    with op.batch_alter_table("role_ai_limits") as batch:
        if "monthly_realtime_minutes_limit" not in limits:
            batch.add_column(sa.Column("monthly_realtime_minutes_limit", sa.Integer(), nullable=True))
    if "monthly_realtime_cost_limit_cents" in limits:
        op.execute(
            "UPDATE role_ai_limits SET monthly_realtime_minutes_limit = 0 "
            "WHERE monthly_realtime_cost_limit_cents = 0"
        )
    with op.batch_alter_table("role_ai_limits") as batch:
        for spalte in (*ENTFALLEN, "monthly_realtime_cost_limit_cents"):
            if spalte in limits:
                batch.drop_column(spalte)
    if "realtime_seconds" not in _spalten("ai_usage_events"):
        with op.batch_alter_table("ai_usage_events") as batch:
            batch.add_column(sa.Column("realtime_seconds", sa.Integer(), nullable=True))


def downgrade() -> None:
    if "realtime_seconds" in _spalten("ai_usage_events"):
        with op.batch_alter_table("ai_usage_events") as batch:
            batch.drop_column("realtime_seconds")
    limits = _spalten("role_ai_limits")
    with op.batch_alter_table("role_ai_limits") as batch:
        for spalte in (*ENTFALLEN, "monthly_realtime_cost_limit_cents"):
            if spalte not in limits:
                batch.add_column(sa.Column(spalte, sa.Integer(), nullable=True))
    if "monthly_realtime_minutes_limit" in limits:
        op.execute(
            "UPDATE role_ai_limits SET monthly_realtime_cost_limit_cents = 0 "
            "WHERE monthly_realtime_minutes_limit = 0"
        )
        with op.batch_alter_table("role_ai_limits") as batch:
            batch.drop_column("monthly_realtime_minutes_limit")
