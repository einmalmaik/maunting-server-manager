"""Zusagen, die im Schema stehen — nicht im Code.

Diese Datei gibt es wegen eines Betriebsfehlers, den 2519 gruene Tests nicht
sehen konnten. `ai_action_proposals.server_id` kaskadierte auf `servers.id`:
loeschte die KI einen Server, vernichtete PostgreSQL im selben Zug den Vorschlag,
der das Loeschen angeordnet hatte. Der Aufruf stolperte danach ueber die eigene,
verschwundene Zeile und meldete "Aktionsvorschlag nicht gefunden" — fuer einen
Vorgang, der tatsaechlich gelungen war.

Die Testsuite konnte das nicht bemerken, weil SQLite Fremdschluessel nur auf
Verlangen prueft und niemand danach verlangt hatte. Ein Verhalten, das die
Datenbank durchsetzt, gehoert deshalb hierher und nicht in die Tests des
jeweiligen Dienstes: dort wuerde es niemand vermissen, wenn es wieder verschwindet.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session


def test_fremdschluessel_sind_im_test_scharf(db: Session) -> None:
    """Ohne diesen Schalter ist jede Aussage ueber ON DELETE hier wertlos.

    Der Test prueft nicht ein Verhalten des Panels, sondern eine Eigenschaft des
    Pruefstands. Faellt der Listener in `conftest.py` irgendwann heraus, laufen
    alle nachfolgenden Kaskadentests weiterhin gruen — sie pruefen dann nur
    nichts mehr. Das ist die gefaehrlichere Sorte Fehlschlag, deshalb steht sie
    hier ausdruecklich.
    """
    assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1
