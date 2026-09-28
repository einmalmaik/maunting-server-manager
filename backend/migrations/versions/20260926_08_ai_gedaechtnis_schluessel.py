"""Gedaechtnis: Name verschluesselt, tote Spalten weg, DIS-Praefix nachgetragen.

Drei Dinge, alle aus demselben Befund vom 26.09.2026: wer die KI-Tabellen im
PostgreSQL-Studio ansah, las mehr, als er sollte.

1. ``ai_memory_entries.key`` stand im Klartext ("zeitzone",
   "character_pairing") und verriet damit oft schon den Inhalt. Die Spalte
   heisst jetzt ``key_encrypted``; daneben kommt ``key_index``, ein HMAC ueber
   Bereich und Namen, auf dem die Eindeutigkeit je Bereich liegt.
   Verschluesseln und Indizieren kann nur der DIS-Sidecar, und der laeuft
   waehrend einer Migration nicht verlaesslich. Bestandszeilen behalten hier
   deshalb ihren Klartext und ``key_index`` NULL; nachgezogen wird beim Start
   des Panels (``ai_memory_service.schluessel_nachziehen``) und vor jeder
   Suche im betroffenen Bereich.
2. Tote Spalten fallen weg: ``ai_memory_entries.embedding_json`` (seit dem
   19.08.2026 nicht mehr geschrieben, zum Teil noch Vektoren im Klartext) und
   ``ai_memory_preferences.updated_at`` (geschrieben, nie gelesen). Was in
   ``embedding_json`` noch steht und in ``embedding_bytes`` fehlt, wird vorher
   in die rohe Byteform umgepackt; der Code verpackt sie beim naechsten Abruf.
3. Werte, die ausschliesslich DIS-Chiffrat tragen, bekommen den Praefix
   ``msm-dis-v1:``. Dafuer braucht es keinen Sidecar: der Umschlag ist nur
   der Praefix vor demselben Base64, und der Sidecar trennt ihn beim Lesen
   wieder ab.

Revision ID: 20260926_08
Revises: 20260926_07
Create Date: 2026-09-26
"""

from __future__ import annotations

import json
from array import array
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260926_08"
down_revision: Union[str, None] = "20260926_07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PRAEFIX = "msm-dis-v1:"

#: Spalten, in denen nie etwas anderes als DIS-Chiffrat stand. Nur hier ist
#: das blosse Voranstellen richtig; eine Spalte mit Klartext-Altbestand
#: wuerde es unlesbar machen.
NUR_DIS = (
    ("ai_memory_entries", "value_encrypted"),
    ("ai_attachments", "content_encrypted"),
    ("ai_attachments", "extracted_text_encrypted"),
    ("ai_action_proposals", "payload_encrypted"),
    ("ai_providers", "operator_api_key_encrypted"),
)

EMBEDDING_DIMENSIONS = 256
BLOCK = 500


def _tabellen() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _spalten(tabelle: str) -> set[str]:
    return {s["name"] for s in sa.inspect(op.get_bind()).get_columns(tabelle)}


def _eindeutige(tabelle: str) -> set[str]:
    # Auf SQLite haben aeltere Batch-Migrationen den Constraint zum Teil schon
    # verloren; geloescht wird deshalb nur, was da ist.
    return {u["name"] for u in sa.inspect(op.get_bind()).get_unique_constraints(tabelle)}


def _packen(text: str | None) -> bytes | None:
    """Wie in ``20260819_01``: JSON-Liste zu float32-Bytes, sonst nichts."""
    try:
        werte = json.loads(text or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(werte, list) or len(werte) != EMBEDDING_DIMENSIONS:
        return None
    try:
        return array("f", werte).tobytes()
    except (TypeError, ValueError, OverflowError):
        return None


def _json_umpacken(conn) -> None:
    letzte = ""
    while True:
        zeilen = conn.execute(
            sa.text(
                "SELECT id, embedding_json FROM ai_memory_entries "
                "WHERE embedding_json IS NOT NULL AND embedding_bytes IS NULL "
                "AND id > :letzte ORDER BY id LIMIT :block"
            ),
            {"letzte": letzte, "block": BLOCK},
        ).fetchall()
        if not zeilen:
            return
        letzte = zeilen[-1].id
        umgerechnet = [
            {"id": zeile.id, "vektor": gepackt}
            for zeile in zeilen
            if (gepackt := _packen(zeile.embedding_json)) is not None
        ]
        if umgerechnet:
            conn.execute(
                sa.text("UPDATE ai_memory_entries SET embedding_bytes = :vektor WHERE id = :id"),
                umgerechnet,
            )


def upgrade() -> None:
    conn = op.get_bind()
    vorhanden = _tabellen()

    if "ai_memory_entries" in vorhanden:
        spalten = _spalten("ai_memory_entries")
        eindeutige = _eindeutige("ai_memory_entries")
        if "embedding_json" in spalten:
            _json_umpacken(conn)
        with op.batch_alter_table("ai_memory_entries") as batch:
            if "embedding_json" in spalten:
                batch.drop_column("embedding_json")
            if "uq_ai_memory_scope_key" in eindeutige:
                batch.drop_constraint("uq_ai_memory_scope_key", type_="unique")
            if "key" in spalten:
                batch.alter_column(
                    "key",
                    new_column_name="key_encrypted",
                    existing_type=sa.String(length=64),
                    type_=sa.Text(),
                    existing_nullable=False,
                )
            if "key_index" not in spalten:
                batch.add_column(sa.Column("key_index", sa.String(length=64), nullable=True))
            batch.create_unique_constraint(
                "uq_ai_memory_scope_key_index", ["scope_identity", "key_index"]
            )

    if "ai_memory_preferences" in vorhanden and "updated_at" in _spalten("ai_memory_preferences"):
        with op.batch_alter_table("ai_memory_preferences") as batch:
            batch.drop_column("updated_at")

    for tabelle, spalte in NUR_DIS:
        if tabelle in vorhanden and spalte in _spalten(tabelle):
            conn.execute(
                sa.text(
                    f"UPDATE {tabelle} SET {spalte} = :praefix || {spalte} "
                    f"WHERE {spalte} IS NOT NULL AND {spalte} <> '' "
                    f"AND {spalte} NOT LIKE 'msm-dis-%'"
                ),
                {"praefix": PRAEFIX},
            )


def downgrade() -> None:
    conn = op.get_bind()
    vorhanden = _tabellen()

    # Der aeltere Sidecar kennt den Praefix nicht; ohne ihn ist es dasselbe
    # Chiffrat wie vorher.
    for tabelle, spalte in NUR_DIS:
        if tabelle in vorhanden and spalte in _spalten(tabelle):
            conn.execute(
                sa.text(
                    f"UPDATE {tabelle} SET {spalte} = substr({spalte}, {len(PRAEFIX) + 1}) "
                    f"WHERE {spalte} LIKE '{PRAEFIX}%'"
                )
            )

    if "ai_memory_preferences" in vorhanden:
        with op.batch_alter_table("ai_memory_preferences") as batch:
            batch.add_column(
                sa.Column(
                    "updated_at", sa.DateTime(timezone=True), nullable=False,
                    server_default=sa.func.now(),
                )
            )

    if "ai_memory_entries" in vorhanden:
        # Ein verschluesselter Name laesst sich hier nicht zurueckholen. Er wird
        # durch einen Platzhalter aus seinem Index ersetzt: eindeutig je Bereich
        # wie vorher, der Wert daneben bleibt lesbar. Erkannt wird er am
        # Praefix, nicht an der Laenge: ein kurzer Name ist auch als Chiffrat
        # unter 64 Zeichen und stuende sonst als Name im alten Code.
        # Klartextnamen aus der Zeit vor der Umstellung bleiben, wie sie sind.
        conn.execute(
            sa.text(
                "UPDATE ai_memory_entries SET key_encrypted = "
                "'eintrag-' || substr(coalesce(key_index, id), 1, 24) "
                "WHERE key_encrypted LIKE 'msm-dis-%'"
            )
        )
        eindeutige = _eindeutige("ai_memory_entries")
        with op.batch_alter_table("ai_memory_entries") as batch:
            if "uq_ai_memory_scope_key_index" in eindeutige:
                batch.drop_constraint("uq_ai_memory_scope_key_index", type_="unique")
            batch.drop_column("key_index")
            batch.alter_column(
                "key_encrypted",
                new_column_name="key",
                existing_type=sa.Text(),
                type_=sa.String(length=64),
                existing_nullable=False,
            )
            batch.create_unique_constraint("uq_ai_memory_scope_key", ["scope_identity", "key"])
            batch.add_column(sa.Column("embedding_json", sa.Text(), nullable=True))
