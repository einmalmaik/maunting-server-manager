"""Pop-up- und Ankündigungssystem: panel_popups und user_popup_states.

Revision ID: 20260826_02
Revises: 20260826_01
Create Date: 2026-08-26

Erstellt die Tabellen:
- `panel_popups`: Panel-weite Pop-up- und Ankündigungsmeldungen mit Markdown-Inhalt,
  Gültigkeitszeitraum (start_at, end_at), Aktionsbutton und Aktivitätsstatus.
- `user_popup_states`: Pro-Benutzer-Gelesen- und Ausblendstatus für Pop-up-Meldungen.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260826_02"
down_revision: Union[str, None] = "20260826_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "panel_popups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("button_text", sa.String(length=100), nullable=True),
        sa.Column("button_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    op.create_table(
        "user_popup_states",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "popup_id",
            sa.Integer(),
            sa.ForeignKey("panel_popups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dismissed_permanently",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "last_dismissed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("user_id", "popup_id", name="uq_user_popup_state"),
    )
    op.create_index(
        "ix_user_popup_states_user_id", "user_popup_states", ["user_id"]
    )
    op.create_index(
        "ix_user_popup_states_popup_id", "user_popup_states", ["popup_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_user_popup_states_popup_id", table_name="user_popup_states")
    op.drop_index("ix_user_popup_states_user_id", table_name="user_popup_states")
    op.drop_table("user_popup_states")
    op.drop_table("panel_popups")
