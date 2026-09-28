"""Der Direktchat nennt keine Menschen mehr

Stufe 6b. `direct_chats.user_a_id`, `user_b_id` und `initiated_by_user_id`
standen im Klartext. Das war die Tabelle, die auf „wer schreibt mit wem?"
antwortete — ein `SELECT` je Konto, und das soziale Netz lag offen, ohne eine
einzige Nachricht zu entschluesseln. Fuer die meisten Fragen ueber einen
Menschen braucht es den Inhalt gar nicht; es genuegt zu wissen, dass er mit
einer Suchtberatung schreibt, mit einer Kanzlei oder mit einer Gewerkschaft.

Uebrig bleibt die Kennung der Mailbox und ihr Zeitstempel. Die Zeile sagt damit
„dieses Gespraech gibt es", nicht „diese beiden fuehren es". Gebraucht wird sie
nur noch als Sperre: `_mailbox_ist_ableitbar` fragt sie, damit ein Fremder eine
bestehende Gespraechsmailbox nicht mit einem eigenen Besitznachweis belegt und
die echten Teilnehmer aussperrt.

**Doppelte Zeilen.** Es gab `UNIQUE(user_a_id, user_b_id)`, aber keinen Riegel
auf `blind_mailbox_id`. Mit den Personenspalten faellt der alte Riegel weg, und
der neue braucht eine eindeutige Spalte; deshalb werden Doppelte vorher
zusammengefasst — die aelteste Zeile bleibt, mit dem juengsten `updated_at`.

*Nicht rueckholbar.* `downgrade()` stellt die Spalten wieder her, aber nicht
ihren Inhalt: wer mit wem geschrieben hat, steht danach nirgends mehr. Die
Spalten kommen als nullable zurueck; eine erzwungene NOT-NULL-Bedingung liesse
sich nur mit erfundenen Konto-Ids erfuellen, und erfundene Zuordnungen waeren
schlimmer als fehlende.

Zurueck muessen aber auch die *Beigaben* der Spalten — `ix_direct_chats_user_a_id`,
`ix_direct_chats_user_b_id` und `uq_direct_chats_users`. `20260907_05.downgrade()`
raeumt sie namentlich ab; faenden sie sich nicht, bliebe die Kette dort mit
„no such index" stehen. Eine Abwaertsstufe, die man nur einmal gehen kann,
waere keine.

Revision ID: 20260923_04
Revises: 20260923_03
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260923_04"
down_revision = "20260923_03"
branch_labels = None
depends_on = None

TABELLE = "direct_chats"
WEG = ("user_a_id", "user_b_id", "initiated_by_user_id")


def _spalten(tabelle: str) -> set[str]:
    bind = op.get_bind()
    pruefer = sa.inspect(bind)
    if tabelle not in pruefer.get_table_names():
        return set()
    return {s["name"] for s in pruefer.get_columns(tabelle)}


def upgrade() -> None:
    vorhanden = _spalten(TABELLE)
    if not vorhanden:
        return

    # Doppelte Kennungen zusammenfassen, bevor der eindeutige Riegel kommt.
    # Die kleinste Id bleibt und erbt den juengsten Zeitstempel.
    op.execute(
        sa.text(
            """
            UPDATE direct_chats AS z
               SET updated_at = j.neuester
              FROM (
                    SELECT blind_mailbox_id,
                           MIN(id) AS bleibt,
                           MAX(updated_at) AS neuester
                      FROM direct_chats
                     GROUP BY blind_mailbox_id
                   ) AS j
             WHERE z.id = j.bleibt
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM direct_chats
             WHERE id NOT IN (
                   SELECT MIN(id) FROM direct_chats GROUP BY blind_mailbox_id
                   )
            """
        )
    )

    # Erst die Indizes der Personenspalten, dann die Spalten. SQLite kennt kein
    # `DROP COLUMN` in Alembics Sinn: der Stapel baut die Tabelle neu und legt
    # dabei **jeden** vorgefundenen Index wieder an — auch einen auf einer
    # Spalte, die er gerade weggelassen hat. Das endet in „no such column".
    namen = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(TABELLE)}
    for spalte in WEG:
        if f"ix_direct_chats_{spalte}" in namen:
            op.drop_index(f"ix_direct_chats_{spalte}", table_name=TABELLE)

    with op.batch_alter_table(TABELLE) as stapel:
        for spalte in WEG:
            if spalte in vorhanden:
                stapel.drop_column(spalte)

    # Der neue Riegel. Er ersetzt `uq_direct_chats_users`, das mit den Spalten
    # gefallen ist — ohne ihn koennte dieselbe Mailbox mehrfach entstehen und
    # `_direktchat_zeile` bekaeme je nach Laune eine andere davon.
    bind = op.get_bind()
    namen = {i["name"] for i in sa.inspect(bind).get_indexes(TABELLE)}
    if "uq_direct_chats_mailbox" not in namen:
        op.create_index(
            "uq_direct_chats_mailbox", TABELLE, ["blind_mailbox_id"], unique=True
        )


def downgrade() -> None:
    vorhanden = _spalten(TABELLE)
    if not vorhanden:
        return

    pruefer = sa.inspect(op.get_bind())
    namen = {i["name"] for i in pruefer.get_indexes(TABELLE)}
    riegel = {c["name"] for c in pruefer.get_unique_constraints(TABELLE)}
    if "uq_direct_chats_mailbox" in namen:
        op.drop_index("uq_direct_chats_mailbox", table_name=TABELLE)

    with op.batch_alter_table(TABELLE) as stapel:
        for spalte in WEG:
            if spalte not in vorhanden:
                # Nullable, und das ist keine Nachlaessigkeit: die Zuordnung ist
                # weg, und eine NOT-NULL-Bedingung liesse sich nur mit
                # erfundenen Konto-Ids erfuellen.
                stapel.add_column(sa.Column(spalte, sa.Integer(), nullable=True))
        if "uq_direct_chats_users" not in riegel:
            stapel.create_unique_constraint(
                "uq_direct_chats_users", ["user_a_id", "user_b_id"]
            )

    # Die beiden Indizes gehoerten zu den Spalten und sind mit ihnen gefallen.
    # `20260907_05.downgrade()` laesst sie namentlich fallen und bricht ab, wenn
    # es sie nicht gibt — ohne diese Zeilen endete die Kette hier.
    namen = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(TABELLE)}
    for spalte in ("user_a_id", "user_b_id"):
        if f"ix_direct_chats_{spalte}" not in namen:
            op.create_index(f"ix_direct_chats_{spalte}", TABELLE, [spalte])
