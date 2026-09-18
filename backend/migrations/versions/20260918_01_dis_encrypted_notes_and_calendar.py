\"\"\"Erweiterung der Spalten fuer DIS-Verschluesselung von Notizen und Kalender.

Sorgt dafuer, dass title und content in 
otes sowie title, description und location
in calendar_events ausreichend Platz fuer DIS AES-256-GCM Base64-Ciphertexte haben.

Revision ID: 20260918_01
Revises: 20260917_01
Create Date: 2026-09-18
\"\"\"

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "20260918_01"
down_revision: str | None = "20260917_01"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tabellen = set(inspector.get_table_names())

    if "notes" in tabellen:
        with op.batch_alter_table("notes") as batch_op:
            batch_op.alter_column(
                "title",
                existing_type=sa.String(length=255),
                type_=sa.Text(),
                nullable=False,
            )

    if "calendar_events" in tabellen:
        with op.batch_alter_table("calendar_events") as batch_op:
            batch_op.alter_column(
                "title",
                existing_type=sa.String(length=255),
                type_=sa.Text(),
                nullable=False,
            )
            batch_op.alter_column(
                "location",
                existing_type=sa.String(length=255),
                type_=sa.Text(),
                nullable=True,
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tabellen = set(inspector.get_table_names())

    if "calendar_events" in tabellen:
        with op.batch_alter_table("calendar_events") as batch_op:
            batch_op.alter_column(
                "location",
                existing_type=sa.Text(),
                type_=sa.String(length=255),
                nullable=True,
            )
            batch_op.alter_column(
                "title",
                existing_type=sa.Text(),
                type_=sa.String(length=255),
                nullable=False,
            )

    if "notes" in tabellen:
        with op.batch_alter_table("notes") as batch_op:
            batch_op.alter_column(
                "title",
                existing_type=sa.Text(),
                type_=sa.String(length=255),
                nullable=False,
            )
