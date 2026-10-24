"""Vereinigt die Hauptlinie mit dem Geräte-Zustellungsschema.

Die Migrationen 20261023_01/02 entstanden auf dem Messenger-Branch, während
``main`` mit 20261012_01 weiterlief. Ein Server, der den Branch bereits
ausgeführt hat, steht auf 20261023_02 und fand beim Update die Hauptlinie
nicht mehr. Dieser leere Merge führt beide Zweige wieder zu einem Head.

Revision ID: 20261024_01
Revises: 20261012_01, 20261023_02
Create Date: 2026-10-24
"""

from __future__ import annotations

from typing import Sequence, Union


revision: str = "20261024_01"
down_revision: Union[tuple[str, str], None] = ("20261012_01", "20261023_02")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
