"""Ein Loeschvorschlag darf sich nicht selbst loeschen.

Revision ID: 20260810_06
Revises: 20260810_05
Create Date: 2026-08-10

`ai_action_proposals.server_id` trug `ON DELETE CASCADE` auf `servers.id`. Fuehrte
die KI ein `propose_server_delete` aus, nahm PostgreSQL im selben Zug die
Vorschlagszeile mit — und `execute_proposal` stolperte vier Zeilen weiter ueber
sie: `db.get(...)` gab `None`, `AI_ACTION_NOT_FOUND` flog, der Router machte 404
"Aktionsvorschlag nicht gefunden" daraus. Der Server war weg, das Panel meldete
einen Fehlschlag, `status='succeeded'` und der Audit-Eintrag `ai.action.executed`
wurden nie geschrieben, und das Modell erfuhr beim Aufwecken ebenfalls einen
Fehlschlag, den es nie gab.

Die Nebenwirkung war groesser als der Anlass: **jeder** je fuer einen Server
erzeugte Vorschlag verschwand aus dem Chatverlauf, sobald dieser Server geloescht
wurde — auch eine Konfigaenderung von vor Wochen. Der Verlauf schrieb sich
rueckwirkend um, ohne dass jemand etwas zurueckgenommen haette.

Der Denkfehler ist die Eltern-Kind-Beziehung. Ein Aktionsvorschlag ist ein Beleg
der Unterhaltung eines Benutzers; an `conversation_id` und `user_id` haengt er zu
Recht, `server_id` ist nur ein Bezug. Deshalb `SET NULL`: der Bezug faellt, der
Beleg bleibt. Welcher Server gemeint war, steht weiterhin in `preview_json` — und
dort als Name, den ein Mensch lesen kann.

Zum Vorgehen: der Constraintname wird **ermittelt**, nicht geraten. PostgreSQL
vergibt ihn selbst (`ai_action_proposals_server_id_fkey`). Vorbild fuers
Inspizieren statt Annehmen: `20260730_01`.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260810_06"
down_revision: Union[str, None] = "20260810_05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABELLE = "ai_action_proposals"
SPALTE = "server_id"
# Nur falls die Inspektion keinen Namen findet; sonst wird der echte benutzt.
ERSATZNAME = "fk_ai_action_proposals_server_id_servers"


def _vorhandener_name() -> str | None:
    """Wie heisst der Fremdschluessel auf `servers.id` in *dieser* Datenbank?"""
    for fk in sa.inspect(op.get_bind()).get_foreign_keys(TABELLE):
        if fk.get("constrained_columns") == [SPALTE]:
            return fk.get("name")
    return None


def _umhaengen(ondelete: str) -> None:
    """Haengt den Fremdschluessel auf ein anderes `ON DELETE` um."""
    name = _vorhandener_name() or ERSATZNAME
    op.drop_constraint(name, TABELLE, type_="foreignkey")
    op.create_foreign_key(name, TABELLE, "servers", [SPALTE], ["id"], ondelete=ondelete)


def upgrade() -> None:
    _umhaengen("SET NULL")


def downgrade() -> None:
    _umhaengen("CASCADE")
