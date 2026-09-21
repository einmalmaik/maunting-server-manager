"""WebPush: die Tabelle, in die ein Browser seine Zustelladresse eintraegt.

Der `push`-Listener in `frontend/public/sw.js` war fertig, aber unerreichbar:
es gab keine Stelle, an der ein Browser haette sagen koennen, wohin zugestellt
werden soll. Damit endete jede Benachrichtigung bei geschlossener Anwendung im
Nichts.

Die Zeile haelt genau das, was `PushSubscription` im Browser herausgibt:
Endpunkt, oeffentlicher P-256-Punkt und Authentifizierungsgeheimnis. Der
Endpunkt ist eindeutig, nicht das Konto — Begruendung im Kopf von
`models/push_subscription.py`.

Das VAPID-Schluesselpaar braucht keine Migration: es entsteht beim ersten
Gebrauch und liegt in `panel_settings` (privater Teil DIS-verschluesselt).

Revision ID: 20260921_01
Revises: 20260918_01
Create Date: 2026-09-21
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260921_01"
down_revision: Union[str, None] = "20260918_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "push_subscriptions" in set(inspector.get_table_names()):
        return

    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            # Das ON DELETE CASCADE muss hier genauso stehen wie im Modell:
            # SQLite uebernimmt eine Zusage aus dem Modell nicht von selbst.
            # Ein geloeschtes Konto darf keine Zustelladresse hinterlassen.
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(length=255), nullable=False),
        sa.Column("auth", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("endpoint", name="uq_push_subscription_endpoint"),
    )
    op.create_index("ix_push_subscriptions_user", "push_subscriptions", ["user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "push_subscriptions" not in set(inspector.get_table_names()):
        return
    op.drop_index("ix_push_subscriptions_user", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
