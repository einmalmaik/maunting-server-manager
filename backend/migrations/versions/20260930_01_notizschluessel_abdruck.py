"""Ein Notizschluessel je Konto statt je Geraet.

``users.notes_key_abdruck`` haelt den Fingerabdruck des Schluessels, der fuer
alle Geraete eines Kontos gilt, ``notes_key_stand``/``_geraet``/``_signatur``
Stand und Unterschrift des Geraets, das ihn gesetzt hat. Bis 5.0.2 erzeugte jedes
Geraet beim ersten Speichern einen eigenen und behielt ihn, und was eines
schrieb, konnten die anderen nicht lesen.

Revision ID: 20260930_01
Revises: 20260929_02
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260930_01"
down_revision: Union[str, None] = "20260929_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _spalten() -> set[str]:
    return {s["name"] for s in sa.inspect(op.get_bind()).get_columns("users")}


_NEU = (
    ("notes_key_abdruck", sa.String(length=32)),
    ("notes_key_stand", sa.Integer()),
    ("notes_key_geraet", sa.String(length=64)),
    ("notes_key_signatur", sa.String(length=128)),
)


def upgrade() -> None:
    vorhanden = _spalten()
    for name, typ in _NEU:
        if name not in vorhanden:
            op.add_column("users", sa.Column(name, typ, nullable=True))


def downgrade() -> None:
    vorhanden = _spalten()
    for name, _ in reversed(_NEU):
        if name in vorhanden:
            op.drop_column("users", name)
