"""Service zur Verwaltung und Interaktion mit verknüpften Benutzer-Kalendern.

Unterstützt CalDAV (Nextcloud, iCloud, Mailbox.org, ownCloud) sowie Google/Microsoft Calendar.
Sicherheitsinvariante:
  - Schreibende Aktionen (Erstellen, Löschen) erfordern immer ein autorisiertes und HMAC-bestätigtes Proposal.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
from typing import Any
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.user import User
from models.user_calendar import UserCalendar

_log = logging.getLogger("msm.calendar")


def _format_ical_date(dt_str: str) -> str:
    """Konvertiert Datums-Strings (ISO oder YYYY-MM-DD HH:MM) in iCal UTC Format."""
    try:
        # Falls ISO Format
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except Exception:
            dt = datetime.now(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _parse_vevents(ical_text: str) -> list[dict[str, Any]]:
    """Extrahiert VEVENT-Blöcke aus einer iCalendar-Antwort."""
    events: list[dict[str, Any]] = []
    vevent_matches = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", ical_text, re.DOTALL)

    for block in vevent_matches:
        uid_m = re.search(r"UID:(.+)", block)
        summary_m = re.search(r"SUMMARY:(.+)", block)
        dtstart_m = re.search(r"DTSTART(?:;[^:]+)?:(.+)", block)
        dtend_m = re.search(r"DTEND(?:;[^:]+)?:(.+)", block)
        desc_m = re.search(r"DESCRIPTION:(.+)", block)
        loc_m = re.search(r"LOCATION:(.+)", block)

        events.append(
            {
                "event_id": uid_m.group(1).strip() if uid_m else str(uuid.uuid4()),
                "title": summary_m.group(1).strip() if summary_m else "Ohne Titel",
                "start": dtstart_m.group(1).strip() if dtstart_m else "",
                "end": dtend_m.group(1).strip() if dtend_m else "",
                "description": desc_m.group(1).strip() if desc_m else "",
                "location": loc_m.group(1).strip() if loc_m else "",
            }
        )

    return events


class CalendarService:
    @staticmethod
    def get_calendar(db: Session, user: User, calendar_id: int | None = None) -> UserCalendar | None:
        if calendar_id is not None:
            return db.scalar(
                select(UserCalendar).where(
                    UserCalendar.id == calendar_id,
                    UserCalendar.user_id == user.id,
                )
            )
        default_cal = db.scalar(
            select(UserCalendar).where(
                UserCalendar.user_id == user.id,
                UserCalendar.is_default == True,  # noqa: E712
            )
        )
        if default_cal:
            return default_cal
        return db.scalar(
            select(UserCalendar).where(UserCalendar.user_id == user.id).order_by(UserCalendar.id.asc())
        )

    @staticmethod
    def test_connection(calendar: UserCalendar) -> tuple[bool, str]:
        """Prüft die Erreichbarkeit und Authentifizierung des CalDAV-Servers."""
        if not calendar.caldav_url:
            return False, "Keine Kalender-URL hinterlegt"

        secret = calendar.get_credentials()
        if not secret:
            return False, "Keine Zugangsdaten hinterlegt"

        try:
            auth = None
            headers = {"Depth": "0"}
            if calendar.provider_type in ("oauth_google", "oauth_microsoft"):
                headers["Authorization"] = f"Bearer {secret}"
            else:
                auth = (calendar.caldav_username or "", secret)

            with httpx.Client(timeout=10, verify=True) as client:
                resp = client.request("PROPFIND", calendar.caldav_url, auth=auth, headers=headers)
                if resp.status_code in (200, 207):
                    return True, "Verbindung zum Kalender erfolgreich"
                return False, f"CalDAV meldet HTTP {resp.status_code}"
        except Exception as e:
            return False, f"Verbindungsfehler: {e}"

    @classmethod
    def get_events(
        cls,
        db: Session,
        user: User,
        calendar_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Liest Termine aus dem CalDAV-Kalender."""
        calendar = cls.get_calendar(db, user, calendar_id)
        if not calendar or not calendar.caldav_url:
            return []

        secret = calendar.get_credentials()
        if not secret:
            return []

        headers = {
            "Depth": "1",
            "Content-Type": "application/xml; charset=utf-8",
        }
        auth = None
        if calendar.provider_type in ("oauth_google", "oauth_microsoft"):
            headers["Authorization"] = f"Bearer {secret}"
        else:
            auth = (calendar.caldav_username or "", secret)

        query_body = (
            '<?xml version="1.0" encoding="utf-8" ?>\n'
            '<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">\n'
            "  <D:prop>\n"
            "    <D:getetag/>\n"
            "    <C:calendar-data/>\n"
            "  </D:prop>\n"
            "  <C:filter>\n"
            '    <C:comp-filter name="VCALENDAR">\n'
            '      <C:comp-filter name="VEVENT" />\n'
            "    </C:comp-filter>\n"
            "  </C:filter>\n"
            "</C:calendar-query>"
        )

        try:
            with httpx.Client(timeout=15, verify=True) as client:
                resp = client.request(
                    "REPORT", calendar.caldav_url, auth=auth, headers=headers, content=query_body
                )
                if resp.status_code in (200, 207):
                    return _parse_vevents(resp.text)
                _log.warning("CalDAV REPORT HTTP %d für %s", resp.status_code, calendar.name)
        except Exception as e:
            _log.warning("Fehler beim Abruf von Terminen für %s: %s", calendar.name, e)

        return []

    @classmethod
    def create_event(
        cls,
        db: Session,
        user: User,
        title: str,
        start_time: str,
        end_time: str,
        description: str | None = None,
        location: str | None = None,
        calendar_id: int | None = None,
    ) -> dict[str, Any]:
        """Erstellt einen neuen Termin im CalDAV-Kalender (nur nach Proposal-Freigabe)."""
        calendar = cls.get_calendar(db, user, calendar_id)
        if not calendar or not calendar.caldav_url:
            raise ValueError(f"Kein Kalender für Benutzer {user.id} konfiguriert")

        secret = calendar.get_credentials()
        if not secret:
            raise ValueError(f"Keine Zugangsdaten für Kalender {calendar.name}")

        event_uid = str(uuid.uuid4())
        dt_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dt_start = _format_ical_date(start_time)
        dt_end = _format_ical_date(end_time)

        ical_payload = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Maunting Studios//MSM AI//DE\r\n"
            "BEGIN:VEVENT\r\n"
            f"UID:{event_uid}\r\n"
            f"DTSTAMP:{dt_stamp}\r\n"
            f"DTSTART:{dt_start}\r\n"
            f"DTEND:{dt_end}\r\n"
            f"SUMMARY:{title}\r\n"
            f"DESCRIPTION:{description or ''}\r\n"
            f"LOCATION:{location or ''}\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )

        target_url = calendar.caldav_url.rstrip("/") + f"/{event_uid}.ics"
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        auth = None
        if calendar.provider_type in ("oauth_google", "oauth_microsoft"):
            headers["Authorization"] = f"Bearer {secret}"
        else:
            auth = (calendar.caldav_username or "", secret)

        with httpx.Client(timeout=15, verify=True) as client:
            resp = client.put(target_url, auth=auth, headers=headers, content=ical_payload)
            if resp.status_code not in (200, 201, 204):
                raise RuntimeError(f"CalDAV Erstellung fehlgeschlagen (HTTP {resp.status_code})")

        return {
            "status": "created",
            "event_id": event_uid,
            "title": title,
            "start": start_time,
            "end": end_time,
            "calendar": calendar.name,
        }

    @classmethod
    def delete_event(
        cls,
        db: Session,
        user: User,
        event_id: str,
        calendar_id: int | None = None,
    ) -> dict[str, Any]:
        """Löscht einen Termin aus dem CalDAV-Kalender (nur nach Proposal-Freigabe)."""
        calendar = cls.get_calendar(db, user, calendar_id)
        if not calendar or not calendar.caldav_url:
            raise ValueError(f"Kein Kalender für Benutzer {user.id} konfiguriert")

        secret = calendar.get_credentials()
        if not secret:
            raise ValueError(f"Keine Zugangsdaten für Kalender {calendar.name}")

        target_url = calendar.caldav_url.rstrip("/") + f"/{event_id}.ics"
        headers = {}
        auth = None
        if calendar.provider_type in ("oauth_google", "oauth_microsoft"):
            headers["Authorization"] = f"Bearer {secret}"
        else:
            auth = (calendar.caldav_username or "", secret)

        with httpx.Client(timeout=15, verify=True) as client:
            resp = client.delete(target_url, auth=auth, headers=headers)
            if resp.status_code not in (200, 204, 404):
                raise RuntimeError(f"CalDAV Löschung fehlgeschlagen (HTTP {resp.status_code})")

        return {
            "status": "deleted",
            "event_id": event_id,
            "calendar": calendar.name,
        }
