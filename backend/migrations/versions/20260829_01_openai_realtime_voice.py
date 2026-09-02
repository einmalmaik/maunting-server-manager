"""Optionalen panelweiten OpenAI-Realtime-Sprachzugang ergaenzen."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260829_01"
down_revision: Union[str, None] = "20260828_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_providers") as batch:
        batch.add_column(sa.Column("realtime_default", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("realtime_model", sa.String(length=256), nullable=True))
        batch.add_column(sa.Column("realtime_voice", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("realtime_reasoning_effort", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("realtime_language", sa.String(length=16), nullable=False, server_default="auto"))
        batch.add_column(sa.Column("realtime_vad_eagerness", sa.String(length=16), nullable=False, server_default="auto"))
        for name in (
            "realtime_text_input_price_micro_usd_per_million",
            "realtime_text_output_price_micro_usd_per_million",
            "realtime_audio_input_price_micro_usd_per_million",
            "realtime_audio_output_price_micro_usd_per_million",
        ):
            batch.add_column(sa.Column(name, sa.BigInteger(), nullable=True))
    op.create_index(
        "uq_ai_providers_realtime_default",
        "ai_providers",
        ["realtime_default"],
        unique=True,
        sqlite_where=sa.text("realtime_default = 1"),
        postgresql_where=sa.text("realtime_default"),
    )
    with op.batch_alter_table("ai_usage_events") as batch:
        for name in (
            "realtime_text_input_tokens",
            "realtime_text_output_tokens",
            "realtime_audio_input_tokens",
            "realtime_audio_output_tokens",
        ):
            batch.add_column(sa.Column(name, sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ai_usage_events") as batch:
        for name in (
            "realtime_audio_output_tokens",
            "realtime_audio_input_tokens",
            "realtime_text_output_tokens",
            "realtime_text_input_tokens",
        ):
            batch.drop_column(name)
    op.drop_index("uq_ai_providers_realtime_default", table_name="ai_providers")
    with op.batch_alter_table("ai_providers") as batch:
        for name in (
            "realtime_audio_output_price_micro_usd_per_million",
            "realtime_audio_input_price_micro_usd_per_million",
            "realtime_text_output_price_micro_usd_per_million",
            "realtime_text_input_price_micro_usd_per_million",
            "realtime_vad_eagerness",
            "realtime_language",
            "realtime_voice",
            "realtime_reasoning_effort",
            "realtime_model",
            "realtime_default",
        ):
            batch.drop_column(name)
