"""Die KI bekommt einen zweiten Ausloeser: die Uhr.

Revision ID: 20260813_01
Revises: 20260812_05
Create Date: 2026-08-13

Tabelle `ai_tasks`: ein stehender Auftrag, den ein Benutzer im Chat diktiert
("jeden Tag um 8 Uhr eine Mail ueber den Zustand meiner Server") und den ein
Takt im Scheduler zur faelligen Zeit als unbeaufsichtigten KI-Lauf startet.

Warum eine Tabelle und kein APScheduler-Job je Aufgabe: der Jobstore liegt in
MSM ausschliesslich im Speicher (kein SQLAlchemyJobStore in
`services/scheduler_service.py`). Nach einem Neustart des Panels gaebe es keinen
einzigen Job mehr, und der Zeitplan muesste ohnehin aus einer Tabelle
wiederhergestellt werden. Dann ist die Tabelle die Wahrheit und der Job nur die
Ausfuehrung — ein einziger, der alle sechzig Sekunden `enabled AND
next_run_at <= now` abfragt.

`next_run_at` ist deshalb keine Zwischenablage, sondern die eigentliche Angabe.
Sie steht in UTC; `time_zone` haelt die IANA-Zone, aus der sie berechnet wurde,
damit "8 Uhr" ueber Sommer- und Winterzeit hinweg 8 Uhr bleibt.

Fremdschluessel bewusst unterschiedlich:

- `user_id` mit ON DELETE CASCADE. Die Aufgabe gehoert diesem Menschen; ohne ihn
  gibt es niemanden, in dessen Namen und mit dessen Rechten sie laufen koennte.
- `last_run_id` mit ON DELETE SET NULL. Raeumt jemand alte Laeufe ab, bleibt die
  Aufgabe bestehen — der Lauf ist hier ein Beleg, kein Besitz.

Beide Constraints tragen ausdrueckliche Namen. `Base` hat keine
naming_convention (`database.py:25`), und SQLite speichert namenlose
Fremdschluessel nicht ansprechbar ab — dieselbe Lehre steht schon in
`20260811_04`.

Die CHECK-Bedingungen stehen woertlich hier und werden im Modell aus
Modulkonstanten erzeugt. Das ist Absicht: eine angewandte Migration ist
Geschichte und wird nicht nachtraeglich umgeschrieben, wenn jemand spaeter eine
vierte Planart ergaenzt.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260813_01"
down_revision: Union[str, None] = "20260812_05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("plan_kind", sa.String(length=16), nullable=False),
        sa.Column("time_of_day", sa.String(length=5), nullable=True),
        sa.Column("weekdays", sa.String(length=16), nullable=True),
        sa.Column("interval_hours", sa.Integer(), nullable=True),
        sa.Column("once_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_ai_tasks_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["last_run_id"], ["ai_runs.id"],
            name="fk_ai_tasks_last_run_id_ai_runs",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("kind IN ('report', 'act')", name="ck_ai_tasks_kind"),
        sa.CheckConstraint(
            "plan_kind IN ('daily', 'interval', 'once')", name="ck_ai_tasks_plan_kind"
        ),
        sa.CheckConstraint(
            "channel IN ('chat', 'email', 'both')", name="ck_ai_tasks_channel"
        ),
    )
    op.create_index("ix_ai_tasks_user_id", "ai_tasks", ["user_id"])
    op.create_index("ix_ai_tasks_enabled_next", "ai_tasks", ["enabled", "next_run_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_tasks_enabled_next", table_name="ai_tasks")
    op.drop_index("ix_ai_tasks_user_id", table_name="ai_tasks")
    op.drop_table("ai_tasks")
