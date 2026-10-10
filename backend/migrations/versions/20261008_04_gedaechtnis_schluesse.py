"""Gedächtnis v2: Schlüsse der nächtlichen Pflege.

- ``ai_memory_entries.art`` fasst ``schluss``: eine Erinnerung, die die Pflege
  aus mindestens zwei anderen geschlossen hat.
- ``ai_memory_belege``: worauf sich ein Schluss stützt. Fällt ein Beleg weg,
  fällt die Zeile mit (ON DELETE CASCADE); vergessen wird der Schluss im
  Dienst (`ai_memory_service._schluesse_entkraeften`).

Das Downgrade löscht die Schlüsse: ohne ihre Belege sähen sie aus wie
Gesagtes.

Revision ID: 20261008_04
Revises: 20261008_03
Create Date: 2026-10-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261008_04"
down_revision: Union[str, None] = "20261008_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ALT = "'fakt', 'vorliebe', 'anweisung', 'ereignis', 'plan', 'beziehung', 'wissen'"
NEU = ALT + ", 'schluss'"


def _inspektor():
    return sa.inspect(op.get_bind())


def _art_pruefung(arten: str) -> None:
    pruefungen = {p["name"] for p in _inspektor().get_check_constraints("ai_memory_entries")}
    with op.batch_alter_table("ai_memory_entries") as batch:
        if "ck_ai_memory_entries_art" in pruefungen:
            batch.drop_constraint("ck_ai_memory_entries_art", type_="check")
        batch.create_check_constraint("ck_ai_memory_entries_art", f"art IS NULL OR art IN ({arten})")


def upgrade() -> None:
    _art_pruefung(NEU)
    if not _inspektor().has_table("ai_memory_belege"):
        op.create_table(
            "ai_memory_belege",
            sa.Column(
                "schluss_id", sa.String(length=36),
                sa.ForeignKey("ai_memory_entries.id", ondelete="CASCADE"), primary_key=True,
            ),
            sa.Column(
                "beleg_id", sa.String(length=36),
                sa.ForeignKey("ai_memory_entries.id", ondelete="CASCADE"), primary_key=True,
            ),
        )
        op.create_index("ix_ai_memory_belege_beleg", "ai_memory_belege", ["beleg_id"])


def downgrade() -> None:
    op.execute("DELETE FROM ai_memory_entries WHERE art = 'schluss'")
    if _inspektor().has_table("ai_memory_belege"):
        op.drop_table("ai_memory_belege")
    _art_pruefung(ALT)
