"""Service zur Verwaltung und Interaktion mit verknüpften und nativen Benutzer-Kalendern.

Unterstützt native MSM-Kalender (in der internen Datenbank) sowie CalDAV
(Nextcloud, iCloud, Mailbox.org, ownCloud) und Google/Microsoft Calendar.

Sicherheitsinvariante:
  - Schreibende Aktionen (Erstellen, Ändern, Löschen) erfordern immer ein autorisiertes
    und HMAC-bestätigtes Proposal bzw. autorisierte REST-Endpunkte.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import re
from typing import Any
import urllib.parse
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.calendar_event import CalendarEvent
from models.user import User
from models.user_calendar import UserCalendar
from services.dis_client import DisClient, DisDecryptionError
from services.email_service import EmailService

_log = logging.getLogger("msm.calendar")

# Dedup-Speicher für gesendete Terminerinnerungen (im Speicher je Server-Lauf)
_sent_reminder_keys: set[str] = set()


def _user_timezone(user: User | None = None, user_tz: str | None = None) -> timezone | ZoneInfo:
    """Bestimmt die Zeitzone des Benutzers oder UTC als Fallback."""
    tz_str = user_tz or (getattr(user, "time_zone", None) or "").strip()
    if tz_str:
        try:
            return ZoneInfo(tz_str)
        except (ZoneInfoNotFoundError, ValueError, ModuleNotFoundError):
            pass
    return timezone.utc


def _iso_utc(dt: datetime) -> str:
    """Gibt ein ISO-8601-Format mit explizitem Z-Suffix in UTC zurück."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_datetime(
    dt_input: str | datetime,
    user: User | None = None,
    user_tz: str | None = None,
) -> datetime:
    """Parst Eingabedaten in ein timezone-aware UTC datetime-Objekt.

    Wenn der Eingabestring KEINE Zeitzoneninformation enthält (z. B. '2026-08-27 12:00'
    oder '2026-08-27T12:00:00'), wird er als Uhrzeit in der Benutzerzeitzone
    (z. B. Europe/Berlin) interpretiert und anschließend sauber nach UTC konvertiert.
    """
    tz = _user_timezone(user, user_tz)

    if isinstance(dt_input, datetime):
        if dt_input.tzinfo is not None:
            return dt_input.astimezone(timezone.utc)
        return dt_input.replace(tzinfo=tz).astimezone(timezone.utc)

    dt_str = str(dt_input).strip()
    # 1. Prüfe auf explizites ISO-Format mit Z oder Offset (+02:00, -05:00)
    try:
        if "Z" in dt_str or "+" in dt_str or re.search(r"-\d\d:\d\d$", dt_str):
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc)
            return dt.replace(tzinfo=tz).astimezone(timezone.utc)
    except Exception:
        pass

    # 2. Formate ohne Zeitzone (gelten als lokale Benutzerzeit!)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(dt_str, fmt)
            return dt.replace(tzinfo=tz).astimezone(timezone.utc)
        except Exception:
            continue

    # Fallback ISO-Parsing
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc)
        return dt.replace(tzinfo=tz).astimezone(timezone.utc)
    except Exception:
        pass

    return datetime.now(timezone.utc)


def _escape_ical_text(text: str | None) -> str:
    """Escaped Sonderzeichen für iCalendar-Textfelder nach RFC 5545."""
    if not text:
        return ""
    return str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def _format_ical_date(dt_input: str | datetime, user: User | None = None) -> str:
    """Konvertiert Datums-Strings oder datetime in iCal UTC Format (YYYYMMDDTHHMMSSZ)."""
    dt = _parse_datetime(dt_input, user=user)
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
                start_dt = _parse_datetime(start_date, user=user)
                query = query.where(CalendarEvent.end_time >= start_dt)
            if end_date:
                end_dt = _parse_datetime(end_date, user=user)
                query = query.where(CalendarEvent.start_time <= end_dt)

            query = query.order_by(CalendarEvent.start_time.asc())
            rows = db.scalars(query).all()
            return [
                {
                    "event_id": ev.event_uid,
                    "id": ev.id,
                    "title": ev.title,
                    "start": _iso_utc(ev.start_time),
                    "end": _iso_utc(ev.end_time),
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
            start_dt = _parse_datetime(start_time, user=user)
            end_dt = _parse_datetime(end_time, user=user)
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
                "start": _iso_utc(ev.start_time),
                "end": _iso_utc(ev.end_time),
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
                ev.start_time = _parse_datetime(start_time, user=user)
            if end_time is not None:
                ev.end_time = _parse_datetime(end_time, user=user)
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
                "start": _iso_utc(ev.start_time),
                "end": _iso_utc(ev.end_time),
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
        dt_start = _format_ical_date(start_time, user=user) if start_time else dt_stamp
        dt_end = _format_ical_date(end_time, user=user) if end_time else dt_stamp

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
        """Exportiert alle Termine des Benutzers als RFC-5545-konformen iCalendar (.ics) String."""
        if calendar_id is not None:
            c = cls.get_calendar(db, user, calendar_id)
            calendars_to_export = [c] if c else []
            cal_name = calendars_to_export[0].name if calendars_to_export else "MSM Kalender"
        else:
            calendars_to_export = db.scalars(
                select(UserCalendar).where(UserCalendar.user_id == user.id)
            ).all()
            if not calendars_to_export:
                calendars_to_export = [cls.get_or_create_native_calendar(db, user)]
            cal_name = "MSM Kalender"

        tz_name = getattr(user, "time_zone", None) or "Europe/Berlin"

        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Maunting Studios//MSM Calendar//DE",
            f"X-WR-CALNAME:{_escape_ical_text(cal_name)}",
            f"X-WR-TIMEZONE:{tz_name}",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
        ]

        seen_uids = set()
        dt_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        for cal in calendars_to_export:
            events = cls.get_events(db, user, cal.id)
            for ev in events:
                raw_uid = str(ev.get("event_id") or uuid.uuid4())
                if raw_uid in seen_uids:
                    continue
                seen_uids.add(raw_uid)

                is_all_day = bool(ev.get("all_day"))
                start_dt = _parse_datetime(ev.get("start", ""), user=user)
                end_dt = _parse_datetime(ev.get("end", ""), user=user)

                if is_all_day:
                    dt_start_line = f"DTSTART;VALUE=DATE:{start_dt.strftime('%Y%m%d')}"
                    dt_end_line = f"DTEND;VALUE=DATE:{end_dt.strftime('%Y%m%d')}"
                else:
                    dt_start_line = f"DTSTART:{start_dt.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
                    dt_end_line = f"DTEND:{end_dt.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

                uid = f"{raw_uid}@msm.mauntingstudios.de" if "@" not in raw_uid else raw_uid
                title = _escape_ical_text(ev.get("title", "Termin"))
                desc = _escape_ical_text(ev.get("description", ""))
                loc = _escape_ical_text(ev.get("location", ""))

                event_lines = [
                    "BEGIN:VEVENT",
                    f"UID:{uid}",
                    f"DTSTAMP:{dt_stamp}",
                    dt_start_line,
                    dt_end_line,
                    f"SUMMARY:{title}",
                ]
                if desc:
                    event_lines.append(f"DESCRIPTION:{desc}")
                if loc:
                    event_lines.append(f"LOCATION:{loc}")
                event_lines.append("END:VEVENT")

                lines.extend(event_lines)

        lines.append("END:VCALENDAR")
        return "\r\n".join(lines) + "\r\n"

    @classmethod
    def generate_feed_token(cls, user: User) -> str:
        """Erzeugt ein mit DIS (AES-256-GCM) verschlüsseltes Feed-Token für externe Kalender-Abonnements."""
        pwd_salt = (user.password_hash or "")[:16]
        payload = f"{user.id}:{pwd_salt}"
        return DisClient.encrypt(payload, aad="msm:calendar:feed")

    @classmethod
    def verify_feed_token(cls, token: str, db: Session) -> User | None:
        """Validiert und entschlüsselt ein iCal-Feed-Token über DIS (AES-256-GCM)."""
        if not token:
            return None
        # URL-Decoding und Reparatur von `+` zu ` ` Ersetzungen durch Query-Parser
        token = urllib.parse.unquote(token.strip())
        if " " in token:
            token = token.replace(" ", "+")

        try:
            decrypted = DisClient.decrypt(token, aad="msm:calendar:feed")
            user_id_str, pwd_salt = decrypted.split(":", 1)
            user_id = int(user_id_str)
        except Exception:
            return None

        user = db.get(User, user_id)
        if not user or not user.is_active:
            return None

        current_salt = (user.password_hash or "")[:16]
        if pwd_salt != current_salt:
            # Passwort wurde geändert -> altes Feed-Token ungültig
            return None

        return user

    @classmethod
    async def check_and_send_due_reminders(cls, db: Session) -> int:
        """Prüft anstehende Kalendertermine auf 48h- und 24h-Erinnerungen und versendet Benachrichtigungen."""
        now = datetime.now(timezone.utc)
        users = db.scalars(select(User)).all()
        sent_count = 0

        for user in users:
            # Wenn weder E-Mail noch Geräte-Benachrichtigung aktiv ist, überspringen
            if not user.email_notifications and not user.device_notifications:
                continue

            try:
                events = cls.get_events(db, user)
            except Exception as e:
                _log.warning("Konnte Termine für Benutzer %s nicht laden: %s", user.id, e)
                continue

            for ev in events:
                start_raw = ev.get("start")
                if not start_raw:
                    continue
                try:
                    start_dt = _parse_datetime(start_raw, user=user)
                except Exception:
                    continue

                diff = start_dt - now
                diff_hours = diff.total_seconds() / 3600.0

                time_hint = None
                key_suffix = None

                # 48h Fenster (zwischen 47 und 49 Stunden vor Termin)
                if 47.0 <= diff_hours <= 49.0:
                    time_hint = "in 2 Tagen"
                    key_suffix = "48h"
                # 24h Fenster (zwischen 23 und 25 Stunden vor Termin)
                elif 23.0 <= diff_hours <= 25.0:
                    time_hint = "in 1 Tag"
                    key_suffix = "24h"

                if not time_hint or not key_suffix:
                    continue

                event_id = str(ev.get("event_id", ""))
                dedup_key = f"{user.id}_{event_id}_{key_suffix}"
                if dedup_key in _sent_reminder_keys:
                    continue

                title = ev.get("title", "Termin")
                loc = ev.get("location", "")
                start_formatted = start_dt.strftime("%d.%m.%Y um %H:%M Uhr")

                # 1. E-Mail Benachrichtigung
                if user.email_notifications and user.email:
                    try:
                        await EmailService.send_calendar_reminder_notification(
                            to=user.email,
                            username=user.username,
                            title=title,
                            start_str=start_formatted,
                            location_str=loc,
                            time_hint=time_hint,
                        )
                    except Exception as e:
                        _log.error("Fehler beim Senden der Terminerinnerungs-Mail an %s: %s", user.email, e)

                _sent_reminder_keys.add(dedup_key)
                sent_count += 1

        return sent_count

    @classmethod
    async def send_test_reminder(cls, db: Session, user: User) -> dict[str, Any]:
        """Sendet einen sofortigen Test-Erinnerungsdurchlauf für den eingeloggten Benutzer."""
        title = "Test-Termin: Server-Wartung & Backup-Check"
        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        start_formatted = tomorrow.strftime("%d.%m.%Y um 14:00 Uhr")
        loc = "MSM Leitstand"
        time_hint = "in 1 Tag"

        email_sent = False
        if user.email_notifications and user.email:
            try:
                email_sent = await EmailService.send_calendar_reminder_notification(
                    to=user.email,
                    username=user.username,
                    title=title,
                    start_str=start_formatted,
                    location_str=loc,
                    time_hint=time_hint,
                )
            except Exception as e:
                _log.error("Fehler beim Test-Senden der Terminerinnerungs-Mail an %s: %s", user.email, e)

        return {
            "status": "success",
            "email_sent": email_sent,
            "device_notifications_enabled": bool(user.device_notifications),
            "email_notifications_enabled": bool(user.email_notifications),
            "title": title,
            "start": start_formatted,
            "location": loc,
            "time_hint": time_hint,
        }

    @classmethod
    def get_due_reminders(cls, db: Session, user: User) -> list[dict[str, Any]]:
        """Liefert anstehende Termine für Push-Benachrichtigungen (48h / 24h vor Beginn)."""
        if not user.device_notifications:
            return []

        now = datetime.now(timezone.utc)
        reminders: list[dict[str, Any]] = []

        try:
            events = cls.get_events(db, user)
        except Exception:
            return []

        for ev in events:
            start_raw = ev.get("start")
            if not start_raw:
                continue
            try:
                start_dt = _parse_datetime(start_raw, user=user)
            except Exception:
                continue

            diff = start_dt - now
            diff_hours = diff.total_seconds() / 3600.0

            time_hint = None
            key_suffix = None
            if 0.0 <= diff_hours <= 49.0:
                if 25.0 < diff_hours <= 49.0:
                    time_hint = "in 2 Tagen"
                    key_suffix = "48h"
                elif 0.0 <= diff_hours <= 25.0:
                    time_hint = "in 1 Tag" if diff_hours > 2.0 else "in Kürze"
                    key_suffix = "24h"

            if not time_hint or not key_suffix:
                continue

            event_id = str(ev.get("event_id", ""))
            reminders.append(
                {
                    "event_id": event_id,
                    "title": ev.get("title", "Termin"),
                    "start": start_dt.strftime("%d.%m.%Y um %H:%M Uhr"),
                    "location": ev.get("location", ""),
                    "time_hint": time_hint,
                    "key": f"{user.id}_{event_id}_{key_suffix}",
                }
            )

        return reminders


