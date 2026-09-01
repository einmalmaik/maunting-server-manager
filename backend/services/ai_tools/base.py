from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import PurePosixPath
import json
import logging
import re
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import AuditLog, Node, Server, User, AiActionProposal
from models.ai_task import ARTEN as _AUFGABENARTEN
from models.ai_task import KANAELE as _KANAELE
from models.ai_task import PLANARTEN as _PLANARTEN
from services import audit_service, permission_service
from services.ai_task_service import (
    MAX_INTERVALL_STUNDEN as _MAX_INTERVALL_STUNDEN,
    MIN_INTERVALL_STUNDEN as _MIN_INTERVALL_STUNDEN,
)
from services.ai_tool_registry import bekannt as _werkzeug_bekannt
from services.ai_action_errors import (
    AiActionStateError,
    AiActionValidationError,
)
from services.ai_redaction import redact_sensitive_text
from services.ai_limit_service import MAX_SYSTEM_SCOPE_ENTRIES as _MAX_SCOPE_ENTRIES

logger = logging.getLogger(__name__)

CONFIRMATION_TTL = timedelta(minutes=5)
MAX_CONFIG_CHARS = 64_000
MAX_DIFF_CHARS = 16_000
MAX_DIFF_LINES = 200
MAX_READ_CONFIG_CHARS = 24_000
MAX_LOG_CHARS = 24_000
MAX_READ_CONFIG_LINES = 400
MAX_SEARCH_QUERY_CHARS = 128
MAX_SEARCH_FILES = 40
MAX_SEARCH_DEPTH = 4
MAX_SEARCH_MATCHES = 40
MAX_SEARCH_LINE_CHARS = 200
MAX_SEARCH_CONTEXT_LINES = 5
MAX_PATCH_EDITS = 20
MAX_PATCH_CHUNK_CHARS = 8_000
MAX_LISTED_MODS = 60
MAX_LISTED_BACKUPS = 20
MAX_LISTED_INCIDENTS = 15
MAX_LISTED_ACTIONS = 20
MAX_LISTED_BLUEPRINTS = 80
MAX_LISTED_NODES = 30
MAX_REASON_CHARS = 500
MAX_BACKUP_NAME_CHARS = 64
MAX_QUESTION_OPTIONS = 4
MAX_QUESTION_CHARS = 300
MAX_OPTION_CHARS = 60
MAX_OPTION_HINT_CHARS = 120
MAX_DESKTOP_INHALT_CHARS = 60_000
MAX_AUFRAEUM_PFADE = 500
MAX_LISTED_SERVERS = 60
MAX_INCIDENT_ATTEMPTS = 8
MAX_TESTMAILS_JE_STUNDE = 3

_SERVER_ID_SCHEMA = {
    "server_id": {
        "type": "integer",
        "minimum": 1,
        "description": "ID des Servers aus list_my_servers.",
    }
}
_MUTEX_TOOLS = {
    "propose_server_lifecycle",
    "propose_server_start",
    "propose_server_stop",
    "propose_server_restart",
    "propose_server_kill",
    "propose_backup_restore",
    "propose_switch_blueprint",
    "propose_server_delete",
    "propose_config_update",
    "propose_config_patch",
    "propose_config_set",
    "propose_bind_ip_update",
    "propose_mod_install",
    "propose_mod_toggle",
    "propose_server_repair",
}

_RATIONALE_SCHEMA = {
    "reason": {
        "type": "string",
        "maxLength": MAX_REASON_CHARS,
        "description": "Kurze Begruendung, warum diese Aktion vorgeschlagen wird",
    },
    "expected_effect": {
        "type": "string",
        "maxLength": MAX_REASON_CHARS,
        "description": "Erwartete Auswirkung auf den Server oder Dienst",
    },
}
_RATIONALE_REQUIRED = ["reason", "expected_effect"]

_MEMORY_TEAM_SCHEMA = {
    "team_id": {
        "type": ["integer", "null"],
        "description": "Nur bei scope=team: die team_id aus dem Suchergebnis.",
    },
    "team": {
        "type": "string",
        "maxLength": 64,
        "description": (
            "Ersatz ohne team_id: Teamname aus einer Rückfrage, genau so "
            "geschrieben."
        ),
    },
}

_PLAN_SCHEMA = {
    "plan_kind": {
        "type": "string",
        "enum": list(_PLANARTEN),
        "description": (
            "daily = jeden Tag (oder an bestimmten Wochentagen) zu einer "
            "Uhrzeit, interval = alle N Stunden, once = einmalig."
        ),
    },
    "time_of_day": {
        "type": "string",
        "maxLength": 5,
        "description": "Nur bei daily. 'HH:MM' in der angegebenen Zeitzone.",
    },
    "weekdays": {
        "type": "array",
        "items": {"type": "integer", "minimum": 1, "maximum": 7},
        "description": "Nur bei daily. 1 = Montag bis 7 = Sonntag. Leer = taeglich.",
    },
    "interval_hours": {
        "type": "integer",
        "minimum": _MIN_INTERVALL_STUNDEN,
        "maximum": _MAX_INTERVALL_STUNDEN,
        "description": "Nur bei interval.",
    },
    "once_at": {
        "type": "string",
        "maxLength": 32,
        "description": (
            "Nur bei once. ISO-8601 wie '2026-08-20T08:00'. Ohne "
            "Zeitzonenangabe gilt die der Aufgabe."
        ),
    },
    "timezone": {
        "type": "string",
        "maxLength": 64,
        "description": "Pflicht. IANA-Zone des Benutzers, z. B. Europe/Berlin.",
    },
    "channel": {
        "type": "string",
        "enum": list(_KANAELE),
        "description": (
            "chat = nur im Panel, email = zusaetzlich per Mail, both = beides. "
            "Im Chat steht das Ergebnis immer."
        ),
    },
}

_MEMORY_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

def _function(name: str, description: str, properties: dict, required: list[str]) -> dict:
    # Ohne Zeile in `ai_tool_registry` waere das Werkzeug zwar im Katalog, aber
    # in keiner Menge â€” das Modell duerfte es aufrufen und die Allowlist wuerde
    # es abweisen. Hier faellt der fehlende Eintrag sofort auf.
    assert _werkzeug_bekannt(name), f"Werkzeug {name!r} fehlt in ai_tool_registry"
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                **({"required": required} if required else {}),
                "additionalProperties": False,
            },
        },
    }

def _server_function(
    name: str, description: str, properties: dict | None = None, required: list[str] | None = None
) -> dict:
    """Wie ``_function``, aber mit verpflichtender ``server_id``."""
    return _function(
        name,
        description,
        {**_SERVER_ID_SCHEMA, **(properties or {})},
        ["server_id", *(required or [])],
    )

def _vorfall_versuche(attempts_json: str | None) -> list[dict]:
    """Die Wiederherstellungsversuche des Agenten, auf das Noetige gekuerzt.

    Sie stehen als JSON-Zeichenkette am Vorfall (`incidents.attempts`) und
    tragen je Eintrag `attempt`, `stage`, `action`, `at` und `result`. Genau
    diese fuenf gehen weiter â€” mehr steht nicht drin, und was der Agent kuenftig
    ergaenzt, soll nicht ungefragt an einen Modellanbieter gehen.

    Ohne diese Liste faengt die KI bei jedem Vorfall mit einem Neustart an. Das
    ist der Schritt, den die Guardian-Engine ausweislich ihrer eigenen
    Eskalationsleiter schon dreimal gemacht hat, bevor sie den Vorfall ueberhaupt
    meldete â€” die KI wuerde also als Erstes das wiederholen, was nachweislich
    nicht geholfen hat.

    Unlesbares gibt eine leere Liste. Der Inhalt kommt vom Agenten, und ein
    kaputtes JSON darf hier kein Werkzeug zum Absturz bringen.
    """
    try:
        geladen = json.loads(attempts_json) if attempts_json else []
    except (TypeError, ValueError):
        return []
    if not isinstance(geladen, list):
        return []
    erlaubt = ("attempt", "stage", "action", "at", "result")
    versuche = [
        {k: v for k, v in eintrag.items() if k in erlaubt}
        for eintrag in geladen
        if isinstance(eintrag, dict)
    ]
    return versuche[-MAX_INCIDENT_ATTEMPTS:]

def _require_no_arguments(tool_name: str, arguments: dict) -> None:
    if arguments:
        raise AiActionValidationError(f"{tool_name} akzeptiert keine Argumente")

def _visible_servers(db: Session, user: User) -> list[Server]:
    """Alle Server, die der Benutzer sehen darf â€” die Grundlage von `list_my_servers`.

    Die AuflÃ¶sung von Rollenrechten *und* einzeln delegierten Serverrechten
    liegt an genau einer Stelle â€” sie ist nur die Mengenfunktion und nicht die
    EinzelprÃ¼fung. Hier stand einmal eine Schleife, die `has_server_permission`
    je Serverzeile rief. Sie lieferte dieselbe Menge, kostete aber drei Abfragen
    je Zeile, und der Deckel griff erst bei 60 *sichtbaren* Treffern: ein Kunde
    mit einem Server unter fÃ¼nfhundert lief alle fÃ¼nfhundert Zeilen durch, auf
    dem Weg zum ersten Token. `list_visible_server_ids` beantwortet dieselbe
    Frage gebÃ¼ndelt, einschlieÃŸlich des Teamwegs.

    Die Obergrenze verhindert, dass ein Betreiber mit hunderten Servern die
    halbe Liste ins Kostenbudget des Benutzers schreibt. Sie steht jetzt in der
    Abfrage statt in der Schleife und zieht dieselbe Grenze.
    """
    # Dreiwertig: `None` heiÃŸt **alle** (EigentÃ¼mer oder pauschale Rolle), eine
    # leere Liste heiÃŸt **keiner**. Die beiden zu verwechseln wÃ¤re in der einen
    # Richtung eine Rechteausweitung und in der anderen eine leere Liste fÃ¼r den
    # Betreiber.
    ids = permission_service.list_visible_server_ids(db, user)
    if ids is not None and not ids:
        return []
    abfrage = db.query(Server)
    if ids is not None:
        abfrage = abfrage.filter(Server.id.in_(ids))
    return abfrage.order_by(Server.id).limit(MAX_LISTED_SERVERS).all()

def _resolve_server(db: Session, user: User, arguments: dict) -> tuple[Server, dict]:
    """Entnimmt ``server_id``, laedt den Server und prueft `server.view`.

    Das ist die Stelle, an der "die KI erbt die Rechte des Benutzers" fuer jedes
    serverbezogene Werkzeug tatsaechlich durchgesetzt wird â€” einmal, zentral,
    fuer Lese- und Schreibwerkzeuge gleichermassen. Ein Modell, das eine fremde
    ID errraet oder aus einem manipulierten Logtext uebernimmt, kommt hier nicht
    vorbei.

    Ein nicht sichtbarer Server ist bewusst nicht von einem nicht existierenden
    zu unterscheiden: sonst waere die Fehlermeldung ein Existenzorakel.
    """
    rest = {key: value for key, value in arguments.items() if key != "server_id"}
    raw = arguments.get("server_id")
    if isinstance(raw, str) and raw.strip().isdigit():
        raw = int(raw.strip())
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise AiActionValidationError(
            "server_id fehlt oder ist ungueltig. Zuerst list_my_servers aufrufen."
        )
    server = db.get(Server, raw)
    if server is None or not permission_service.has_server_permission(
        db, user, raw, "server.view"
    ):
        raise AiActionValidationError("Server nicht gefunden")
    return server, rest

def _node_health(db: Session) -> dict:
    """Zustand aller Hosts â€” ohne Hostnamen und ohne IP.

    Dieselbe Zurueckhaltung wie bei `read_node_capacity`: das Modell soll
    Auslastung und Erreichbarkeit vergleichen koennen, nicht die Netzstruktur
    des Betreibers kennen. Ein Node-Name waere zusaetzlich frei befuellter Text
    und damit ein weiterer Einfallsweg fuer Prompt Injection.
    """
    from models import Node
    from services.node_service import is_node_offline

    rows = db.query(Node).order_by(Node.id).limit(MAX_LISTED_NODES).all()
    nodes = []
    for node in rows:
        ram_percent = (
            round(node.ram_used / node.ram_total * 100, 1)
            if node.ram_total and node.ram_used is not None
            else None
        )
        disk_percent = (
            round(node.disk_used / node.disk_total * 100, 1)
            if node.disk_total and node.disk_used is not None
            else None
        )
        nodes.append({
            "node_id": node.id,
            "is_local": bool(node.is_local),
            "status": node.status,
            "offline": is_node_offline(node),
            "docker_connected": node.docker_connected,
            "container_count": node.container_count,
            "cpu_total": node.cpu_total,
            "cpu_percent": node.cpu_percent,
            "ram_total_bytes": node.ram_total,
            "ram_used_bytes": node.ram_used,
            "ram_used_percent": ram_percent,
            "disk_total_bytes": node.disk_total,
            "disk_used_bytes": node.disk_used,
            "disk_used_percent": disk_percent,
            "agent_version": node.agent_version,
            "last_heartbeat": node.last_heartbeat.isoformat() if node.last_heartbeat else None,
        })
    return {"nodes": nodes, "count": len(nodes)}

def is_binary_text(content: str) -> bool:
    """Erkennt an, was `read_text` aus einer Nicht-Textdatei gemacht hat.

    Der Dateizugriff dekodiert mit ``errors="replace"``: eine Binaerdatei kommt
    als Folge von Ersatzzeichen (U+FFFD) zurueck, ein Nullbyte als solches. Beides
    kann in einer echten Textdatei nicht in Menge auftreten.

    Die Schwelle ist bewusst grosszuegig â€” eine einzelne kaputte Umlautstelle in
    einer sonst brauchbaren Konfigurationsdatei soll nicht dazu fuehren, dass die
    KI sie fuer binaer haelt und nicht mehr anfasst.
    """
    if "\x00" in content:
        return True
    if not content:
        return False
    return content.count("ï¿½") / len(content) > 0.02

def _config_path(value: object) -> str:
    """Prueft einen Pfad relativ zum Serververzeichnis.

    **Keine Endungsliste mehr.** Frueher stand hier ein Filter auf neun
    Erweiterungen, und alles andere war fuer die KI unsichtbar â€” Dateien **ohne**
    Endung (`Dockerfile`, `.env`, `whitelist`, `banlist`), `.xml` (Ark, Unreal),
    `.lua` (Garry's Mod, DayZ), `.sh`, `.md`. Ein Mensch bearbeitet die im
    Dateimanager selbstverstaendlich; die Vorgabe des Betreibers ist, dass die
    KI denselben Umfang hat und nicht "an einer anderen Stelle etwas anderes
    einstellt".

    Die Endung war ohnehin nie die Sicherheitsgrenze. Die liegt in `safe_path`,
    das ueber `resolve()` und `relative_to()` auch Symlinks nach aussen abfaengt,
    und in der Rechtepruefung. Was hier bleibt, ist die Formpruefung: relativ,
    kein Ausbruch, keine Backslashes, begrenzte Laenge.
    """
    if not isinstance(value, str) or not value or len(value) > 256 or "\\" in value:
        raise AiActionValidationError("Ungueltiger Dateipfad")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise AiActionValidationError("Dateipfad ist nicht erlaubt")
    # Kein Namensteil darf mit einem Bindestrich beginnen.
    #
    # Nicht wegen des Dateisystems â€” dort ist das erlaubt â€”, sondern wegen der
    # Werkzeuge, die diese Namen spaeter als Argumente weiterreichen. `tar`
    # deutet einen Operanden, der mit `-` beginnt, als Option; `games/updater.py`
    # sichert seine Aufrufe deshalb zusaetzlich mit `--` ab. Diese Pruefung ist
    # die zweite Haelfte davon: eine Datei, die niemand von Hand so genannt
    # haette, entsteht hier gar nicht erst.
    #
    # Ein Mensch verliert dadurch nichts Sinnvolles. Die KI legt Dateien
    # ungefragt an; ein Name wie `--use-compress-program=...` ist keine
    # Konfiguration, sondern ein Versuch.
    if any(teil.startswith("-") for teil in path.parts):
        raise AiActionValidationError(
            "Dateiname darf nicht mit einem Bindestrich beginnen"
        )
    return path.as_posix()

def _positive_int(
    value: object,
    *,
    name: str,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    """Ein optionales Zahlenargument, oder der Vorgabewert.

    ``None`` ist ausdruecklich erlaubt: manche Modelle setzen ein weggelassenes
    Argument auf null, statt es wegzulassen. Das als Fehler zu behandeln waere
    eine Huerde ohne Zweck.
    """
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise AiActionValidationError(f"'{name}' muss eine ganze Zahl sein")
    if value < minimum or (maximum is not None and value > maximum):
        grenze = f"{minimum}..{maximum}" if maximum is not None else f"ab {minimum}"
        raise AiActionValidationError(f"'{name}' liegt ausserhalb von {grenze}")
    return value
