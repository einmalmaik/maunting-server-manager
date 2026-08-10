"""KI-Meldungen sind nicht dasselbe wie E-Mails.

Revision ID: 20260810_03
Revises: 20260810_02
Create Date: 2026-08-10

Die Glocke oben rechts war ein einzelner Schalter fuer ``email_notifications``.
Er steuerte ausschliesslich den Versand von E-Mails — in der Anwendung selbst
bewirkte er nichts.

Seit die KI Auftraege im Hintergrund zu Ende fuehrt, gibt es aber etwas zu
melden: ein Lauf ist fertig, oder er wartet auf eine Bestaetigung, waehrend man
gerade auf einer anderen Seite ist. Das an denselben Schalter zu haengen waere
falsch: die KI verschickt keine E-Mails, und wer keine Post will, will deswegen
nicht auch keinen Hinweis mehr, dass ein laufender Auftrag auf ihn wartet.

Standard ist ``true``. Wer nichts einstellt, bekommt die Hinweise — sie sind
der Ersatz fuer das Zusehen, das man sich mit der Hintergrundausfuehrung gerade
erspart hat.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260810_03"
down_revision: Union[str, None] = "20260810_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "ai_notifications",
                sa.Boolean(),
                nullable=False,
                # Ohne Servervorgabe schlaegt das Hinzufuegen einer NOT-NULL-Spalte
                # auf einer gefuellten Tabelle fehl — hier stehen echte Benutzer drin.
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("ai_notifications")
