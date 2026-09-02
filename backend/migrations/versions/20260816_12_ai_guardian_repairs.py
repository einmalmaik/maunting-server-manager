"""Ein Vorfall bekommt einen Auftrag statt eines Laufs.

Revision ID: 20260816_12
Revises: 20260816_11
Create Date: 2026-08-16

Bisher war die Heilungsnotiz (``ai_guardian_notices`` mit ``mode='healing'``)
zugleich die Buchfuehrung darueber, ob ein Vorfall noch drankommt. Sie entsteht
beim *Start* des Laufs, und beide Filter des Takts ueberspringen den Vorfall
danach. Ein Lauf, der nach achtundvierzig Leserunden auf
``stop_reason='budget'`` endet, hat den Vorfall damit fuer immer versorgt —
obwohl der Server noch steht.

Diese Tabelle trennt beides: die Notiz bleibt der Beleg "es wurde etwas
veranlasst", der Auftrag traegt "und es ist noch nicht fertig".

``deadline_at`` ist ``NOT NULL``, und das ist Absicht. Ein Auftrag ohne Frist
kann bei jedem Anlauf ein bisschen weiterkommen und den Server tagelang
beschaeftigen, ohne dass je eine Mail den Betreiber erreicht.

``server_id`` faellt bei geloeschtem Server auf ``NULL`` statt die Zeile
mitzunehmen: der Takt findet sie dann noch einmal, sieht keinen Server und
beendet den Auftrag ordentlich. Ein CASCADE haette dieselbe Wirkung ohne Spur.

Der CHECK wird ausgeschrieben und nicht aus ``PHASEN`` importiert — dasselbe
Verfahren wie beim Zustands-CHECK in ``ai_runs`` und bei der Unterhaltungsart in
``20260816_11``. Eine angewandte Migration ist Geschichte; laese sie das Tupel
aus dem Modell, schriebe eine spaetere Erweiterung rueckwirkend um, was diese
Migration getan hat.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260816_12"
down_revision: Union[str, None] = "20260816_11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PHASEN = (
    "phase IN ('diagnose', 'eingriff', 'beobachtung', "
    "'erledigt', 'eskaliert', 'aufgegeben', 'abgebrochen')"
)


def upgrade() -> None:
    op.create_table(
        "ai_guardian_repairs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "incident_id",
            sa.Integer(),
            sa.ForeignKey("incidents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "server_id",
            sa.Integer(),
            sa.ForeignKey("servers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phase", sa.String(length=16), nullable=False, server_default="diagnose"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_run_id",
            sa.String(length=36),
            sa.ForeignKey("ai_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("erkenntnisse", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "incident_id", "user_id", name="uq_ai_guardian_repairs_incident_user"
        ),
        sa.CheckConstraint(_PHASEN, name="ck_ai_guardian_repairs_phase"),
    )
    op.create_index(
        "ix_ai_guardian_repairs_incident_id", "ai_guardian_repairs", ["incident_id"]
    )
    op.create_index(
        "ix_ai_guardian_repairs_server_id", "ai_guardian_repairs", ["server_id"]
    )
    op.create_index("ix_ai_guardian_repairs_user_id", "ai_guardian_repairs", ["user_id"])
    op.create_index("ix_ai_guardian_repairs_next", "ai_guardian_repairs", ["next_run_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_guardian_repairs_next", table_name="ai_guardian_repairs")
    op.drop_index("ix_ai_guardian_repairs_user_id", table_name="ai_guardian_repairs")
    op.drop_index("ix_ai_guardian_repairs_server_id", table_name="ai_guardian_repairs")
    op.drop_index("ix_ai_guardian_repairs_incident_id", table_name="ai_guardian_repairs")
    op.drop_table("ai_guardian_repairs")
