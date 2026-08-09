"""Skills werden Prosa: Makro-Tabelle ersetzt.

Revision ID: 20260809_04
Revises: 20260809_03
Create Date: 2026-08-09

Der Vorgaenger speicherte in `steps_json` eine feste Folge von hoechstens
zwanzig Tool-Aufrufen aus einer Zwoelfer-Allowlist. Das ist ein Makro und
etwas anderes als das, was ChatGPT, Claude oder Hermes unter einem Skill
verstehen: eine Textdatei, die beschreibt, *wie* man an eine Sache herangeht.

Der Unterschied ist nicht nur begrifflich, sondern auch sicherheitsrelevant —
und zwar zugunsten der neuen Fassung. Prosa fuehrt nichts aus. Ein selbst
gelernter Skill kann damit nichts, was das Modell nicht ohnehin duerfte; er
aendert nur die Herangehensweise. Automatisch erzeugte *ausfuehrbare*
Schrittfolgen waeren deutlich heikler gewesen.

**Warum die Tabelle neu aufgebaut und nicht umgebaut wird:** Es gibt keine
sinnvolle Uebersetzung von einer Aufrufliste in Fliesstext, und der Betreiber
hat bestaetigt, dass das Makro-System nie in Betrieb war. Eine Migration, die
aus `[{"tool_name": "read_server_logs"}]` einen Prosatext erfindet, waere
schlechter als ein sauberer Neuanfang.

Die Ersteinrichtung braucht keine Daten: die sechs mitgelieferten Skills liegen
als Dateien in `backend/ai_skills/` und nicht in der Datenbank. So verbessert
ein MSM-Update die KI jeder Installation, ohne dass eine Migration laufen muss.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260809_04"
down_revision: Union[str, None] = "20260809_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("ai_skills")
    op.create_table(
        "ai_skills",
        sa.Column("id", sa.String(length=36), primary_key=True),
        # "global" oder "team:{id}" — als Zeichenkette, nicht als nullbare
        # Spalte: in PostgreSQL kollidieren NULL-Werte in einer
        # UNIQUE-Bedingung nicht, zwei globale Skills mit demselben Schluessel
        # waeren also erlaubt gewesen. Dasselbe Muster wie beim Memory.
        sa.Column("scope_identity", sa.String(length=64), nullable=False),
        sa.Column(
            "team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("skill_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False, server_default="operator"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("embedding_json", sa.Text(), nullable=True),
        sa.Column("embedding_model", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("origin IN ('operator', 'ai')", name="ck_ai_skills_origin"),
        sa.CheckConstraint("status IN ('active', 'pending')", name="ck_ai_skills_status"),
        sa.UniqueConstraint("scope_identity", "skill_key", name="uq_ai_skills_scope_key"),
    )
    op.create_index("ix_ai_skills_scope", "ai_skills", ["scope_identity", "enabled"])
    op.create_index("ix_ai_skills_team", "ai_skills", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_skills_team", table_name="ai_skills")
    op.drop_index("ix_ai_skills_scope", table_name="ai_skills")
    op.drop_table("ai_skills")
    # Der Stand vor dieser Revision, wie ihn 20260801_06 angelegt hat.
    op.create_table(
        "ai_skills",
        sa.Column("id", sa.String(length=36), primary_key=True),
        # Ohne `index=True`: die drei Indizes entstehen unten einzeln, genau wie
        # in 20260801_06. Sonst legte SQLAlchemy `ix_ai_skills_skill_key`
        # bereits hier an und der explizite Aufruf liefe auf "already exists".
        sa.Column("skill_key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("steps_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("skill_key", "version", name="uq_ai_skills_key_version"),
    )
    # Alle drei Indizes von 20260801_06 muessen wieder da sein: dessen eigener
    # Rueckbau loescht sie namentlich und scheitert sonst mit "no such index".
    # Ein unvollstaendig wiederhergestellter Zustand bricht die Migrationskette
    # erst zwei Revisionen spaeter — genau die Art Fehler, die man im Betrieb
    # nicht bemerkt, bis man ihn braucht.
    op.create_index("ix_ai_skills_skill_key", "ai_skills", ["skill_key"])
    op.create_index("ix_ai_skills_enabled", "ai_skills", ["enabled"])
    op.create_index("ix_ai_skills_key_created", "ai_skills", ["skill_key", "created_at"])
