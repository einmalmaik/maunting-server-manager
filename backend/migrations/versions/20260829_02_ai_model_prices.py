"""Separate input/output prices for each configured AI model role."""

from alembic import op
import sqlalchemy as sa

revision = "20260829_02"
down_revision = "20260829_01"
branch_labels = None
depends_on = None

_COLUMNS = (
    "standard_input_price_micro_usd_per_million",
    "standard_output_price_micro_usd_per_million",
    "worker_input_price_micro_usd_per_million",
    "worker_output_price_micro_usd_per_million",
    "ethics_input_price_micro_usd_per_million",
    "ethics_output_price_micro_usd_per_million",
)


def upgrade() -> None:
    with op.batch_alter_table("ai_providers") as batch:
        for name in _COLUMNS:
            batch.add_column(sa.Column(name, sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ai_providers") as batch:
        for name in reversed(_COLUMNS):
            batch.drop_column(name)
