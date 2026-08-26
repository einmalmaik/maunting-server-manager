"""Service zur Verwaltung und Interaktion mit verknüpften und nativen Benutzer-Kalendern.

Unterstützt native MSM-Kalender (in der internen Datenbank) sowie CalDAV
(Nextcloud, iCloud, Mailbox.org, ownCloud) und Google/Microsoft Calendar.

Sicherheitsinvariante:
  - Schreibende Aktionen (Erstellen, Ändern, Löschen) erfordern immer ein autorisiertes
    und HMAC-bestätigtes Proposal bzw. autorisierte REST-Endpunkte.
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

from models.calendar_event import CalendarEvent
from models.user import User
from models.user_calendar import UserCalendar

_log = logging.getLogger("msm.calendar")


def _parse_datetime(dt_input: str | datetime) -> datetime:
    """Parst Eingabedaten in ein timezone-aware UTC datetime-Objekt."""
    if isinstance(dt_input, datetime):
        return dt_input if dt_input.tzinfo else dt_input.replace(tzinfo=timezone.utc)

    dt_str = str(dt_input).strip()
    try:
        # ISO-Format (z. B. 2026-08-26T15:00:00Z oder 2026-08-26T15:00:00+02:00)
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(dt_str, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue

    return datetime.now(timezone.utc)


def _format_ical_date(dt_input: str | datetime) -> str:
    """Konvertiert Datums-Strings oder datetime in iCal UTC Format (YYYYMMDDTHHMMSSZ)."""
    dt = _parse_datetime(dt_input)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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
    def get_or_create_native_calendar(db: Session, user: User) -> UserCalendar:
        """Stellt sicher, dass ein nativer Standard-Kalender für den Benutzer existiert."""
        native_cal = db.scalar(
            select(UserCalendar).where(
                UserCalendar.user_id == user.id,
                UserCalendar.provider_type == "native",
            )
        )
        if native_cal:
            return native_cal

        has_other_default = db.scalar(
            select(UserCalendar).where(
                UserCalendar.user_id == user.id,
                UserCalendar.is_default == True,  # noqa: E712
            )
        )

        new_cal = UserCalendar(
            user_id=user.id,
            name="Persönlicher Kalender",
            provider_type="native",
            is_default=not bool(has_other_default),
        )
        db.add(new_cal)
        db.commit()
        db.refresh(new_cal)
        return new_cal

    @classmethod
    def get_calendar(cls, db: Session, user: User, calendar_id: int | None = None) -> UserCalendar | None:
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

        first_cal = db.scalar(
            select(UserCalendar).where(UserCalendar.user_id == user.id).order_by(UserCalendar.id.asc())
        )
        if first_cal:
            return first_cal

        # Wenn noch kein Kalender existiert, automatisch nativen Standard-Kalender anlegen
        return cls.get_or_create_native_calendar(db, user)

    @staticmethod
    def test_connection(calendar: UserCalendar) -> tuple[bool, str]:
        """Prüft die Erreichbarkeit und Authentifizierung des Kalenders."""
        if calendar.provider_type == "native":
            return True, "Nativer MSM-Kalender aktiv"

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
        """Liest Termine aus dem nativen oder CalDAV-Kalender."""
        calendar = cls.get_calendar(db, user, calendar_id)
        if not calendar:
            return []

        # 1. Nativer Kalender
        if calendar.provider_type == "native":
            query = select(CalendarEvent).where(
                CalendarEvent.calendar_id == calendar.id,
                CalendarEvent.user_id == user.id,
            )
            if start_date:
                start_dt = _parse_datetime(start_date)
                query = query.where(CalendarEvent.end_time >= start_dt)
            if end_date:
                end_dt = _parse_datetime(end_date)
                query = query.where(CalendarEvent.start_time <= end_dt)

            query = query.order_by(CalendarEvent.start_time.asc())
            rows = db.scalars(query).all()
            return [
                {
                    "event_id": ev.event_uid,
                    "id": ev.id,
                    "title": ev.title,
                    "start": ev.start_time.isoformat(),
                    "end": ev.end_time.isoformat(),
                    "description": ev.description or "",
                    "location": ev.location or "",
                    "all_day": ev.all_day,
                    "color": ev.color or "",
                    "calendar": calendar.name,
                }
                for ev in rows
            ]

        # 2. Externer CalDAV-Kalender
        if not calendar.caldav_url:
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
        all_day: bool = False,
        color: str | None = None,
    ) -> dict[str, Any]:
        """Erstellt einen neuen Termin im nativen oder CalDAV-Kalender."""
        calendar = cls.get_calendar(db, user, calendar_id)
        if not calendar:
            calendar = cls.get_or_create_native_calendar(db, user)

        # 1. Nativer Kalender
        if calendar.provider_type == "native":
            start_dt = _parse_datetime(start_time)
            end_dt = _parse_datetime(end_time)
            if end_dt <= start_dt:
                # Falls Endzeit vor Startzeit liegt, auf mindestens 30 Min danach setzen
                from datetime import timedelta
                end_dt = start_dt + timedelta(minutes=30)

            event_uid = str(uuid.uuid4())
            ev = CalendarEvent(
                calendar_id=calendar.id,
                user_id=user.id,
                event_uid=event_uid,
                title=title,
                description=description,
                location=location,
                start_time=start_dt,
                end_time=end_dt,
                all_day=all_day,
                color=color,
            )
            db.add(ev)
            db.commit()
            db.refresh(ev)
            return {
                "status": "created",
                "event_id": ev.event_uid,
                "id": ev.id,
                "title": ev.title,
                "start": ev.start_time.isoformat(),
                "end": ev.end_time.isoformat(),
                "description": ev.description or "",
                "location": ev.location or "",
                "all_day": ev.all_day,
                "color": ev.color or "",
                "calendar": calendar.name,
            }

        # 2. Externer CalDAV-Kalender
        if not calendar.caldav_url:
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
    def update_event(
        cls,
        db: Session,
        user: User,
        event_id: str,
        title: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        description: str | None = None,
        location: str | None = None,
        calendar_id: int | None = None,
        all_day: bool | None = None,
        color: str | None = None,
    ) -> dict[str, Any]:
        """Aktualisiert einen bestehenden Termin."""
        calendar = cls.get_calendar(db, user, calendar_id)
        if not calendar:
            raise ValueError(f"Kein Kalender für Benutzer {user.id} verfügbar")

        # 1. Nativer Kalender
        if calendar.provider_type == "native":
            query = select(CalendarEvent).where(
                CalendarEvent.user_id == user.id,
            )
            # Match by event_uid or integer ID
            if event_id.isdigit():
                query = query.where(
                    (CalendarEvent.event_uid == event_id) | (CalendarEvent.id == int(event_id))
                )
            else:
                query = query.where(CalendarEvent.event_uid == event_id)

            ev = db.scalar(query)
            if not ev:
                raise ValueError(f"Termin '{event_id}' wurde nicht gefunden.")

            if title is not None:
                ev.title = title
            if start_time is not None:
                ev.start_time = _parse_datetime(start_time)
            if end_time is not None:
                ev.end_time = _parse_datetime(end_time)
            if description is not None:
                ev.description = description
            if location is not None:
                ev.location = location
            if all_day is not None:
                ev.all_day = all_day
            if color is not None:
                ev.color = color

            if ev.end_time <= ev.start_time:
                from datetime import timedelta
                ev.end_time = ev.start_time + timedelta(minutes=30)

            db.commit()
            db.refresh(ev)
            return {
                "status": "updated",
                "event_id": ev.event_uid,
                "id": ev.id,
                "title": ev.title,
                "start": ev.start_time.isoformat(),
                "end": ev.end_time.isoformat(),
                "description": ev.description or "",
                "location": ev.location or "",
                "all_day": ev.all_day,
                "color": ev.color or "",
                "calendar": calendar.name,
            }

        # 2. Externer CalDAV-Kalender
        if not calendar.caldav_url:
            raise ValueError(f"Kein Kalender für Benutzer {user.id} konfiguriert")

        secret = calendar.get_credentials()
        if not secret:
            raise ValueError(f"Keine Zugangsdaten für Kalender {calendar.name}")

        dt_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dt_start = _format_ical_date(start_time) if start_time else dt_stamp
        dt_end = _format_ical_date(end_time) if end_time else dt_stamp

        ical_payload = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Maunting Studios//MSM AI//DE\r\n"
            "BEGIN:VEVENT\r\n"
            f"UID:{event_id}\r\n"
            f"DTSTAMP:{dt_stamp}\r\n"
            f"DTSTART:{dt_start}\r\n"
            f"DTEND:{dt_end}\r\n"
            f"SUMMARY:{title or 'Termin'}\r\n"
            f"DESCRIPTION:{description or ''}\r\n"
            f"LOCATION:{location or ''}\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )

        target_url = calendar.caldav_url.rstrip("/") + f"/{event_id}.ics"
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        auth = None
        if calendar.provider_type in ("oauth_google", "oauth_microsoft"):
            headers["Authorization"] = f"Bearer {secret}"
        else:
            auth = (calendar.caldav_username or "", secret)

        with httpx.Client(timeout=15, verify=True) as client:
            resp = client.put(target_url, auth=auth, headers=headers, content=ical_payload)
            if resp.status_code not in (200, 201, 204):
                raise RuntimeError(f"CalDAV Aktualisierung fehlgeschlagen (HTTP {resp.status_code})")

        return {
            "status": "updated",
            "event_id": event_id,
            "title": title or "Termin",
            "start": start_time or "",
            "end": end_time or "",
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
        """Löscht einen Termin aus dem nativen oder CalDAV-Kalender."""
        calendar = cls.get_calendar(db, user, calendar_id)
        if not calendar:
            raise ValueError(f"Kein Kalender für Benutzer {user.id} konfiguriert")

        # 1. Nativer Kalender
        if calendar.provider_type == "native":
            query = select(CalendarEvent).where(
                CalendarEvent.user_id == user.id,
            )
            if event_id.isdigit():
                query = query.where(
                    (CalendarEvent.event_uid == event_id) | (CalendarEvent.id == int(event_id))
                )
            else:
                query = query.where(CalendarEvent.event_uid == event_id)

            ev = db.scalar(query)
            if ev:
                db.delete(ev)
                db.commit()

            return {
                "status": "deleted",
                "event_id": event_id,
                "calendar": calendar.name,
            }

        # 2. Externer CalDAV-Kalender
        if not calendar.caldav_url:
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

    @classmethod
    def export_ical(cls, db: Session, user: User, calendar_id: int | None = None) -> str:
        """Exportiert alle Termine des Benutzers als iCalendar (.ics) String."""
        calendar = cls.get_calendar(db, user, calendar_id)
        if not calendar:
            return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Maunting Studios//MSM Calendar//DE\r\nEND:VCALENDAR\r\n"

        events = cls.get_events(db, user, calendar.id)
        dt_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Maunting Studios//MSM Calendar//DE",
            f"X-WR-CALNAME:{calendar.name}",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
        ]

        for ev in events:
            dt_start = _format_ical_date(ev.get("start", ""))
            dt_end = _format_ical_date(ev.get("end", ""))
            uid = ev.get("event_id", str(uuid.uuid4()))
            title = ev.get("title", "Termin")
            desc = ev.get("description", "")
            loc = ev.get("location", "")

            lines.extend([
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{dt_stamp}",
                f"DTSTART:{dt_start}",
                f"DTEND:{dt_end}",
                f"SUMMARY:{title}",
                f"DESCRIPTION:{desc}",
                f"LOCATION:{loc}",
                "END:VEVENT",
            ])

        lines.append("END:VCALENDAR\r\n")
        return "\r\n".join(lines)
