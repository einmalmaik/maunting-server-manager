"""Der Passwort-Hinweis passt nicht in seine eigene Spalte.

`vault_hints.hint` war `VARCHAR(512)` — dieselbe Zahl, die das Schema als
Obergrenze fuer den **Klartext** erlaubt. Gespeichert wird aber der
verschluesselte Wert: AES-GCM legt IV und Tag dazu, Base64 streckt um ein
Drittel. Ein 512 Zeichen langer Hinweis wird so zu rund 720 Zeichen Ciphertext.

Auf PostgreSQL endete das Hinterlegen eines langen Hinweises deshalb in
`value too long for type character varying(512)` und damit in einem HTTP 500 —
auf SQLite fiel es nicht auf, weil SQLite Laengenangaben ignoriert. Die
Obergrenze gehoert an den Klartext (Schema), nicht an den Ciphertext (Spalte).

Revision ID: 20260922_01
Revises: 20260921_03
Create Date: 2026-09-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260922_01"
down_revision: Union[str, None] = "20260921_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "vault_hints" not in set(inspector.get_table_names()):
        return

    with op.batch_alter_table("vault_hints") as batch_op:
        batch_op.alter_column(
            "hint",
            existing_type=sa.String(length=512),
            type_=sa.Text(),
            existing_nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "vault_hints" not in set(inspector.get_table_names()):
        return

    # Zurueck geht nur, was hineinpasst: laengere Hinweise werden vorher
    # geleert, sonst bricht die Typaenderung an den Bestandsdaten.
    bind.execute(
        sa.text("DELETE FROM vault_hints WHERE length(hint) > 512")
    )
    with op.batch_alter_table("vault_hints") as batch_op:
        batch_op.alter_column(
            "hint",
            existing_type=sa.Text(),
            type_=sa.String(length=512),
            existing_nullable=False,
        )
