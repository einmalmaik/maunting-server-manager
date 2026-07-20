"""add_guardian_incident_deliveries

Revision ID: 93a7c6f012e1
Revises: beef3761b732
Create Date: 2026-07-20 22:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '93a7c6f012e1'
down_revision: Union[str, None] = 'beef3761b732'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'guardian_incident_deliveries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('incident_uuid', sa.String(length=36), nullable=False),
        sa.Column('incident_id', sa.Integer(), nullable=False),
        sa.Column('server_id', sa.Integer(), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['server_id'], ['servers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_guardian_incident_deliveries_id'), 'guardian_incident_deliveries', ['id'], unique=False)
    op.create_index(op.f('ix_guardian_incident_deliveries_incident_id'), 'guardian_incident_deliveries', ['incident_id'], unique=False)
    op.create_index(op.f('ix_guardian_incident_deliveries_incident_uuid'), 'guardian_incident_deliveries', ['incident_uuid'], unique=True)
    op.create_index(op.f('ix_guardian_incident_deliveries_server_id'), 'guardian_incident_deliveries', ['server_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_guardian_incident_deliveries_server_id'), table_name='guardian_incident_deliveries')
    op.drop_index(op.f('ix_guardian_incident_deliveries_incident_uuid'), table_name='guardian_incident_deliveries')
    op.drop_index(op.f('ix_guardian_incident_deliveries_incident_id'), table_name='guardian_incident_deliveries')
    op.drop_index(op.f('ix_guardian_incident_deliveries_id'), table_name='guardian_incident_deliveries')
    op.drop_table('guardian_incident_deliveries')
