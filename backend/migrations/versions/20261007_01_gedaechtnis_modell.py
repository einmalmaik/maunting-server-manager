"""Gedaechtnis v2, Stufe 3: ein eigenes Modell fuer das Gedaechtnis.

Der Hintergrundschreiber, das Umschreiben des Altbestands und der Import lesen
Text und schreiben daraus Erinnerungen. Bis hierher nahmen sie das
Worker-Modell des Zugangs, sonst das Standardmodell. Betreiberentscheid vom
07.10.2026: ein eigener Platz am Zugang, wie die Ethik-Engine — Modell
waehlen, Preise fuellen sich aus dem Katalog, fertig.

- ``ai_providers`` bekommt ``memory_model``, den Schalter ``memory_enabled``
  und die drei Rollenpreise. Bestehende Zugaenge bleiben ohne: dann gilt das
  Gedaechtnismodell eines anderen Zugangs und ohne ein solches das
  Standardmodell — das Gedaechtnis laeuft also auch ohne Einrichtung weiter.

Revision ID: 20261007_01
Revises: 20261006_02
Create Date: 2026-10-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261007_01"
down_revision: Union[str, None] = "20261006_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PREISE = (
    "memory_input_price_micro_usd_per_million",
    "memory_output_price_micro_usd_per_million",
    "memory_cache_price_micro_usd_per_million",
)


def _spalten() -> set[str]:
    return {s["name"] for s in sa.inspect(op.get_bind()).get_columns("ai_providers")}


def upgrade() -> None:
    spalten = _spalten()
    with op.batch_alter_table("ai_providers") as batch:
        if "memory_model" not in spalten:
            batch.add_column(sa.Column("memory_model", sa.String(length=256), nullable=True))
        if "memory_enabled" not in spalten:
            batch.add_column(
                sa.Column(
                    "memory_enabled", sa.Boolean(), nullable=False,
                    server_default=sa.text("false"),
                )
            )
        for preis in PREISE:
            if preis not in spalten:
                batch.add_column(sa.Column(preis, sa.BigInteger(), nullable=True))


def downgrade() -> None:
    spalten = _spalten()
    with op.batch_alter_table("ai_providers") as batch:
        for spalte in (*PREISE, "memory_enabled", "memory_model"):
            if spalte in spalten:
                batch.drop_column(spalte)
