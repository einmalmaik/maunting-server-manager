"""Gedaechtnis v2, Stufe 5: Staerke und naechtliche Pflege.

- ``haltbarkeit_tage``: nach wie vielen Tagen ohne Gebrauch die Praesenz auf
  1/e gefallen ist. Bestehende Zeilen: NULL, es gilt der Startwert ihrer
  Wichtigkeit.
- ``kopf_rang``: Wichtigkeit × Praesenz, einmal am Tag gerechnet; danach
  ordnet sich, was „im Kopf“ steht. Bestehende Zeilen bekommen ihn hier.
- ``ix_ai_memory_kopf`` ordnet nach ``kopf_rang`` statt nach Wichtigkeit.
- ``ai_memory_pflege``: wie weit die Pflege einen Bereich gelesen hat.

Revision ID: 20261008_02
Revises: 20261008_01
Create Date: 2026-10-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261008_02"
down_revision: Union[str, None] = "20261008_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ALT = ["scope_identity", "status", "angeheftet", "wichtigkeit", "created_at", "id"]
NEU = ["scope_identity", "status", "angeheftet", "kopf_rang", "created_at", "id"]

#: Wie `ai_memory_service.HALTBARKEIT_START` — hier festgeschrieben, damit die
#: Migration nicht vom Stand des Dienstes abhaengt.
_START = "CASE wichtigkeit WHEN 1 THEN 7 WHEN 2 THEN 14 WHEN 3 THEN 30 WHEN 4 THEN 90 ELSE 365 END"


def _inspektor():
    return sa.inspect(op.get_bind())


def _index_neu(spalten: list[str]) -> None:
    indizes = {i["name"] for i in _inspektor().get_indexes("ai_memory_entries")}
    if "ix_ai_memory_kopf" in indizes:
        op.drop_index("ix_ai_memory_kopf", table_name="ai_memory_entries")
    op.create_index("ix_ai_memory_kopf", "ai_memory_entries", spalten)


def upgrade() -> None:
    spalten = {s["name"] for s in _inspektor().get_columns("ai_memory_entries")}
    with op.batch_alter_table("ai_memory_entries") as batch:
        if "haltbarkeit_tage" not in spalten:
            batch.add_column(sa.Column("haltbarkeit_tage", sa.Float(), nullable=True))
        if "kopf_rang" not in spalten:
            batch.add_column(
                sa.Column("kopf_rang", sa.Float(), nullable=False, server_default=sa.text("0"))
            )
    # Der Exponent ist bei 700 gedeckelt: darunter meldet PostgreSQL bei
    # ``exp`` einen Unterlauf, statt 0 zu liefern.
    op.execute(
        "UPDATE ai_memory_entries SET kopf_rang = wichtigkeit * exp(-least(700, greatest(0, "
        "extract(epoch FROM now() - coalesce(last_used_at, created_at)) / 86400.0) "
        f"/ ({_START})))"
    )
    _index_neu(NEU)
    if not _inspektor().has_table("ai_memory_pflege"):
        op.create_table(
            "ai_memory_pflege",
            sa.Column("scope_identity", sa.String(length=128), primary_key=True),
            sa.Column("gepflegt_bis", sa.DateTime(timezone=True), nullable=False),
            sa.Column("gelaufen_am", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    if _inspektor().has_table("ai_memory_pflege"):
        op.drop_table("ai_memory_pflege")
    _index_neu(ALT)
    spalten = {s["name"] for s in _inspektor().get_columns("ai_memory_entries")}
    with op.batch_alter_table("ai_memory_entries") as batch:
        for spalte in ("kopf_rang", "haltbarkeit_tage"):
            if spalte in spalten:
                batch.drop_column(spalte)
