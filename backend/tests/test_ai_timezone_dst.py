"""Gezielte Tests für Zeitzonen-Harmonisierung und DST-Grenzfälle (Sommer-/Winterzeit)."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from models import AiTask, Role, RolePermission, User
from schemas.user import TimezoneUpdateRequest
from services import ai_lage, ai_task_service
from services.auth_service import AuthService
from services.role_service import set_user_roles


def _benutzer(db: Session, name: str, *rechte: str) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "UserPass123!")
    user.email_verified = True
    db.commit()
    all_rights = list(rechte) if rechte else ["ai.tasks.manage", "ai.chat.use"]
    rolle = Role(name=f"dst-{name}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    for key in all_rights:
        db.add(RolePermission(role_id=rolle.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [rolle.id])
    db.refresh(user)
    return user


def test_schema_validiert_iana_zeitzonen() -> None:
    """Valide IANA-Namen werden akzeptiert, ungültige abgewiesen."""
    req = TimezoneUpdateRequest(time_zone="Europe/Berlin")
    assert req.time_zone == "Europe/Berlin"

    req_none = TimezoneUpdateRequest(time_zone=None)
    assert req_none.time_zone is None

    req_empty = TimezoneUpdateRequest(time_zone="")
    assert req_empty.time_zone is None

    with pytest.raises(ValidationError):
        TimezoneUpdateRequest(time_zone="Invalid/Zone_Name_123")

    with pytest.raises(ValidationError):
        TimezoneUpdateRequest(time_zone="abends meist müde")


def test_task_erstellung_erbt_benutzer_zeitzone(db: Session) -> None:
    """Eine neue Aufgabe ohne explizite Zone erbt user.time_zone."""
    user = _benutzer(db, "tasktzuser")
    user.time_zone = "America/New_York"
    db.commit()

    aufgabe = ai_task_service.anlegen(
        db,
        user=user,
        felder={
            "title": "New York Task",
            "instruction": "Prüfe New York Server",
            "kind": "report",
            "plan_kind": "daily",
            "time_of_day": "09:00",
        },
    )

    assert aufgabe.time_zone == "America/New_York"


def test_dst_spring_forward_terminberechnung(db: Session) -> None:
    """Zeitumstellung Frühling (Sprung 02:00 -> 03:00 MEZ/MESZ in Europe/Berlin).

    Am 29.03.2026 springt die Zeit in Berlin um 02:00 Uhr auf 03:00 Uhr.
    Eine Aufgabe für 02:30 Uhr darf nicht crashen und muss den nächsten gültigen Termin finden.
    """
    user = _benutzer(db, "springuser")
    user.time_zone = "Europe/Berlin"
    db.commit()

    aufgabe = AiTask(
        id="test-spring-dst",
        user_id=user.id,
        title="02:30 Uhr Task",
        instruction="Nachtjob",
        kind="report",
        plan_kind="daily",
        time_zone="Europe/Berlin",
        time_of_day="02:30",
        channel="chat",
        enabled=True,
    )

    # Berechne nächsten Termin ab 28.03.2026 23:00 UTC (00:00 Berlin Zeit am Tag der Umstellung)
    start_utc = datetime(2026, 3, 28, 23, 0, tzinfo=timezone.utc)
    naechster = ai_task_service.naechste_faelligkeit(aufgabe, ab=start_utc)

    assert naechster is not None
    # Der Termin ist in UTC formatiert und muss in der Zukunft liegen
    assert naechster > start_utc


def test_dst_fall_back_terminberechnung(db: Session) -> None:
    """Zeitumstellung Herbst (Sprung 03:00 -> 02:00 MESZ/MEZ in Europe/Berlin).

    Am 25.10.2026 wiederholt sich die Stunde von 02:00 bis 03:00 Uhr in Berlin.
    Eine Aufgabe für 02:30 Uhr muss sauber berechnet werden.
    """
    user = _benutzer(db, "falluser")
    user.time_zone = "Europe/Berlin"
    db.commit()

    aufgabe = AiTask(
        id="test-fall-dst",
        user_id=user.id,
        title="02:30 Uhr Task",
        instruction="Nachtjob",
        kind="report",
        plan_kind="daily",
        time_zone="Europe/Berlin",
        time_of_day="02:30",
        channel="chat",
        enabled=True,
    )

    # Berechne nächsten Termin ab 24.10.2026 22:00 UTC (00:00 Berlin Zeit am Tag der Umstellung)
    start_utc = datetime(2026, 10, 24, 22, 0, tzinfo=timezone.utc)
    naechster = ai_task_service.naechste_faelligkeit(aufgabe, ab=start_utc)

    assert naechster is not None
    assert naechster > start_utc
