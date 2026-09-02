"""Die Aufgabenliste über HTTP — derselbe Fachweg wie die Chat-Werkzeuge.

Der Router (`routers/ai_tasks.py`) existiert seit dem 20.08.2026; vorher wurden
stehende Aufträge ausschließlich im Chat verwaltet. Geprüft wird hier der Rand:
Rechte-Gate, CSRF, Fehlerübersetzung und dass die Routen wirklich durch
`ai_task_service` gehen (Zeitzonenpflicht, Teilangaben beim Ändern). Die
Fachregeln selbst haben ihre eigenen Dateien (test_ai_task_service u. a.).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiTask, Role, RolePermission, User


AUFGABE = {
    "title": "Serverbericht",
    "instruction": "Sieh nach den Servern und fasse zusammen.",
    "kind": "report",
    "plan_kind": "daily",
    "time_of_day": "08:00",
    "timezone": "Europe/Berlin",
}


def _rolle_mit_aufgabenrecht(db: Session, user: User) -> None:
    from services.role_service import set_user_roles

    rolle = Role(name=f"aufgaben-{user.username}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    for key in ("ai.chat.use", "ai.tasks.manage"):
        db.add(RolePermission(role_id=rolle.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [rolle.id])
    db.refresh(user)


def _kopf(csrf: str | None) -> dict:
    return {"X-CSRF-Token": csrf} if csrf else {}


def test_ohne_das_recht_gibt_es_keine_liste(
    client: TestClient, user_cookies: dict
) -> None:
    """`ai.tasks.manage` ist das Gate — dieselbe Grenze wie an den Werkzeugen."""
    response = client.get("/api/ai/tasks", cookies=user_cookies)
    assert response.status_code == 403


def test_anlegen_aendern_loeschen_ueber_die_liste(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
    user_csrf_token: str | None,
) -> None:
    _rolle_mit_aufgabenrecht(db, regular_user)

    erstellt = client.post(
        "/api/ai/tasks", json=AUFGABE,
        cookies=user_cookies, headers=_kopf(user_csrf_token),
    )
    assert erstellt.status_code == 201, erstellt.text
    aufgabe = erstellt.json()
    assert aufgabe["title"] == "Serverbericht"
    assert aufgabe["enabled"] is True
    assert aufgabe["next_run"] is not None
    assert "08:00" in aufgabe["plan"]

    liste = client.get("/api/ai/tasks", cookies=user_cookies)
    assert liste.status_code == 200
    assert [zeile["task_id"] for zeile in liste.json()] == [aufgabe["task_id"]]

    # Teilangabe: nur pausieren — der Plan bleibt unangetastet.
    pausiert = client.patch(
        f"/api/ai/tasks/{aufgabe['task_id']}", json={"enabled": False},
        cookies=user_cookies, headers=_kopf(user_csrf_token),
    )
    assert pausiert.status_code == 200, pausiert.text
    assert pausiert.json()["enabled"] is False
    assert pausiert.json()["next_run"] is None
    assert "08:00" in pausiert.json()["plan"]

    geloescht = client.delete(
        f"/api/ai/tasks/{aufgabe['task_id']}",
        cookies=user_cookies, headers=_kopf(user_csrf_token),
    )
    assert geloescht.status_code == 200
    assert geloescht.json() == {"deleted": True, "title": "Serverbericht"}
    assert db.query(AiTask).count() == 0


def test_die_dienstpruefung_wird_als_400_uebersetzt(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
    user_csrf_token: str | None,
) -> None:
    """Ungültige Zeitzone wird als 400 Bad Request gemeldet."""
    _rolle_mit_aufgabenrecht(db, regular_user)
    ungueltig = {**AUFGABE, "timezone": "Ungueltige/Zeitzone"}

    antwort = client.post(
        "/api/ai/tasks", json=ungueltig,
        cookies=user_cookies, headers=_kopf(user_csrf_token),
    )

    assert antwort.status_code == 400
    assert "Zeitzone" in antwort.json()["detail"]
    assert db.query(AiTask).count() == 0


def test_eine_fremde_aufgabe_sieht_aus_wie_keine(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
    user_csrf_token: str | None,
) -> None:
    """Kein Existenzorakel: fremde Kennungen werden wie erfundene behandelt."""
    from services import ai_task_service
    from services.auth_service import AuthService

    _rolle_mit_aufgabenrecht(db, regular_user)
    anderer = AuthService.create_user(db, "fremd", "fremd@test.de", "FremdPass123!")
    anderer.email_verified = True
    db.commit()
    _rolle_mit_aufgabenrecht(db, anderer)
    fremde = ai_task_service.anlegen(db, user=anderer, felder=dict(AUFGABE))
    db.commit()

    antwort = client.delete(
        f"/api/ai/tasks/{fremde.id}",
        cookies=user_cookies, headers=_kopf(user_csrf_token),
    )

    assert antwort.status_code == 400
    assert db.query(AiTask).count() == 1


def test_schreiben_ohne_csrf_wird_abgewiesen(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict
) -> None:
    _rolle_mit_aufgabenrecht(db, regular_user)

    antwort = client.post("/api/ai/tasks", json=AUFGABE, cookies=user_cookies)

    assert antwort.status_code in (403, 419)
    assert db.query(AiTask).count() == 0
