"""Add durable Guardian desired/observed state and incident UUIDs.

Revision ID: 20260719_02
Revises: 20260719_01
Create Date: 2026-07-19
"""

from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260719_02"
down_revision: Union[str, None] = "20260719_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column("desired_power_state", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "servers",
        sa.Column(
            "guardian_observed_state",
            sa.String(length=32),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "servers",
        sa.Column(
            "desired_state_generation",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column("servers", sa.Column("guardian_config_hash", sa.String(length=71), nullable=True))
    op.add_column("servers", sa.Column("guardian_recovery_suspension", sa.Text(), nullable=True))
    op.add_column("servers", sa.Column("guardian_quarantine_control", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE servers
        SET desired_power_state = CASE
            WHEN status IN ('running', 'starting', 'restarting') THEN 'running'
            ELSE 'stopped'
        END
        WHERE desired_power_state IS NULL
        """
    )
    op.alter_column(
        "servers",
        "desired_power_state",
        existing_type=sa.String(length=16),
        nullable=False,
        server_default="stopped",
    )

    op.add_column("incidents", sa.Column("uuid", sa.String(length=36), nullable=True))
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id FROM incidents WHERE uuid IS NULL")).fetchall()
    for row in rows:
        connection.execute(
            sa.text("UPDATE incidents SET uuid = :uuid WHERE id = :id"),
            {"uuid": str(uuid.uuid4()), "id": row.id},
        )
    op.create_index("ix_incidents_uuid", "incidents", ["uuid"], unique=True)
    op.alter_column(
        "incidents",
        "uuid",
        existing_type=sa.String(length=36),
        nullable=False,
    )


def downgrade() -> None:
    raise RuntimeError("Guardian Verified Recovery Core darf nicht automatisch zurueckgestuft werden.")

