"""Gruppenzustand: ein verschluesselter Block je Gruppe, mit Revision.

Eigene Rollen einer Gruppe standen bis hierher nirgends. Der Rechte-Dialog
meldete „Rolle erstellt" und legte sie auf React-State ab; beim Schliessen war
sie weg. Diese Tabelle ist die Ablage dafuer — und zwar eine, aus der der
Betreiber nichts lernt: `blob` ist ein Umschlag unter dem Gruppenschluessel,
der Schluessel liegt bei den Geraeten der Mitglieder.

`revision` ist der einzige Wert, den der Server versteht, und er muss ihn
verstehen: ohne monotone Revision koennte ein Mitschreibender einen alten
Stand zurueckspielen und damit einen Rechteentzug ruecknehmen, ohne je etwas
entschluesselt zu haben. Dieselbe Lehre wie beim Passwort-Tresor.

Die Revisionsnummer wird bewusst **nicht** vom Datenbankserver vergeben: der
schreibende Client nennt die Revision, die er ueberschreiben will, und das
Backend weist alles ab, was nicht genau eins weiter ist. Ein zweites Geraet,
das gleichzeitig schreibt, bekommt 409 und liest neu.

Ein bewusst fehlendes Feld: keine Spalte „zuletzt geschrieben von". Sie waere
eine Klartextzeile darueber, wer diese Gruppe verwaltet. Die Unterschrift des
schreibenden Geraets liegt **innerhalb** des Umschlags.

Revision ID: 20260922_10
Revises: 20260922_01
Create Date: 2026-09-22
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260922_10"
down_revision: Union[str, None] = "20260922_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "chat_group_configs" in set(inspector.get_table_names()):
        return

    op.create_table(
        "chat_group_configs",
        sa.Column(
            "group_id",
            sa.Integer(),
            # Das ON DELETE CASCADE steht hier genauso wie im Modell: eine
            # geloeschte Gruppe darf keinen verschluesselten Rest hinterlassen,
            # den niemand mehr oeffnen kann.
            sa.ForeignKey("chat_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("blob", sa.Text(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "chat_group_configs" not in set(inspector.get_table_names()):
        return
    op.drop_table("chat_group_configs")
