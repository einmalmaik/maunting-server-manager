"""Worker-Fenster und der weckbare Wartezustand.

Revision ID: 20260818_01
Revises: 20260817_01
Create Date: 2026-08-18

Umsetzung von docs/agentic-framework.md (v3, Gehirn und Worker), Datenmodell:

**``waiting_wake`` und ``wake_at`` an ``ai_runs``.** Ein Worker-Lauf, der auf
die Zeit oder ein Ereignis wartet ("in 10 Minuten nachsehen", "der Neustart
ist eingereiht"), parkt als Zeile statt zu schleifen. ``wake_at`` ist eine
Spalte und kein ``state_json``-Feld, weil der 60-s-Takt faellige Laeufe per
SQL finden muss (``status='waiting_wake' AND wake_at <= now``).

**``worker`` als dritte Unterhaltungsart.** Jeder vom Gehirn deklarierte
Auftrag bekommt sein eigenes Fenster — mehrere je Benutzer gleichzeitig.
Deshalb wird der eindeutige Index ``uq_ai_conversations_user_kind``
**partiell**: die Eindeutigkeit gilt weiter fuer ``primary`` und ``guardian``
(genau ein Dauerchat, genau ein Guardian-Fenster), ``worker``-Zeilen sind
ausgenommen. Gleicher Indexname wie bisher, damit Modell (``create_all``) und
Migration deckungsgleich bleiben.

**Die CHECK-Texte stehen hier ausgeschrieben und werden nicht aus den
Modellen importiert.** Eine angewandte Migration ist Geschichte; wuerde sie
die Tupel aus dem Modell lesen, schriebe eine spaetere Erweiterung
rueckwirkend um, was diese Migration getan hat. ``_STATUS_ALT`` ist
zeichengenau der Text aus ``20260810_02``, ``_KIND_ALT`` der aus
``20260816_11``.

**Der Downgrade vernichtet alle Worker-Unterhaltungen** samt Nachrichten,
Laeufen, Vorschlaegen und Anhaengen (ON DELETE CASCADE) — zurueck geht es nur
ohne die dritte Art. ``waiting_wake``-Laeufe werden vor dem Verengen des
CHECK ehrlich auf ``failed`` gesetzt: niemand wird sie je wieder wecken.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260818_01"
down_revision: Union[str, None] = "20260817_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_STATUS_ALT = (
    "status IN ('running', 'waiting_confirmation', 'waiting_user', "
    "'completed', 'failed', 'cancelled')"
)
_STATUS_NEU = (
    "status IN ('running', 'waiting_confirmation', 'waiting_user', "
    "'waiting_wake', 'completed', 'failed', 'cancelled')"
)
_KIND_ALT = "kind IN ('primary', 'guardian')"
_KIND_NEU = "kind IN ('primary', 'guardian', 'worker')"
_EINZELFENSTER = "kind IN ('primary', 'guardian')"


def upgrade() -> None:
    # Ein Batch-Block je Tabelle: auf SQLite wird jede Tabelle dabei genau
    # einmal neu aufgebaut, auf PostgreSQL sind es gewoehnliche ALTERs.
    with op.batch_alter_table("ai_runs") as batch:
        batch.add_column(sa.Column("wake_at", sa.DateTime(timezone=True), nullable=True))
        batch.drop_constraint("ck_ai_runs_status", type_="check")
        batch.create_check_constraint("ck_ai_runs_status", _STATUS_NEU)

    with op.batch_alter_table("ai_conversations") as batch:
        batch.drop_constraint("ck_ai_conversations_kind", type_="check")
        batch.create_check_constraint("ck_ai_conversations_kind", _KIND_NEU)

    # Index-Arbeit bewusst AUSSERHALB der Batch-Bloecke (Muster 20260816_11).
    op.drop_index("uq_ai_conversations_user_kind", table_name="ai_conversations")
    op.create_index(
        "uq_ai_conversations_user_kind",
        "ai_conversations",
        ["user_id", "kind"],
        unique=True,
        sqlite_where=sa.text(_EINZELFENSTER),
        postgresql_where=sa.text(_EINZELFENSTER),
    )


def downgrade() -> None:
    # Erst raeumen, dann verengen. Die Worker-Fenster samt allem, was per
    # ON DELETE CASCADE daran haengt, gehen verloren — das ist der Preis eines
    # Downgrades und steht deshalb hier statt in einer Fussnote.
    op.execute("DELETE FROM ai_conversations WHERE kind = 'worker'")
    op.execute(
        "UPDATE ai_runs SET status = 'failed', stop_reason = 'downgrade', "
        "wake_at = NULL WHERE status = 'waiting_wake'"
    )

    op.drop_index("uq_ai_conversations_user_kind", table_name="ai_conversations")
    op.create_index(
        "uq_ai_conversations_user_kind",
        "ai_conversations",
        ["user_id", "kind"],
        unique=True,
    )

    with op.batch_alter_table("ai_conversations") as batch:
        batch.drop_constraint("ck_ai_conversations_kind", type_="check")
        batch.create_check_constraint("ck_ai_conversations_kind", _KIND_ALT)

    with op.batch_alter_table("ai_runs") as batch:
        batch.drop_constraint("ck_ai_runs_status", type_="check")
        batch.create_check_constraint("ck_ai_runs_status", _STATUS_ALT)
        batch.drop_column("wake_at")
