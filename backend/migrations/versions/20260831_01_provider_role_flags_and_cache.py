"""Provider-Rollen-Umbau: je Rolle schaltbar + Cache-Preise, Realtime fuer Azure."""

from alembic import op
import sqlalchemy as sa


revision = "20260831_01"
down_revision = "20260830_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ai_providers") as batch:
        for name in (
            "standard_enabled",
            "worker_enabled",
            "ethics_enabled",
            "transcription_enabled",
            "realtime_enabled",
        ):
            batch.add_column(sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()))
        for name in (
            "standard_cache_price_micro_usd_per_million",
            "worker_cache_price_micro_usd_per_million",
            "ethics_cache_price_micro_usd_per_million",
        ):
            batch.add_column(sa.Column(name, sa.BigInteger(), nullable=True))
    op.execute(sa.text(
        "UPDATE ai_providers SET "
        "standard_enabled = CASE WHEN default_model IS NOT NULL AND trim(default_model) != '' THEN 1 ELSE 0 END, "
        "worker_enabled = CASE WHEN worker_model IS NOT NULL AND trim(worker_model) != '' THEN 1 ELSE 0 END, "
        "ethics_enabled = CASE WHEN ethics_model IS NOT NULL AND trim(ethics_model) != '' THEN 1 ELSE 0 END, "
        "transcription_enabled = CASE WHEN transcription_model IS NOT NULL AND trim(transcription_model) != '' THEN 1 ELSE 0 END, "
        "realtime_enabled = realtime_default"
    ))


def downgrade() -> None:
    with op.batch_alter_table("ai_providers") as batch:
        for name in (
            "ethics_cache_price_micro_usd_per_million",
            "worker_cache_price_micro_usd_per_million",
            "standard_cache_price_micro_usd_per_million",
            "realtime_enabled",
            "transcription_enabled",
            "ethics_enabled",
            "worker_enabled",
            "standard_enabled",
        ):
            batch.drop_column(name)
