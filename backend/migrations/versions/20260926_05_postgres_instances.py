"""Eigene PostgreSQL-Instanzen und abrufbare Datenbank-Passwoerter.

Revision ID: 20260926_05
Revises: 20260926_01
Create Date: 2026-09-26

Ein Datenbankserver bekommt eine eigene Instanz statt einer Datenbank im
geteilten ``msm-postgres``. Was diese Instanz ausser dem Server selbst braucht
(Superuser-Passwort, Netzwerkfreigabe), steht in ``postgres_instances``.

``postgres_users.password_encrypted``: bis 09/2026 kannte das Panel das
Passwort eines App-Users nur im Moment des Anlegens. Bestandszeilen bleiben
NULL, bis jemand rotiert — ein Passwort, das nie gespeichert wurde, laesst sich
nicht nachtragen.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260926_05"
down_revision: Union[str, None] = "20260926_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tabellen() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _spalten(tabelle: str) -> set[str]:
    return {s["name"] for s in sa.inspect(op.get_bind()).get_columns(tabelle)}


def upgrade() -> None:
    if "password_encrypted" not in _spalten("postgres_users"):
        with op.batch_alter_table("postgres_users") as batch:
            batch.add_column(sa.Column("password_encrypted", sa.String(length=4096), nullable=True))

    if "postgres_instances" in _tabellen():
        return
    op.create_table(
        "postgres_instances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "server_id",
            sa.Integer(),
            sa.ForeignKey("servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("admin_password_encrypted", sa.String(length=4096), nullable=False),
        sa.Column("allowed_cidrs", sa.Text(), nullable=False, server_default=""),
        sa.Column("ssl_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_postgres_instances_id", "postgres_instances", ["id"])
    op.create_index(
        "ix_postgres_instances_server_id", "postgres_instances", ["server_id"], unique=True
    )


def downgrade() -> None:
    if "postgres_instances" in _tabellen():
        op.drop_index("ix_postgres_instances_server_id", table_name="postgres_instances")
        op.drop_index("ix_postgres_instances_id", table_name="postgres_instances")
        op.drop_table("postgres_instances")
    if "password_encrypted" in _spalten("postgres_users"):
        with op.batch_alter_table("postgres_users") as batch:
            batch.drop_column("password_encrypted")
