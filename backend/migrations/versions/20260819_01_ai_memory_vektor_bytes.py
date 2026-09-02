"""Die Gedächtnisvektoren wechseln von JSON-Text auf float32-Bytes.

Revision ID: 20260819_01
Revises: 20260818_04
Create Date: 2026-08-19

Gemessen am 19.08.2026 an einem Bereich mit 5.000 Einträgen kostete ein
einzelner Chatabruf 717 ms Rechenzeit. Davon waren 381 ms — mehr als die
Hälfte — nichts als das Lesen der Vektoren aus ``embedding_json``: 26,7 MB
Text, in dem Zahlen stehen, Ziffer für Ziffer zurückgerechnet. Dieselben
Zahlen als rohe float32-Bytes kosten 4 ms und 5,1 MB.

Das ist kein Größenordnungsproblem, sondern eine Formatentscheidung, und die
Begründung von damals steht in `20260808_04`: „JSON-Text statt Binaerspalte:
portabel [...] und im Zweifel mit blossem Auge lesbar. 256 Werte sind rund
3 KB — bei hundert Einträgen je Bereich unerheblich." Die Rechnung stimmte.
Nur ist aus „hundert je Bereich" inzwischen bis zu 5.000 geworden, und damit
wird aus unerheblich die größte einzelne Position vor dem ersten Byte an den
Anbieter.

**Die alte Spalte bleibt stehen.** Sie wird nicht mehr geschrieben und beim
Lesen nur noch als Rückfall benutzt (`ai_memory_service._stored_vector`).
Zwei Gründe, und der erste ist der wichtigere:

1. Zwischen dem Einspielen des neuen Codes und dem Durchlauf dieser Migration
   liegen bei jedem Betreiber ein paar Sekunden. Ohne Rückfall fände sein
   Gedächtnis in dieser Zeit zu keiner Frage mehr etwas.
2. Ein Rückbau muss möglich bleiben. ``downgrade`` wirft nur die neue Spalte
   weg; der ältere Code liest danach weiter aus ``embedding_json``.

Zeilen, die der neue Code seither geschrieben hat, tragen kein JSON mehr — sie
verlieren bei einem Rückbau ihren Vektor. Das ist verkraftbar und heilt von
selbst: ``_vektoren_nachziehen`` rechnet einen fehlenden Vektor beim nächsten
Abruf in den Kontext neu, in der Form, die der dann laufende Code schreibt.

**Warum die Umrechnung hier passiert und nicht im Nachziehweg.** Der Weg über
``_vektoren_nachziehen`` hätte nahegelegen — er rechnet ohnehin fehlende
Vektoren nach. Er ist aber der falsche für diesen Fall, gleich dreifach: er
*berechnet* neu, wo hier nur *umgepackt* wird (dieselben Zahlen, andere Form);
er braucht dafür das Embeddingmodell, das ein Betreiber nicht installiert
haben muss; und er fasst nur Zeilen an, die tatsächlich in einen Kontext
geraten — eine selten gebrauchte Zeile bliebe auf Dauer im alten Format. Hier
ist es ein Durchlauf, deterministisch, ohne Modell.

Gelesen und geschrieben wird in Blöcken. Bei 5.000 Einträgen je Bereich und
mehreren Bereichen kann die Tabelle sechsstellig werden; die JSON-Spalte auf
einmal in den Speicher zu holen wäre ein halbes Gigabyte für eine Migration,
die während eines Updates läuft.
"""

from __future__ import annotations

import json
from array import array
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260819_01"
down_revision: Union[str, None] = "20260818_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Ausgabegröße von `potion-multilingual-128M`, wie in
# `ai_embedding_service.EMBEDDING_DIMENSIONS`. Hier als Literal und nicht als
# Import: eine Migration muss auch dann noch laufen, wenn der Anwendungscode
# sich längst weitergedreht hat (dieselbe Regel wie in `20260812_01`).
EMBEDDING_DIMENSIONS = 256

#: Wieviele Zeilen ein Durchgang liest. 500 Zeilen sind rund 1,5 MB JSON.
BLOCK = 500


def _packen(text: str | None) -> bytes | None:
    """Rechnet eine gespeicherte JSON-Liste in float32-Bytes um.

    Dieselbe Form wie in `ai_embedding_service.vektor_zu_bytes`: die Zahlen
    hintereinander als ``array("f")``, ohne Rahmen und ohne Kopf. Bewusst
    nachgebaut statt importiert — eine Migration muss auch dann noch laufen,
    wenn der Anwendungscode sich längst weitergedreht hat.

    Alles, was nicht nach einem vollständigen Vektor aussieht, wird
    übersprungen statt geraten. Die Zeile behält dann ihr JSON und wird beim
    nächsten Abruf ohnehin neu berechnet.
    """
    if not text:
        return None
    try:
        werte = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(werte, list) or len(werte) != EMBEDDING_DIMENSIONS:
        return None
    try:
        return array("f", werte).tobytes()
    except (TypeError, ValueError, OverflowError):
        return None


def upgrade() -> None:
    op.add_column(
        "ai_memory_entries",
        sa.Column("embedding_bytes", sa.LargeBinary(), nullable=True),
    )

    conn = op.get_bind()
    # Blockweise über die Schlüssel statt über OFFSET: `id` ist der
    # Primärschlüssel (UUID als Text), damit ist die Reihenfolge stabil und
    # jeder Block kostet einen Indexzugriff statt eines wachsenden Überlesens.
    letzte = ""
    while True:
        zeilen = conn.execute(
            sa.text(
                "SELECT id, embedding_json FROM ai_memory_entries "
                "WHERE embedding_json IS NOT NULL AND id > :letzte "
                "ORDER BY id LIMIT :block"
            ),
            {"letzte": letzte, "block": BLOCK},
        ).fetchall()
        if not zeilen:
            break
        letzte = zeilen[-1].id
        umgerechnet = [
            {"id": zeile.id, "vektor": gepackt}
            for zeile in zeilen
            if (gepackt := _packen(zeile.embedding_json)) is not None
        ]
        if umgerechnet:
            conn.execute(
                sa.text(
                    "UPDATE ai_memory_entries SET embedding_bytes = :vektor "
                    "WHERE id = :id"
                ),
                umgerechnet,
            )


def downgrade() -> None:
    # Nur die neue Spalte fällt. ``embedding_json`` steht unberührt daneben —
    # genau dafür wurde sie beim Umstieg nicht geleert.
    op.drop_column("ai_memory_entries", "embedding_bytes")
