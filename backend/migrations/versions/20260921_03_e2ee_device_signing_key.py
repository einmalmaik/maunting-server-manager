"""Der Signaturschluessel eines Geraets — Absenderbeglaubigung in Gruppen.

Eine Gruppe teilt sich einen symmetrischen Schluessel. Damit kann jedes
Mitglied jede Nachricht der Gruppe erzeugen, und bis 09/2026 nannte die
Nutzlast ihren Absender selbst (`sender_id`). Wer das Feld umschrieb, stand
bei allen anderen als jemand anderes im Verlauf.

Symmetrisch ist das nicht zu schliessen: ein MAC-Schluessel, den alle
pruefen koennen, koennen auch alle erzeugen. Jedes Geraet bekommt deshalb
zusaetzlich ein ECDSA-P-256-Paar und veroeffentlicht hier den oeffentlichen
Teil.

Nullable und leer fuer den Bestand: ein Geraet traegt ihn nach, sobald es
das naechste Mal startet. Bis dahin sind seine Nachrichten wie bisher
ungeprueft — ein harter Schnitt haette jede laufende Installation
stillgelegt.

Revision ID: 20260921_03
Revises: 20260921_02
Create Date: 2026-09-21
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260921_03"
down_revision: Union[str, None] = "20260921_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "user_e2ee_devices" not in set(inspector.get_table_names()):
        return

    columns = {col["name"] for col in inspector.get_columns("user_e2ee_devices")}
    if "signing_public_key_jwk" in columns:
        return

    with op.batch_alter_table("user_e2ee_devices") as batch_op:
        batch_op.add_column(sa.Column("signing_public_key_jwk", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "user_e2ee_devices" not in set(inspector.get_table_names()):
        return

    columns = {col["name"] for col in inspector.get_columns("user_e2ee_devices")}
    if "signing_public_key_jwk" not in columns:
        return

    with op.batch_alter_table("user_e2ee_devices") as batch_op:
        batch_op.drop_column("signing_public_key_jwk")
