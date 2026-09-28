"""Blinder Besitznachweis je Mailbox: Kennung und Verifier, sonst nichts.

Die Mailbox-Kennung war bis hierher aus zwei kleinen Ganzzahlen nachrechenbar
— sha256("msm:group:<id>") und sha256("msm:dm:<min>:<max>"). Die einzige
Schranke davor war deshalb `assert_mailbox_participant`, und die funktioniert
nur, **weil** der Server die Mitgliedschaften kennt. Wer die Mitgliedschaften
loswerden will, muss zuerst eine Schranke haben, die ohne sie auskommt.

Diese Tabelle ist sie, nach dem Vorbild von `vault_blind_buckets`: der Client
leitet ein Token aus eigenem Schluesselmaterial ab, der Server speichert nur
dessen SHA-256. Kein `user_id`, kein Fremdschluessel, keine Adresse — es gibt
hier nichts, was auf einen Menschen zeigt, und deshalb auch nichts, was ein
Datenbankabzug darueber verraet.

In dieser Stufe ist der Nachweis ein **zweites** Schloss vor dem ersten, nicht
sein Ersatz. Solange die Mitgliedschaft eine Datenbankzeile ist, kann ein
hinausgeworfenes Mitglied das Gruppengeheimnis kennen und sich den Nachweis
selbst ausrechnen; erst MLS traegt die Zugehoerigkeit kryptographisch.

Revision ID: 20260922_11
Revises: 20260922_10
Create Date: 2026-09-22
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260922_11"
down_revision: Union[str, None] = "20260922_10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "e2ee_blind_mailboxes" in set(inspector.get_table_names()):
        return

    op.create_table(
        "e2ee_blind_mailboxes",
        sa.Column("mailbox_id", sa.String(length=64), primary_key=True),
        sa.Column("auth_verifier", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "e2ee_blind_mailboxes" not in set(inspector.get_table_names()):
        return
    op.drop_table("e2ee_blind_mailboxes")
