"""Add AI role limits and audit context.

Revision ID: 20260801_02
Revises: 20260801_01
Create Date: 2026-08-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260801_02"
down_revision: Union[str, None] = "20260801_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ergänzt additive Limits sowie sichere Audit-Metadaten."""
    op.create_table(
        "role_ai_limits",
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("daily_token_limit", sa.Integer(), nullable=True),
        sa.Column("weekly_token_limit", sa.Integer(), nullable=True),
        sa.Column("monthly_token_limit", sa.Integer(), nullable=True),
        sa.Column("requests_per_minute", sa.Integer(), nullable=True),
        sa.Column("concurrent_operations", sa.Integer(), nullable=True),
        sa.Column("monthly_cost_limit_cents", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id"),
    )
    op.create_table(
        "ai_usage_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("reserved_cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("accounted_tokens", sa.BigInteger(), nullable=False),
        sa.Column("accounted_cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('reserved', 'completed', 'failed')",
            name="ck_ai_usage_events_status",
        ),
        sa.CheckConstraint("accounted_tokens >= 0", name="ck_ai_usage_events_tokens"),
        sa.CheckConstraint("reserved_tokens >= 0", name="ck_ai_usage_events_reserved_tokens"),
        sa.CheckConstraint(
            "accounted_cost_microunits >= 0",
            name="ck_ai_usage_events_cost",
        ),
        sa.CheckConstraint(
            "reserved_cost_microunits >= 0",
            name="ck_ai_usage_events_reserved_cost",
        ),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_usage_events_request_id", "ai_usage_events", ["request_id"], unique=True)
    op.create_index("ix_ai_usage_events_user_id", "ai_usage_events", ["user_id"], unique=False)
    op.create_index("ix_ai_usage_events_server_id", "ai_usage_events", ["server_id"], unique=False)
    op.create_index("ix_ai_usage_events_status", "ai_usage_events", ["status"], unique=False)
    op.create_index("ix_ai_usage_events_created_at", "ai_usage_events", ["created_at"], unique=False)

    op.add_column(
        "audit_logs",
        sa.Column("origin", sa.String(length=16), server_default="direct", nullable=False),
    )
    op.add_column(
        "audit_logs",
        sa.Column("correlation_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_audit_logs_correlation_id",
        "audit_logs",
        ["correlation_id"],
        unique=False,
    )


def downgrade() -> None:
    """Entfernt ausschließlich die in dieser Revision ergänzten Strukturen."""
    op.drop_index("ix_audit_logs_correlation_id", table_name="audit_logs")
    op.drop_column("audit_logs", "correlation_id")
    op.drop_column("audit_logs", "origin")
    op.drop_index("ix_ai_usage_events_created_at", table_name="ai_usage_events")
    op.drop_index("ix_ai_usage_events_status", table_name="ai_usage_events")
    op.drop_index("ix_ai_usage_events_server_id", table_name="ai_usage_events")
    op.drop_index("ix_ai_usage_events_user_id", table_name="ai_usage_events")
    op.drop_index("ix_ai_usage_events_request_id", table_name="ai_usage_events")
    op.drop_table("ai_usage_events")
    op.drop_table("role_ai_limits")
