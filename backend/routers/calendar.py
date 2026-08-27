"""Router für Kalender-Verwaltung und Termine (Nativ und CalDAV).

Ermöglicht das Anzeigen, Erstellen, Bearbeiten, Löschen und Exportieren von Terminen.
"""

from __future__ import annotations

from typing import Any
import urllib.parse
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from dependencies import _bearer_token, _user_from_token
from models.user import User
from routers.auth import get_current_user
from services.calendar_service import CalendarService
from services.panel_settings_service import PanelSettingsService

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


class CalendarEventCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    start_time: str
    end_time: str
    description: str | None = None
    location: str | None = None
    calendar_id: int | None = None
    all_day: bool = False
    color: str | None = None


class CalendarEventUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    start_time: str | None = None
    end_time: str | None = None
    description: str | None = None
    location: str | None = None
    calendar_id: int | None = None
    all_day: bool | None = None
    color: str | None = None


def _check_calendar_enabled() -> None:
    """Prüft, ob das Kalendermodul in den Panel-Einstellungen aktiv ist."""
    if PanelSettingsService.get("calendar_enabled", "true") == "false":
        raise HTTPException(
            status_code=403,
            detail="Das Kalendermodul ist in diesem Panel deaktiviert.",
        )


@router.get("/status")
def get_calendar_status(
    _: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Liefert den Aktivierungsstatus des Kalenders."""
    enabled = PanelSettingsService.get("calendar_enabled", "true") != "false"
    return {"enabled": enabled}


@router.get("/events")
def list_events(
    start: str | None = Query(None, description="Startzeitpunkt (ISO)"),
    end: str | None = Query(None, description="Endzeitpunkt (ISO)"),
    calendar_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Gibt Termine des Benutzers im angegebenen Zeitraum zurück."""
    _check_calendar_enabled()
    return CalendarService.get_events(
        db=db,
        user=user,
        calendar_id=calendar_id,
        start_date=start,
        end_date=end,
    )


@router.post("/events", status_code=201)
def create_event(
    payload: CalendarEventCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Erstellt einen neuen Termin im Kalender des Benutzers."""
    _check_calendar_enabled()
    try:
        return CalendarService.create_event(
            db=db,
            user=user,
            title=payload.title,
            start_time=payload.start_time,
            end_time=payload.end_time,
            description=payload.description,
            location=payload.location,
            calendar_id=payload.calendar_id,
            all_day=payload.all_day,
            color=payload.color,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/events/{event_id}")
def update_event(
    event_id: str,
    payload: CalendarEventUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Aktualisiert einen bestehenden Termin."""
    _check_calendar_enabled()
    try:
        return CalendarService.update_event(
            db=db,
            user=user,
            event_id=event_id,
            title=payload.title,
            start_time=payload.start_time,
            end_time=payload.end_time,
            description=payload.description,
            location=payload.location,
            calendar_id=payload.calendar_id,
            all_day=payload.all_day,
            color=payload.color,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/events/{event_id}")
def delete_event(
    event_id: str,
    calendar_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Löscht einen Termin aus dem Kalender."""
    _check_calendar_enabled()
    try:
        return CalendarService.delete_event(
            db=db,
            user=user,
            event_id=event_id,
            calendar_id=calendar_id,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


def get_calendar_feed_user(
    request: Request,
    token: str | None = Query(None, description="Signiertes Feed-Token für externe Kalender-Apps"),
    db: Session = Depends(get_db),
) -> User:
    """Authentifiziert den iCal-Feed-Aufruf entweder per signiertem URL-Token oder per Session/Bearer."""
    if token:
        user = CalendarService.verify_feed_token(token, db)
        if user:
            return user
        raise HTTPException(
            status_code=401,
            detail="Ungültiges Kalender-Token. Bitte verwende den vollständigen Feed-Link aus deinem MSM-Panel.",
        )

    from main import app
    if get_current_user in app.dependency_overrides:
        override = app.dependency_overrides[get_current_user]
        return override()

    token_str = _bearer_token(request) or request.cookies.get("__Secure-access_token")
    if not token_str:
        raise HTTPException(
            status_code=401,
            detail="Ungültiges oder fehlendes Kalender-Token. Bitte verwende den vollständigen Feed-Link aus deinem MSM-Panel.",
        )
    return _user_from_token(token_str, db)


@router.get("/feed-url")
def get_calendar_feed_url(
    user: User = Depends(get_current_user),
) -> dict[str, str]:
    """Liefert die signierte Feed-URL für externe Kalender-Abonnements (z. B. Google Kalender, Apple, Outlook)."""
    _check_calendar_enabled()
    token = CalendarService.generate_feed_token(user)
    encoded_token = urllib.parse.quote(token, safe="")
    return {"feed_url": f"/api/calendar/feed.ics?token={encoded_token}", "token": token}


@router.get("/feed.ics")
def export_ical_feed(
    calendar_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_calendar_feed_user),
) -> Response:
    """Exportiert alle Termine als iCal (.ics) zur Einbindung in externe Kalender-Apps."""
    _check_calendar_enabled()
    ical_content = CalendarService.export_ical(db, user, calendar_id)
    return Response(
        content=ical_content,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": 'inline; filename="msm-calendar.ics"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@router.post("/test-reminder")
async def test_calendar_reminder(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Sendet eine Test-Terminerinnerung per E-Mail und gibt die Push-Payload zurück."""
    _check_calendar_enabled()
    return await CalendarService.send_test_reminder(db=db, user=user)


@router.get("/due-reminders")
def get_due_calendar_reminders(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Gibt anstehende Termine zurück, die für Push-Benachrichtigungen relevant sind."""
    _check_calendar_enabled()
    return CalendarService.get_due_reminders(db=db, user=user)


