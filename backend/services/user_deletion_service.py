"""Was einer Benutzerloeschung im Weg steht — geprueft, bevor die Datenbank es tut.

Genau drei Fremdschluessel im ganzen Panel tragen ``ON DELETE RESTRICT``, und
alle drei enden beim selben Benutzer: ``teams.owner_user_id``,
``hoster_integrations.service_user_id`` und — ueber den Umweg des kaskadierten
``user_credentials`` — ``server_credential_bindings.credential_id``. Jedes
einzelne RESTRICT ist an seiner Stelle richtig begruendet; es fehlte nur die
Gegenseite. Kein Loeschpfad hat je nachgesehen.

Die Folge war fuer den Betreiber nicht erkennbar: ``db.delete(user)`` lief in
den Fremdschluessel, die ``IntegrityError`` fiel aus ``db.commit()`` heraus,
wurde nirgends gefangen und wurde zu einer nackten HTTP 500. Der Account blieb
bestehen, ohne dass irgendwo stand, warum. Wer einmal ein Team gegruendet hatte,
war damit dauerhaft nicht mehr loeschbar — ueber die Adminoberflaeche wie ueber
die Selbstloeschung.

Dieser Dienst prueft dieselben drei Bedingungen **vorher** und beantwortet sie
mit 409 samt Namen der blockierenden Objekte, damit der Betreiber sieht, was er
aufloesen muss. Bewusst wird nichts davon selbsttaetig mitgeloescht: ein Team
traegt das gemeinsame Wissen anderer Menschen, eine Hoster-Anbindung die
Provisionierung fremder Kunden, und eine Serverbindung entscheidet, mit welchem
Zugang ein fremder Server installiert wird. Nichts davon darf als stiller
Nebeneffekt einer Kontoloeschung verschwinden.

Die einzige Ausnahme ist das persoenliche Ein-Mann-Team: es gehoert nur diesem
Benutzer und wird hier ausdruecklich geloescht. Sein ``personal_for_user_id``
kaskadiert zwar, aber dieselbe Zeile haengt zugleich per RESTRICT an
``owner_user_id`` — welcher der beiden Fremdschluessel auf PostgreSQL zuerst
greift, ist nicht zugesichert. Ein ausdrueckliches DELETE davor macht die Frage
gegenstandslos, statt sich auf eine Reihenfolge zu verlassen, die nur auf
SQLite nachgewiesen ist.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import (
    HosterIntegration,
    ServerCredentialBinding,
    Team,
    User,
    UserCredential,
)


def prepare_user_deletion(db: Session, user: User) -> None:
    """Weist zurueck, was die Loeschung zerreissen wuerde, und raeumt den Rest ab.

    Aufzurufen unmittelbar vor ``db.delete(user)``, in derselben Transaktion.
    Ohne Fund kehrt die Funktion stumm zurueck. Geschrieben wird ausschliesslich
    das Loeschen des persoenlichen Teams, und zwar nur per ``flush`` — bricht
    die umgebende Transaktion spaeter ab, ist auch das Team wieder da.
    """
    # Echte Teams: `personal_for_user_id IS NULL` ist die Unterscheidung, die
    # das Schema ohnehin traegt (siehe models/team.py) — kein zweites Flag.
    owned_teams = (
        db.query(Team)
        .filter(Team.owner_user_id == user.id, Team.personal_for_user_id.is_(None))
        .all()
    )
    if owned_teams:
        names = ", ".join(sorted(team.name for team in owned_teams))
        raise HTTPException(
            status_code=409,
            detail=(
                "Benutzer ist Gruender folgender Teams und kann deshalb nicht "
                f"geloescht werden: {names}. Teams vorher aufloesen oder an "
                "einen anderen Gruender uebergeben."
            ),
        )

    # Dieselbe Bauart wie oben: das Dienstkonto ist der Bezugspunkt der Rechte
    # einer Anbindung. Faellt es weg, provisioniert der Shop namenlos.
    integrations = (
        db.query(HosterIntegration)
        .filter(HosterIntegration.service_user_id == user.id)
        .all()
    )
    if integrations:
        names = ", ".join(sorted(item.name for item in integrations))
        raise HTTPException(
            status_code=409,
            detail=(
                "Benutzer ist Dienstkonto folgender Hoster-Anbindungen und kann "
                f"deshalb nicht geloescht werden: {names}. Anbindung vorher "
                "umhaengen oder entfernen."
            ),
        )

    # Der Umweg ueber user_credentials: dessen CASCADE will die Zeile beim
    # Loeschen des Benutzers mitnehmen, das RESTRICT der Bindung verbietet
    # genau das. `credential_service.delete_user_credential` beantwortet den
    # gleichen Konflikt seit jeher mit 409 — hier lief die Loeschung nur daran
    # vorbei.
    bound = (
        db.query(ServerCredentialBinding)
        .join(
            UserCredential,
            ServerCredentialBinding.credential_id == UserCredential.id,
        )
        .filter(UserCredential.user_id == user.id)
        .count()
    )
    if bound:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Zugangsdaten dieses Benutzers werden noch von {bound} Server(n) "
                "verwendet. Bindungen vorher loesen, sonst wuerden die Server "
                "unbemerkt auf den Panel-Zugang zurueckfallen."
            ),
        )

    # Ueber die Instanz und nicht per `query(...).delete()`: nur so raeumt
    # SQLAlchemy die Mitgliedschaften und Server-Wuensche des Teams mit ab.
    personal = db.query(Team).filter(Team.personal_for_user_id == user.id).first()
    if personal is not None:
        db.delete(personal)
        db.flush()
