"""Die KI merkt sich, welchen Vorfall sie schon behandelt hat.

Revision ID: 20260812_05
Revises: 20260812_04
Create Date: 2026-08-12

Tabelle `ai_guardian_notices`: eine Zeile je Paar aus Guardian-Vorfall und
Benutzer, mit `mode` ('briefed' oder 'healing') und optionalem `run_id`.

Der Grund ist der Takt. Der Ausloeser der KI-Kopplung laeuft als
Scheduler-Auftrag alle sechzig Sekunden ueber die offenen Vorfaelle, und ein
Vorfall bleibt offen, bis ihn jemand loest. Ohne eine Gedaechtniszeile saehe
jeder Durchlauf denselben Vorfall als neu, startete einen weiteren Lauf und
haette das KI-Kontingent des Benutzers in einer Viertelstunde aufgebraucht.

Die Eindeutigkeit ist deshalb ein Datenbank-Constraint und keine Pruefung im
Code davor. `max_instances=1` am Scheduler gilt nur innerhalb eines Prozesses;
laeuft das Panel je mit mehreren Uvicorn-Arbeitern, gibt es den Auftrag mehrfach,
und dann ist `uq_ai_guardian_notices_incident_user` die einzige Schranke, die
noch traegt.

Fremdschluessel bewusst unterschiedlich:

- `incident_id` und `user_id` mit ON DELETE CASCADE. Verschwindet der Vorfall
  oder der Benutzer, gibt es nichts mehr zu merken.
- `run_id` mit ON DELETE SET NULL. Raeumt jemand alte Laeufe ab, bleibt die
  Aussage "dieser Vorfall war versorgt" trotzdem wahr. Mit CASCADE verschwaende
  sie mit dem Lauf, und der Ausloeser finge auf einem laengst behandelten
  Vorfall von vorne an — genau der Fall, den diese Tabelle verhindern soll.

Der CHECK auf `mode` steht in der Datenbank und nicht nur in Python: ein
Tippfehler in einer spaeteren Migration oder in einem Wartungsskript soll nicht
als gueltige dritte Art durchgehen und den Ausloeser stumm anders entscheiden
lassen.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260812_05"
down_revision: Union[str, None] = "20260812_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_guardian_notices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("incident_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.id"],
            name="fk_ai_guardian_notices_incident_id_incidents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_ai_guardian_notices_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["ai_runs.id"],
            name="fk_ai_guardian_notices_run_id_ai_runs",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "incident_id", "user_id", name="uq_ai_guardian_notices_incident_user"
        ),
        sa.CheckConstraint(
            "mode IN ('briefed', 'healing')", name="ck_ai_guardian_notices_mode"
        ),
    )
    op.create_index(
        "ix_ai_guardian_notices_incident_id", "ai_guardian_notices", ["incident_id"]
    )
    op.create_index(
        "ix_ai_guardian_notices_user_id", "ai_guardian_notices", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_ai_guardian_notices_user_id", table_name="ai_guardian_notices")
    op.drop_index("ix_ai_guardian_notices_incident_id", table_name="ai_guardian_notices")
    op.drop_table("ai_guardian_notices")
