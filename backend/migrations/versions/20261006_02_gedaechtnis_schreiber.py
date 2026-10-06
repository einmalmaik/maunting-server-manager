"""Gedaechtnis v2, Stufe 2: der Hintergrund schreibt das Gedaechtnis.

Bis hierher schrieb das Chatmodell selbst, ueber die Werkzeuge ``remember``
und ``forget_memory`` — mitten in der Antwort, je Aufruf eine ganze Runde
beim Anbieter. Betreiberentscheid vom 06.10.2026 (Vorbild ChatGPT Dreaming):
ein Hintergrundschreiber liest das Gespraech, wenn es fuenf Minuten ruht, und
sofort, wenn der Mensch "merk dir" oder "vergiss" sagt.

- ``ai_conversations`` bekommt die Marke des Schreibers: bis wohin gelesen ist
  (``gedaechtnis_bis`` und ``gedaechtnis_bis_id`` — ein Paar, weil zwei
  Nachrichten denselben Zeitstempel tragen koennen), wann der naechste
  Durchgang faellig ist, wer ihn gerade haelt und wie oft er zuletzt
  scheiterte.
- Bestehende Gespraeche beginnen **jetzt**: die Marke steht auf dem Zeitpunkt
  der Migration. Sonst laese der erste Takt nach dem Update den ganzen Verlauf
  jedes Benutzers auf einmal — ein Kostenstoss, den niemand bestellt hat.
- ``ai_usage_events.zweck`` kennt ``gedaechtnis``: der Durchgang zaehlt in
  Tokens und Kosten, aber nicht als Anfrage des Benutzers.
- ``ai_memory_preferences.eingeschaltet_am``: seit wann die Einwilligung
  gilt. Persoenliches schreibt der Schreiber nur aus dem, was seitdem gesagt
  wurde — auch der Zusammenhang vor der Marke zaehlt dazu. Wer heute schon
  eingeschaltet hat, gilt ab der Migration: wann es davor war, weiss niemand.

Revision ID: 20261006_02
Revises: 20261006_01
Create Date: 2026-10-06
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261006_02"
down_revision: Union[str, None] = "20261006_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ZWECKE_VORHER = "zweck IS NULL OR zweck IN ('ethik')"
ZWECKE = "zweck IS NULL OR zweck IN ('ethik', 'gedaechtnis')"


def _spalten(tabelle: str) -> set[str]:
    return {s["name"] for s in sa.inspect(op.get_bind()).get_columns(tabelle)}


def _indizes(tabelle: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(tabelle)}


def upgrade() -> None:
    spalten = _spalten("ai_conversations")
    with op.batch_alter_table("ai_conversations") as batch:
        if "gedaechtnis_bis" not in spalten:
            batch.add_column(sa.Column("gedaechtnis_bis", sa.DateTime(timezone=True), nullable=True))
        if "gedaechtnis_bis_id" not in spalten:
            batch.add_column(sa.Column("gedaechtnis_bis_id", sa.String(length=36), nullable=True))
        if "gedaechtnis_faellig" not in spalten:
            batch.add_column(
                sa.Column("gedaechtnis_faellig", sa.DateTime(timezone=True), nullable=True)
            )
        if "gedaechtnis_sperre" not in spalten:
            batch.add_column(
                sa.Column("gedaechtnis_sperre", sa.DateTime(timezone=True), nullable=True)
            )
        if "gedaechtnis_fehlversuche" not in spalten:
            batch.add_column(
                sa.Column(
                    "gedaechtnis_fehlversuche", sa.SmallInteger(), nullable=False,
                    server_default="0",
                )
            )
    if "gedaechtnis_bis" not in spalten:
        op.execute("UPDATE ai_conversations SET gedaechtnis_bis = CURRENT_TIMESTAMP")
    if "ix_ai_conversations_gedaechtnis_faellig" not in _indizes("ai_conversations"):
        op.create_index(
            "ix_ai_conversations_gedaechtnis_faellig",
            "ai_conversations",
            ["gedaechtnis_faellig"],
            postgresql_where=sa.text("gedaechtnis_faellig IS NOT NULL"),
        )

    with op.batch_alter_table("ai_usage_events") as batch:
        batch.drop_constraint("ck_ai_usage_events_zweck", type_="check")
        batch.create_check_constraint("ck_ai_usage_events_zweck", ZWECKE)

    if "eingeschaltet_am" not in _spalten("ai_memory_preferences"):
        with op.batch_alter_table("ai_memory_preferences") as batch:
            batch.add_column(
                sa.Column("eingeschaltet_am", sa.DateTime(timezone=True), nullable=True)
            )
        op.execute(
            "UPDATE ai_memory_preferences SET eingeschaltet_am = CURRENT_TIMESTAMP WHERE enabled"
        )


def downgrade() -> None:
    if "eingeschaltet_am" in _spalten("ai_memory_preferences"):
        with op.batch_alter_table("ai_memory_preferences") as batch:
            batch.drop_column("eingeschaltet_am")

    # Die Zeilen bleiben, ihr Zweck nicht: der alte Stand kennt nur `ethik`.
    # Ohne Zweck zaehlte ein Durchgang dort als Anfrage des Benutzers; `ethik`
    # sagt das Richtige — eine Anfrage, die MSM fuer ihn stellt.
    op.execute("UPDATE ai_usage_events SET zweck = 'ethik' WHERE zweck = 'gedaechtnis'")
    with op.batch_alter_table("ai_usage_events") as batch:
        batch.drop_constraint("ck_ai_usage_events_zweck", type_="check")
        batch.create_check_constraint("ck_ai_usage_events_zweck", ZWECKE_VORHER)

    if "ix_ai_conversations_gedaechtnis_faellig" in _indizes("ai_conversations"):
        op.drop_index("ix_ai_conversations_gedaechtnis_faellig", table_name="ai_conversations")
    spalten = _spalten("ai_conversations")
    with op.batch_alter_table("ai_conversations") as batch:
        for spalte in (
            "gedaechtnis_fehlversuche",
            "gedaechtnis_sperre",
            "gedaechtnis_faellig",
            "gedaechtnis_bis_id",
            "gedaechtnis_bis",
        ):
            if spalte in spalten:
                batch.drop_column(spalte)
