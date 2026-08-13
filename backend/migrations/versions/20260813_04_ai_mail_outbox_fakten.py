"""Der Ausgangskorb haelt die Fakten, nicht nur den fertigen Text.

Revision ID: 20260813_04
Revises: 20260813_03
Create Date: 2026-08-13

Mit `20260813_03` gab es den Korb, aber keinen Weg hinein fuer die Berichte:
`ai_task_report`, `ai_guardian_report` und die Testmail nahmen weiter den
Koroutinenweg. Der Grund war eine Reihenfolge — der Verfassungsschritt
(`ai_mail_text.verfassen`) ist asynchron, das Einreihen verlangte aber eine
**fertig** gerenderte Mail. Also blieb der Modellaufruf vor dem Korb, und damit
blieb die Mail in einem Thread: stuerzte der Prozess zwischen dem Ende eines
Laufs und dem Versand ab, war sie weg.

Diese Revision dreht die Reihenfolge um. Der Korb speichert, was das Modell
wissen muss (`fakten`) und was zum Rendern noetig ist (`rahmen_json`); verfasst
wird erst im Arbeiter. Das hat zwei Folgen, und beide sind der Zweck:

**Der Modellaufruf liegt innerhalb der Begrenzung.** Beim Einreihen zu
verfassen hiesse, dass zehntausend gleichzeitig endende Auftraege zehntausend
gleichzeitige Modellaufrufe ausloesen — dieselbe Rechnung, wegen der es diesen
Korb ueberhaupt gibt, nur eine Ebene hoeher. Im Arbeiter gilt dagegen dieselbe
Schranke wie fuer den Versand.

**Ein Neustart verliert nichts.** Die Angaben stehen in der Datenbank statt in
einem Thread. Kommt der Prozess zurueck, verfasst er und verschickt.

**Beide Spalten sind nullable, und das bleibt so.** Eine Zeile ohne sie geht
mit dem Text hinaus, mit dem sie eingereiht wurde — der aeltere Fall, und der
Rueckfall fuer jede Zeile, die schon im Korb lag, als diese Migration lief.
Ein `server_default` waere hier falsch: „keine Angaben“ ist eine Aussage, ein
leerer String waere eine leere Antwort an ein Modell.

**Kein `ALTER` an bestehenden Spalten**, also auch keine Sonderbehandlung fuer
SQLite: `add_column` beherrscht es ohne Tabellenkopie.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260813_04"
down_revision: Union[str, None] = "20260813_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_mail_outbox", sa.Column("fakten", sa.Text(), nullable=True))
    op.add_column("ai_mail_outbox", sa.Column("rahmen_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_mail_outbox", "rahmen_json")
    op.drop_column("ai_mail_outbox", "fakten")
