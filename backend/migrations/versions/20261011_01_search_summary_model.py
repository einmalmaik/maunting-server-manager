"""Suchzusammenfassung (SearXNG AI Overviews) je KI-Anbieter.

Fügt den KI-Anbietern Felder für das Modell der neutralen Suchzusammenfassung
sowie Preisfelder hinzu.

Revision ID: 20261011_01
Revises: 20261008_04
Create Date: 2026-10-11
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261011_01"
down_revision: Union[str, None] = "20261008_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PREISE = (
    "search_summary_input_price_micro_usd_per_million",
    "search_summary_output_price_micro_usd_per_million",
    "search_summary_cache_price_micro_usd_per_million",
)


def _spalten() -> set[str]:
    return {s["name"] for s in sa.inspect(op.get_bind()).get_columns("ai_providers")}


def upgrade() -> None:
    spalten = _spalten()
    with op.batch_alter_table("ai_providers") as batch:
        if "search_summary_model" not in spalten:
            batch.add_column(sa.Column("search_summary_model", sa.String(length=256), nullable=True))
        if "search_summary_enabled" not in spalten:
            batch.add_column(
                sa.Column(
                    "search_summary_enabled", sa.Boolean(), nullable=False,
                    server_default=sa.text("false"),
                )
            )
        for preis in PREISE:
            if preis not in spalten:
                batch.add_column(sa.Column(preis, sa.BigInteger(), nullable=True))


def downgrade() -> None:
    spalten = _spalten()
    with op.batch_alter_table("ai_providers") as batch:
        for spalte in (*PREISE, "search_summary_enabled", "search_summary_model"):
            if spalte in spalten:
                batch.drop_column(spalte)
