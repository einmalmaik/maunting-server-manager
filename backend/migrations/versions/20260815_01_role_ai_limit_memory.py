"""Der Memory-Vorrat hängt jetzt an der Rolle.

Revision ID: 20260815_01
Revises: 20260814_01
Create Date: 2026-08-15

Wieviele Memory-Einträge ein Bereich fasst, stand bisher als Konstante im
Memory-Service und galt für jeden gleich. Als Rollenspalte lässt sich der
Vorrat je Tarif staffeln; warum die Obergrenze trotzdem niedrig bleibt, steht
bei ``ai_limit_service.MAX_MEMORY_ENTRIES_MAX``.

Bestandsrollen bekommen NULL, und das ist hier der Punkt, nicht bloß der
bequeme Default: die Migration ändert für keine bestehende Rolle etwas. Der
Grund dafür ist aber nicht, dass NULL „unbegrenzt“ hieße — bei dieser Spalte
heißt es „nichts hinterlegt“, und ``resolve_scope_memory_limit`` löst das auf
genau die Grenze auf, die bisher fest im Memory-Service stand. Nichts ändert
sich also, weil der Rückfall dieselbe Zahl liefert wie der Code vorher, nicht
weil die Grenze verschwände.

Eine 100 als Bestandswert wäre deshalb nicht bloß überflüssig, sondern
schädlich: sie fröre dieselbe Zahl als Politik in die Datenbank ein — eine, die
der Betreiber nie gesetzt hat und die er beim nächsten Blick in die Oberfläche
für seine eigene halten müsste. NULL lässt das Feld leer und die Entscheidung
bei ihm.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260815_01"
down_revision: Union[str, None] = "20260814_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("role_ai_limits") as batch:
        batch.add_column(sa.Column("max_memory_entries", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("role_ai_limits") as batch:
        batch.drop_column("max_memory_entries")
