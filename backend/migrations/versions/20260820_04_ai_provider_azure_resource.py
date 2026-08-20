"""Ein Zugang darf wieder ein Stück seiner Adresse mitbringen — genau eines.

Revision ID: 20260820_04
Revises: 20260820_03
Create Date: 2026-08-20

Azure hat keinen gemeinsamen Einstieg für alle Kunden: jede Ressource ist ihr
eigener Host (``https://mein-ai-hub.services.ai.azure.com/…``). Ohne den Namen
dieser Ressource ist der Anbieter nicht erreichbar — er lässt sich also nicht
unterstützen, ohne eine Betreibereingabe in den Hostnamen zu lassen.

**Das ist nicht die Rückkehr von `base_url`**, die `20260811_01` entfernt hat.
Dort stand eine ganze Adresse — Schema, Host, Port, Pfad —, und jedes Stück
davon war Eingabe; deshalb brauchte MSM damals eine SSRF-Prüfung mit IP-Pinning
gegen umgeschriebene DNS-Antworten. Hier steht ein einzelnes **DNS-Label**.
Schema, Suffix und Pfad bleiben als Vorlage im Programm
(`ai_provider_registry.azure_openai` / `azure_anthropic`), und das Label wird
mit ``re.fullmatch`` gegen die Form eines Labels geprüft — kein Punkt, kein
Schrägstrich, kein Zeilenumbruch, höchstens 63 Zeichen
(`ai_provider_service._assert_ressource`).

Die Spalte ist ``nullable``, und das ist kein Kompromiss: für jeden Anbieter
ohne ``ressource_noetig`` — OpenRouter, OpenAI, ElevenLabs — gibt es nichts
einzutragen, und ein leerer Pflichtwert wäre eine erfundene Angabe in jeder
Bestandszeile. Für Azure erzwingt der Service den Namen beim Anlegen und beim
Aktivieren; die Datenbank muss das nicht ein zweites Mal tun.

**Keine Datenwanderung.** Vor dieser Revision gibt es keinen Azure-Zugang, den
man umschreiben könnte — Bestandszeilen bekommen ``NULL`` und verhalten sich
unverändert.

Zum Downgrade: der Name geht verloren. Wiederherstellen liesse er sich nur aus
einer Quelle, die es nicht gibt; ein Azure-Zugang muss nach einem Downgrade neu
eingerichtet werden. Der Schlüssel bleibt dabei stehen — er gehört weiterhin zu
derselben Ressource, und beim nächsten Upgrade trägt der Betreiber denselben
Namen wieder ein.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260820_04"
down_revision: Union[str, None] = "20260820_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Batch-Modus wie bei den Nachbarmigrationen: SQLite baut die Tabelle neu,
    # PostgreSQL macht daraus ein gewöhnliches ALTER. ``ai_providers`` hat je
    # Panel eine Handvoll Zeilen, der Neubau ist unbedenklich.
    with op.batch_alter_table("ai_providers") as batch:
        batch.add_column(
            sa.Column("azure_resource_name", sa.String(length=64), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_providers") as batch:
        batch.drop_column("azure_resource_name")
