"""Wetter-Widget-Token (nur für das Android-Homescreen-Widget).

Das Token ist absichtlich eng: es gilt nur für `POST /api/browser/wetter/widget`,
nicht für Suche, Konto oder Kopplung. Der Klartext wird einmal ausgegeben und
danach nur als Hash gespeichert.
"""

from __future__ import annotations

from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision: str = "20261012_01"
down_revision: str | None = "20261011_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "browser_widget_tokens" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "browser_widget_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False, server_default="browser.wetter"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_browser_widget_tokens_user_id", "browser_widget_tokens", ["user_id"])
    op.create_index("ix_browser_widget_tokens_token_hash", "browser_widget_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    if "browser_widget_tokens" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("browser_widget_tokens")
