"""Explizite Einwilligung zur einmaligen Standortnutzung.

Die Spalte enthält bewusst keine Koordinate und keinen Ort. Sie erlaubt nur,
dass eine Oberfläche für eine konkrete Anfrage nach der Geräteposition fragen
darf; die Position bleibt kurzlebig im jeweiligen Lauf.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260828_01"
down_revision: Union[str, None] = "20260827_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("location_sharing_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("location_sharing_enabled")
