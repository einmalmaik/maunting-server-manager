"""Geraetefreigabe mit Beleg: Freigebender, Unterschrift und Sitzungskette

`is_approved` allein ist ein Wort des Servers. Mit Freigebendem und
Unterschrift pruefen die Clients selbst, ob ein Geraet von einem Geraet
freigegeben wurde, das sie schon kennen. `auth_family` bindet ein Geraet an
seine Sitzungskette, damit Entfernen sie sofort aussperrt.

Revision ID: 20260924_01
Revises: 20260923_06
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260924_01"
down_revision = "20260923_06"
branch_labels = None
depends_on = None

TABELLE = "user_e2ee_devices"
SPALTEN = (
    ("approved_by", sa.String(length=64)),
    ("approval_signature", sa.Text()),
    ("auth_family", sa.String(length=64)),
)


def _spalten() -> set[str]:
    pruefer = sa.inspect(op.get_bind())
    if TABELLE not in pruefer.get_table_names():
        return set()
    return {s["name"] for s in pruefer.get_columns(TABELLE)}


def upgrade() -> None:
    vorhanden = _spalten()
    if not vorhanden:
        return
    fehlend = [(name, typ) for name, typ in SPALTEN if name not in vorhanden]
    if not fehlend:
        return
    with op.batch_alter_table(TABELLE) as batch_op:
        for name, typ in fehlend:
            batch_op.add_column(sa.Column(name, typ, nullable=True))


def downgrade() -> None:
    vorhanden = _spalten()
    weg = [name for name, _ in SPALTEN if name in vorhanden]
    if not weg:
        return
    with op.batch_alter_table(TABELLE) as batch_op:
        for name in weg:
            batch_op.drop_column(name)
