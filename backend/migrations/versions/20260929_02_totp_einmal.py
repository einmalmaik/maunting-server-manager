"""Ein App-Code gilt nur einmal.

``users.two_factor_totp_last_step`` haelt den 30-s-Schritt des zuletzt
angenommenen Codes. Bis 5.0.1 galt ein Code, solange er gueltig war (bis zu
90 s), beliebig oft: wer ihn mitlas, konnte ihn nach dem Inhaber noch einmal
benutzen.

Revision ID: 20260929_02
Revises: 20260929_01
Create Date: 2026-09-29
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260929_02"
down_revision: Union[str, None] = "20260929_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _spalten() -> set[str]:
    return {s["name"] for s in sa.inspect(op.get_bind()).get_columns("users")}


def upgrade() -> None:
    if "two_factor_totp_last_step" not in _spalten():
        op.add_column("users", sa.Column("two_factor_totp_last_step", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    if "two_factor_totp_last_step" in _spalten():
        op.drop_column("users", "two_factor_totp_last_step")
