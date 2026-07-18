"""Phase 8 PostgreSQL baseline.

Revision ID: 20260716_01
Revises: None
Create Date: 2026-07-16

Existing installations are verified and stamped by schema_manager. New
installations create the current SQLAlchemy metadata and are then stamped.
Incremental schema changes start after this baseline.
"""

from typing import Sequence, Union

revision: str = "20260716_01"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError("Die Phase-8-Baseline darf nicht automatisch zurückgestuft werden.")
