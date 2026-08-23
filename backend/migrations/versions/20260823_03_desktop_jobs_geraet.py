"""Ein Desktop-Auftrag gehoert einem Geraet, nicht nur einem Benutzer.

Revision ID: 20260823_03
Revises: 20260823_02
Create Date: 2026-08-23

`desktop_jobs` kannte bisher nur `user_id`, und `desktop_job_service.naechster`
filterte auch nur danach. Mehrere gekoppelte Rechner je Benutzer sind aber
ausdruecklich vorgesehen (`device_pairings`, Geraeteliste mit einzelnem
Widerruf), und jeder von ihnen fragt im Sekundentakt nach Arbeit. Wer den
Auftrag bekam, entschied damit der Zufall des Taktes — fuer einen Blick auf
"meinen Bildschirm" oder eine Uebernahme von Maus und Tastatur ist das der
falsche Rechner.

Der Wert ist die Refresh-Familie der Sitzung, aus der der Lauf kam. Dieselbe
Kennung, unter der die Geraeteliste ein Geraet fuehrt und einzeln widerruft —
und sie ueberlebt jede Token-Rotation, anders als eine `jti`.

**Nullable, und das ist die eigentliche Entscheidung.** Ein Auftrag ohne
Kennung bleibt fuer jedes Geraet des Benutzers abholbar. Anders haetten im
Moment des Deploys alle wartenden Auftraege bis zu ihrer Frist gehangen: sie
tragen die Spalte nicht, und ein Rechner haette sie nie wieder gesehen. Kein
`server_default`, denn es gibt keinen sinnvollen Vorgabewert — "unbekannt" ist
hier eine Aussage und kein fehlender Wert.

Kein neuer Index: gefiltert wird innerhalb dessen, was
`ix_desktop_jobs_user_status` schon auf wenige Zeilen eingegrenzt hat (ein
Benutzer, Zustand `pending`).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260823_03"
down_revision: Union[str, None] = "20260823_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("desktop_jobs") as batch:
        batch.add_column(sa.Column("device_family", sa.String(length=64), nullable=True))


def downgrade() -> None:
    # Die Spalte traegt eine Kennung, keine Fremddaten — es bleibt nichts
    # aufzuraeumen. Die Auftraege selbst gelten danach wieder fuer jedes Geraet.
    with op.batch_alter_table("desktop_jobs") as batch:
        batch.drop_column("device_family")
