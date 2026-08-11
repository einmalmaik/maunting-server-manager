"""Ein Gedaechtnis, das der Anlage gehoert.

Revision ID: 20260811_03
Revises: 20260811_02
Create Date: 2026-08-11

Der Anlass ist ein einzelner Satz aus dem Betrieb: *"Bei diesem Server muss man
nach jedem Neustart die Whitelist neu laden, sonst kommt keiner rein."* Die KI
legte ihn im **persoenlichen** Gedaechtnis ab. Das war nicht falsch angewandt,
sondern eine fehlende Schublade — der Satz gehoert weder einem Menschen noch
einem Team, sondern der Anlage. Der Kollege, der morgen Dienst hat, findet ihn
im persoenlichen Gedaechtnis nie.

`server_shared` ist deshalb **ein weiterer Wert in einer vorhandenen Spalte**
und keine neue Tabelle: `ai_memory_entries` traegt Embedding, `use_count`,
`last_used_at`, `origin` und `aad_version` bereits, und `server_id` mit
`ON DELETE CASCADE` liegt seit `20260801_06` bereit. Wird der Server geloescht,
verschwindet sein Wissen mit ihm; wird der Kollege geloescht, der es
aufschrieb, bleibt es stehen (`owner_user_id` ist bei diesem Scope NULL, wie
bei `team`).

Zur Laenge: die Spalte ist `String(16)`, `server_shared` hat 13 Zeichen. Das
ist knapp genug, um es hier zu erwaehnen — ein laengerer Scopename schluege auf
PostgreSQL fehl, waehrend SQLite ihn stillschweigend abnimmt.

**Bestandszeilen werden nicht umgehaengt.** Persoenliche Servernotizen bleiben
persoenlich. Zwei Gruende, beide zwingend: `aad_version=2` bindet den
Ciphertext an `scope_identity`, ein `UPDATE` machte die Zeile also *unlesbar*
statt sie zu verschieben (festgehalten in `test_ai_memory_isolation.py`) — und
eine automatische Umwandlung waere ohnehin falsch, weil sie privat Notiertes
fuer alle Kollegen sichtbar machte.

Das Downgrade loescht die Eintraege dieses Scopes, bevor es den CHECK
zurueckbaut: sie haetten danach keinen gueltigen Scope mehr. Reihenfolge und
Aufbau woertlich nach `20260809_02`, das denselben Schritt fuer `team`
gegangen ist.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260811_03"
down_revision: Union[str, None] = "20260811_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ALT = "scope IN ('user', 'server', 'team', 'panel')"
_NEU = "scope IN ('user', 'server', 'server_shared', 'team', 'panel')"


def upgrade() -> None:
    # Ein einziger Batch-Block: auf SQLite wird die Tabelle dabei genau einmal
    # neu aufgebaut, samt ersetztem CHECK.
    with op.batch_alter_table("ai_memory_entries") as batch:
        batch.drop_constraint("ck_ai_memory_entries_scope", type_="check")
        batch.create_check_constraint("ck_ai_memory_entries_scope", _NEU)


def downgrade() -> None:
    # Erst raeumen, dann verengen. Andersherum bricht das Downgrade an der
    # ersten Zeile ab, die es selbst haette wegraeumen sollen.
    op.execute("DELETE FROM ai_memory_entries WHERE scope = 'server_shared'")
    with op.batch_alter_table("ai_memory_entries") as batch:
        batch.drop_constraint("ck_ai_memory_entries_scope", type_="check")
        batch.create_check_constraint("ck_ai_memory_entries_scope", _ALT)
