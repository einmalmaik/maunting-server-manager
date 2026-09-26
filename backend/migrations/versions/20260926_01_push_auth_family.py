"""Push-Zustelladressen kennen die Sitzung, die sie angelegt hat.

Revision ID: 20260926_01
Revises: 20260924_10
Create Date: 2026-09-26

Bis 09/2026 bekam ein entferntes oder gestohlenes Gerät weiter
Benachrichtigungen: das Aussperren sperrte die Refresh-Familie, aber die
Zustelladresse des Geräts blieb stehen, und nichts verband sie mit der
gesperrten Sitzung. ``auth_family`` ist diese Verbindung; beim Sperren fällt
die Zeile samt ihren Mailbox-Zustellungen (`webpush_service.austragen_familie`).

Bestandszeilen bleiben NULL. Sie bekommen die Familie bei der nächsten
Anmeldung, denn der Client meldet seine Adresse bei jeder Anmeldung neu.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260926_01"
down_revision: Union[str, None] = "20260924_10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _spalten() -> set[str]:
    return {s["name"] for s in sa.inspect(op.get_bind()).get_columns("push_subscriptions")}


def upgrade() -> None:
    # Wie 20260921_02: eine Datenbank aus `create_all` hat die Spalte schon.
    if "auth_family" in _spalten():
        return
    with op.batch_alter_table("push_subscriptions") as batch:
        batch.add_column(sa.Column("auth_family", sa.String(length=64), nullable=True))
    op.create_index("ix_push_subscriptions_auth_family", "push_subscriptions", ["auth_family"])


def downgrade() -> None:
    if "auth_family" not in _spalten():
        return
    # Index vor der Spalte: sonst baut SQLite im Batch die Tabelle mit einem
    # Index auf eine Spalte neu, die es nicht mehr gibt.
    op.drop_index("ix_push_subscriptions_auth_family", table_name="push_subscriptions")
    with op.batch_alter_table("push_subscriptions") as batch:
        batch.drop_column("auth_family")
