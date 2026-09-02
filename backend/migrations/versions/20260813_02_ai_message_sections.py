"""Die Gliederung einer Antwort ueberlebt das Neuladen.

Revision ID: 20260813_02
Revises: 20260813_01
Create Date: 2026-08-13

Eine Spalte `sections_json` auf `ai_messages`: Text und Werkzeuge in der
Reihenfolge, in der sie entstanden sind.

**Warum das eine Spalte braucht und keine Ableitung ist.** Werkzeugaufrufe
gingen bisher ausschliesslich als SSE-Ereignis an den Browser und waren nach
einem Neuladen weg — der Betreiber hat es so beschrieben: "man sieht das ja
nur waehrenddessen". Naheliegend waere `ai_tool_results` gewesen, die Tabelle
gibt es ja. Sie traegt aber das *Ergebnis* (bis zu 24.000 Zeichen Logtext) und
nicht die Anzeigeangaben — keine Gruppe fuer das Symbol, keinen Skillnamen,
kein `failed`, keine Zugehoerigkeit zu einer Nachricht und vor allem keine
Stellung im Text. Aus ihr liesse sich die Reihenfolge nur raten.

**Warum neben `content` und nicht statt dessen.** Die beiden sagen Verschiedenes.
`content` ist der reine Text: er geht in der naechsten Runde an den Anbieter
zurueck, fliesst in die Zusammenfassung ein und wird durchsucht. `sections_json`
ist, was der Browser zeichnet — Text mit Werkzeugchips dazwischen. Wer nur eines
haette, muesste das andere herstellen: aus dem Text die Stellung der Werkzeuge
(unmoeglich) oder aus den Abschnitten einen Text, aus dem die Chips wieder
verschwinden (moeglich, aber dann steht die Regel dafuer an einer dritten
Stelle).

Dasselbe Muster wie `question_json` eine Spalte weiter, aus demselben Anlass und
mit derselben Begruendung: was an einer Nachricht haengt, gehoert an die
Nachricht.

`nullable=True` ohne Vorbefuellung. `NULL` heisst "aus der Zeit vor dieser
Spalte" und ist etwas anderes als `[]` ("diese Antwort hatte keine Abschnitte").
Alte Nachrichten aus ihrem `content` heraus zu einem einzigen Textabschnitt
umzuschreiben waere moeglich — aber es waere eine Behauptung ueber die
Vergangenheit, und die Oberflaeche kommt ohne sie aus: fehlt die Gliederung,
zeigt sie den Text so, wie sie ihn immer gezeigt hat.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260813_02"
down_revision: Union[str, None] = "20260813_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_messages",
        sa.Column("sections_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_messages", "sections_json")
