"""Gedaechtnis standardmaessig aus, mit Einwilligungshinweis im Chat.

Revision ID: 20260809_03
Revises: 20260809_02
Create Date: 2026-08-09

Bisher lieferte `preference()` ohne gespeicherte Zeile `True`: das Gedaechtnis
war fuer jeden neuen Benutzer stillschweigend eingeschaltet. Bei einer
Funktion, deren Inhalt bei jeder Anfrage an einen externen KI-Anbieter geht,
ist das die falsche Voreinstellung — unabhaengig davon, wie nuetzlich sie ist.

Zwei neue Spalten steuern den Hinweis, den der Chat vor der ersten Nachricht
zeigt:

- `notice_last_shown_at` — nach einem "Nein" wird 24 Stunden nicht erneut
  gefragt.
- `notice_hidden` — "nicht mehr anzeigen". Beendet das Fragen, nicht die
  Moeglichkeit: unter Profil > Memory bleibt der Schalter erreichbar.

**Bestandsbenutzer behalten ihre Einstellung.** Wer bereits eine Zeile hat,
wird nicht umgeschaltet — nur der Standard fuer alle *ohne* Zeile aendert sich.
Das ist bewusst so: eine Migration, die eine aktive Funktion abschaltet, waere
eine Ueberraschung im Betrieb. Fuer Bestandsbenutzer ohne Zeile bedeutet es
allerdings, dass ihr Gedaechtnis ab jetzt aus ist, bis sie zustimmen — genau
das ist der Zweck der Aenderung.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260809_03"
down_revision: Union[str, None] = "20260809_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_memory_preferences") as batch:
        batch.add_column(
            sa.Column("notice_last_shown_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "notice_hidden", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        # Der Standard der Spalte folgt dem neuen Verhalten. Bestehende Zeilen
        # behalten ihren Wert — `server_default` wirkt nur auf neue.
        batch.alter_column(
            "enabled",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.false(),
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_memory_preferences") as batch:
        batch.alter_column(
            "enabled",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.true(),
        )
        batch.drop_column("notice_hidden")
        batch.drop_column("notice_last_shown_at")
