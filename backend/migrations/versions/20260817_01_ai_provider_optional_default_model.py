"""default_model in ai_providers nullable machen.

Revision ID: 20260817_01
Revises: 20260816_14
Create Date: 2026-08-17

Ermöglicht es, einen Provider rein für Transkription (STT / Whisper) oder rein
für Sprachausgabe (TTS / ElevenLabs) einzurichten, ohne dass zwingend ein
Standard-Chatmodell angegeben werden muss.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260817_01"
down_revision: Union[str, None] = "20260816_14"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_providers") as batch:
        batch.alter_column(
            "default_model",
            existing_type=sa.String(length=256),
            type_=sa.String(length=256),
            nullable=True,
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE ai_providers SET default_model = 'default' WHERE default_model IS NULL"
        )
    )
    with op.batch_alter_table("ai_providers") as batch:
        batch.alter_column(
            "default_model",
            existing_type=sa.String(length=256),
            type_=sa.String(length=256),
            nullable=False,
        )
