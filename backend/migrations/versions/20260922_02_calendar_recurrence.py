"""Serientermine: verschluesselte Wiederholungsregel am Kalendereintrag.

Eine einzige Spalte, `NOT NULL` mit leerem Vorgabewert. Der Inhalt ist ein
verschluesseltes JSON-Dokument; die Migration schreibt ihn **nicht**, weil das
den DIS-Sidecar zur Laufzeit der Migration voraussetzen wuerde und DIS
fail-closed arbeitet — ein nicht erreichbarer Sidecar haette damit den Start
des Panels verhindert. Der Lesepfad (`_decrypt_or_migrate_calendar_event`)
zieht Altbestand nach, wie er es mit unverschluesselten Titeln auch tut.

Revision ID: 20260922_02
Revises: 20260922_01
Create Date: 2026-09-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "20260922_02"
down_revision: str | None = "20260922_01"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tabellen = set(inspector.get_table_names())
    if "calendar_events" not in tabellen:
        return

    spalten = {s["name"] for s in inspector.get_columns("calendar_events")}
    if "recurrence" in spalten:
        return

    with op.batch_alter_table("calendar_events") as batch_op:
        batch_op.add_column(
            sa.Column(
                "recurrence",
                sa.Text(),
                nullable=False,
                server_default="",
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tabellen = set(inspector.get_table_names())
    if "calendar_events" not in tabellen:
        return

    spalten = {s["name"] for s in inspector.get_columns("calendar_events")}
    if "recurrence" not in spalten:
        return

    with op.batch_alter_table("calendar_events") as batch_op:
        batch_op.drop_column("recurrence")
