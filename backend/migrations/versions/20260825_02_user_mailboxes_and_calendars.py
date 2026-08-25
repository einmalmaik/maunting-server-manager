"""Erstellt Tabellen user_mailboxes und user_calendars fuer KI-Mail/Kalender-Integration.

Revision ID: 20260825_02
Revises: 20260825_01
Create Date: 2026-08-25

Ermöglicht verknüpfte Postfächer (IMAP/SMTP/OAuth) und Kalender (CalDAV/OAuth) pro Benutzer.
Zugangsdaten und Token werden DIS-AES-256-GCM verschlüsselt gespeichert.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260825_02"
down_revision: Union[str, None] = "20260825_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_mailboxes",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False, index=True),
        sa.Column(
            "provider_type",
            sa.String(length=32),
            nullable=False,
            server_default="imap_smtp",
        ),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("imap_host", sa.String(length=255), nullable=True),
        sa.Column("imap_port", sa.Integer(), nullable=True, server_default="993"),
        sa.Column(
            "imap_use_ssl",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("imap_username", sa.String(length=255), nullable=True),
        sa.Column("smtp_host", sa.String(length=255), nullable=True),
        sa.Column("smtp_port", sa.Integer(), nullable=True, server_default="587"),
        sa.Column(
            "smtp_use_tls",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("smtp_username", sa.String(length=255), nullable=True),
        sa.Column("credentials_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "sync_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("notify_filter_rules_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "user_calendars",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "provider_type",
            sa.String(length=32),
            nullable=False,
            server_default="caldav",
        ),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("caldav_url", sa.String(length=512), nullable=True),
        sa.Column("caldav_username", sa.String(length=255), nullable=True),
        sa.Column("credentials_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("user_calendars")
    op.drop_table("user_mailboxes")
