"""Give AI memory entries an origin and a usage record.

Revision ID: 20260808_03
Revises: 20260808_02
Create Date: 2026-08-08

Das Memory war ein Schluessel-Wert-Speicher: hinterlegt, ausgelesen, fertig.
Zwei Dinge fehlten, um daraus ein brauchbares Gedaechtnis zu machen.

**Herkunft.** Bisher war nicht unterscheidbar, ob ein Eintrag vom Benutzer
stammt oder von der KI abgeleitet wurde. Sobald die KI selbst schreiben darf
(Werkzeug `remember`), ist das der Unterschied zwischen "du hast gesagt, du
willst 8 GB" und "ich habe vermutet, dass du 8 GB willst". Bestehende
Eintraege sind alle von Hand entstanden und bekommen deshalb `user`.

**Nutzung.** `provider_memory_context` haengte die Eintraege alphabetisch nach
Schluessel aneinander und brach bei 6.000 Zeichen ab. Was hinten stand,
verschwand — unabhaengig davon, wie wichtig es war. Das ist kein
Auswahlverfahren, sondern ein Abschneiden. Mit `use_count` und `last_used_at`
kann die Auswahl stattdessen dem folgen, was tatsaechlich gebraucht wird.

Bewusst **kein** Vektorfeld: bei hoechstens 100 Eintraegen je Scope passt in
aller Regel alles gleichzeitig in den Kontext. Dann liefert das Sprachmodell
das Verstaendnis — sprachunabhaengig, ohne Index, ohne Embedding-Anbieter. Ein
Embedding waere spaeter eine zusaetzliche Spalte neben diesen hier, kein Umbau.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260808_03"
down_revision: Union[str, None] = "20260808_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_memory_entries") as batch:
        # server_default, damit bestehende Zeilen nicht NULL werden. Der
        # Default bleibt danach stehen: er ist auch fuer neue Zeilen richtig,
        # die ueber die bestehende Profil-Oberflaeche entstehen.
        batch.add_column(
            sa.Column("origin", sa.String(length=8), nullable=False, server_default="user")
        )
        batch.add_column(
            sa.Column("use_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_check_constraint(
            "ck_ai_memory_entries_origin", "origin IN ('user', 'ai')"
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_memory_entries") as batch:
        batch.drop_constraint("ck_ai_memory_entries_origin", type_="check")
        batch.drop_column("last_used_at")
        batch.drop_column("use_count")
        batch.drop_column("origin")
