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

Seit Stufe 6c sind die Spalten nicht mehr leer, sondern **weg**. Der
Unterschied zaehlt nicht fuer den Bestand, sondern fuer den naechsten Zweig:
in eine Spalte, die es gibt, schreibt sich still zurueck, was dort nie wieder
stehen soll.

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
import sqlalchemy as sa
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

    for feld in ("name", "description", "avatar_url"):
        assert not hasattr(gruppe, feld), f"{feld} ist wieder am Modell"
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
    assert not hasattr(in_der_db, "name")
    assert VERRAETERISCH not in (antwort.text or "")


def test_die_spalten_gibt_es_nicht_mehr(db: Session) -> None:
    """Nicht geleert, sondern weg.

    Bis Stufe 6c stand hier der harte Fall: eine Bestandsgruppe, deren Zeile
    noch einen Namen trug, darf ihn trotzdem nicht ausliefern. Den Fall gibt es
    nicht mehr, weil es die Spalte nicht mehr gibt — und das ist die staerkere
    Zusage. Eine geleerte Spalte haelt nur so lange, wie niemand hineinschreibt;
    eine fehlende bricht den Bau.
    """
    spalten = {s["name"] for s in sa.inspect(db.get_bind()).get_columns("chat_groups")}

    assert "name" not in spalten
    assert "description" not in spalten
    assert "avatar_url" not in spalten
    # Und die Einladungskarte, die die Vorschau verschluesselt traegt, steht.
    assert "invite_card" in spalten


def test_keine_antwort_traegt_den_namen_zurueck(als_owner: TestClient) -> None:
    """Die Antwortform sagt weiter `name`, und zwar leer.

    Angelegt wird hier ueber die Route und **mit** dem Namen im Rumpf — so wie
    ein nicht aktualisiertes Geraet es taete. Waere die Gruppe ueber
    `create_group` entstanden, das den Namen gar nicht kennt, prueften die
    folgenden Zeilen nichts: es gaebe nichts, was durchschlagen koennte.

    Ein alter Client liest `name` aus der Antwort. Das Feld wegzulassen liesse
    ihn auf `undefined` laufen; `null` sagt ihm sauber „dazu weiss dieser
    Server nichts" — und genau das soll er vom Server auch nie erfahren.
    """
    angelegt = als_owner.post(
        "/api/social/groups",
        json={"name": VERRAETERISCH, "description": "Termine", "avatar_url": "/x.png"},
    )
    assert angelegt.status_code in (200, 201)
    gruppe_id = angelegt.json()["id"]
    invite_code = angelegt.json()["invite_code"]

    liste = als_owner.get("/api/social/groups")
    assert liste.status_code == 200
    assert VERRAETERISCH not in liste.text
    eintrag = next(g for g in liste.json() if g["id"] == gruppe_id)
    assert eintrag["name"] is None
    assert eintrag["description"] is None
    assert eintrag["avatar_url"] is None

    einladung = als_owner.get(f"/api/social/groups/invite/{invite_code}")
    assert einladung.status_code == 200
    assert VERRAETERISCH not in einladung.text
    assert einladung.json()["name"] is None

    mitglieder = als_owner.get(f"/api/social/groups/{gruppe_id}/members")
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
