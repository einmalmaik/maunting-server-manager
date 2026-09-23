"""Was der Server ueber eine Gruppe weiss — und was er seit 09/2026 nicht mehr weiss.

`chat_groups.name`, `description` und `avatar_url` standen bis Stufe 6 im
Klartext in der Datenbank. Das war die eine Zeile, aus der sich ohne jede
Nachricht ablesen liess, worum es in einer Gruppe geht: „Anwalt Sorgerecht",
„Selbsthilfe Depression", „Betriebsrat". Wer die Datenbank in die Hand bekam —
ein Einbrecher, ein Backup-Band, eine Beschlagnahme — las sie mit.

Die Zusage lautet: **wo keine Metadaten gebraucht werden, nutzen wir keine.**
Ein Gruppenname wird vom Server nicht gebraucht. Er ordnet nichts zu, er
berechtigt nichts, er stellt nichts zu — er stand nur da, weil er von Anfang an
dort stand.

Diese Datei haelt fest, dass die Raeumung nicht nur eine Migration war, sondern
eine Schranke:

* Das Anlegen nimmt keinen Namen mehr entgegen. Ein alter Client, der einen
  mitschickt, bekommt trotzdem eine Gruppe — nur ohne Namen.
* Keine Antwort traegt ihn zurueck: nicht die Liste, nicht die Einladung, nicht
  die Mitgliedersicht.
* Die Bildroute, die ein Gruppenlogo ohne Anmeldung auslieferte, gibt es nicht
  mehr.
* Was bleibt, funktioniert: beitreten, verlassen, Rechte, Anrufe.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from dependencies import get_current_user, verify_csrf
from main import app
from models import ChatGroup, User
from services.auth_service import AuthService
from services.social_service import SocialService

# Ein Name, der in keiner Antwort und in keiner Spalte auftauchen darf.
VERRAETERISCH = "Anwalt Sorgerecht"


@pytest.fixture
def als_owner(client: TestClient, owner_user: User):
    app.dependency_overrides[get_current_user] = lambda: owner_user
    app.dependency_overrides[verify_csrf] = lambda: None
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(verify_csrf, None)


def test_das_anlegen_nimmt_keinen_namen_mehr(db: Session, owner_user: User) -> None:
    gruppe = SocialService.create_group(db, user=owner_user)

    assert gruppe.name is None
    assert gruppe.description is None
    assert gruppe.avatar_url is None
    # Was bleibt, ist das, was der Server zum Zustellen wirklich braucht.
    assert gruppe.owner_user_id == owner_user.id
    assert len(gruppe.invite_code) >= 16


def test_ein_alter_client_bekommt_eine_gruppe_ohne_namen(als_owner: TestClient, db: Session) -> None:
    """Weggelassen statt abgewiesen.

    Ein Client aus der Zeit vor der Raeumung schickt weiter `name` mit. Ihn
    dafuer mit 422 abzuweisen hiesse, dass ein nicht aktualisiertes Geraet gar
    keine Gruppe mehr anlegen kann. Pydantic laesst unbekannte Felder fallen —
    die Gruppe entsteht, der Name nicht.
    """
    antwort = als_owner.post(
        "/api/social/groups",
        json={"name": VERRAETERISCH, "description": "Termine", "avatar_url": "/x.png"},
    )

    assert antwort.status_code in (200, 201)
    daten = antwort.json()
    assert daten["name"] is None
    assert daten["description"] is None
    assert daten["avatar_url"] is None

    # Und auch nicht auf dem Umweg ueber die Datenbank.
    in_der_db = db.query(ChatGroup).filter(ChatGroup.id == daten["id"]).first()
    assert in_der_db is not None
    assert in_der_db.name is None
    assert VERRAETERISCH not in (antwort.text or "")


def test_keine_antwort_traegt_den_namen_zurueck(als_owner: TestClient, db: Session, owner_user: User) -> None:
    """Auch dann nicht, wenn er noch in der Spalte steht.

    Der harte Fall: eine Bestandsgruppe, deren Zeile die Migration noch nicht
    gesehen hat, oder die ein anderer Weg nachtraeglich gefuellt hat. Die
    Antwort darf ihn trotzdem nicht ausliefern — sonst haengt die Zusage an
    einer einmal gelaufenen Migration statt an einer Schranke im Code.
    """
    gruppe = SocialService.create_group(db, user=owner_user)
    gruppe.name = VERRAETERISCH
    gruppe.description = "Termine mit der Kanzlei"
    gruppe.avatar_url = "/api/social/groups/avatar/group_1_abc.png"
    db.commit()

    liste = als_owner.get("/api/social/groups")
    assert liste.status_code == 200
    assert VERRAETERISCH not in liste.text
    eintrag = next(g for g in liste.json() if g["id"] == gruppe.id)
    assert eintrag["name"] is None
    assert eintrag["description"] is None
    assert eintrag["avatar_url"] is None

    einladung = als_owner.get(f"/api/social/groups/invite/{gruppe.invite_code}")
    assert einladung.status_code == 200
    assert VERRAETERISCH not in einladung.text
    assert einladung.json()["name"] is None

    mitglieder = als_owner.get(f"/api/social/groups/{gruppe.id}/members")
    assert mitglieder.status_code == 200
    assert VERRAETERISCH not in mitglieder.text


def test_die_bildroute_gibt_es_nicht_mehr(client: TestClient, db: Session, owner_user: User) -> None:
    """Ein Gruppenlogo hinter einer rate-URL war ohne Anmeldung abrufbar.

    Und ein Bild sagt ueber eine Gruppe oft mehr als ihr Name. Das Logo lebt
    jetzt als Data-URL im verschluesselten Block; diese drei Routen sind weg.
    """
    gruppe = SocialService.create_group(db, user=owner_user)

    hoch = client.post(f"/api/social/groups/{gruppe.id}/avatar", files={"file": ("a.png", b"x")})
    assert hoch.status_code == 404
    weg = client.delete(f"/api/social/groups/{gruppe.id}/avatar")
    assert weg.status_code == 404
    hol = client.get("/api/social/groups/avatar/group_1_abc.png")
    assert hol.status_code == 404


def test_die_gruppe_funktioniert_ohne_namen_weiter(db: Session, owner_user: User) -> None:
    """Die Gegenprobe: ohne Namen bleibt eine Gruppe eine Gruppe.

    Beitreten, auflisten, Rechte, Austritt — nichts davon hing je am Namen, und
    genau deshalb konnte er gehen.
    """
    fremder = AuthService.create_user(db, "beitreter", "beitreter@test.de", "BeiPass123!")
    gruppe = SocialService.create_group(db, user=owner_user)

    beigetreten = SocialService.join_group_by_invite_code(
        db, user=fremder, invite_code=gruppe.invite_code
    )
    assert beigetreten.id == gruppe.id

    liste = SocialService.list_user_groups(db, fremder.id)
    assert [g["id"] for g in liste] == [gruppe.id]
    assert liste[0]["role"] == "member"
    assert liste[0]["member_count"] == 2

    # `pin_messages` traegt der Eigentuemer pauschal (GROUP_MODERATION_PERMISSIONS),
    # ein frisch Beigetretener nicht. `manage_roles` waere hier der falsche
    # Pruefstein: das haengt bewusst nur an einer Rolle, auch beim Eigentuemer.
    assert SocialService.has_group_permission(db, gruppe.id, owner_user.id, "pin_messages") is True
    assert SocialService.has_group_permission(db, gruppe.id, fremder.id, "pin_messages") is False

    SocialService.leave_group(db, fremder, gruppe.id)
    assert SocialService.list_user_groups(db, fremder.id) == []


def test_die_liste_ordnet_stabil_ohne_namen(db: Session, owner_user: User) -> None:
    """Sortiert wird nach Alter, weil es nichts anderes mehr gibt.

    Vorher stand hier `sorted(..., key=lambda x: x["name"].casefold())` — mit
    geraeumten Spalten haette das die ganze Gruppenliste zum Absturz gebracht.
    Die alphabetische Ordnung macht jetzt der Client, hinter der
    Entschluesselung; der Server liefert eine Reihenfolge, die er kennt.
    """
    erste = SocialService.create_group(db, user=owner_user)
    zweite = SocialService.create_group(db, user=owner_user)
    dritte = SocialService.create_group(db, user=owner_user)

    liste = SocialService.list_user_groups(db, owner_user.id)

    assert [g["id"] for g in liste] == [erste.id, zweite.id, dritte.id]
