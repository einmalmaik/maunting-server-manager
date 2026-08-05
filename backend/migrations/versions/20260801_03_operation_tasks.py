"""Add persistent operation tasks.

Revision ID: 20260801_03
Revises: 20260801_02
Create Date: 2026-08-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260801_03"
down_revision: Union[str, None] = "20260801_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Erstellt ausschließlich secret-freie Task-Metadaten."""
    op.create_table(
        "operation_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("retry_of_id", sa.String(length=36), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_operation_tasks_status",
        ),
        sa.CheckConstraint(
            "origin IN ('direct', 'ai', 'external', 'system')",
            name="ck_operation_tasks_origin",
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_operation_tasks_attempt"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["retry_of_id"], ["operation_tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_user_id",
            "task_type",
            "idempotency_key_hash",
            name="uq_operation_tasks_actor_type_idempotency",
        ),
    )
    op.create_index("ix_operation_tasks_task_type", "operation_tasks", ["task_type"], unique=False)
    op.create_index("ix_operation_tasks_actor_user_id", "operation_tasks", ["actor_user_id"], unique=False)
    op.create_index("ix_operation_tasks_correlation_id", "operation_tasks", ["correlation_id"], unique=False)
    op.create_index("ix_operation_tasks_status", "operation_tasks", ["status"], unique=False)
    op.create_index("ix_operation_tasks_server_id", "operation_tasks", ["server_id"], unique=False)
    op.create_index("ix_operation_tasks_retry_of_id", "operation_tasks", ["retry_of_id"], unique=False)
    op.create_index("ix_operation_tasks_created_at", "operation_tasks", ["created_at"], unique=False)
    op.create_index("ix_operation_tasks_actor_created", "operation_tasks", ["actor_user_id", "created_at"], unique=False)
    op.create_index("ix_operation_tasks_server_created", "operation_tasks", ["server_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_operation_tasks_server_created", table_name="operation_tasks")
    op.drop_index("ix_operation_tasks_actor_created", table_name="operation_tasks")
    op.drop_index("ix_operation_tasks_created_at", table_name="operation_tasks")
    op.drop_index("ix_operation_tasks_retry_of_id", table_name="operation_tasks")
    op.drop_index("ix_operation_tasks_server_id", table_name="operation_tasks")
    op.drop_index("ix_operation_tasks_status", table_name="operation_tasks")
    op.drop_index("ix_operation_tasks_correlation_id", table_name="operation_tasks")
    op.drop_index("ix_operation_tasks_actor_user_id", table_name="operation_tasks")
    op.drop_index("ix_operation_tasks_task_type", table_name="operation_tasks")
    op.drop_table("operation_tasks")
