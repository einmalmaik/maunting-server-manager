"""Tresortabellen ohne Klartextbezug zum Konto.

``vault_user_settings`` und ``vault_hints`` bekommen ``konto_index`` (HMAC der
Kontonummer aus dem DIS-Sidecar) und einen eigenen Schluessel ``id``; die
``user_id`` verliert Primaer- und Fremdschluessel und wird nullable. Den Index
kann nur der Sidecar rechnen, deshalb setzt ihn nicht diese Migration, sondern
der Nachzug beim Start (``vault_service.kontoindex_nachziehen``), der danach
die ``user_id`` leert.

``vault_blobs`` (seit 20260930_02, nie veroeffentlicht) gehoert danach dem
Bucket statt dem Konto. Blobs ohne Bucket werden entfernt; ihre Verzeichnisse
raeumt ``vault_blob_service.aufraeumen`` als verwaist ab.

Downgrade: die Kontonummer laesst sich aus dem Index nicht zurueckrechnen.
Zeilen, deren ``user_id`` schon geleert ist, gehen dabei verloren.

Revision ID: 20261001_01
Revises: 20260930_02
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261001_01"
down_revision: Union[str, None] = "20260930_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KONTO_TABELLEN = ("vault_user_settings", "vault_hints")


def _spalten(tabelle: str) -> set[str]:
    return {s["name"] for s in sa.inspect(op.get_bind()).get_columns(tabelle)}


def _fremdschluessel_auf_users(tabelle: str) -> list[str]:
    return [
        fk["name"]
        for fk in sa.inspect(op.get_bind()).get_foreign_keys(tabelle)
        if fk.get("referred_table") == "users" and fk.get("name")
    ]


def _primaerschluessel(tabelle: str) -> str | None:
    return sa.inspect(op.get_bind()).get_pk_constraint(tabelle).get("name")


def upgrade() -> None:
    # Erst die Blobs: ihr Bucket kommt ueber die user_id der Einstellungen,
    # und die steht dort bis zum Nachzug noch.
    if "bucket_id" not in _spalten("vault_blobs"):
        op.add_column("vault_blobs", sa.Column("bucket_id", sa.String(64), nullable=True))
        op.execute(
            "UPDATE vault_blobs AS b SET bucket_id = s.bucket_id FROM vault_user_settings AS s "
            "WHERE s.user_id = b.user_id AND s.bucket_id IS NOT NULL"
        )
        op.execute("DELETE FROM vault_blobs WHERE bucket_id IS NULL")
        op.alter_column("vault_blobs", "bucket_id", nullable=False)
        op.create_index("ix_vault_blobs_bucket_id", "vault_blobs", ["bucket_id"])
        op.drop_index("ix_vault_blobs_user_id", table_name="vault_blobs")
        op.drop_column("vault_blobs", "user_id")

    for tabelle in KONTO_TABELLEN:
        if "konto_index" in _spalten(tabelle):
            continue
        for name in _fremdschluessel_auf_users(tabelle):
            op.drop_constraint(name, tabelle, type_="foreignkey")
        pk = _primaerschluessel(tabelle)
        if pk:
            op.drop_constraint(pk, tabelle, type_="primary")
        op.execute(f'ALTER TABLE "{tabelle}" ADD COLUMN id SERIAL')
        op.create_primary_key(f"{tabelle}_pkey", tabelle, ["id"])
        op.alter_column(tabelle, "user_id", nullable=True)
        op.add_column(tabelle, sa.Column("konto_index", sa.String(64), nullable=True))
        op.create_index(f"ix_{tabelle}_konto_index", tabelle, ["konto_index"], unique=True)


def downgrade() -> None:
    for tabelle in KONTO_TABELLEN:
        if "konto_index" not in _spalten(tabelle):
            continue
        op.execute(f'DELETE FROM "{tabelle}" WHERE user_id IS NULL')
        op.drop_index(f"ix_{tabelle}_konto_index", table_name=tabelle)
        op.drop_column(tabelle, "konto_index")
        op.drop_constraint(_primaerschluessel(tabelle) or f"{tabelle}_pkey", tabelle, type_="primary")
        op.drop_column(tabelle, "id")
        op.alter_column(tabelle, "user_id", nullable=False)
        op.create_primary_key(f"{tabelle}_pkey", tabelle, ["user_id"])
        op.create_foreign_key(
            f"{tabelle}_user_id_fkey", tabelle, "users", ["user_id"], ["id"], ondelete="CASCADE"
        )

    if "bucket_id" in _spalten("vault_blobs"):
        op.add_column("vault_blobs", sa.Column("user_id", sa.Integer(), nullable=True))
        op.execute(
            "UPDATE vault_blobs AS b SET user_id = s.user_id FROM vault_user_settings AS s "
            "WHERE s.bucket_id = b.bucket_id"
        )
        op.execute("DELETE FROM vault_blobs WHERE user_id IS NULL")
        op.alter_column("vault_blobs", "user_id", nullable=False)
        op.create_foreign_key(
            "vault_blobs_user_id_fkey", "vault_blobs", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        op.create_index("ix_vault_blobs_user_id", "vault_blobs", ["user_id"])
        op.drop_index("ix_vault_blobs_bucket_id", table_name="vault_blobs")
        op.drop_column("vault_blobs", "bucket_id")
