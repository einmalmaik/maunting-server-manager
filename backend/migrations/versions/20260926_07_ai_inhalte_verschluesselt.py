"""Mail-Korb, Aufgaben und Anhangnamen der KI werden Chiffrat.

Wie in 20260926_06: die Werte stehen jetzt als ``DisText`` in der Datenbank,
und ein Chiffrat passt nicht in die alten ``VARCHAR``-Laengen. Betroffen sind
``ai_mail_outbox.betreff`` (255) und ``letzter_fehler`` (500),
``ai_tasks.title`` (120) und ``ai_attachments.original_name`` (128).

Dazu der Mail-Korb selbst: eine zugestellte Mail wird ab jetzt geloescht statt
als ``zugestellt`` aufbewahrt (Begruendung an ``models/ai_mail_outbox.py``).
Die schon zugestellten Zeilen fallen hier weg, mit ihnen ``sent_at`` und der
Zustand ``zugestellt``.

Verschluesselt wird der Altbestand nicht hier, sondern von
``services/dis_altbestand.py`` beim Start des Panels.

Revision ID: 20260926_07
Revises: 20260926_06
Create Date: 2026-09-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260926_07"
down_revision: Union[str, None] = "20260926_06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: (Tabelle, Spalte, alte Laenge, nullable)
_SPALTEN = (
    ("ai_mail_outbox", "betreff", 255, False),
    ("ai_mail_outbox", "letzter_fehler", 500, True),
    ("ai_tasks", "title", 120, False),
    ("ai_attachments", "original_name", 128, False),
)


def _tabellen() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _spalten(tabelle: str) -> set[str]:
    return {s["name"] for s in sa.inspect(op.get_bind()).get_columns(tabelle)}


def upgrade() -> None:
    vorhanden = _tabellen()
    if "ai_mail_outbox" in vorhanden:
        op.get_bind().execute(sa.text("DELETE FROM ai_mail_outbox WHERE status = 'zugestellt'"))
    for tabelle, spalte, laenge, nullable in _SPALTEN:
        if tabelle not in vorhanden:
            continue
        with op.batch_alter_table(tabelle) as batch:
            batch.alter_column(
                spalte,
                existing_type=sa.String(length=laenge),
                type_=sa.Text(),
                existing_nullable=nullable,
            )
            if tabelle == "ai_mail_outbox" and spalte == "betreff":
                if "sent_at" in _spalten("ai_mail_outbox"):
                    batch.drop_column("sent_at")
                batch.drop_constraint("ck_ai_mail_outbox_status", type_="check")
                batch.create_check_constraint(
                    "ck_ai_mail_outbox_status", "status IN ('offen', 'aufgegeben')"
                )


def downgrade() -> None:
    bind = op.get_bind()
    vorhanden = _tabellen()
    for tabelle, spalte, laenge, nullable in _SPALTEN:
        if tabelle not in vorhanden:
            continue
        # Ein Chiffrat laesst sich hier nicht zurueckverwandeln. Was nicht
        # passt, wird leer (oder NULL); abschneiden machte es nur unbrauchbar.
        ersatz = "NULL" if nullable else "''"
        bind.execute(
            sa.text(f"UPDATE {tabelle} SET {spalte} = {ersatz} WHERE length({spalte}) > {laenge}")
        )
        with op.batch_alter_table(tabelle) as batch:
            batch.alter_column(
                spalte,
                existing_type=sa.Text(),
                type_=sa.String(length=laenge),
                existing_nullable=nullable,
            )
            if tabelle == "ai_mail_outbox" and spalte == "betreff":
                batch.add_column(sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
                batch.drop_constraint("ck_ai_mail_outbox_status", type_="check")
                batch.create_check_constraint(
                    "ck_ai_mail_outbox_status", "status IN ('offen', 'zugestellt', 'aufgegeben')"
                )
