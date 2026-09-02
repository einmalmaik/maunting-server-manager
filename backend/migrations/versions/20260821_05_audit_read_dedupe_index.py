"""Audit-Log: Index fuer die Dedupe-Abfrage der Lesezugriffe.

Revision ID: 20260821_05
Revises: 20260821_04
Create Date: 2026-08-21

`record_read_access` fragt vor jedem Eintrag, ob es fuer (user_id, action,
target_id) im 10-Minuten-Fenster schon einen gibt. Die Abfrage laeuft auf dem
heissesten Lesepfad des Panels (Dateibrowser, Konsole, Logs), und audit_logs
waechst unbegrenzt — ohne den Index zahlt jede Interaktion einen Heap-Scan
ueber alle Zeilen derselben Action.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260821_05"
down_revision: Union[str, None] = "20260821_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_logs_read_dedupe",
        "audit_logs",
        ["user_id", "action", "target_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_read_dedupe", table_name="audit_logs")
