"""ai_provider_disable_safety

Revision ID: 20260916_01
Revises: 20260913_01
Create Date: 2026-09-16 23:22:53.127600
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20260916_01'
down_revision: Union[str, None] = '20260913_01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "ai_providers" not in set(inspector.get_table_names()):
        return

    columns = {col["name"] for col in inspector.get_columns("ai_providers")}

    if "disable_safety" not in columns:
        op.add_column(
            "ai_providers",
            sa.Column(
                "disable_safety",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "ai_providers" not in set(inspector.get_table_names()):
        return

    columns = {col["name"] for col in inspector.get_columns("ai_providers")}

    if "disable_safety" in columns:
        op.drop_column("ai_providers", "disable_safety")
