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
from services.kalender_serie import (
    LEERES_DOKUMENT,
    Serie,
    SerienRegelFehler,
    ausbreiten,
    regel_lesen,
    regel_schreiben,
    serie_lesen,
    zeitzone_von,
)
from services import permission_service, team_service
from services.ai_latency_metrics import measure
from services.sync_event_service import SyncEventService

_log = logging.getLogger("msm.calendar")

# Dedup-Speicher für gesendete Terminerinnerungen (im Speicher je Server-Lauf)
_sent_reminder_keys: set[str] = set()
# Seit die Schluessel das Vorkommen tragen, waechst die Menge mit jeder Serie
# weiter statt sich auf die Zahl der Termine einzupendeln. Bei 20.000 wird
# geleert: ein doppelt verschickter Hinweis ist ein kleiner Schaden, ein
# unbegrenzt wachsender Prozessspeicher ein grosser.
_MAX_DEDUP_SCHLUESSEL = 20_000
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


CALENDAR_CIPHERTEXT_PREFIX = "sv-cal-v1:"


def _cal_aad(user_id: int, event_uid: str) -> str:
    return f"msm:cal:{user_id}:{event_uid}"


def _feld_entschluesseln(db: Session, ev: CalendarEvent, spalte: str, aad: str) -> str:
    """Ein einzelnes Feld lesen, Altbestand dabei nachziehen.

    Drei Faelle, in dieser Reihenfolge:
      1. E2EE-Ciphertext (`sv-cal-v1:`) — bleibt unangetastet, der Server hat
         den Schluessel nicht und reicht ihn blind an den Client durch.
      2. DIS-Ciphertext — wird entschluesselt.
      3. Klartext aus der Zeit vor der Verschluesselung — wird gelesen **und**
         gleich verschluesselt zurueckgeschrieben.
    """
    roh = getattr(ev, spalte) or ""
    if roh.startswith(CALENDAR_CIPHERTEXT_PREFIX):
        return roh
    if not roh:
        return ""
    try:
        return DisClient.decrypt(roh, aad=aad)
    except DisDecryptionError:
        setattr(ev, spalte, DisClient.encrypt(roh, aad=aad))
        try:
            db.commit()
            db.refresh(ev)
        except Exception as e:
            _log.warning(
                "Fehler bei Altdaten-Verschluesselung von %s am Termin %s: %s",
                spalte,
                ev.event_uid,
                e,
            )
            db.rollback()
        return roh


def _wiederholung_entschluesseln(db: Session, ev: CalendarEvent) -> str:
    """Liest das Wiederholungsdokument und legt es bei Altbestand erst an.

    Anders als Titel oder Ort darf dieses Feld **nie** leer bleiben: eine leere
    Spalte neben lauter gefuellten wuerde verraten, dass diese Zeile keine
    Serie ist — und damit umgekehrt, dass die gefuellten welche sind. Wo noch
    nichts steht, wird deshalb der verschluesselte Satz "keine Wiederholung"
    nachgetragen (Betreiberentscheid 22.09.2026).
    """
    aad = _cal_aad(ev.user_id, ev.event_uid)
    roh = ev.recurrence or ""

    if roh.startswith(CALENDAR_CIPHERTEXT_PREFIX):
        return roh

    if not roh:
        try:
            ev.recurrence = DisClient.encrypt(LEERES_DOKUMENT, aad=aad)
            db.commit()
            db.refresh(ev)
        except Exception as e:
            # Fehlschlag ist nicht schlimm: der naechste Lesevorgang versucht
            # es erneut, und die Antwort stimmt in beiden Faellen.
            _log.warning(
                "Konnte Wiederholungsfeld des Termins %s nicht nachtragen: %s", ev.event_uid, e
            )
            db.rollback()
        return LEERES_DOKUMENT

    try:
        return DisClient.decrypt(roh, aad=aad)
    except DisDecryptionError:
        # Klartext aus einer aelteren Fassung — lesen und verschluesselt
        # zurueckschreiben, wie bei den uebrigen Feldern.
        try:
            ev.recurrence = DisClient.encrypt(roh, aad=aad)
            db.commit()
            db.refresh(ev)
        except Exception as e:
            _log.warning(
                "Fehler bei Altdaten-Verschluesselung der Wiederholung des Termins %s: %s",
                ev.event_uid,
                e,
            )
            db.rollback()
        return roh


def _dedup_beschneiden() -> None:
    """Haelt den Dedup-Speicher endlich."""
    if len(_sent_reminder_keys) > _MAX_DEDUP_SCHLUESSEL:
        _sent_reminder_keys.clear()


def _anzeigetitel(roh: str | None) -> str:
    """Titel fuer etwas, das der Server verschickt (Mail, Geraetemeldung).

    Bei einem E2EE-Termin steht in `title` der Umschlag `sv-cal-v1:…`, und
    ohne diese Stelle stuende genau das in der Erinnerungsmail. Der Server hat
    den Schluessel nicht und wird ihn nie haben; er kann den Termin nur
    ankuendigen, nicht benennen.
    """
    text = (roh or "").strip()
    if not text or text.startswith(CALENDAR_CIPHERTEXT_PREFIX):
        return "Ein Termin"
    return text


def _anzeigeort(roh: str | None) -> str:
    """Wie `_anzeigetitel`, aber ein unlesbarer Ort entfaellt ersatzlos."""
    text = (roh or "").strip()
    if not text or text.startswith(CALENDAR_CIPHERTEXT_PREFIX):
        return ""
    return text


def _wiederholung_verschluesseln(roh: str | None, aad: str) -> str:
    """Schreibfassung des Wiederholungsfelds.

    `None` und leer heissen "keine Wiederholung" — und werden trotzdem
    gespeichert, nicht weggelassen.
    """
    text = (roh or "").strip()
    if text.startswith(CALENDAR_CIPHERTEXT_PREFIX):
        return text
    if not text:
        text = LEERES_DOKUMENT
    return DisClient.encrypt(text, aad=aad)


def _decrypt_or_migrate_calendar_event(db: Session, ev: CalendarEvent) -> tuple[str, str, str]:
    """Entschluesselt title, description und location eines nativen Termins bei Altdaten; E2EE-Ciphertexte bleiben unangetastet."""
    aad = _cal_aad(ev.user_id, ev.event_uid)
    return (
        _feld_entschluesseln(db, ev, "title", aad),
        _feld_entschluesseln(db, ev, "description", aad),
        _feld_entschluesseln(db, ev, "location", aad),
    )


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


def _ical_zeit_lesen(roh: str, tz: timezone | ZoneInfo) -> datetime | None:
    """Liest DTSTART/DTEND/EXDATE im iCal-Kompaktformat.

    `20260314T090000Z` ist UTC, `20260314T090000` gilt als Ortszeit des
    Benutzers, `20260314` als ganzer Tag ab Mitternacht Ortszeit.
    """
    text = (roh or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        if "T" in text:
            return datetime.strptime(text, "%Y%m%dT%H%M%S").replace(tzinfo=tz).astimezone(timezone.utc)
        return datetime.strptime(text, "%Y%m%d").replace(tzinfo=tz).astimezone(timezone.utc)
    except ValueError:
        return None


def _ical_zeit_schreiben(dt: datetime, ganztaegig: bool, tz: timezone | ZoneInfo) -> str:
    """Zurueck in die Form, in der CalDAV-Termine bisher schon geliefert wurden.

    Bei einem ganzen Tag zaehlt das **lokale** Datum. Ueber UTC formatiert
    verliert jede Zone oestlich von Greenwich einen Tag: Berliner Mitternacht
    ist 23:00 UTC des Vortags, und aus `DTSTART;VALUE=DATE:20260314` wuerde
    `20260313` — der Geburtstag saesse einen Tag zu frueh im Kalender.
    """
    if ganztaegig:
        return dt.astimezone(tz).strftime("%Y%m%d")
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_vevents(
    ical_text: str,
    *,
    von: datetime | None = None,
    bis: datetime | None = None,
    tz_name: str | None = None,
) -> list[dict[str, Any]]:
    """Extrahiert VEVENT-Blöcke aus einer iCalendar-Antwort und breitet Serien aus.

    Bis zum 22.09.2026 stand `RRULE` nicht in der Liste der gelesenen Felder.
    Wer seinen Google- oder Nextcloud-Kalender angebunden hatte, sah eine
    jaehrliche Geburtstagsserie deshalb **einmal**, am Ursprungsdatum, und nie
    wieder — kein fehlendes Merkmal, sondern eine stille Falschauskunft.

    Regeln ausserhalb der unterstuetzten Teilmenge (`BYSETPOS` & Co., die
    andere Kalender durchaus schreiben) fallen auf das bisherige Verhalten
    zurueck: ein Vorkommen am Ursprungsdatum. Das ist unvollstaendig, aber es
    erfindet nichts.
    """
    events: list[dict[str, Any]] = []
    tz = zeitzone_von(tz_name)
    vevent_matches = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", ical_text, re.DOTALL)

    for block in vevent_matches:
        uid_m = re.search(r"UID:(.+)", block)
        summary_m = re.search(r"SUMMARY:(.+)", block)
        dtstart_m = re.search(r"DTSTART(;[^:]+)?:(.+)", block)
        dtend_m = re.search(r"DTEND(;[^:]+)?:(.+)", block)
        desc_m = re.search(r"DESCRIPTION:(.+)", block)
        loc_m = re.search(r"LOCATION:(.+)", block)
        rrule_m = re.search(r"^RRULE:(.+)$", block, re.MULTILINE)

        start_roh = dtstart_m.group(2).strip() if dtstart_m else ""
        ende_roh = dtend_m.group(2).strip() if dtend_m else ""
        ganztaegig = "VALUE=DATE" in (dtstart_m.group(1) or "") if dtstart_m else False

        grund = {
            "event_id": uid_m.group(1).strip() if uid_m else str(uuid.uuid4()),
            "title": summary_m.group(1).strip() if summary_m else "Ohne Titel",
            "start": start_roh,
            "end": ende_roh,
            "description": desc_m.group(1).strip() if desc_m else "",
            "location": loc_m.group(1).strip() if loc_m else "",
        }

        start_dt = _ical_zeit_lesen(start_roh, tz)
        if not rrule_m or start_dt is None:
            events.append(grund)
            continue

        ende_dt = _ical_zeit_lesen(ende_roh, tz) or start_dt

        try:
            regel = regel_lesen(rrule_m.group(1).strip())
        except SerienRegelFehler:
            _log.debug("RRULE ausserhalb der Teilmenge, Termin bleibt einmalig: %s", rrule_m.group(1))
            events.append(grund)
            continue

        ausnahmen: set[str] = set()
        for zeile in re.findall(r"^EXDATE(?:;[^:]+)?:(.+)$", block, re.MULTILINE):
            for stueck in zeile.split(","):
                ausgenommen = _ical_zeit_lesen(stueck, tz)
                if ausgenommen is not None:
                    ausnahmen.add(ausgenommen.astimezone(tz).date().isoformat())

        serie = Serie(rrule=regel_schreiben(regel), ausnahmen=frozenset(ausnahmen))
        for v in ausbreiten(
            serie,
            start_dt,
            ende_dt,
            ganztaegig=ganztaegig,
            zeitzone=tz_name,
            fenster_von=von,
            fenster_bis=bis,
        ):
            eintrag = dict(grund)
            eintrag["start"] = _ical_zeit_schreiben(v.start, ganztaegig, tz)
            eintrag["end"] = _ical_zeit_schreiben(v.ende, ganztaegig, tz)
            eintrag["vorkommen"] = v.schluessel
            eintrag["ist_serie"] = True
            events.append(eintrag)

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

        **Serien werden hier nicht ausgebreitet.** Geliefert wird der Termin
        selbst, mitsamt seinem Feld `recurrence`; die einzelnen Vorkommen
        rechnet der Client aus. Das ist kein Versehen, sondern die einzige
        Stelle, an der beides zusammenpasst: der Server kann die Regel eines
        E2EE-Termins nicht lesen, und breitete er die uebrigen aus, stuende
        jedes Vorkommen doppelt in der Ansicht — einmal vom Server, einmal vom
        Client.

        Wer ausgebreitete Vorkommen braucht und serverseitig laeuft
        (Erinnerungen, `calendar_read`), nimmt `vorkommen_im_fenster`.

        Die Zeitraumgrenzen filtern deshalb den **Termin**, nicht seine
        Vorkommen: ein Geburtstag von 1995 faellt aus einer Abfrage fuer 2026
        heraus. Der Client holt seinen Grundbestand einmal ohne Grenzen und
        haelt ihn vor.
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

                title, desc, loc = _decrypt_or_migrate_calendar_event(db, ev)
                recurrence = _wiederholung_entschluesseln(db, ev)

                result.append({
                    "event_id": ev.event_uid,
                    "id": ev.id,
                    "title": title,
                    "start": _iso_utc(ev.start_time),
                    "end": _iso_utc(ev.end_time),
                    "description": desc,
                    "location": loc,
                    "recurrence": recurrence,
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
                    # Fenster und Zeitzone muessen mit: eine Serie breitet der
                    # Anbieter nicht aus, das tun wir, und ohne Grenzen waere
                    # "jaehrlich, ohne Ende" nicht zu beenden.
                    events = _parse_vevents(
                        resp.text,
                        von=_parse_datetime(start_date, user=user) if start_date else None,
                        bis=_parse_datetime(end_date, user=user) if end_date else None,
                        tz_name=getattr(user, "time_zone", None),
                    )
                    with _caldav_cache_lock:
                        _caldav_cache[cache_key] = (time.monotonic() + _CALDAV_CACHE_TTL_SECONDS, events)
                    return events
                _log.warning("CalDAV REPORT HTTP %d für %s", resp.status_code, calendar.name)
        except Exception as e:
            _log.warning("Fehler beim Abruf von Terminen für %s: %s", calendar.name, e)

        return []

    @classmethod
    def vorkommen_im_fenster(
        cls,
        db: Session,
        user: User,
        *,
        von: datetime | None = None,
        bis: datetime | None = None,
        calendar_id: int | None = None,
        event_type: str | None = None,
        team_id: int | None = None,
        server_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Termine im Fenster, Serien ausgebreitet — soweit der Server sie lesen kann.

        Fuer alles, was serverseitig laeuft und echte Vorkommen braucht:
        Erinnerungen und `calendar_read`. Die Ansicht nimmt `get_events` und
        breitet selbst aus.

        Zwei Grenzen, die hier zusammenkommen:

        * **E2EE-Serien bleiben ein Termin.** Der Server hat den Schluessel
          nicht, `serie_lesen` findet kein JSON und liefert "keine
          Wiederholung". Das ist die ehrliche Antwort — der Server weiss es
          wirklich nicht — und nicht schlechter als heute, wo er bei solchen
          Terminen schon den Titel nicht lesen kann.
        * **Der Termin selbst wird ohne Zeitraumgrenze geladen.** Ein
          Geburtstag von 1995 traegt bis heute, also darf ihn kein Filter
          vorher wegnehmen. Das ist der Preis der Metadaten-Entscheidung; der
          Erinnerungslauf zahlte ihn schon vorher, weil er `get_events` ohnehin
          ohne Zeitraum aufruft.
        """
        kalender = cls.get_calendar(db, user, calendar_id)

        # CalDAV filtert der Anbieter selbst, und `_parse_vevents` breitet die
        # dort gelieferten RRULEs bereits aus.
        if kalender is not None and kalender.provider_type != "native":
            return cls.get_events(
                db,
                user,
                calendar_id=calendar_id,
                start_date=_iso_utc(von) if von else None,
                end_date=_iso_utc(bis) if bis else None,
                event_type=event_type,
                team_id=team_id,
                server_id=server_id,
            )

        termine = cls.get_events(
            db,
            user,
            calendar_id=calendar_id,
            event_type=event_type,
            team_id=team_id,
            server_id=server_id,
        )

        tz_name = getattr(user, "time_zone", None)
        ergebnis: list[dict[str, Any]] = []

        for termin in termine:
            serie = serie_lesen(termin.get("recurrence"))
            try:
                start_dt = _parse_datetime(termin.get("start", ""), user=user)
                ende_dt = _parse_datetime(termin.get("end", ""), user=user)
            except Exception:
                continue

            for v in ausbreiten(
                serie,
                start_dt,
                ende_dt,
                ganztaegig=bool(termin.get("all_day")),
                zeitzone=tz_name,
                fenster_von=von,
                fenster_bis=bis,
            ):
                eintrag = dict(termin)
                eintrag["start"] = _iso_utc(v.start)
                eintrag["end"] = _iso_utc(v.ende)
                eintrag["vorkommen"] = v.schluessel
                eintrag["ist_serie"] = serie.ist_serie
                if v.titel:
                    eintrag["title"] = v.titel
                ergebnis.append(eintrag)

        ergebnis.sort(key=lambda e: e.get("start") or "")
        return ergebnis

    @classmethod
    def create_event(
        cls,
        db: Session,
        user: User,
        title: str,
        start_time: str,
        end_time: str,
        event_uid: str | None = None,
        description: str | None = None,
        location: str | None = None,
        calendar_id: int | None = None,
        all_day: bool = False,
        color: str | None = None,
        event_type: str = "personal",
        team_id: int | None = None,
        server_id: int | None = None,
        recurrence: str | None = None,
    ) -> dict[str, Any]:
        """Erstellt einen neuen Termin im nativen oder CalDAV-Kalender.

        `recurrence` ist das Wiederholungsdokument — entweder Klartext-JSON
        (dann verschluesselt es der Server) oder ein fertiger E2EE-Umschlag
        (`sv-cal-v1:`, dann reicht der Server ihn blind durch). Fehlt es, wird
        trotzdem etwas gespeichert: der verschluesselte Satz "keine
        Wiederholung". Ein leeres Feld neben lauter gefuellten waere selbst
        eine Auskunft.
        """
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

            if event_uid and event_uid.strip():
                final_event_uid = event_uid.strip()
                existing = db.scalar(select(CalendarEvent).where(CalendarEvent.event_uid == final_event_uid))
                if existing:
                    if existing.user_id == user.id:
                        dec_title, dec_desc, dec_loc = _decrypt_or_migrate_calendar_event(db, existing)
                        return {
                            "status": "created",
                            "event_id": existing.event_uid,
                            "id": existing.id,
                            "title": dec_title,
                            "start": _iso_utc(existing.start_time),
                            "end": _iso_utc(existing.end_time),
                            "description": dec_desc,
                            "location": dec_loc,
                            "recurrence": _wiederholung_entschluesseln(db, existing),
                            "all_day": existing.all_day,
                            "color": existing.color or "",
                            "event_type": existing.event_type,
                            "team_id": existing.team_id,
                            "team_name": existing.team.name if existing.team else None,
                            "server_id": existing.server_id,
                            "server_name": existing.server.name if existing.server else None,
                            "creator_name": user.username,
                            "user_id": user.id,
                            "can_edit": True,
                            "calendar": calendar.name,
                        }
                    raise ValueError(f"Termin mit UID '{final_event_uid}' existiert bereits.")
            else:
                final_event_uid = str(uuid.uuid4())

            aad = _cal_aad(user.id, final_event_uid)

            clean_title = title.strip()
            if clean_title.startswith(CALENDAR_CIPHERTEXT_PREFIX):
                encrypted_title = clean_title
            else:
                encrypted_title = DisClient.encrypt(clean_title, aad=aad)

            if description and description.startswith(CALENDAR_CIPHERTEXT_PREFIX):
                encrypted_desc = description
            elif description:
                encrypted_desc = DisClient.encrypt(description, aad=aad)
            else:
                encrypted_desc = None

            if location and location.startswith(CALENDAR_CIPHERTEXT_PREFIX):
                encrypted_loc = location
            elif location:
                encrypted_loc = DisClient.encrypt(location, aad=aad)
            else:
                encrypted_loc = None

            encrypted_recurrence = _wiederholung_verschluesseln(recurrence, aad)

            ev = CalendarEvent(
                calendar_id=calendar.id,
                user_id=user.id,
                event_uid=final_event_uid,
                title=encrypted_title,
                description=encrypted_desc,
                location=encrypted_loc,
                recurrence=encrypted_recurrence,
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
                "title": clean_title,
                "start": _iso_utc(ev.start_time),
                "end": _iso_utc(ev.end_time),
                "description": description or "",
                "location": location or "",
                "recurrence": (recurrence or "").strip() or LEERES_DOKUMENT,
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
        recurrence: str | None = None,
    ) -> dict[str, Any]:
        """Aktualisiert einen bestehenden Termin.

        `recurrence=None` heisst "die Wiederholung bleibt, wie sie ist" — wie
        bei allen anderen Feldern hier. Wer eine Serie **aufloesen** will,
        schickt das Dokument "keine Wiederholung", nicht `None`: ein Termin,
        der seine Serie verliert, weil jemand nur den Titel geaendert hat,
        waere genau die Art stiller Datenverlust, die niemand bemerkt.
        """
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

            aad = _cal_aad(ev.user_id, ev.event_uid)

            if title is not None:
                clean_title = title.strip()
                if clean_title.startswith(CALENDAR_CIPHERTEXT_PREFIX):
                    ev.title = clean_title
                else:
                    ev.title = DisClient.encrypt(clean_title, aad=aad)
            if start_time is not None:
                ev.start_time = _parse_datetime(start_time, user=user)
            if end_time is not None:
                ev.end_time = _parse_datetime(end_time, user=user)
            if description is not None:
                if description.startswith(CALENDAR_CIPHERTEXT_PREFIX):
                    ev.description = description
                elif description:
                    ev.description = DisClient.encrypt(description, aad=aad)
                else:
                    ev.description = None
            if location is not None:
                if location.startswith(CALENDAR_CIPHERTEXT_PREFIX):
                    ev.location = location
                elif location:
                    ev.location = DisClient.encrypt(location, aad=aad)
                else:
                    ev.location = None
            if all_day is not None:
                ev.all_day = all_day
            if recurrence is not None:
                ev.recurrence = _wiederholung_verschluesseln(recurrence, aad)

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

            dec_title, dec_desc, dec_loc = _decrypt_or_migrate_calendar_event(db, ev)

            res = {
                "status": "updated",
                "event_id": ev.event_uid,
                "id": ev.id,
                "title": dec_title,
                "start": _iso_utc(ev.start_time),
                "end": _iso_utc(ev.end_time),
                "description": dec_desc,
                "location": dec_loc,
                "recurrence": _wiederholung_entschluesseln(db, ev),
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

                # Die Serie geht als Regel hinaus, nicht als Liste von
                # Vorkommen — das abonnierende Programm rechnet selbst, und ein
                # Geburtstag ohne Ende braucht so keine kuenstliche Grenze.
                #
                # Nur fuer Serien, deren Regel der Server lesen kann. Bei
                # E2EE-Terminen fehlt sie, so wie dort schon heute der Titel
                # fehlt: der Feed ist fuer sie ohnehin unbrauchbar, weil in
                # SUMMARY der Umschlag steht.
                serie = serie_lesen(ev.get("recurrence"))
                if serie.ist_serie:
                    event_lines.append(f"RRULE:{serie.rrule}")
                    if serie.ausnahmen:
                        for tag in sorted(serie.ausnahmen):
                            kompakt = tag.replace("-", "")
                            if is_all_day:
                                event_lines.append(f"EXDATE;VALUE=DATE:{kompakt}")
                            else:
                                zeit = start_dt.astimezone(timezone.utc).strftime("%H%M%S")
                                event_lines.append(f"EXDATE:{kompakt}T{zeit}Z")

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
                # Nur das Fenster, in dem ueberhaupt erinnert wird (48h/24h
                # voraus, mit etwas Luft). Ohne Grenzen wuerde eine Serie ohne
                # Ende bis an die Schrittgrenze ausgebreitet — je Benutzer, je
                # Takt.
                events = cls.vorkommen_im_fenster(
                    db, user, von=now, bis=now + timedelta(hours=50)
                )
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
                # Das Vorkommen gehoert in den Schluessel. Ohne es traegt jede
                # Serie denselben Schluessel wie beim ersten Mal — der
                # Geburtstag 2027 gilt als schon erinnert, weil 2026 erinnert
                # wurde, und meldet sich nie wieder.
                vorkommen = str(ev.get("vorkommen") or "")
                dedup_key = f"{user.id}_{event_id}_{vorkommen}_{key_suffix}"
                if dedup_key in _sent_reminder_keys:
                    continue

                title = _anzeigetitel(ev.get("title", ""))
                loc = _anzeigeort(ev.get("location", ""))
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

        _dedup_beschneiden()
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
        """Liefert anstehende Termine für Push-Benachrichtigungen (48h / 24h vor Beginn).

        Titel und Ort gehen **unveraendert** hinaus, auch als E2EE-Umschlag:
        Empfaenger ist der Client, und der hat den Schluessel. Anders als bei
        der Erinnerungsmail (`_anzeigetitel`) ist hier nichts zu ersetzen.

        Serien, deren Regel der Server nicht lesen kann, fehlen in dieser
        Liste. Der Client ergaenzt sie aus seinem eigenen Bestand — er ist die
        einzige Stelle, die sie ausbreiten kann.
        """
        if not user.device_notifications:
            return []

        now = datetime.now(timezone.utc)
        reminders: list[dict[str, Any]] = []

        try:
            events = cls.vorkommen_im_fenster(db, user, von=now, bis=now + timedelta(hours=50))
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
            vorkommen = str(ev.get("vorkommen") or "")
            reminders.append(
                {
                    "event_id": event_id,
                    "title": ev.get("title", "Termin"),
                    "start": start_dt.strftime("%d.%m.%Y um %H:%M Uhr"),
                    "location": ev.get("location", ""),
                    "time_hint": time_hint,
                    "vorkommen": vorkommen,
                    # Derselbe Schluesselbau wie in `check_and_send_due_reminders`:
                    # ohne das Vorkommen gilt die Serie nach dem ersten Mal als
                    # erledigt, und der Client blendet sie fuer immer aus.
                    "key": f"{user.id}_{event_id}_{vorkommen}_{key_suffix}",
                }
            )

        return reminders
