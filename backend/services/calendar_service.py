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
import threading
import time
from typing import Any
import urllib.parse
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from models.calendar_event import CalendarEvent
from models.user import User
from models.user_calendar import UserCalendar
from services.dis_client import DisClient, DisDecryptionError
from services.email_service import EmailService
from services import permission_service, team_service
from services.ai_latency_metrics import measure
from services.sync_event_service import SyncEventService

_log = logging.getLogger("msm.calendar")

# Dedup-Speicher für gesendete Terminerinnerungen (im Speicher je Server-Lauf)
_sent_reminder_keys: set[str] = set()
_CALDAV_CACHE_TTL_SECONDS = 10.0
_caldav_cache: dict[tuple[int, int, str | None, str | None], tuple[float, list[dict[str, Any]]]] = {}
_caldav_cache_lock = threading.Lock()
_caldav_client: httpx.Client | None = None
_caldav_client_lock = threading.Lock()


def _caldav_http_client() -> httpx.Client:
    """Langlebiger, TLS-prüfender Client für CalDAV-Rundreisen."""
    global _caldav_client
    with _caldav_client_lock:
        if _caldav_client is None:
            _caldav_client = httpx.Client(
                timeout=httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=8),
                verify=True,
                follow_redirects=False,
            )
        return _caldav_client


def shutdown_caldav_client() -> None:
    """Wird beim App-Shutdown aufgerufen; Tests können den Prozesszustand leeren."""
    global _caldav_client
    with _caldav_client_lock:
        if _caldav_client is not None:
            _caldav_client.close()
            _caldav_client = None
    with _caldav_cache_lock:
        _caldav_cache.clear()


def _invalidate_caldav_cache(calendar_id: int) -> None:
    with _caldav_cache_lock:
        for key in list(_caldav_cache):
            if key[1] == calendar_id:
                _caldav_cache.pop(key, None)


def _caldav_time_range(start_date: str | None, end_date: str | None, *, user: User) -> str:
    if not start_date and not end_date:
        return ""
    attrs: list[str] = []
    if start_date:
        attrs.append(f'start="{_parse_datetime(start_date, user=user).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}"')
    if end_date:
        attrs.append(f'end="{_parse_datetime(end_date, user=user).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}"')
    return "      <C:time-range " + " ".join(attrs) + " />\n"


def _default_color_for_type(event_type: str | None) -> str:
    """Liefert die semantische Standardfarbe für einen Termintyp."""
    et = (event_type or "personal").lower().strip()
    if et == "team":
        return "green"
    elif et == "server":
        return "purple"
    elif et == "node":
        return "amber"
    return "blue"


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

            with measure("calendar", "caldav_connection_test"):
                resp = _caldav_http_client().request("PROPFIND", calendar.caldav_url, auth=auth, headers=headers)
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
        event_type: str | None = None,
        team_id: int | None = None,
        server_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Liest Termine aus dem nativen oder CalDAV-Kalender.
        
        Nativer Kalender berücksichtigt persönliche Termine, Team-Termine des Nutzers,
        Server-Wartungstermine für zugängliche Server und Node-Termine.
        """
        calendar = cls.get_calendar(db, user, calendar_id)
        if not calendar:
            # Falls noch kein nativer Kalender existiert, automatisch anlegen
            calendar = cls.get_or_create_native_calendar(db, user)

        # 1. Nativer Kalender
        if calendar.provider_type == "native":
            user_teams = team_service.list_user_teams(db, user)
            user_team_ids = [t.id for t in user_teams]

            visible_servers = permission_service.list_visible_servers(db, user)
            user_server_ids = [s.id for s in visible_servers]

            has_nodes_access = user.is_owner or permission_service.has_global_permission(db, user, "nodes.read")

            # Sichtbarkeits-Filter:
            # 1. Eigene Termine
            visibility_filters = [CalendarEvent.user_id == user.id]
            # 2. Team-Termine für Teams, in denen der User Mitglied ist
            if user_team_ids:
                visibility_filters.append(
                    (CalendarEvent.event_type == "team") & (CalendarEvent.team_id.in_(user_team_ids))
                )
            # 3. Server-Termine für Server, auf die der User Zugriff hat
            if user_server_ids:
                visibility_filters.append(
                    (CalendarEvent.event_type == "server") & (CalendarEvent.server_id.in_(user_server_ids))
                )
            # 4. Node-Termine für Admins / Operator mit nodes.read
            if has_nodes_access:
                visibility_filters.append(CalendarEvent.event_type == "node")

            query = select(CalendarEvent).where(or_(*visibility_filters))

            if event_type:
                query = query.where(CalendarEvent.event_type == event_type)
            if team_id is not None:
                query = query.where(CalendarEvent.team_id == team_id)
            if server_id is not None:
                query = query.where(CalendarEvent.server_id == server_id)

            if start_date:
                start_dt = _parse_datetime(start_date, user=user)
                query = query.where(CalendarEvent.end_time >= start_dt)
            if end_date:
                end_dt = _parse_datetime(end_date, user=user)
                query = query.where(CalendarEvent.start_time <= end_dt)

            query = query.order_by(CalendarEvent.start_time.asc())
            rows = db.scalars(query).all()

            result: list[dict[str, Any]] = []
            for ev in rows:
                ev_type = ev.event_type or "personal"
                ev_color = ev.color or _default_color_for_type(ev_type)

                can_edit = (ev.user_id == user.id) or user.is_owner
                if ev.event_type == "team" and ev.team and ev.team.owner_user_id == user.id:
                    can_edit = True

                result.append({
                    "event_id": ev.event_uid,
                    "id": ev.id,
                    "title": ev.title,
                    "start": _iso_utc(ev.start_time),
                    "end": _iso_utc(ev.end_time),
                    "description": ev.description or "",
                    "location": ev.location or "",
                    "all_day": ev.all_day,
                    "color": ev_color,
                    "event_type": ev_type,
                    "team_id": ev.team_id,
                    "team_name": ev.team.name if ev.team else None,
                    "server_id": ev.server_id,
                    "server_name": ev.server.name if ev.server else None,
                    "creator_name": ev.user.username if ev.user else None,
                    "user_id": ev.user_id,
                    "can_edit": can_edit,
                    "calendar": calendar.name if calendar else "MSM Kalender",
                })
            return result

        # 2. Externer CalDAV-Kalender
        if not calendar.caldav_url:
            return []

        cache_key = (user.id, calendar.id, start_date, end_date)
        with _caldav_cache_lock:
            cached = _caldav_cache.get(cache_key)
            if cached and cached[0] > time.monotonic():
                return cached[1]

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
            '      <C:comp-filter name="VEVENT">\n'
            + _caldav_time_range(start_date, end_date, user=user)
            + '      </C:comp-filter>\n'
            "    </C:comp-filter>\n"
            "  </C:filter>\n"
            "</C:calendar-query>"
        )

        try:
            with measure("calendar", "caldav_read"):
                resp = _caldav_http_client().request(
                    "REPORT", calendar.caldav_url, auth=auth, headers=headers, content=query_body
                )
                if resp.status_code in (200, 207):
                    events = _parse_vevents(resp.text)
                    with _caldav_cache_lock:
                        _caldav_cache[cache_key] = (time.monotonic() + _CALDAV_CACHE_TTL_SECONDS, events)
                    return events
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
        event_type: str = "personal",
        team_id: int | None = None,
        server_id: int | None = None,
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
                end_dt = start_dt + timedelta(minutes=30)

            # Validierung event_type & Verknüpfungen
            norm_type = (event_type or "personal").lower().strip()
            if norm_type not in ("personal", "team", "server", "node"):
                norm_type = "personal"

            final_team_id = None
            if norm_type == "team" and team_id:
                user_teams = team_service.list_user_teams(db, user)
                if not any(t.id == team_id for t in user_teams) and not user.is_owner:
                    raise ValueError(f"Sie sind kein Mitglied von Team {team_id}.")
                final_team_id = team_id

            final_server_id = None
            if norm_type == "server" and server_id:
                visible_servers = permission_service.list_visible_servers(db, user)
                if not any(s.id == server_id for s in visible_servers) and not user.is_owner:
                    raise ValueError(f"Kein Zugriff auf Server {server_id}.")
                final_server_id = server_id

            final_color = color or _default_color_for_type(norm_type)

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
                color=final_color,
                event_type=norm_type,
                team_id=final_team_id,
                server_id=final_server_id,
            )
            db.add(ev)
            db.commit()
            db.refresh(ev)
            res = {
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
                "event_type": ev.event_type,
                "team_id": ev.team_id,
                "team_name": ev.team.name if ev.team else None,
                "server_id": ev.server_id,
                "server_name": ev.server.name if ev.server else None,
                "creator_name": user.username,
                "user_id": user.id,
                "can_edit": True,
                "calendar": calendar.name,
            }
            SyncEventService.publish(
                {
                    "entity": "calendar",
                    "action": "created",
                    "id": ev.event_uid,
                    "event_id": ev.event_uid,
                    "team_id": ev.team_id,
                    "user_id": user.id,
                    "data": res,
                },
                user_id=user.id,
                team_id=ev.team_id,
            )
            return res

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

        with measure("calendar", "caldav_create"):
            resp = _caldav_http_client().put(target_url, auth=auth, headers=headers, content=ical_payload)
            if resp.status_code not in (200, 201, 204):
                raise RuntimeError(f"CalDAV Erstellung fehlgeschlagen (HTTP {resp.status_code})")
        _invalidate_caldav_cache(calendar.id)

        res = {
            "status": "created",
            "event_id": event_uid,
            "title": title,
            "start": start_time,
            "end": end_time,
            "calendar": calendar.name,
        }
        SyncEventService.publish(
            {
                "entity": "calendar",
                "action": "created",
                "id": event_uid,
                "event_id": event_uid,
                "user_id": user.id,
                "data": res,
            },
            user_id=user.id,
        )
        return res

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
        event_type: str | None = None,
        team_id: int | None = None,
        server_id: int | None = None,
    ) -> dict[str, Any]:
        """Aktualisiert einen bestehenden Termin."""
        calendar = cls.get_calendar(db, user, calendar_id)
        if not calendar:
            calendar = cls.get_or_create_native_calendar(db, user)

        # 1. Nativer Kalender
        if calendar.provider_type == "native":
            # Match by event_uid or integer ID
            if event_id.isdigit():
                query = select(CalendarEvent).where(
                    (CalendarEvent.event_uid == event_id) | (CalendarEvent.id == int(event_id))
                )
            else:
                query = select(CalendarEvent).where(CalendarEvent.event_uid == event_id)

            ev = db.scalar(query)
            if not ev:
                raise ValueError(f"Termin '{event_id}' wurde nicht gefunden.")

            # Berechtigungsprüfung: User selbst, Owner oder Team-Owner
            can_edit = (ev.user_id == user.id) or user.is_owner
            if ev.event_type == "team" and ev.team and ev.team.owner_user_id == user.id:
                can_edit = True

            if not can_edit:
                raise ValueError("Keine Berechtigung zur Bearbeitung dieses Termins.")

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

            if event_type is not None:
                norm_type = event_type.lower().strip()
                if norm_type in ("personal", "team", "server", "node"):
                    ev.event_type = norm_type
                    if norm_type in ("personal", "node"):
                        ev.team_id = None
                        ev.server_id = None
                    elif norm_type == "team":
                        ev.server_id = None
                    elif norm_type == "server":
                        ev.team_id = None

            if team_id is not None:
                if team_id <= 0:
                    ev.team_id = None
                else:
                    if not user.is_owner:
                        user_teams = team_service.list_user_teams(db, user)
                        if not any(t.id == team_id for t in user_teams):
                            raise ValueError(f"Sie sind kein Mitglied des Teams {team_id}.")
                    ev.team_id = team_id

            if server_id is not None:
                if server_id <= 0:
                    ev.server_id = None
                else:
                    if not user.is_owner:
                        visible_servers = permission_service.list_visible_servers(db, user)
                        if not any(s.id == server_id for s in visible_servers):
                            raise ValueError(f"Sie haben keinen Zugriff auf Server {server_id}.")
                    ev.server_id = server_id

            if color is not None:
                ev.color = color if color.strip() else _default_color_for_type(ev.event_type)

            if ev.end_time <= ev.start_time:
                ev.end_time = ev.start_time + timedelta(minutes=30)

            db.commit()
            db.refresh(ev)
            res = {
                "status": "updated",
                "event_id": ev.event_uid,
                "id": ev.id,
                "title": ev.title,
                "start": _iso_utc(ev.start_time),
                "end": _iso_utc(ev.end_time),
                "description": ev.description or "",
                "location": ev.location or "",
                "all_day": ev.all_day,
                "color": ev.color or _default_color_for_type(ev.event_type),
                "event_type": ev.event_type,
                "team_id": ev.team_id,
                "team_name": ev.team.name if ev.team else None,
                "server_id": ev.server_id,
                "server_name": ev.server.name if ev.server else None,
                "creator_name": ev.user.username if ev.user else None,
                "user_id": ev.user_id,
                "can_edit": True,
                "calendar": calendar.name,
            }
            SyncEventService.publish(
                {
                    "entity": "calendar",
                    "action": "updated",
                    "id": ev.event_uid,
                    "event_id": ev.event_uid,
                    "team_id": ev.team_id,
                    "user_id": ev.user_id,
                    "data": res,
                },
                user_id=ev.user_id,
                team_id=ev.team_id,
            )
            return res

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

        with measure("calendar", "caldav_update"):
            resp = _caldav_http_client().put(target_url, auth=auth, headers=headers, content=ical_payload)
            if resp.status_code not in (200, 201, 204):
                raise RuntimeError(f"CalDAV Aktualisierung fehlgeschlagen (HTTP {resp.status_code})")
        _invalidate_caldav_cache(calendar.id)

        res = {
            "status": "updated",
            "event_id": event_id,
            "title": title or "Termin",
            "start": start_time or "",
            "end": end_time or "",
            "calendar": calendar.name,
        }
        SyncEventService.publish(
            {
                "entity": "calendar",
                "action": "updated",
                "id": event_id,
                "event_id": event_id,
                "user_id": user.id,
                "data": res,
            },
            user_id=user.id,
        )
        return res

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
            calendar = cls.get_or_create_native_calendar(db, user)

        # 1. Nativer Kalender
        if calendar.provider_type == "native":
            if event_id.isdigit():
                query = select(CalendarEvent).where(
                    (CalendarEvent.event_uid == event_id) | (CalendarEvent.id == int(event_id))
                )
            else:
                query = select(CalendarEvent).where(CalendarEvent.event_uid == event_id)

            ev = db.scalar(query)
            team_id = None
            event_user_id = user.id
            if ev:
                can_delete = (ev.user_id == user.id) or user.is_owner
                if ev.event_type == "team" and ev.team and ev.team.owner_user_id == user.id:
                    can_delete = True

                if not can_delete:
                    raise ValueError("Keine Berechtigung zum Löschen dieses Termins.")

                team_id = ev.team_id
                event_uid = ev.event_uid
                event_user_id = ev.user_id
                db.delete(ev)
                db.commit()
            else:
                event_uid = event_id

            SyncEventService.publish(
                {
                    "entity": "calendar",
                    "action": "deleted",
                    "id": event_uid,
                    "event_id": event_uid,
                    "team_id": team_id,
                    "user_id": event_user_id,
                },
                user_id=event_user_id,
                team_id=team_id,
            )

            return {
                "status": "deleted",
                "event_id": event_uid,
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

        with measure("calendar", "caldav_delete"):
            resp = _caldav_http_client().delete(target_url, auth=auth, headers=headers)
            if resp.status_code not in (200, 204, 404):
                raise RuntimeError(f"CalDAV Löschung fehlgeschlagen (HTTP {resp.status_code})")
        _invalidate_caldav_cache(calendar.id)

        SyncEventService.publish(
            {
                "entity": "calendar",
                "action": "deleted",
                "id": event_id,
                "event_id": event_id,
                "user_id": user.id,
            },
            user_id=user.id,
        )

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
