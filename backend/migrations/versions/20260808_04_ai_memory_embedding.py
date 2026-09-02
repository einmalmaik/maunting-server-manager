"""Store a local embedding next to each AI memory entry.

Revision ID: 20260808_04
Revises: 20260808_03
Create Date: 2026-08-08

Die Auswahl bei knappem Kontext lief bisher ueber Wortueberlappung. Die greift
nur innerhalb einer Sprache: ein deutscher Eintrag und eine englische Frage
haben keine gemeinsamen Woerter, und das Gedaechtnis wirkte in dem Moment
kaputt. Mit einem lokalen Vektor je Eintrag entscheidet stattdessen Bedeutung.

**Warum unverschluesselt.** Der Wert selbst bleibt DIS-verschluesselt. Der
Vektor liegt daneben im Klartext, aus zwei Gruenden:

1. Der `key` steht ohnehin unverschluesselt in derselben Zeile
   (`ram.bevorzugt`, `backup.zeitpunkt`). Er verraet mehr ueber den Inhalt als
   256 Gleitkommazahlen, aus denen sich der Text nicht rekonstruieren laesst.
2. Die Suche kann damit *vor* dem Entschluesseln stattfinden. Bisher wurde bei
   jeder Chatnachricht **jeder** Eintrag entschluesselt — und jede
   Entschluesselung ist ein HTTP-Aufruf an den DIS-Sidecar. Mit dem Vektor
   werden nur noch die tatsaechlich ausgewaehlten Eintraege entschluesselt.

Restrisiko, ausdruecklich benannt: Wer die Datenbank hat, kann mit den Vektoren
Vermutungen ueber Inhalte bestaetigen ("aehnelt dieser Eintrag dem Satz X?").
Den Text bekommt er nicht.

**Warum kein pgvector.** MSM verwaltet seinen PostgreSQL selbst mit dem
Standard-Image `postgres:17-alpine`, das die Erweiterung nicht enthaelt — ein
Wechsel traefe jede Installation. Ein Vektorindex lohnt ab etwa zehntausend
Eintraegen; hier sind es hoechstens hundert je Bereich, und dafuer ist ein
Skalarprodukt in Python schneller als der Datenbank-Roundtrip.

Bestehende Zeilen bekommen NULL und werden beim naechsten Schreibzugriff
nachgerechnet. Ein Eintrag ohne Vektor faellt in der Auswahl nicht heraus — er
wird dann nur ueber Wortabgleich, Nutzung und Aktualitaet bewertet.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260808_04"
down_revision: Union[str, None] = "20260808_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_memory_entries") as batch:
        # JSON-Text statt Binaerspalte: portabel zwischen PostgreSQL und dem
        # SQLite der Migrationstests, und im Zweifel mit blossem Auge lesbar.
        # 256 Werte sind rund 3 KB — bei hundert Eintraegen je Bereich
        # unerheblich.
        batch.add_column(sa.Column("embedding_json", sa.Text(), nullable=True))
        # Merkt sich, womit gerechnet wurde. Wechselt der Betreiber das Modell,
        # sind alte Vektoren nicht mehr vergleichbar und werden ignoriert,
        # statt stillschweigend falsche Aehnlichkeiten zu liefern.
        batch.add_column(sa.Column("embedding_model", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ai_memory_entries") as batch:
        batch.drop_column("embedding_model")
        batch.drop_column("embedding_json")
