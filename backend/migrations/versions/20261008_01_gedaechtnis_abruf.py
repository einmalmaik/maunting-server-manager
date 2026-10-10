"""Gedaechtnis v2, Stufe 4: Abruf fuer grosse Gedaechtnisse.

Bis hierher lud jede Chatnachricht alle sichtbaren Erinnerungen samt Vektoren
und oeffnete Namen und Titel aller Zeilen, nur um zu sortieren. Bei 100.000
Eintraegen waren das Sekunden vor dem ersten Token. Der Abruf
(``services/ai_gedaechtnis_abruf.py``) waehlt jetzt in der Datenbank aus:

- ``angeheftet``: steht immer „im Kopf“. Bestehende Zeilen: nein.
- ``indiziert_am``: wann Vektor und Wortindex zuletzt gerechnet wurden.
  Bestehende Zeilen bleiben NULL; der Takt rechnet sie nach.
- ``ai_memory_begriffe``: die Woerter jeder Erinnerung als Pruefwerte, fuer
  die Suche nach Woertern ohne Entschluesseln. Startet leer und fuellt sich
  ueber dasselbe Nachziehen.
- ``ix_ai_memory_kopf``: die Reihenfolge „im Kopf“ als Index.

Revision ID: 20261008_01
Revises: 20261007_01
Create Date: 2026-10-08
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261008_01"
down_revision: Union[str, None] = "20261007_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KOPF_SPALTEN = ["scope_identity", "status", "angeheftet", "wichtigkeit", "created_at", "id"]


def _inspektor():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    inspektor = _inspektor()
    spalten = {s["name"] for s in inspektor.get_columns("ai_memory_entries")}
    with op.batch_alter_table("ai_memory_entries") as batch:
        if "angeheftet" not in spalten:
            batch.add_column(
                sa.Column(
                    "angeheftet", sa.Boolean(), nullable=False,
                    server_default=sa.text("false"),
                )
            )
        if "indiziert_am" not in spalten:
            batch.add_column(sa.Column("indiziert_am", sa.DateTime(timezone=True), nullable=True))
    indizes = {i["name"] for i in _inspektor().get_indexes("ai_memory_entries")}
    if "ix_ai_memory_kopf" not in indizes:
        op.create_index("ix_ai_memory_kopf", "ai_memory_entries", KOPF_SPALTEN)
    if not _inspektor().has_table("ai_memory_begriffe"):
        op.create_table(
            "ai_memory_begriffe",
            sa.Column("begriff", sa.BigInteger(), nullable=False),
            sa.Column(
                "memory_id", sa.String(length=36),
                sa.ForeignKey("ai_memory_entries.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.PrimaryKeyConstraint("begriff", "memory_id"),
        )
        op.create_index("ix_ai_memory_begriffe_eintrag", "ai_memory_begriffe", ["memory_id"])


def downgrade() -> None:
    if _inspektor().has_table("ai_memory_begriffe"):
        op.drop_index("ix_ai_memory_begriffe_eintrag", table_name="ai_memory_begriffe")
        op.drop_table("ai_memory_begriffe")
    indizes = {i["name"] for i in _inspektor().get_indexes("ai_memory_entries")}
    if "ix_ai_memory_kopf" in indizes:
        op.drop_index("ix_ai_memory_kopf", table_name="ai_memory_entries")
    spalten = {s["name"] for s in _inspektor().get_columns("ai_memory_entries")}
    with op.batch_alter_table("ai_memory_entries") as batch:
        for spalte in ("indiziert_am", "angeheftet"):
            if spalte in spalten:
                batch.drop_column(spalte)
