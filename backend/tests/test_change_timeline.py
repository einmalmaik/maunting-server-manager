"""Tests für die Change-Timeline-Route: der Deckel schneidet nur das Alte ab."""

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models import ChangeEvent, Server, User
from routers.change_timeline import MAX_EREIGNISSE, list_change_timeline


def _ereignisse_anlegen(db: Session, server: Server, anzahl: int) -> None:
    """Legt `anzahl` Ereignisse an; Nummer 0 ist das neueste, Nummer n-1 das älteste."""
    jetzt = datetime.now(timezone.utc)
    for nummer in range(anzahl):
        db.add(ChangeEvent(
            server_id=server.id,
            timestamp=jetzt - timedelta(minutes=nummer),
            event_type="restart",
            description=f"Neustart {nummer}",
            details=json.dumps({"nummer": nummer}),
        ))
    db.commit()


def test_timeline_liefert_hoechstens_den_deckel(
    db: Session, owner_user: User, test_server: Server
) -> None:
    """Mehr Ereignisse als der Deckel zulässt — es kommen genau MAX_EREIGNISSE zurück."""
    _ereignisse_anlegen(db, test_server, MAX_EREIGNISSE + 5)

    ergebnis = list_change_timeline(test_server.id, user=owner_user, db=db)

    assert len(ergebnis) == MAX_EREIGNISSE


def test_timeline_liefert_die_neuesten_ereignisse(
    db: Session, owner_user: User, test_server: Server
) -> None:
    """Abgeschnitten wird das Alte: zurück kommen die neuesten, absteigend sortiert."""
    _ereignisse_anlegen(db, test_server, MAX_EREIGNISSE + 5)

    ergebnis = list_change_timeline(test_server.id, user=owner_user, db=db)

    nummern = [eintrag["details"]["nummer"] for eintrag in ergebnis]
    assert nummern == list(range(MAX_EREIGNISSE))
    zeitstempel = [eintrag["timestamp"] for eintrag in ergebnis]
    assert zeitstempel == sorted(zeitstempel, reverse=True)


def test_timeline_liefert_alles_unterhalb_des_deckels(
    db: Session, owner_user: User, test_server: Server
) -> None:
    """Wenige Ereignisse bleiben vollständig — der Deckel greift nur bei Überlauf."""
    _ereignisse_anlegen(db, test_server, 3)

    ergebnis = list_change_timeline(test_server.id, user=owner_user, db=db)

    assert len(ergebnis) == 3
    assert [eintrag["description"] for eintrag in ergebnis] == [
        "Neustart 0", "Neustart 1", "Neustart 2",
    ]
