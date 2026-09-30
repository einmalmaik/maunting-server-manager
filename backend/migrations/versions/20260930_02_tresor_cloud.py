"""Tresor-Cloud: Blobs, Quote je Konto, Mindestformat je Bucket.

``vault_blobs`` fuehrt die verschluesselten Dateien der Tresor-Cloud; der
Chiffrat selbst liegt auf der Platte. ``users.vault_quota_bytes`` ist die Quote
eines Kontos (NULL heisst Vorgabe des Panels). ``vault_bucket_formats`` sperrt
Apps aus, die neuere Eintraege beim Speichern verstuemmeln wuerden.

Revision ID: 20260930_02
Revises: 20260930_01
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260930_02"
down_revision: Union[str, None] = "20260930_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tabellen() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _spalten(tabelle: str) -> set[str]:
    return {s["name"] for s in sa.inspect(op.get_bind()).get_columns(tabelle)}


def upgrade() -> None:
    if "vault_blobs" not in _tabellen():
        op.create_table(
            "vault_blobs",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("chunk_count", sa.Integer(), nullable=False),
            sa.Column("bytes_total", sa.BigInteger(), nullable=False),
            sa.Column("delete_verifier", sa.String(64), nullable=False),
            sa.Column("state", sa.String(16), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_vault_blobs_user_id", "vault_blobs", ["user_id"])

    if "vault_bucket_formats" not in _tabellen():
        op.create_table(
            "vault_bucket_formats",
            sa.Column("bucket_id", sa.String(64), primary_key=True),
            sa.Column("min_client_format", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "vault_quota_bytes" not in _spalten("users"):
        op.add_column("users", sa.Column("vault_quota_bytes", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    if "vault_quota_bytes" in _spalten("users"):
        op.drop_column("users", "vault_quota_bytes")
    if "vault_bucket_formats" in _tabellen():
        op.drop_table("vault_bucket_formats")
    if "vault_blobs" in _tabellen():
        op.drop_index("ix_vault_blobs_user_id", table_name="vault_blobs")
        op.drop_table("vault_blobs")
