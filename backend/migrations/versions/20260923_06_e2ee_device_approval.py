"""Geraetefreigabe: is_approved fuer user_e2ee_devices

Wer das Passwort hat, liest trotzdem nicht mit: Ein neues Geraet muss
auf einem bestehenden Geraet bestaetigt werden, bevor es Nachrichten
oder Schluessel empfangen darf.

Fuer den Bestand werden alle bereits vorhandenen Geraete als bestaetigt markiert.

Revision ID: 20260923_06
Revises: 20260923_05
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260923_06"
down_revision = "20260923_05"
branch_labels = None
depends_on = None

TABELLE = "user_e2ee_devices"
SPALTE = "is_approved"


def _spalten() -> set[str]:
    bind = op.get_bind()
    pruefer = sa.inspect(bind)
    if TABELLE not in pruefer.get_table_names():
        return set()
    return {s["name"] for s in pruefer.get_columns(TABELLE)}


def upgrade() -> None:
    spalten = _spalten()
    if not spalten or SPALTE in spalten:
        return

    with op.batch_alter_table(TABELLE) as batch_op:
        batch_op.add_column(
            sa.Column(
                SPALTE,
                sa.Boolean(),
                nullable=False,
                # `sa.true()`, nicht `text("1")`: PostgreSQL nimmt fuer eine
                # Boolean-Spalte keine Ganzzahl als Vorgabe.
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    spalten = _spalten()
    if SPALTE not in spalten:
        return

    with op.batch_alter_table(TABELLE) as batch_op:
        batch_op.drop_column(SPALTE)
