"""Push an eine Mailbox statt an ein Konto.

Die Zustellung im Vordergrund haengt seit `20260922_11` an der Mailbox: wer sie
abonniert hat, erfaehrt etwas, und der Server muss dafuer niemanden kennen.
WebPush war der letzte Weg, auf dem dieselbe Nachricht noch ueber ein Konto
lief — `push_subscriptions.user_id`. Damit wusste der Server bei jeder
Zustellung wieder, welches Konto Post bekommt, und zwar ausgerechnet fuer die
Mailboxen, die er sonst niemandem zuordnen kann.

Die neue Tabelle traegt kein `user_id` und keinen Fremdschluessel. Sie sagt
nur: in diese Mailbox faellt etwas, schick es an diese Adresse.

`push_subscriptions` bleibt stehen und wird nicht angeruehrt. Sie ist der
Bestand fuer die kontogebundenen Wege, die es noch gibt (Direktchat und Gruppe,
solange ihre Kennungen ableitbar sind); sie faellt, wenn in Stufe 4c
`recipient_id` faellt.

Revision ID: 20260923_01
Revises: 20260922_11
Create Date: 2026-09-23
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260923_01"
down_revision: Union[str, None] = "20260922_11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "e2ee_mailbox_push" in set(inspector.get_table_names()):
        return

    op.create_table(
        "e2ee_mailbox_push",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mailbox_id", sa.String(length=64), nullable=False),
        sa.Column("endpoint_hash", sa.String(length=64), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(length=255), nullable=False),
        sa.Column("auth", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("mailbox_id", "endpoint_hash", name="uq_mailbox_push_ziel"),
    )
    op.create_index("ix_e2ee_mailbox_push_mailbox", "e2ee_mailbox_push", ["mailbox_id"])
    op.create_index("ix_e2ee_mailbox_push_endpoint", "e2ee_mailbox_push", ["endpoint_hash"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "e2ee_mailbox_push" not in set(inspector.get_table_names()):
        return
    op.drop_index("ix_e2ee_mailbox_push_endpoint", table_name="e2ee_mailbox_push")
    op.drop_index("ix_e2ee_mailbox_push_mailbox", table_name="e2ee_mailbox_push")
    op.drop_table("e2ee_mailbox_push")
