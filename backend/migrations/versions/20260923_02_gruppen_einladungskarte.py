"""Die Einladungskarte einer Gruppe, verschluesselt

Bis hierher kam die Vorschau eines Einladungslinks aus `chat_groups.name`,
`description` und `avatar_url` — der Server musste sie also kennen und lieferte
sie ohne Anmeldung aus. Mit Stufe 6 wandern genau diese Felder in den
verschluesselten Block, und die Karte bliebe leer.

`invite_card` haelt sie stattdessen als Chiffretext. Der Schluessel faellt aus
dem Gruppengeheimnis und reist hinter der Raute im Link; dieser Server sieht
ihn nie.

Revision ID: 20260923_02
Revises: 20260923_01
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260923_02"
down_revision = "20260923_01"
branch_labels = None
depends_on = None


def _hat_spalte(tabelle: str, spalte: str) -> bool:
    """Idempotent, wie jede Migration hier.

    Eine Entwicklungsdatenbank hat den Stand oft schon; ein zweiter Lauf soll
    dann nichts tun statt abzubrechen.
    """
    bind = op.get_bind()
    pruefer = sa.inspect(bind)
    if tabelle not in pruefer.get_table_names():
        return False
    return any(s["name"] == spalte for s in pruefer.get_columns(tabelle))


def upgrade() -> None:
    if not _hat_spalte("chat_groups", "invite_card"):
        op.add_column("chat_groups", sa.Column("invite_card", sa.Text(), nullable=True))


def downgrade() -> None:
    if _hat_spalte("chat_groups", "invite_card"):
        op.drop_column("chat_groups", "invite_card")
