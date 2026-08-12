"""Ein Backup sagt jetzt, ob es nachgemessen wurde.

Revision ID: 20260812_04
Revises: 20260812_03
Create Date: 2026-08-12

`backups.sha256` und `backups.verified_at`.

Der Anlass ist eine Zusage, die ohne diese beiden Spalten nicht einloesbar ist:
die KI darf im autonomen Guardian-Betrieb nichts ueberschreiben oder loeschen,
bevor ein Backup nachweislich geglueckt ist. "Nachweislich" liess sich bisher
nirgends festhalten.

Was es vorher gab und warum es nicht reicht:

- Das blosse Vorhandensein einer Zeile. Sie entsteht nach dem Schreiben des
  Archivs — aber der Remote-Agent-Pfad legt sie **vor** der Arbeit des Agenten
  an, mit einem Platzhalter-Dateinamen, der lokal nie existiert.
- `size_mb`. Das ist `bytes // (1024*1024)` und damit **0** fuer jedes Archiv
  unter einem Megabyte. Ein frisch angelegter Server hat genau so eines. Eine
  Pruefung `size_mb > 0` haette also ausgerechnet den Fall abgelehnt, in dem
  am wenigsten schiefgehen kann.
- `s3_key`. Der wird gesetzt, wenn der Upload keine Ausnahme geworfen hat —
  nicht, wenn das Objekt danach im Bucket liegt.

`verified_at` ist deshalb bewusst ein Zeitpunkt und kein Statuswort: es gibt
genau eine Aussage zu treffen ("wurde nachgemessen, und zwar dann"), und ein
Freitextstatus haette sofort die Frage nach seinen erlaubten Werten aufgeworfen.
NULL heisst **unbewiesen**, nie "kaputt" — alle Bestandszeilen bleiben damit
korrekt unbewiesen, statt rueckwirkend als geprueft zu gelten. Das ist die
sichere Richtung: ein Altbestand blockiert dann hoechstens eine autonome
Heilung, statt sie auf einer Annahme laufen zu lassen.

`sha256` ist die Pruefsumme des Archivs **so wie es auf der Platte liegt** —
beim lokal verschluesselten Backup also die der `.enc`-Datei. Nur diese laesst
sich ohne Schluessel nachrechnen. Beim Remote-Agent-Pfad liefert der Agent den
Wert bereits (`backup_orchestrator`), er wurde bisher nur verworfen.

Beide Spalten nullable und ohne Server-Default: ein Backup ohne Nachweis bleibt
ein gueltiges Backup, es taugt nur nicht als Freigabe fuer einen KI-Eingriff.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260812_04"
down_revision: Union[str, None] = "20260812_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("backups") as batch:
        batch.add_column(sa.Column("sha256", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("backups") as batch:
        batch.drop_column("verified_at")
        batch.drop_column("sha256")
