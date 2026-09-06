"""Update vault_entries primary key to composite (bucket_id, id).

Revision ID: 20260906_02
Revises: 20260906_01
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260906_02"
down_revision: Union[str, None] = "20260906_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _vorhandener_pk_name(default_fallback: str) -> str:
    bind = op.get_bind()
    try:
        if bind is not None and not getattr(bind, "is_mock", False) and type(bind).__name__ != "MockConnection":
            insp = sa.inspect(bind)
            pk = insp.get_pk_constraint("vault_entries")
            if pk and pk.get("name"):
                return pk["name"]
    except Exception:
        pass
    return default_fallback


def upgrade() -> None:
    dialect = op.get_context().dialect.name
    fallback = "vault_entries_pkey" if dialect == "postgresql" else "pk_vault_entries"
    pk_name = _vorhandener_pk_name(fallback)
    naming = {"pk": "pk_%(table_name)s"}
    with op.batch_alter_table("vault_entries", naming_convention=naming) as batch:
        batch.drop_constraint(pk_name, type_="primary")
        batch.create_primary_key("pk_vault_entries", ["bucket_id", "id"])


def downgrade() -> None:
    pk_name = _vorhandener_pk_name("pk_vault_entries")
    naming = {"pk": "pk_%(table_name)s"}
    with op.batch_alter_table("vault_entries", naming_convention=naming) as batch:
        batch.drop_constraint(pk_name, type_="primary")
        batch.create_primary_key("vault_entries_pkey", ["id"])

