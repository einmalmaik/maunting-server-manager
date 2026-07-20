"""add guardian_accepted_payload_hash

Revision ID: beef3761b732
Revises: 20260720_02
Create Date: 2026-07-20 21:01:33.813225
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'beef3761b732'
down_revision: Union[str, None] = '20260720_02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('servers', sa.Column('guardian_accepted_payload_hash', sa.String(length=71), nullable=True))


def downgrade() -> None:
    op.drop_column('servers', 'guardian_accepted_payload_hash')
