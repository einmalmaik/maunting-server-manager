"""Separate monthly Realtime cost limit for AI roles."""

from alembic import op
import sqlalchemy as sa


revision = "20260830_01"
down_revision = "20260829_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("role_ai_limits") as batch:
        batch.add_column(sa.Column("monthly_realtime_cost_limit_cents", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("role_ai_limits") as batch:
        batch.drop_column("monthly_realtime_cost_limit_cents")
