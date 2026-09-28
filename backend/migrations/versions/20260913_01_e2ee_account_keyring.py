"""Kontoweiter E2EE-Schluesselbund statt Schluessel pro Geraet.

Bisher trug ``users.social_e2ee_public_key`` den Schluessel *eines* Geraets:
der private Teil lag in der IndexedDB des jeweiligen Browsers, und jedes neue
Geraet hat den Servereintrag mit seinem eigenen Schluessel ueberschrieben.
Danach konnte keine Seite mehr lesen, was die andere geschrieben hatte.

Diese Migration legt daneben den verpackten Schluesselbund ab. Er enthaelt den
privaten Kontoschluessel, clientseitig mit Argon2id aus dem
Wiederherstellungsschluessel des Benutzers verschluesselt. Der Server speichert
ihn, kann ihn aber nicht oeffnen.

``social_e2ee_keyring_version`` ist kein Schema-Zaehler, sondern die Schranke
gegen verlorene Rettungen: adoptieren zwei Geraete gleichzeitig ihren alten
Geraetesschluessel, faellt der zweite Schreibvorgang mit 409 auf, statt den
geretteten Schluessel des ersten stillschweigend zu ueberschreiben.

Revision ID: 20260913_01
Revises: 20260907_07
Create Date: 2026-09-13
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260913_01"
down_revision: Union[str, None] = "20260907_07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "users" not in set(inspector.get_table_names()):
        return

    columns = {col["name"] for col in inspector.get_columns("users")}

    with op.batch_alter_table("users") as batch_op:
        if "social_e2ee_wrapped_keyring" not in columns:
            batch_op.add_column(sa.Column("social_e2ee_wrapped_keyring", sa.Text(), nullable=True))
        if "social_e2ee_keyring_version" not in columns:
            # server_default, damit bestehende Zeilen nicht auf NULL stehen
            # bleiben: die Versionspruefung vergleicht mit einer Zahl.
            batch_op.add_column(
                sa.Column(
                    "social_e2ee_keyring_version",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "users" not in set(inspector.get_table_names()):
        return

    columns = {col["name"] for col in inspector.get_columns("users")}

    with op.batch_alter_table("users") as batch_op:
        if "social_e2ee_keyring_version" in columns:
            batch_op.drop_column("social_e2ee_keyring_version")
        if "social_e2ee_wrapped_keyring" in columns:
            batch_op.drop_column("social_e2ee_wrapped_keyring")
