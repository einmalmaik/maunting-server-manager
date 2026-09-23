"""Die leeren Klartextspalten der Gruppe fallen weg

Stufe 6c. `20260923_03` hat `chat_groups.name`, `description` und `avatar_url`
geleert und damit das Dringendste erledigt: der Bestand war weg. Stehen
geblieben sind die Spalten — mit der Begruendung, eine geleerte Spalte sei
genauso still wie eine geloeschte.

Das stimmt fuer den heutigen Stand und nicht fuer den naechsten. Eine Spalte,
die es gibt, ist eine Stelle, an die jemand wieder schreiben kann: ein
`group.name = req.name` in einem spaeteren Zweig faellt niemandem auf, weil das
Feld ja da ist. Ein Feld, das es nicht gibt, bricht den Bau.

Genau das ist in Stufe 6a passiert, eine Ebene hoeher: `social_calls.py` gab
weiter `group_name: g.name` heraus, und weil die Spalte noch existierte, war
das kein Fehler, sondern nur ein `null` im Anruf-Banner. Niemand sah es.

*Nicht rueckholbar.* `downgrade()` legt die Spalten wieder an — leer. Die Namen
liegen verschluesselt bei den Mitgliedern und kommen aus keiner Datenbank
zurueck; `20260923_03.downgrade()` setzt darunter seinen Platzhalter, damit die
NOT-NULL-Bedingung wieder haelt.

Revision ID: 20260923_05
Revises: 20260923_04
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260923_05"
down_revision = "20260923_04"
branch_labels = None
depends_on = None

TABELLE = "chat_groups"
# Die Formen, mit denen die Spalten zurueckkommen muessen: `20260923_03`
# greift beim Abwaertsgang nach `name` als `String(64)` und setzt es auf
# NOT NULL. Kaeme es als etwas anderes zurueck, stuende die Kette dort.
WEG = (
    ("name", sa.String(length=64)),
    ("description", sa.String(length=256)),
    ("avatar_url", sa.Text()),
)


def _spalten() -> set[str]:
    bind = op.get_bind()
    pruefer = sa.inspect(bind)
    if TABELLE not in pruefer.get_table_names():
        return set()
    return {s["name"] for s in pruefer.get_columns(TABELLE)}


def upgrade() -> None:
    vorhanden = _spalten()
    if not vorhanden:
        return

    # Kein Index zeigt auf diese drei — anders als bei `direct_chats` in
    # `20260923_04` ist deshalb nichts vorab abzuraeumen. Der Stapel bleibt
    # trotzdem: SQLite kennt kein `DROP COLUMN` in Alembics Sinn.
    with op.batch_alter_table(TABELLE) as stapel:
        for spalte, _ in WEG:
            if spalte in vorhanden:
                stapel.drop_column(spalte)


def downgrade() -> None:
    vorhanden = _spalten()
    if not vorhanden:
        return

    with op.batch_alter_table(TABELLE) as stapel:
        for spalte, art in WEG:
            if spalte not in vorhanden:
                # Nullable, auch `name`: der Inhalt ist weg, und
                # `20260923_03.downgrade()` setzt erst seinen Platzhalter,
                # bevor es die NOT-NULL-Bedingung wieder anzieht.
                stapel.add_column(sa.Column(spalte, art, nullable=True))
