"""Add social, achievements, activity times, presence, and e2ee blind envelopes.

Revision ID: 20260907_01
Revises: 20260906_02
Create Date: 2026-09-07
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260907_01"
down_revision: Union[str, None] = "20260906_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # 1. users Spalten: social_privacy, social_e2ee_public_key
    if "users" in tables:
        user_cols = {c["name"] for c in inspector.get_columns("users")}
        if "social_privacy" not in user_cols:
            op.add_column(
                "users",
                sa.Column("social_privacy", sa.String(length=16), server_default="friends", nullable=False),
            )
        if "social_e2ee_public_key" not in user_cols:
            op.add_column(
                "users",
                sa.Column("social_e2ee_public_key", sa.Text(), nullable=True),
            )

    # 2. user_achievements
    if "user_achievements" not in tables:
        op.create_table(
            "user_achievements",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("achievement_id", sa.String(length=64), nullable=False, index=True),
            sa.Column("unlocked_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", "achievement_id", name="uq_user_achievements_user_achievement"),
        )

    # 3. user_activity_times
    if "user_activity_times" not in tables:
        op.create_table(
            "user_activity_times",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("category", sa.String(length=32), server_default="general", nullable=False, index=True),
            sa.Column("seconds", sa.Integer(), server_default="0", nullable=False),
            sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", "category", name="uq_user_activity_times_user_category"),
        )

    # 4. user_friends
    if "user_friends" not in tables:
        op.create_table(
            "user_friends",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("friend_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", "friend_id", name="uq_user_friends_user_friend"),
        )

    # 5. user_presences
    if "user_presences" not in tables:
        op.create_table(
            "user_presences",
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, nullable=False),
            sa.Column("status", sa.String(length=16), server_default="online", nullable=False),
            sa.Column("device_type", sa.String(length=16), server_default="web", nullable=False),
            sa.Column("custom_status", sa.String(length=128), nullable=True),
            sa.Column("activity_label", sa.String(length=128), nullable=True),
            sa.Column("activity_detail", sa.String(length=128), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    # 6. e2ee_blind_envelopes
    if "e2ee_blind_envelopes" not in tables:
        op.create_table(
            "e2ee_blind_envelopes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
            sa.Column("blind_mailbox_id", sa.String(length=64), nullable=False, index=True),
            sa.Column("ciphertext_envelope", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "e2ee_blind_envelopes" in tables:
        op.drop_table("e2ee_blind_envelopes")
    if "user_presences" in tables:
        op.drop_table("user_presences")
    if "user_friends" in tables:
        op.drop_table("user_friends")
    if "user_activity_times" in tables:
        op.drop_table("user_activity_times")
    if "user_achievements" in tables:
        op.drop_table("user_achievements")

    if "users" in tables:
        user_cols = {c["name"] for c in inspector.get_columns("users")}
        if "social_e2ee_public_key" in user_cols:
            op.drop_column("users", "social_e2ee_public_key")
        if "social_privacy" in user_cols:
            op.drop_column("users", "social_privacy")
