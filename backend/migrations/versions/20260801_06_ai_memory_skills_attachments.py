"""Add encrypted AI memory, versioned skills and quarantined attachments.

Revision ID: 20260801_06
Revises: 20260801_05
Create Date: 2026-08-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260801_06"
down_revision: Union[str, None] = "20260801_05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_memory_preferences",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "ai_memory_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("scope_identity", sa.String(length=128), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope IN ('user', 'server', 'panel')", name="ck_ai_memory_entries_scope"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_identity", "key", name="uq_ai_memory_scope_key"),
    )
    op.create_index("ix_ai_memory_entries_owner_user_id", "ai_memory_entries", ["owner_user_id"])
    op.create_index("ix_ai_memory_entries_server_id", "ai_memory_entries", ["server_id"])
    op.create_index("ix_ai_memory_owner_scope", "ai_memory_entries", ["owner_user_id", "scope"])
    op.create_table(
        "ai_skills",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("skill_key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("steps_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("skill_key", "version", name="uq_ai_skills_key_version"),
    )
    op.create_index("ix_ai_skills_skill_key", "ai_skills", ["skill_key"])
    op.create_index("ix_ai_skills_enabled", "ai_skills", ["enabled"])
    op.create_index("ix_ai_skills_key_created", "ai_skills", ["skill_key", "created_at"])
    op.create_table(
        "ai_attachments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("original_name", sa.String(length=128), nullable=False),
        sa.Column("media_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content_encrypted", sa.Text(), nullable=False),
        sa.Column("extracted_text_encrypted", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("rejection_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('quarantined', 'ready', 'rejected')", name="ck_ai_attachments_status"),
        sa.ForeignKeyConstraint(["conversation_id"], ["ai_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_attachments_user_id", "ai_attachments", ["user_id"])
    op.create_index("ix_ai_attachments_status", "ai_attachments", ["status"])
    op.create_index("ix_ai_attachments_conversation_created", "ai_attachments", ["conversation_id", "created_at"])


def _drop_index_if_present(table: str, name: str) -> None:
    """Entfernt einen Index nur, wenn er tatsaechlich existiert.

    Neue Installationen erzeugen ihr Schema aus den Modellen und werden danach
    gestempelt. Seit die Skill-Tabelle auf Prosa umgestellt wurde
    (20260809_04), fuehrt das Modell die alten Skill-Indizes nicht mehr — ein
    bedingungsloses DROP braeche deren Migrationslauf ab, obwohl nichts zu tun
    ist. Dasselbe Muster steht bereits in 20260808_02.
    """
    connection = op.get_bind()
    existing = {index["name"] for index in sa.inspect(connection).get_indexes(table)}
    if name in existing:
        op.drop_index(name, table_name=table)


def downgrade() -> None:
    op.drop_index("ix_ai_attachments_conversation_created", table_name="ai_attachments")
    op.drop_index("ix_ai_attachments_status", table_name="ai_attachments")
    op.drop_index("ix_ai_attachments_user_id", table_name="ai_attachments")
    op.drop_table("ai_attachments")
    _drop_index_if_present("ai_skills", "ix_ai_skills_key_created")
    _drop_index_if_present("ai_skills", "ix_ai_skills_enabled")
    _drop_index_if_present("ai_skills", "ix_ai_skills_skill_key")
    op.drop_table("ai_skills")
    op.drop_index("ix_ai_memory_owner_scope", table_name="ai_memory_entries")
    op.drop_index("ix_ai_memory_entries_server_id", table_name="ai_memory_entries")
    op.drop_index("ix_ai_memory_entries_owner_user_id", table_name="ai_memory_entries")
    op.drop_table("ai_memory_entries")
    op.drop_table("ai_memory_preferences")
