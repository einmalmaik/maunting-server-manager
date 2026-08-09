"""Allowlist, Preview, Bestaetigung und Ausfuehrung fuer AI-Aktionen."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import difflib
import hashlib
import hmac
import json
from pathlib import PurePosixPath
import re
import secrets
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import AiActionProposal, AiConversation, Server, User
from services import audit_service, permission_service
from services.actor_context import ActorContext
from services.ai_redaction import redact_sensitive_text
from services.dis_client import DisClient
from services.server_file_access_service import read_server_text, write_server_text


CONFIRMATION_TTL = timedelta(minutes=5)
MAX_CONFIG_CHARS = 64_000
MAX_DIFF_CHARS = 16_000
MAX_DIFF_LINES = 200
# Harte Obergrenzen fuer alles, was aus einem Server zum Provider fliesst.
# Bewusst als Konstanten, weil dieselben Werte im Phase-4-Vertrag stehen.
MAX_READ_CONFIG_CHARS = 24_000
MAX_LOG_CHARS = 24_000
CONFIG_EXTENSIONS = {
    ".cfg", ".conf", ".ini", ".json", ".properties", ".toml", ".txt", ".yaml", ".yml"
}
# Obergrenzen fuer die Listen-Tools. Jede Zeile landet als unvertrauenswuerdiger
# Text im Modellkontext und damit im Kostenbudget des Benutzers.
MAX_LISTED_MODS = 60
MAX_LISTED_BACKUPS = 20
MAX_LISTED_INCIDENTS = 15
MAX_LISTED_ACTIONS = 20
MAX_LISTED_BLUEPRINTS = 80
MAX_LISTED_NODES = 30
MAX_REASON_CHARS = 500

# ── Tool-Mengen ───────────────────────────────────────────────────────────
# Server-Tools brauchen eine serverbezogene Unterhaltung. Globale Tools laufen
# im Panel-Chat, in dem es noch keinen Server gibt — genau dort muss die
# Servererstellung andocken (Zielpunkt 3.1).

SERVER_READ_TOOLS = {
    "read_server_status",
    "read_server_capacity",
    "read_server_logs",
    "read_config",
    "read_server_ports",
    "read_server_mods",
    "read_server_backups",
    "read_guardian_incidents",
    "read_ai_action_history",
    "read_mod_updates",
    "search_workshop_mods",
    "read_server_network",
    "check_server_reachability",
}
GLOBAL_READ_TOOLS = {
    "list_my_servers", "list_blueprints", "read_node_capacity",
    "read_node_health", "remember", "web_search",
}
READ_TOOLS = SERVER_READ_TOOLS | GLOBAL_READ_TOOLS

# `remember` steht bewusst bei den Read-Tools, obwohl es schreibt. Der
# Unterschied zwischen beiden Mengen ist nicht "aendert etwas", sondern "fasst
# einen Server an und braucht deshalb eine Bestaetigung". Ein gemerkter Satz im
# eigenen Profil des Benutzers tut das nicht: er ist jederzeit einsehbar,
# aenderbar und loeschbar, beruehrt keinen Server und keine Datei. Als
# Write-Tool wuerde jedes Merken den Chat unterbrechen und eine Rueckfrage
# erzeugen — ein Gedaechtnis, das man einzeln bestaetigen muss, ist keines.
MEMORY_TOOLS = {"remember"}

# Jedes serverbezogene Werkzeug traegt seit dem Einzelchat seine eigene
# `server_id`. Vorher stand sie an der Unterhaltung — dadurch konnte der
# Panel-Chat gar kein Server-Werkzeug anbieten und man musste erst wissen,
# welcher Server gemeint ist, bevor man fragen durfte.
_SERVER_ID_SCHEMA = {
    "server_id": {
        "type": "integer",
        "minimum": 1,
        "description": "ID des Servers aus list_my_servers.",
    }
}
MAX_LISTED_SERVERS = 60

SERVER_WRITE_TOOLS = {
    "propose_server_lifecycle",
    "propose_backup",
    "propose_config_update",
    "propose_mod_install",
    "propose_bind_ip_update",
}
GLOBAL_WRITE_TOOLS = {"propose_server_create"}
WRITE_TOOLS = SERVER_WRITE_TOOLS | GLOBAL_WRITE_TOOLS

# Diese Aktionen fassen Serverdateien an und teilen sich deshalb den
# vorhandenen, nicht blockierenden Server-Lifecycle-Mutex. Lifecycle-Aktionen
# brauchen ihn nicht: `request_lifecycle_operation` hat eine eigene Job-Sperre.
# Mod-Installation ebenso wenig: `install_mod_bg` haelt den Install-Lock selbst.
_MUTEX_TOOLS = {"propose_backup", "propose_config_update"}

# Aktionen aus Zielbild 3.7, die **immer** eine menschliche Bestaetigung
# verlangen — auch im autonomen Modus. Aktuell ist keines dieser Werkzeuge
# gebaut; die Menge steht hier trotzdem, damit ein kuenftiges Tool sich
# ausdruecklich einordnen muss statt stillschweigend autonomiefaehig zu sein.
ALWAYS_CONFIRM_TOOLS = {
    # Eine Netzwerkaenderung startet den Container neu und kann einen Server
    # unerreichbar machen, wenn die Adresse falsch ist. Sie laeuft deshalb nie
    # autonom — auch bei erteilter Freigabe nicht. Als einziges Werkzeug dieser
    # Menge ist es tatsaechlich gebaut; die uebrigen sind Platzhalter.
    "propose_bind_ip_update",
    "propose_server_delete",
    "propose_server_wipe",
    "propose_server_reinstall",
    "propose_backup_restore",
    "propose_blueprint_change",
    "propose_secret_rotation",
    "propose_permission_change",
}

# Ein "reason" beschreibt, warum die KI die Aenderung vorschlaegt, ein
# "expected_effect" was danach anders sein soll. Beides ist eine Begruendung des
# Modells, keine Zusicherung des Panels — und wird deshalb redigiert und gekuerzt.
_RATIONALE_SCHEMA = {
    "reason": {"type": "string", "maxLength": MAX_REASON_CHARS},
    "expected_effect": {"type": "string", "maxLength": MAX_REASON_CHARS},
}
_RATIONALE_REQUIRED = ["reason", "expected_effect"]


def _function(name: str, description: str, properties: dict, required: list[str]) -> dict:
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


def _global_tool_definitions() -> list[dict]:
    """Werkzeuge ohne Serverbezug: Serverliste, Blueprints, Kapazitaet, Anlage."""
    optional: list[dict] = []
    # Ohne hinterlegten Schluessel gar nicht erst anbieten. Ein Werkzeug, das
    # immer scheitert, verwirrt ein Modell mehr als es hilft: es versucht es
    # erneut, formuliert um und verbraucht dabei Tokens.
    from services.ai_web_search_service import MAX_RESULTS, is_configured

    if is_configured():
        optional.append(_function(
            "web_search",
            "Sucht im Web. Fuer aktuelle Informationen, die nicht aus dem "
            "Panel kommen — Fehlermeldungen, Modkompatibilitaet, "
            "Spielversionen. Liefert Titel, Adresse und Kurztext.",
            {
                "query": {"type": "string", "maxLength": 200},
                "count": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS},
            },
            ["query"],
        ))

    return optional + [
        _function(
            "list_my_servers",
            "Listet alle Server, die der Benutzer sehen darf, mit ID, Name, Spiel "
            "und Status. Immer zuerst aufrufen, wenn der Benutzer einen Server "
            "nur mit Namen nennt oder gar nicht benennt.",
            {},
            [],
        ),
        _function(
            "remember",
            "Merkt sich eine dauerhafte Vorliebe oder Eigenheit. Nur fuer "
            "Dinge, die ueber dieses Gespraech hinaus gelten — nicht fuer "
            "Zwischenergebnisse. Verwende einen bereits vorhandenen Schluessel "
            "erneut, wenn du einen Fakt aktualisierst, statt einen aehnlichen "
            "neuen anzulegen. Niemals Passwoerter, Schluessel oder Tokens "
            "merken.\n"
            "Wahl des Bereichs: Persoenlich ist, was jemand *will* "
            "(\"ich nehme immer 8 GB\"). Team ist, wie etwas *ist* — eine "
            "Eigenschaft der Anlage, die fuer alle Kollegen gilt "
            "(\"dieser Server braucht mindestens 6 GB\"). Pruefsatz: ein "
            "Team-Eintrag muss wahr bleiben, egal wer ihn liest. Steht \"ich\", "
            "\"mein\" oder ein Name darin, ist er persoenlich. Im Zweifel "
            "persoenlich.",
            {
                "scope": {
                    "type": "string",
                    "enum": ["user", "server", "team"],
                    "description": (
                        "user = persoenlich, nur fuer diesen Benutzer. "
                        "server = persoenlich, aber nur zu diesem Server. "
                        "team = geteilt mit allen Kollegen im Team."
                    ),
                },
                "server_id": {
                    "type": ["integer", "null"],
                    "description": "Nur bei scope=server. Sonst null.",
                },
                "key": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Kurzer stabiler Bezeichner, z. B. ram.bevorzugt.",
                },
                "value": {"type": "string", "maxLength": 2_000},
            },
            ["scope", "key", "value"],
        ),
        _function(
            "list_blueprints",
            "Listet verfuegbare Servertypen (Blueprints) mit Modunterstuetzung und Portrollen.",
            {},
            [],
        ),
        _function(
            "read_node_capacity",
            "Liest freie und belegte Kapazitaet aller Hosts, um Ressourcen sinnvoll zu waehlen.",
            {},
            [],
        ),
        _function(
            "read_node_health",
            "Liest den Gesundheitszustand aller Hosts: erreichbar, Docker "
            "verbunden, CPU, RAM, Festplatte, Containerzahl, letzter Kontakt. "
            "Fuer Fragen wie 'bei einer meiner Nodes stimmt etwas nicht'.",
            {},
            [],
        ),
        _function(
            "propose_server_create",
            "Schlaegt die Erstellung eines neuen Servers zur manuellen Bestaetigung vor. "
            "Ports, Installationsverzeichnis und Host werden von MSM vergeben.",
            {
                "name": {"type": "string", "maxLength": 128},
                "game_type": {"type": "string", "maxLength": 64},
                "ram_limit_mb": {"type": "integer", "minimum": 512, "maximum": 4_194_304},
                "cpu_limit_percent": {"type": "integer", "minimum": 10, "maximum": 3_200},
                "disk_limit_gb": {"type": "integer", "minimum": 1, "maximum": 1_048_576},
                "node_id": {"type": ["integer", "null"]},
                **_RATIONALE_SCHEMA,
            },
            [
                "name",
                "game_type",
                "ram_limit_mb",
                "cpu_limit_percent",
                "disk_limit_gb",
                *_RATIONALE_REQUIRED,
            ],
        ),
    ]


def provider_tool_definitions() -> list[dict]:
    """Feste OpenAI-Tool-Allowlist; keine freie Command-Ausfuehrung.

    Es gibt genau *einen* Werkzeugsatz. Die frueher noetige Unterscheidung
    zwischen Panel-Chat und Server-Chat ist mit dem Einzelchat entfallen: der
    Server steht jetzt in den Argumenten, nicht im Gespraech. Das Modell findet
    ihn ueber `list_my_servers` und fragt bei Mehrdeutigkeit nach.
    """
    return [
        *_global_tool_definitions(),
        _server_function(
            "read_server_status",
            "Liest den minimierten Status eines Servers.",
        ),
        _server_function(
            "read_server_capacity",
            "Liest minimierte, zuletzt bekannte Kapazitaetswerte des Servers und seines Nodes.",
        ),
        _server_function(
            "read_server_logs",
            "Liest einen begrenzten, redigierten Log-Ausschnitt des Servers.",
            {"lines": {"type": "integer", "minimum": 1, "maximum": 200}},
        ),
        _server_function(
            "read_config",
            "Liest eine erlaubte Text-Konfigurationsdatei revisionssicher.",
            {"path": {"type": "string", "maxLength": 256}},
            ["path"],
        ),
        # ── Erweiterter Serverkontext (Zielpunkt 3.3) ──────────────────────
        _server_function(
            "read_server_ports",
            "Liest die vergebenen Ports des Servers mit Rolle und Protokoll.",
        ),
        _server_function(
            "read_server_network",
            "Liest die Netzwerkeinrichtung: Bind-IP mit Einordnung, Ports, "
            "verfuegbare Host-Adressen und Firewall-Zustand. Erster Schritt, "
            "wenn ein Server laeuft, aber niemand sich verbinden kann. "
            "Rufe danach check_server_reachability auf — erst beide zusammen "
            "ergeben eine Diagnose. read_server_status ist dafuer nicht noetig, "
            "der Status steht bereits in dieser Antwort.",
        ),
        _server_function(
            "check_server_reachability",
            "Misst, ob auf den Ports des Servers tatsaechlich etwas lauscht. "
            "Der eigentliche Beweis bei 'laeuft, aber niemand kommt drauf': "
            "meldet ein Port sich als frei, obwohl der Server laeuft, horcht "
            "der Dienst nicht oder horcht auf einer anderen Adresse. "
            "Beantwortet nicht, ob der Server aus dem Internet erreichbar ist — "
            "das kann MSM nicht messen und behauptet es auch nicht.",
        ),
        _server_function(
            "read_server_mods",
            "Liest die installierten Mods mit Aktivierungs-, Installations- und Updatestatus.",
        ),
        _server_function(
            "read_server_backups",
            "Liest die vorhandenen Backups mit Groesse und Zeitpunkt.",
        ),
        _server_function(
            "read_guardian_incidents",
            "Liest die zuletzt erkannten Guardian-Vorfaelle dieses Servers.",
        ),
        _server_function(
            "read_ai_action_history",
            "Liest frueher vorgeschlagene und ausgefuehrte KI-Aktionen dieses Servers.",
        ),
        _server_function(
            "read_mod_updates",
            "Prueft, fuer welche Mods ein Update oder eine Nachinstallation aussteht.",
        ),
        _server_function(
            "search_workshop_mods",
            "Sucht Mods im Steam Workshop fuer das Spiel dieses Servers. "
            "Liefert Kennung, Titel und Tags — keine Beschreibungstexte.",
            {
                "query": {"type": "string", "maxLength": 128},
                "page": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            ["query"],
        ),
        # ── Schreib-Tools: erzeugen ausschliesslich Vorschlaege ────────────
        _server_function(
            "propose_server_lifecycle",
            "Schlaegt Start, Stop oder Neustart zur manuellen Bestaetigung vor.",
            {
                "operation": {"type": "string", "enum": ["start", "stop", "restart"]},
                **_RATIONALE_SCHEMA,
            },
            ["operation", *_RATIONALE_REQUIRED],
        ),
        _server_function(
            "propose_backup",
            "Schlaegt ein Server-Backup zur manuellen Bestaetigung vor.",
            dict(_RATIONALE_SCHEMA),
            list(_RATIONALE_REQUIRED),
        ),
        _server_function(
            "propose_config_update",
            "Schlaegt eine revisionsgebundene Config-Aenderung vor. Niemals Secrets einfuegen.",
            {
                "path": {"type": "string", "maxLength": 256},
                "content": {"type": "string", "maxLength": MAX_CONFIG_CHARS},
                "expected_revision": {"type": ["string", "null"]},
                **_RATIONALE_SCHEMA,
            },
            ["path", "content", "expected_revision", *_RATIONALE_REQUIRED],
        ),
        _server_function(
            "propose_bind_ip_update",
            "Schlaegt eine andere Bind-IP vor — etwa wenn der Server an eine "
            "Docker- oder Loopback-Adresse gebunden ist und deshalb von aussen "
            "nicht erreichbar sein kann. Nur Adressen, die dem Host tatsaechlich "
            "gehoeren; nimm sie aus read_server_network. Ein laufender Server "
            "wird dabei neu gestartet.",
            {
                "bind_ip": {"type": "string", "maxLength": 45},
                **_RATIONALE_SCHEMA,
            },
            ["bind_ip", *_RATIONALE_REQUIRED],
        ),
        _server_function(
            "propose_mod_install",
            "Schlaegt Installation, Aktualisierung oder Neuinstallation einer Workshop-Mod vor. "
            "Der Download laeuft ueber den vorhandenen MSM-Installationspfad.",
            {
                "workshop_id": {"type": "string", "maxLength": 20},
                "action": {"type": "string", "enum": ["install", "update", "reinstall"]},
                **_RATIONALE_SCHEMA,
            },
            ["workshop_id", "action", *_RATIONALE_REQUIRED],
        ),
    ]


def _require_no_arguments(tool_name: str, arguments: dict) -> None:
    if arguments:
        raise AiActionValidationError(f"{tool_name} akzeptiert keine Argumente")


def _visible_servers(db: Session, user: User) -> list[Server]:
    """Alle Server, die der Benutzer sehen darf — die Grundlage von `list_my_servers`.

    Die Pruefung laeuft je Zeile ueber `has_server_permission` und nicht ueber
    eine gefilterte Abfrage: Sichtbarkeit entsteht aus Rollenrechten *und*
    einzeln delegierten Serverrechten, und diese Aufloesung gehoert an genau
    eine Stelle. Die Obergrenze verhindert, dass ein Betreiber mit hunderten
    Servern die halbe Liste ins Kostenbudget des Benutzers schreibt.
    """
    rows = db.query(Server).order_by(Server.id).all()
    visible: list[Server] = []
    for server in rows:
        if permission_service.has_server_permission(db, user, server.id, "server.view"):
            visible.append(server)
        if len(visible) >= MAX_LISTED_SERVERS:
            break
    return visible


def _resolve_server(db: Session, user: User, arguments: dict) -> tuple[Server, dict]:
    """Entnimmt ``server_id``, laedt den Server und prueft `server.view`.

    Das ist die Stelle, an der "die KI erbt die Rechte des Benutzers" fuer jedes
    serverbezogene Werkzeug tatsaechlich durchgesetzt wird — einmal, zentral,
    fuer Lese- und Schreibwerkzeuge gleichermassen. Ein Modell, das eine fremde
    ID errraet oder aus einem manipulierten Logtext uebernimmt, kommt hier nicht
    vorbei.

    Ein nicht sichtbarer Server ist bewusst nicht von einem nicht existierenden
    zu unterscheiden: sonst waere die Fehlermeldung ein Existenzorakel.
    """
    rest = {key: value for key, value in arguments.items() if key != "server_id"}
    raw = arguments.get("server_id")
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


_MEMORY_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _execute_remember(db: Session, *, user: User, arguments: dict) -> dict:
    """Laesst die KI einen dauerhaften Fakt im Memory des Benutzers ablegen.

    Die Rechtegrenze ist `ai.memory.use` — dasselbe Recht, das entscheidet, ob
    Memory ueberhaupt in den Kontext fliesst. Wer sein Memory nicht nutzen darf,
    bekommt auch keines geschrieben.

    Alle inhaltlichen Schutzmassnahmen liegen bereits in
    `ai_memory_service.upsert_entry`: Secret-Abweisung, Groessengrenze,
    DIS-Verschluesselung, Scope-Trennung je Benutzer und die Regel, dass eine
    Ableitung der KI keine ausdrueckliche Ansage des Benutzers ueberschreibt.
    Hier steht nur die Argumentpruefung.
    """
    from services import ai_memory_service

    if not permission_service.has_global_permission(db, user, "ai.memory.use"):
        raise AiActionValidationError("Memory ist fuer diesen Benutzer nicht freigegeben")
    if set(arguments) - {"scope", "server_id", "key", "value"}:
        raise AiActionValidationError("Memory-Werkzeug hat ungueltige Argumente")

    scope = arguments.get("scope")
    if scope not in {"user", "server", "team"}:
        # "panel" ist bewusst nicht erreichbar: panelweites Memory gilt fuer
        # alle Benutzer und ist eine Betreiberentscheidung, keine der KI.
        raise AiActionValidationError("Unbekannter Memory-Bereich")

    key = arguments.get("key")
    if not isinstance(key, str) or not _MEMORY_KEY_RE.match(key):
        raise AiActionValidationError("Ungueltiger Memory-Schluessel")
    value = arguments.get("value")
    if not isinstance(value, str) or not value.strip():
        raise AiActionValidationError("Memory-Inhalt ist leer")

    server_id = arguments.get("server_id")
    if scope == "server":
        if isinstance(server_id, bool) or not isinstance(server_id, int) or server_id < 1:
            raise AiActionValidationError("Server-Memory braucht eine gueltige server_id")
    elif server_id is not None:
        raise AiActionValidationError("Benutzer-Memory akzeptiert keinen Server")

    # Das Team nennt nicht das Modell, sondern der Dienst: welchem Team ein
    # Benutzer angehoert, ist eine Tatsache der Datenbank und keine Angabe, die
    # aus einem Prompt stammen darf. Ist die Lage nicht eindeutig, bekommt das
    # Modell die Rueckfrage als Ergebnis und fragt den Benutzer.
    team_id = None
    if scope == "team":
        from services import team_service

        target, question = team_service.learning_team(db, user)
        if target is None:
            return {"remembered": False, "ask_user": question}
        if target.is_personal:
            # Kein echtes Team vorhanden oder keine Verwaltungsberechtigung:
            # der Eintrag wird persoenlich statt gar nicht. Lieber zu eng
            # gespeichert als zu weit.
            scope = "user"
        else:
            team_id = target.id

    try:
        row, stored = ai_memory_service.upsert_entry(
            db, user=user, scope=scope, server_id=server_id if scope == "server" else None,
            team_id=team_id, key=key, value=value, origin="ai",
        )
    except HTTPException as exc:
        # Volles Scope, Secret im Wert, fremder Server, geschuetzter Eintrag:
        # alles regulaere Faelle, die das Modell erfahren soll, statt dass der
        # Stream mit einem Serverfehler abbricht.
        raise AiActionValidationError(str(exc.detail)) from exc
    return {
        "remembered": True, "scope": row.scope, "key": row.key, "value": stored,
        "team_id": row.team_id,
    }


def _execute_web_search(db: Session, *, user: User, arguments: dict) -> dict:
    """Websuche im Namen des Benutzers.

    Die Rechtegrenze ist `ai.web_search.use`. Bis hierher stand dieses Recht im
    Katalog, ohne an irgendeiner Stelle geprueft zu werden.
    """
    from services import ai_web_search_service

    if not permission_service.has_global_permission(db, user, "ai.web_search.use"):
        raise AiActionValidationError("Websuche ist fuer diesen Benutzer nicht freigegeben")
    if set(arguments) - {"query", "count"}:
        raise AiActionValidationError("Websuche hat ungueltige Argumente")
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise AiActionValidationError("Suchanfrage ist leer")
    count = arguments.get("count", ai_web_search_service.MAX_RESULTS)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= ai_web_search_service.MAX_RESULTS:
        raise AiActionValidationError("Ungueltige Trefferanzahl")

    try:
        results = ai_web_search_service.search(query, count)
    except ai_web_search_service.WebSearchUnavailable as exc:
        # Ehrlich melden statt eine leere Trefferliste liefern: "nichts
        # gefunden" waere eine falsche Aussage ueber das Web.
        return {"available": False, "reason": exc.code, "results": []}
    return {"available": True, "query": query.strip()[:200], "results": results}


def _node_health(db: Session) -> dict:
    """Zustand aller Hosts — ohne Hostnamen und ohne IP.

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


def _execute_global_read_tool(db: Session, *, user: User, tool_name: str, arguments: dict) -> dict:
    """Werkzeuge ohne Serverbezug.

    `list_my_servers` ist die Einstiegsfrage jedes Gespraechs und deshalb an
    kein zusaetzliches Recht gebunden — es zeigt ausschliesslich Server, die der
    Benutzer ohnehin sieht, und ohne die Liste kann er den Assistenten gar nicht
    sinnvoll benutzen.

    Blueprintliste und Hostkapazitaet sind dagegen die Vorbereitung einer
    Servererstellung. Wer keine Server anlegen darf, hat auch keinen Grund, die
    Kapazitaetsplanung des Betreibers zu sehen.
    """
    if tool_name == "remember":
        return _execute_remember(db, user=user, arguments=arguments)

    if tool_name == "web_search":
        return _execute_web_search(db, user=user, arguments=arguments)

    _require_no_arguments(tool_name, arguments)

    if tool_name == "list_my_servers":
        servers = _visible_servers(db, user)
        return {
            "servers": [
                {
                    "server_id": server.id,
                    # Der Name ist frei vom Benutzer gesetzt und wird redigiert.
                    "name": redact_sensitive_text(str(server.name or ""))[:128],
                    "game_type": server.game_type,
                    "status": server.status,
                }
                for server in servers
            ],
            "count": len(servers),
            "truncated": len(servers) >= MAX_LISTED_SERVERS,
        }

    if tool_name == "read_node_health":
        # Bewusst `nodes.read` statt `servers.create`: den Zustand der Hosts zu
        # sehen ist eine Aufgabe des Betriebs, nicht der Serverplanung. Ein
        # Support-Mitarbeiter soll nachsehen koennen, ohne Server anlegen zu
        # duerfen.
        if not permission_service.has_global_permission(db, user, "nodes.read"):
            raise AiActionValidationError("Node-Einsicht ist nicht erlaubt")
        return _node_health(db)

    if not permission_service.has_global_permission(db, user, "servers.create"):
        raise AiActionValidationError("Serverplanung ist nicht erlaubt")

    if tool_name == "list_blueprints":
        from games import list_game_info

        entries = []
        for info in list_game_info()[:MAX_LISTED_BLUEPRINTS]:
            entries.append({
                "game_type": info.get("id"),
                "name": info.get("name"),
                "platform": info.get("platform"),
                "mod_support": bool(info.get("mod_support")),
                "ports": [port.get("name") for port in (info.get("ports") or [])],
            })
        return {"blueprints": entries, "count": len(entries)}

    from models import Node
    from services.node_capacity import allocatable_ram_mb, sum_allocated_ram_mb

    nodes = db.query(Node).order_by(Node.id).limit(MAX_LISTED_NODES).all()
    return {
        "nodes": [
            {
                # Bewusst ohne Hostname und IP: das Modell soll Kapazitaet
                # vergleichen koennen, nicht die Netzstruktur des Betreibers
                # kennen. Die Auswahl trifft ohnehin MSM.
                "node_id": node.id,
                "status": node.status,
                "is_local": bool(node.is_local),
                "cpu_total": node.cpu_total,
                "ram_allocated_mb": (allocated := sum_allocated_ram_mb(db, node.id)),
                "ram_allocatable_mb": allocatable_ram_mb(node, allocated),
            }
            for node in nodes
        ]
    }


def _execute_server_context_tool(
    db: Session, *, user: User, server: Server, tool_name: str, arguments: dict
) -> dict | None:
    """Die Kontext-Tools aus Zielpunkt 3.3. Jedes prueft sein eigenes Recht."""
    if tool_name == "read_server_ports":
        _require_no_arguments(tool_name, arguments)
        from models import ServerPort

        rows = db.query(ServerPort).filter(ServerPort.server_id == server.id).order_by(
            ServerPort.role
        ).all()
        return {
            "server_id": server.id,
            "ports": [
                {"role": row.role, "port": row.port, "protocol": row.protocol} for row in rows
            ],
        }

    if tool_name in {"read_server_network", "check_server_reachability"}:
        _require_no_arguments(tool_name, arguments)
        from services import server_network_diagnostics

        if tool_name == "check_server_reachability":
            return server_network_diagnostics.check_reachability(db, server)
        # Host-Adressen und Firewall-Regeln sind die Netzstruktur des
        # Betreibers, nicht die des Servers. Wer sie nicht aendern darf, muss
        # sie auch nicht sehen — die Ports des eigenen Servers schon.
        return server_network_diagnostics.describe_network(
            db, server,
            include_host_details=permission_service.has_server_permission(
                db, user, server.id, "server.network.manage"
            ),
        )

    if tool_name in {"read_server_mods", "read_mod_updates", "search_workshop_mods"}:
        if not permission_service.has_server_permission(db, user, server.id, "server.mods.read"):
            raise AiActionValidationError("Mod-Lesezugriff ist nicht erlaubt")
        return _execute_mod_tool(db, server=server, tool_name=tool_name, arguments=arguments)

    if tool_name == "read_server_backups":
        _require_no_arguments(tool_name, arguments)
        if not permission_service.has_server_permission(db, user, server.id, "server.backups.read"):
            raise AiActionValidationError("Backup-Lesezugriff ist nicht erlaubt")
        from models import Backup

        rows = (
            db.query(Backup)
            .filter(Backup.server_id == server.id)
            .order_by(Backup.created_at.desc())
            .limit(MAX_LISTED_BACKUPS)
            .all()
        )
        return {
            "server_id": server.id,
            "backups": [
                {
                    "id": row.id,
                    # Der Name ist frei vom Benutzer gesetzt und wird redigiert.
                    "name": redact_sensitive_text(str(row.name or ""))[:128],
                    "size_mb": row.size_mb,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ],
        }

    if tool_name == "read_guardian_incidents":
        _require_no_arguments(tool_name, arguments)
        from models import Incident

        rows = (
            db.query(Incident)
            .filter(Incident.server_id == server.id)
            .order_by(Incident.created_at.desc())
            .limit(MAX_LISTED_INCIDENTS)
            .all()
        )
        return {
            "server_id": server.id,
            "incidents": [
                {
                    "type": row.type,
                    "status": row.status,
                    "title": redact_sensitive_text(str(row.title))[:128],
                    # Guardian-Beschreibungen enthalten Ausschnitte aus Logs.
                    "description": redact_sensitive_text(str(row.description))[:512],
                    "occurrences": row.occurrences,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
                }
                for row in rows
            ],
        }

    if tool_name == "read_ai_action_history":
        _require_no_arguments(tool_name, arguments)
        rows = (
            db.query(AiActionProposal)
            .filter(AiActionProposal.server_id == server.id)
            .order_by(AiActionProposal.created_at.desc())
            .limit(MAX_LISTED_ACTIONS)
            .all()
        )
        return {
            "server_id": server.id,
            "actions": [
                {
                    # Kein Payload und kein Diff: die Historie soll zeigen, was
                    # passiert ist, nicht frueheren Configinhalt erneut ausgeben.
                    "tool": row.tool_name,
                    "status": row.status,
                    "autonomous": bool(row.autonomous),
                    "error_code": row.error_code,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ],
        }
    return None


def _execute_mod_tool(db: Session, *, server: Server, tool_name: str, arguments: dict) -> dict:
    from games import get_plugin
    from models import Mod
    from services import mod_update_service

    if tool_name == "read_server_mods":
        _require_no_arguments(tool_name, arguments)
        rows = (
            db.query(Mod)
            .filter(Mod.server_id == server.id)
            .order_by(Mod.load_order, Mod.id)
            .limit(MAX_LISTED_MODS)
            .all()
        )
        return {
            "server_id": server.id,
            "mods": [
                {
                    "workshop_id": row.workshop_id,
                    "name": redact_sensitive_text(str(row.name or ""))[:128],
                    "enabled": bool(row.enabled),
                    "install_status": row.install_status,
                    "update_status": row.update_status,
                    "update_reason": row.update_reason,
                    "load_order": row.load_order,
                }
                for row in rows
            ],
        }

    plugin = get_plugin(server.game_type)
    if plugin is None or not getattr(plugin, "supports_mods", False):
        return {
            "server_id": server.id,
            "available": False,
            "reason": "mods_not_supported",
        }

    if tool_name == "read_mod_updates":
        _require_no_arguments(tool_name, arguments)
        updates = mod_update_service.refresh_update_availability(db, server, plugin)
        return {
            "server_id": server.id,
            "available": True,
            "updates": [
                {
                    "workshop_id": str(item.get("workshop_id") or ""),
                    "action": str(item.get("action") or ""),
                    "reason": str(item.get("reason") or "")[:128],
                }
                for item in updates[:MAX_LISTED_MODS]
            ],
        }

    # search_workshop_mods
    if set(arguments) - {"query", "page"} or not isinstance(arguments.get("query"), str):
        raise AiActionValidationError("Workshop-Suche hat ungueltige Argumente")
    page = arguments.get("page", 1)
    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 50:
        raise AiActionValidationError("Ungueltige Seitenzahl")
    mod_support = plugin.get_mod_support() or {}
    appid = mod_support.get("workshop_id")
    if not appid:
        return {"server_id": server.id, "available": False, "reason": "workshop_id_missing"}
    try:
        results = mod_update_service.search_workshop(
            appid=str(appid),
            query=arguments["query"],
            page=page,
            required_tags=mod_support.get("required_tags") or None,
        )
    except mod_update_service.ModSearchUnavailable as exc:
        # Ehrlich melden statt eine leere Trefferliste liefern: "nichts
        # gefunden" waere hier eine falsche Aussage ueber den Workshop.
        return {"server_id": server.id, "available": False, "reason": exc.code}
    return {"server_id": server.id, "available": True, "results": results}


def execute_read_tool(
    db: Session,
    *,
    user: User,
    tool_name: str,
    arguments: dict,
) -> dict:
    """Fuehrt ein Lesewerkzeug im Namen des Benutzers aus.

    Die Unterhaltung wird bewusst nicht mehr uebergeben: sie traegt keinen
    Kontext mehr, der die Ausfuehrung beeinflusst. Alles, was ein Werkzeug
    braucht, steht in seinen Argumenten und wird gegen die Rechte von ``user``
    geprueft.
    """
    if tool_name not in READ_TOOLS:
        raise AiActionValidationError("Read-Tool ist in diesem Kontext nicht erlaubt")
    if tool_name in GLOBAL_READ_TOOLS:
        return _execute_global_read_tool(
            db, user=user, tool_name=tool_name, arguments=arguments
        )
    server, arguments = _resolve_server(db, user, arguments)
    context = _execute_server_context_tool(
        db, user=user, server=server, tool_name=tool_name, arguments=arguments
    )
    if context is not None:
        return context
    if tool_name == "read_server_status":
        if arguments:
            raise AiActionValidationError("Status-Tool akzeptiert keine Argumente")
        return {
            "server_id": server.id,
            "game": server.game_type,
            "status": server.status,
            "cpu_limit_percent": server.cpu_limit_percent,
            "ram_limit_mb": server.ram_limit_mb,
            "disk_limit_gb": server.disk_limit_gb,
        }
    if tool_name == "read_server_capacity":
        if arguments:
            raise AiActionValidationError("Kapazitaets-Tool akzeptiert keine Argumente")
        node = server.node
        if node is None:
            return {"server_id": server.id, "node_status": "unassigned"}
        from services.node_capacity import allocatable_ram_mb, sum_allocated_ram_mb

        allocated_ram_mb = sum_allocated_ram_mb(db, node.id)
        return {
            "server_id": server.id,
            "node_status": node.status,
            "cpu_total": node.cpu_total,
            "cpu_percent": node.cpu_percent,
            "ram_total_bytes": node.ram_total,
            "ram_used_bytes": node.ram_used,
            "ram_allocated_mb": allocated_ram_mb,
            "ram_allocatable_mb": allocatable_ram_mb(node, allocated_ram_mb),
            "disk_total_bytes": node.disk_total,
            "disk_used_bytes": node.disk_used,
        }
    if tool_name == "read_server_logs":
        if set(arguments) - {"lines"}:
            raise AiActionValidationError("Log-Tool hat ungueltige Argumente")
        lines = arguments.get("lines", 100)
        if not isinstance(lines, int) or isinstance(lines, bool) or not 1 <= lines <= 200:
            raise AiActionValidationError("Ungueltige Log-Zeilenanzahl")
        from services import docker_service
        from services.node_service import is_node_offline

        # docker_service.logs() liefert bei einem nicht erreichbaren Node
        # denselben leeren String wie bei einem Container ohne Ausgabe. Ohne
        # diese Unterscheidung wuerde das Modell "keine Fehler gefunden"
        # antworten, obwohl es in Wahrheit gar nichts gelesen hat.
        if is_node_offline(server.node):
            return {
                "server_id": server.id,
                "lines_requested": lines,
                "content": "",
                "available": False,
                "reason": "node_unreachable",
            }
        if not server.container_name:
            return {
                "server_id": server.id,
                "lines_requested": lines,
                "content": "",
                "available": False,
                "reason": "container_missing",
            }
        content = docker_service.logs(server.container_name, lines=lines, node=server.node)
        redacted = redact_sensitive_text(content)
        return {
            "server_id": server.id,
            "lines_requested": lines,
            "content": redacted[-MAX_LOG_CHARS:],
            "available": True,
            "truncated": len(redacted) > MAX_LOG_CHARS,
            "redacted": redacted != content,
        }
    if set(arguments) != {"path"} or not permission_service.has_server_permission(
        db, user, server.id, "server.files.read"
    ):
        raise AiActionValidationError("Config-Lesezugriff ist nicht erlaubt")
    path = _config_path(arguments["path"])
    result = read_server_text(db, server_id=server.id, relative_path=path)
    content = str(result["content"])
    redacted = redact_sensitive_text(content)
    truncated = len(redacted) > MAX_READ_CONFIG_CHARS
    was_redacted = redacted != content
    # Die Revision ist die Zusage "du hast den aktuellen Stand vollstaendig und
    # unveraendert gesehen". Eine redigierte oder gekuerzte Ansicht ist das
    # nicht: ein darauf aufgebauter Vorschlag wuerde echte Zugangsdaten durch
    # den Platzhalter ersetzen bzw. die Datei hinter dem Limit abschneiden.
    # Ohne Revision kommt propose_config_update fuer diese Datei nicht durch.
    editable = not (truncated or was_redacted)
    return {
        "path": path,
        "revision": result["revision"] if editable else None,
        "content": redacted[:MAX_READ_CONFIG_CHARS],
        "truncated": truncated,
        "redacted": was_redacted,
        "editable": editable,
        **(
            {}
            if editable
            else {
                "edit_blocked_reason": (
                    "Diese Datei kann nicht automatisch geaendert werden, weil sie "
                    "gekuerzt oder redigiert gelesen wurde. Bitte die Aenderung im "
                    "Dateimanager vornehmen."
                )
            }
        ),
    }


class AiActionValidationError(ValueError):
    pass


class AiActionStateError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _aad(proposal_id: str) -> str:
    return f"msm:ai:action-proposal:v1:{proposal_id}"


def _json_object(value: str) -> dict:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AiActionStateError("AI_ACTION_PAYLOAD_INVALID") from exc
    if not isinstance(decoded, dict):
        raise AiActionStateError("AI_ACTION_PAYLOAD_INVALID")
    return decoded


def _permission_for(tool_name: str, payload: dict) -> str:
    if tool_name == "propose_server_lifecycle":
        return {
            "start": "server.start",
            "stop": "server.stop",
            "restart": "server.restart",
        }.get(str(payload.get("operation")), "")
    if tool_name == "propose_backup":
        return "server.backups.create"
    if tool_name == "propose_config_update":
        return "server.files.write"
    if tool_name == "propose_mod_install":
        return "server.mods.write"
    if tool_name == "propose_bind_ip_update":
        return "server.network.manage"
    return ""


def _require_tool_permission(
    db: Session, user: User, server_id: int | None, tool_name: str, payload: dict
) -> None:
    if tool_name in GLOBAL_WRITE_TOOLS:
        # Servererstellung ist global gerechtet, genau wie im Panel. Ein
        # server-scoped Recht gibt es dafuer nicht — es gibt ja noch keinen
        # Server, auf den es sich beziehen koennte.
        if not permission_service.has_global_permission(db, user, "servers.create"):
            raise AiActionValidationError("AI-Aktion ist nicht erlaubt")
        return
    if server_id is None:
        raise AiActionValidationError("AI-Aktion ist nicht erlaubt")
    permission = _permission_for(tool_name, payload)
    if not permission or not permission_service.has_server_permission(
        db, user, server_id, permission
    ):
        raise AiActionValidationError("AI-Aktion ist nicht erlaubt")
    if tool_name == "propose_config_update" and not permission_service.has_server_permission(
        db, user, server_id, "server.files.read"
    ):
        raise AiActionValidationError("Config-Vorschlag benoetigt Lese- und Schreibrecht")


def _config_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or "\\" in value:
        raise AiActionValidationError("Ungueltiger Config-Pfad")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() not in CONFIG_EXTENSIONS:
        raise AiActionValidationError("Config-Pfad ist nicht erlaubt")
    return path.as_posix()


def _config_payload(db: Session, server_id: int, arguments: dict) -> tuple[dict, dict, str | None]:
    if set(arguments) != {"path", "content", "expected_revision"}:
        raise AiActionValidationError("Config-Tool hat ungueltige Argumente")
    path = _config_path(arguments["path"])
    content = arguments["content"]
    expected = arguments["expected_revision"]
    if not isinstance(content, str) or len(content) > MAX_CONFIG_CHARS:
        raise AiActionValidationError("Config-Inhalt ist zu gross oder ungueltig")
    if redact_sensitive_text(content) != content:
        raise AiActionValidationError("Config-Vorschlag enthaelt moegliche Zugangsdaten")
    if expected is not None and (
        not isinstance(expected, str) or not expected.startswith("sha256:") or len(expected) != 71
    ):
        raise AiActionValidationError("Ungueltige Config-Revision")

    try:
        current = read_server_text(db, server_id=server_id, relative_path=path)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        current = None
    current_revision = str(current["revision"]) if current is not None else None
    if expected is None and current is not None:
        # read_config gibt fuer gekuerzte oder redigierte Dateien bewusst keine
        # Revision aus. Ein Vorschlag ohne Revision auf eine existierende Datei
        # kann daher nur aus einer unvollstaendigen Sicht stammen.
        raise AiActionValidationError(
            "Diese Datei kann nicht automatisch geaendert werden, weil sie nicht "
            "vollstaendig gelesen werden konnte"
        )
    if current_revision != expected:
        raise AiActionValidationError("Config wurde seit der Analyse veraendert")
    old_content = str(current["content"]) if current is not None else ""
    # Unabhaengige zweite Schranke: eine Datei mit erkennbaren Zugangsdaten wird
    # nie durch einen KI-Vorschlag ueberschrieben. Das gilt auch dann, wenn der
    # Vorschlag auf einem anderen Weg als read_config entstanden ist.
    if redact_sensitive_text(old_content) != old_content:
        raise AiActionValidationError(
            "Diese Datei enthaelt moegliche Zugangsdaten und wird nicht automatisch geaendert"
        )
    # Auch entfernte Zeilen koennen Zugangsdaten enthalten. Deshalb wird nur
    # aus redigierten Inhalten eine sichtbare Vorschau erzeugt.
    preview_old_content = redact_sensitive_text(old_content)
    preview_content = redact_sensitive_text(content)
    diff_lines = list(difflib.unified_diff(
        preview_old_content.splitlines(),
        preview_content.splitlines(),
        fromfile=f"{path}:vorher",
        tofile=f"{path}:nachher",
        lineterm="",
    ))
    truncated = len(diff_lines) > MAX_DIFF_LINES
    diff = "\n".join(diff_lines[:MAX_DIFF_LINES])[:MAX_DIFF_CHARS]
    preview = {
        "path": path,
        "change": "create" if current is None else "update",
        "diff": diff,
        "diff_truncated": truncated or len("\n".join(diff_lines[:MAX_DIFF_LINES])) > MAX_DIFF_CHARS,
        "restart_required": True,
    }
    return {
        "path": path,
        "content": content,
        "create_only": current is None,
    }, preview, current_revision


def _rationale(arguments: dict, *, fallback: tuple[str, str] | None) -> tuple[str, str]:
    """Zieht Begruendung und erwartete Wirkung aus den Tool-Argumenten.

    Zielpunkt 3.6 verlangt beides in der Vorschau. Der Text stammt vom Modell,
    ist also unvertrauenswuerdig — er wird redigiert und gekuerzt und niemals
    als Zusicherung dargestellt.

    Ein Skill-Schritt liefert stattdessen einen `fallback`: dort ist die
    Herkunft ("Schritt 2 aus Skill X, Version 3") die ehrlichere Begruendung als
    ein Satz, den ein Modell gerade formuliert hat.
    """
    values = []
    for index, key in enumerate(("reason", "expected_effect")):
        raw = arguments.get(key)
        if not isinstance(raw, str) or not raw.strip():
            if fallback is None:
                raise AiActionValidationError(f"Der Vorschlag braucht eine Angabe zu '{key}'")
            values.append(fallback[index][:MAX_REASON_CHARS])
            continue
        values.append(redact_sensitive_text(raw.strip())[:MAX_REASON_CHARS])
    return values[0], values[1]


def _server_create_payload(db: Session, arguments: dict) -> tuple[dict, dict]:
    """Prueft die Argumente einer Servererstellung gegen das Panel-Schema.

    Die eigentliche Validierung — Blueprint, Kapazitaet, Ports, Rechte — macht
    `server_provisioning_service`. Hier wird nur so weit geprueft, dass ein
    offensichtlich unbrauchbarer Vorschlag gar nicht erst entsteht.
    """
    from games import get_plugin
    from models import Node

    expected = {
        "name", "game_type", "ram_limit_mb", "cpu_limit_percent", "disk_limit_gb",
        "reason", "expected_effect",
    }
    if not expected.issubset(set(arguments)) or set(arguments) - (expected | {"node_id"}):
        raise AiActionValidationError("Servererstellung hat ungueltige Argumente")

    name = arguments["name"]
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 128:
        raise AiActionValidationError("Ungueltiger Servername")
    name = redact_sensitive_text(name.strip())

    game_type = arguments["game_type"]
    if not isinstance(game_type, str) or get_plugin(game_type) is None:
        raise AiActionValidationError("Unbekannter Servertyp")

    limits: dict[str, int] = {}
    for key, low, high in (
        ("ram_limit_mb", 512, 4_194_304),
        ("cpu_limit_percent", 10, 3_200),
        ("disk_limit_gb", 1, 1_048_576),
    ):
        value = arguments[key]
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise AiActionValidationError(f"Ungueltiger Wert fuer {key}")
        limits[key] = value

    node_id = arguments.get("node_id")
    if node_id is not None:
        if not isinstance(node_id, int) or isinstance(node_id, bool):
            raise AiActionValidationError("Ungueltige Node-Kennung")
        if db.query(Node).filter(Node.id == node_id).first() is None:
            raise AiActionValidationError("Unbekannte Node")

    payload = {"name": name, "game_type": game_type, "node_id": node_id, **limits}
    preview = {
        "operation": "create_server",
        "name": name,
        "game_type": game_type,
        **limits,
        "node_id": node_id,
        # Ports und Installationsverzeichnis vergibt MSM. Eine Vorschau, die
        # konkrete Ports nennt, waere eine Zusage, die erst die Portvergabe
        # einloesen kann — und die kann bis dahin belegt sein.
        "ports": "auto",
        "restart_required": False,
    }
    return payload, preview


def _bind_ip_payload(db: Session, server: Server, arguments: dict) -> tuple[dict, dict]:
    """Prueft eine vorgeschlagene Bind-IP, bevor der Vorschlag ueberhaupt entsteht.

    Die Pruefung laeuft bewusst schon hier und nicht erst bei der Ausfuehrung:
    ein Vorschlag, der garantiert scheitert, soll dem Benutzer gar nicht erst
    zur Bestaetigung vorgelegt werden. Vor der Ausfuehrung wird sie trotzdem
    wiederholt — zwischen Vorschlag und Klick koennen Minuten liegen.
    """
    from services.server_network_service import BindIpRejected, assert_bind_ip_usable

    if set(arguments) != {"bind_ip"}:
        raise AiActionValidationError("Netzwerk-Tool hat ungueltige Argumente")
    bind_ip = arguments["bind_ip"]
    if not isinstance(bind_ip, str) or not bind_ip.strip():
        raise AiActionValidationError("Ungueltige Bind-IP")
    bind_ip = bind_ip.strip()
    if bind_ip == (server.public_bind_ip or ""):
        raise AiActionValidationError("Diese Bind-IP ist bereits eingestellt")

    try:
        assert_bind_ip_usable(db, server, bind_ip)
    except BindIpRejected as exc:
        raise AiActionValidationError(exc.detail) from exc

    from services.server_network_diagnostics import _classify_bind_ip

    return {"bind_ip": bind_ip}, {
        "operation": "bind_ip_update",
        "current_bind_ip": server.public_bind_ip,
        "new_bind_ip": bind_ip,
        "current_kind": _classify_bind_ip(server.public_bind_ip)["kind"],
        "new_kind": _classify_bind_ip(bind_ip)["kind"],
        "current_status": server.status,
        # Ein laufender Server wird dabei gestoppt und neu angelegt — das muss
        # in der Vorschau stehen, nicht in der Ueberraschung danach.
        "restart_required": server.status == "running",
    }


def _mod_install_payload(db: Session, server: Server, arguments: dict) -> tuple[dict, dict]:
    """Erwartet die Argumente *ohne* Begruendung und ohne `server_id`."""
    from games import get_plugin
    from models import Mod

    if set(arguments) != {"workshop_id", "action"}:
        raise AiActionValidationError("Mod-Tool hat ungueltige Argumente")
    workshop_id = arguments["workshop_id"]
    if not isinstance(workshop_id, str) or not workshop_id.isdigit() or len(workshop_id) > 20:
        raise AiActionValidationError("Ungueltige Workshop-Kennung")
    action = arguments["action"]
    if action not in {"install", "update", "reinstall"}:
        raise AiActionValidationError("Ungueltige Mod-Aktion")

    plugin = get_plugin(server.game_type)
    if plugin is None or not getattr(plugin, "supports_mods", False):
        raise AiActionValidationError("Dieses Spiel unterstuetzt keine Workshop-Mods")

    existing = (
        db.query(Mod)
        .filter(Mod.server_id == server.id, Mod.workshop_id == workshop_id)
        .first()
    )
    payload = {"workshop_id": workshop_id, "action": action}
    preview = {
        "operation": f"mod_{action}",
        "workshop_id": workshop_id,
        "known_name": redact_sensitive_text(str(existing.name or ""))[:128] if existing else None,
        "already_installed": existing is not None,
        "current_status": server.status,
        # Eine Mod wird beim Start geladen — ohne Neustart wirkt sie nicht.
        "restart_required": True,
    }
    return payload, preview


def create_proposal(
    db: Session,
    *,
    user: User,
    conversation: AiConversation,
    tool_name: str,
    arguments: dict,
    correlation_id: str,
    rationale_fallback: tuple[str, str] | None = None,
) -> AiActionProposal:
    if tool_name not in WRITE_TOOLS:
        raise AiActionValidationError("Tool ist in diesem Kontext nicht erlaubt")
    reason, expected_effect = _rationale(arguments, fallback=rationale_fallback)
    rest = {key: value for key, value in arguments.items() if key not in {"reason", "expected_effect"}}

    server: Server | None = None
    if tool_name in GLOBAL_WRITE_TOOLS:
        payload, preview = _server_create_payload(db, arguments)
        expected_revision = None
    else:
        # Dieselbe zentrale Rechtepruefung wie bei den Lesewerkzeugen. `rest`
        # verliert dabei die `server_id`, damit die nachfolgenden
        # Argumentpruefungen ihre exakten Schluesselmengen behalten.
        server, rest = _resolve_server(db, user, rest)

        if tool_name == "propose_server_lifecycle":
            if set(rest) != {"operation"} or rest.get("operation") not in {"start", "stop", "restart"}:
                raise AiActionValidationError("Ungueltige Lifecycle-Aktion")
            payload = {"operation": rest["operation"]}
            preview = {
                "operation": rest["operation"],
                "current_status": server.status,
                "restart_required": rest["operation"] == "restart",
            }
            expected_revision = None
        elif tool_name == "propose_backup":
            if rest:
                raise AiActionValidationError("Backup-Tool akzeptiert keine Argumente")
            payload = {}
            preview = {
                "operation": "backup",
                "current_status": server.status,
                "restart_required": False,
            }
            expected_revision = None
        elif tool_name == "propose_bind_ip_update":
            payload, preview = _bind_ip_payload(db, server, rest)
            expected_revision = None
        elif tool_name == "propose_mod_install":
            payload, preview = _mod_install_payload(db, server, rest)
            expected_revision = None
        else:
            payload, preview, expected_revision = _config_payload(db, server.id, rest)

    preview["reason"] = reason
    preview["expected_effect"] = expected_effect
    server_id = server.id if server is not None else None
    _require_tool_permission(db, user, server_id, tool_name, payload)
    proposal_id = str(uuid4())
    encrypted = DisClient.encrypt(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        aad=_aad(proposal_id),
    )
    # Spaeter Import: `ai_autonomy_service` liest `ALWAYS_CONFIRM_TOOLS` aus
    # diesem Modul und wuerde beim Modulimport einen Zirkel bilden.
    from services.ai_autonomy_service import autonomy_allows

    autonomous = autonomy_allows(db, user=user, server_id=server_id, tool_name=tool_name)
    proposal = AiActionProposal(
        id=proposal_id,
        conversation_id=conversation.id,
        user_id=user.id,
        server_id=server_id,
        tool_name=tool_name,
        payload_encrypted=encrypted,
        preview_json=json.dumps(preview, ensure_ascii=True, separators=(",", ":")),
        expected_revision=expected_revision,
        # Autonomie entfernt genau eine Sache: die menschliche Bestaetigung.
        # Jede Rechtepruefung, der Server-Mutex und das Audit bleiben.
        requires_confirmation=not autonomous,
        autonomous=autonomous,
        reason=reason,
        expected_effect=expected_effect,
        correlation_id=str(UUID(correlation_id)),
    )
    db.add(proposal)
    db.flush()
    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="ai.action.proposed",
        target_type="server" if server_id is not None else "ai_action",
        target_id=server_id,
        details={
            "proposal_id": proposal.id,
            "tool": tool_name,
            **({"autonomous": True} if autonomous else {}),
        },
        origin="ai",
        correlation_id=proposal.correlation_id,
    )
    return proposal


def owned_proposal(db: Session, proposal_id: str, user: User) -> AiActionProposal | None:
    try:
        canonical = str(UUID(proposal_id))
    except (TypeError, ValueError, AttributeError):
        return None
    proposal = db.query(AiActionProposal).filter(
        AiActionProposal.id == canonical,
        AiActionProposal.user_id == user.id,
    ).first()
    if proposal is None:
        return None
    if proposal.server_id is None:
        # Ein Erstellungsvorschlag hat noch keinen Server, gegen den sich
        # `server.view` pruefen liesse. Die Grenze ist hier das globale Recht —
        # dasselbe, das die Ausfuehrung spaeter erneut verlangt.
        if not permission_service.has_global_permission(db, user, "servers.create"):
            return None
        return proposal
    if not permission_service.has_server_permission(
        db, user, proposal.server_id, "server.view"
    ):
        return None
    return proposal


def _lock_proposal(db: Session, proposal_id: str) -> AiActionProposal:
    """Laedt eine Proposal-Zeile gesperrt und garantiert frisch aus der Datenbank.

    `with_for_update()` sperrt zwar die Zeile, liefert ohne `populate_existing()`
    aber das bereits geladene Objekt aus der Identity Map zurueck — also den
    Stand *vor* der Sperre. Genau dadurch konnten zwei parallele Execute-Aufrufe
    denselben Einmal-Token als noch gueltig sehen.
    """
    return (
        db.query(AiActionProposal)
        .filter(AiActionProposal.id == proposal_id)
        .populate_existing()
        .with_for_update()
        .one()
    )


def confirm_proposal(
    db: Session, *, proposal_id: str, user: User, now: datetime | None = None
) -> tuple[AiActionProposal, str]:
    proposal = owned_proposal(db, proposal_id, user)
    if proposal is None:
        raise AiActionStateError("AI_ACTION_NOT_FOUND")
    proposal = _lock_proposal(db, proposal.id)
    if proposal.status != "proposed":
        raise AiActionStateError("AI_ACTION_NOT_PROPOSED")
    payload = _json_object(DisClient.decrypt(proposal.payload_encrypted, aad=_aad(proposal.id)))
    try:
        _require_tool_permission(db, user, proposal.server_id, proposal.tool_name, payload)
    except AiActionValidationError as exc:
        raise AiActionStateError("AI_ACTION_ACCESS_REVOKED") from exc
    token = secrets.token_urlsafe(32)
    current = now or datetime.now(timezone.utc)
    proposal.confirmation_token_hash = hashlib.sha256(token.encode()).hexdigest()
    proposal.confirmation_expires_at = current + CONFIRMATION_TTL
    proposal.confirmed_at = current
    proposal.status = "confirmed"
    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="ai.action.confirmed",
        target_type="server" if proposal.server_id is not None else "ai_action",
        target_id=proposal.server_id,
        details={
            "proposal_id": proposal.id,
            "tool": proposal.tool_name,
            # Bei einer autonomen Aktion hat kein Mensch zugestimmt. Das im
            # Audit als "confirmed: true" zu fuehren waere schlicht falsch.
            "confirmed": not proposal.autonomous,
            **({"autonomous": True} if proposal.autonomous else {}),
        },
        origin="ai",
        correlation_id=proposal.correlation_id,
    )
    db.commit()
    db.refresh(proposal)
    return proposal, token


def execute_autonomously(
    db: Session, *, proposal_id: str, user: User
) -> tuple[AiActionProposal, dict]:
    """Fuehrt einen autonom freigegebenen Vorschlag ohne Rueckfrage aus.

    Bewusst ueber dieselben zwei Schritte wie eine bestaetigte Aktion, statt an
    ihnen vorbei: `confirm_proposal` prueft die Rechte erneut und erzeugt den
    Einmal-Token, `execute_proposal` prueft ein drittes Mal, nimmt den
    Server-Mutex und entwertet den Token atomar. Autonomie ersetzt genau einen
    Schritt — den Klick des Menschen — und keinen einzigen der Schutzmechanismen.
    """
    proposal = owned_proposal(db, proposal_id, user)
    if proposal is None:
        raise AiActionStateError("AI_ACTION_NOT_FOUND")
    if not proposal.autonomous or proposal.requires_confirmation:
        raise AiActionStateError("AI_ACTION_NOT_AUTONOMOUS")
    _, token = confirm_proposal(db, proposal_id=proposal_id, user=user)
    return execute_proposal(
        db, proposal_id=proposal_id, user=user, confirmation_token=token
    )


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _execute_server_create(
    db: Session, *, user: User, payload: dict, correlation_id: str, proposal_id: str
) -> tuple[dict, int, str | None]:
    """Erstellt den Server ueber den gemeinsamen Provisionierungsservice.

    Zielpunkt 10 ist hier die Leitplanke: es darf keinen zweiten Weg geben, einen
    Server anzulegen. Deshalb wird genau derselbe Service aufgerufen wie beim
    Klick im Panel und bei einer Shop-Bestellung — inklusive Blueprintpruefung,
    Kapazitaets- und Portvergabe, Installationsstart und kompensierendem
    Rollback. Die KI liefert nur die Wunschwerte.

    Der Idempotency-Key ist die Vorschlags-ID. Ein zweiter Ausfuehrungsversuch
    desselben Vorschlags trifft damit dieselbe Task und erzeugt keinen zweiten
    Server.
    """
    from schemas import ServerCreate
    from services.server_provisioning_service import provision_server

    request = ServerCreate(
        name=str(payload["name"]),
        game_type=str(payload["game_type"]),
        cpu_limit_percent=int(payload["cpu_limit_percent"]),
        ram_limit_mb=int(payload["ram_limit_mb"]),
        disk_limit_gb=int(payload["disk_limit_gb"]),
        node_id=payload.get("node_id"),
    )
    result = provision_server(
        db,
        request,
        ActorContext.for_user(user, origin="ai", correlation_id=correlation_id),
        idempotency_key=f"ai-{proposal_id}",
    )
    return (
        {
            "server_id": result.server.id,
            "task_id": result.task.id,
            "status": result.server.status,
            "installation": "running",
        },
        result.server.id,
        result.task.id,
    )


def _execute_bind_ip_update(db: Session, *, server_id: int, payload: dict) -> dict:
    """Setzt die Bind-IP und baut die Netzwerkregeln neu auf.

    Die Pruefung wird hier wiederholt, obwohl sie beim Anlegen des Vorschlags
    schon lief: zwischen Vorschlag und Bestaetigung koennen Minuten liegen, und
    in der Zeit kann ein anderer Server denselben Port belegt oder ein
    Interface verschwunden sein.

    Der Neuaufbau laeuft ueber dieselbe Funktion wie der Netzwerk-Tab. Es gibt
    keinen KI-Sonderweg — genau das verlangt Zielpunkt 10.
    """
    from services.server_network_service import (
        BindIpRejected,
        assert_bind_ip_usable,
        recreate_server_network,
    )

    server = db.get(Server, server_id)
    if server is None:
        raise AiActionStateError("AI_ACTION_TARGET_MISSING")
    bind_ip = str(payload["bind_ip"])
    old_bind_ip = server.public_bind_ip
    old_ports = [(row.port, row.protocol, row.role) for row in server.ports]

    try:
        assert_bind_ip_usable(db, server, bind_ip)
    except BindIpRejected as exc:
        logger.info("Bind-IP-Aenderung abgelehnt code=%s", exc.code)
        raise AiActionStateError("AI_ACTION_BIND_IP_REJECTED") from exc

    server.public_bind_ip = bind_ip
    # Guardian vergleicht den gewuenschten mit dem beobachteten Zustand. Ohne
    # diese Marke wuerde er die Aenderung als Abweichung melden.
    from services.guardian_state_service import mark_guardian_configuration_changed

    mark_guardian_configuration_changed(server)
    db.commit()

    restarted = recreate_server_network(server, old_ports, old_bind_ip)
    return {
        "bind_ip": bind_ip,
        "previous_bind_ip": old_bind_ip,
        "restarted": restarted,
    }


def _execute_mod_install(db: Session, *, server_id: int, payload: dict) -> dict:
    """Stoesst die Mod-Installation ueber den vorhandenen Panel-Pfad an.

    Zielpunkt 16 bleibt dadurch unangetastet: es entsteht kein eigener
    Downloadbereich und keine Archivuebernahme der KI. Genutzt wird
    `install_mod_bg` mit seinem Install-Lock, seiner Statusfuehrung und seiner
    Fehlerbehandlung — derselbe Code, den auch der Mod-Tab ausloest.
    """
    from models import Mod
    from routers.mods import install_mod_bg
    from services.mod_install_status_service import INSTALL_RUNNING
    import threading

    workshop_id = str(payload["workshop_id"])
    action = str(payload["action"])

    running = (
        db.query(Mod)
        .filter(
            Mod.server_id == server_id,
            Mod.workshop_id == workshop_id,
            Mod.install_status == INSTALL_RUNNING,
        )
        .first()
    )
    if running is not None:
        raise AiActionStateError("AI_ACTION_SERVER_BUSY")

    existing = (
        db.query(Mod)
        .filter(Mod.server_id == server_id, Mod.workshop_id == workshop_id)
        .first()
    )
    if existing is None:
        if action != "install":
            raise AiActionStateError("AI_ACTION_EXECUTION_FAILED")
        db.add(Mod(server_id=server_id, workshop_id=workshop_id, install_status="pending"))
        db.commit()

    # Bewusst ein eigener Thread und keine BackgroundTasks: dieser Pfad haengt
    # nicht an einer Request-Session, sondern kann auch aus einem autonomen Lauf
    # kommen. `install_mod_bg` oeffnet seine eigene Session.
    threading.Thread(
        target=install_mod_bg,
        args=(server_id, workshop_id, action),
        daemon=True,
        name=f"ai-mod-{action}-{server_id}",
    ).start()
    return {
        "server_id": server_id,
        "workshop_id": workshop_id,
        "action": action,
        "installation": "running",
    }


def execute_proposal(
    db: Session,
    *,
    proposal_id: str,
    user: User,
    confirmation_token: str,
    now: datetime | None = None,
) -> tuple[AiActionProposal, dict]:
    proposal = owned_proposal(db, proposal_id, user)
    if proposal is None:
        raise AiActionStateError("AI_ACTION_NOT_FOUND")
    proposal = _lock_proposal(db, proposal.id)
    # Feste Kopien, damit die spaetere Fehlerbehandlung nach einem Rollback
    # nicht auf ein abgelaufenes ORM-Objekt zugreifen muss.
    row_id = proposal.id
    server_id = proposal.server_id
    tool_name = proposal.tool_name
    correlation_id = proposal.correlation_id
    expected_revision = proposal.expected_revision
    current = now or datetime.now(timezone.utc)
    token_hash = hashlib.sha256(confirmation_token.encode()).hexdigest()
    if proposal.status != "confirmed" or not proposal.confirmation_token_hash:
        raise AiActionStateError("AI_ACTION_NOT_CONFIRMED")
    if proposal.confirmation_expires_at is None or _utc(proposal.confirmation_expires_at) <= current:
        proposal.status = "expired"
        proposal.confirmation_token_hash = None
        db.commit()
        raise AiActionStateError("AI_ACTION_CONFIRMATION_EXPIRED")
    if not hmac.compare_digest(proposal.confirmation_token_hash, token_hash):
        raise AiActionStateError("AI_ACTION_CONFIRMATION_INVALID")
    active_user = db.query(User).filter(User.id == user.id, User.is_active.is_(True)).first()
    if active_user is None:
        raise AiActionStateError("AI_ACTION_ACCESS_REVOKED")
    payload = _json_object(DisClient.decrypt(proposal.payload_encrypted, aad=_aad(proposal.id)))
    try:
        _require_tool_permission(db, active_user, proposal.server_id, proposal.tool_name, payload)
    except AiActionValidationError as exc:
        raise AiActionStateError("AI_ACTION_ACCESS_REVOKED") from exc

    # Der Server-Mutex wird VOR dem Verbrauch des Einmal-Tokens geholt. Vorher
    # entwertete ein nur kurz belegter Server die Bestaetigung dauerhaft: der
    # Token war bereits geloescht, der Vorschlag wurde als `failed` abgelegt und
    # der Benutzer musste ohne fachlichen Grund neu bestaetigen.
    lock = None
    if tool_name in _MUTEX_TOOLS:
        from services.server_lifecycle_service import get_server_lifecycle_lock

        lock = get_server_lifecycle_lock(server_id)
        if not lock.acquire(blocking=False):
            raise AiActionStateError("AI_ACTION_SERVER_BUSY")
    try:
        # Atomarer Einmal-Verbrauch. Das bedingte UPDATE gewinnt genau einmal,
        # unabhaengig davon ob die Datenbank Zeilensperren unterstuetzt.
        consumed = (
            db.query(AiActionProposal)
            .filter(
                AiActionProposal.id == row_id,
                AiActionProposal.status == "confirmed",
                AiActionProposal.confirmation_token_hash == token_hash,
            )
            .update(
                {"status": "executing", "confirmation_token_hash": None},
                synchronize_session=False,
            )
        )
        db.commit()
        if consumed != 1:
            raise AiActionStateError("AI_ACTION_NOT_CONFIRMED")

        try:
            if tool_name == "propose_server_lifecycle":
                from services.server_action_service import request_lifecycle_operation

                result = request_lifecycle_operation(
                    db,
                    server_id=server_id,
                    operation=str(payload["operation"]),
                    actor=ActorContext.for_user(
                        active_user, origin="ai", correlation_id=correlation_id
                    ),
                    idempotency_key=row_id,
                )
                task_id = result.get("task_id")
                # Start/Stop/Restart laufen in einem Hintergrund-Thread weiter.
                # Zum Zeitpunkt dieser Antwort ist die Aktion nur eingereiht,
                # nicht ausgefuehrt. Der Vorschlag bleibt deshalb "executing";
                # den Endzustand setzt `finish_lifecycle_task`, sobald der
                # Vorgang wirklich fertig ist. Ein bereits abgeschlossener Task
                # (Wiederverwendung derselben Idempotency-ID) bleibt terminal.
                queued = result.get("status") == "queued"
            elif tool_name == "propose_backup":
                from services.backup_orchestrator import create_server_backup

                backup = create_server_backup(server_id, db, name="AI-confirmed snapshot")
                result = {"backup_id": backup.id}
                task_id = None
                queued = False
            elif tool_name == "propose_config_update":
                result = write_server_text(
                    db,
                    user=active_user,
                    server_id=server_id,
                    relative_path=str(payload["path"]),
                    content=str(payload["content"]),
                    expected_revision=expected_revision,
                    create_only=bool(payload.get("create_only")),
                )
                task_id = None
                queued = False
            elif tool_name == "propose_bind_ip_update":
                result = _execute_bind_ip_update(
                    db, server_id=server_id, payload=payload
                )
                task_id = None
                queued = False
            elif tool_name == "propose_mod_install":
                # Anders als beim Lifecycle gibt es fuer den Mod-Download keinen
                # Rueckkanal, der den Vorschlag spaeter abschliesst. Ein
                # dauerhaftes "executing" waere deshalb kein ehrlicherer Zustand,
                # sondern ein fuer immer offener Vorgang. Abgeschlossen ist hier
                # das, was der Vorschlag zugesagt hat: die Installation ist
                # angestossen. Ihren Ausgang traegt die Mod-Zeile.
                result = _execute_mod_install(db, server_id=server_id, payload=payload)
                task_id = None
                queued = False
            elif tool_name == "propose_server_create":
                # Ebenso: `provision_server` kehrt zurueck, sobald der Server
                # existiert und die Installation laeuft — exakt der Punkt, an dem
                # auch `POST /api/servers` dem Panel antwortet. Der weitere
                # Verlauf haengt an der Operation-Task, deren ID mitgegeben wird.
                result, created_server_id, task_id = _execute_server_create(
                    db, user=active_user, payload=payload, correlation_id=correlation_id,
                    proposal_id=row_id,
                )
                server_id = created_server_id
                queued = False
            else:
                raise AiActionStateError("AI_ACTION_TOOL_NOT_ALLOWED")

            proposal = db.get(AiActionProposal, row_id)
            if proposal is None:
                raise AiActionStateError("AI_ACTION_NOT_FOUND")
            # "succeeded" bedeutet: die Aktion ist fertig. Fuer eine nur
            # eingereihte Lifecycle-Aktion waere das eine Behauptung ueber einen
            # Ausgang, der noch gar nicht feststeht.
            proposal.status = "executing" if queued else "succeeded"
            proposal.task_id = task_id
            proposal.executed_at = None if queued else datetime.now(timezone.utc)
            # Ein Erstellungsvorschlag bekommt jetzt seinen Server. Danach ist er
            # ueber `server.view` adressierbar wie jeder andere Vorschlag.
            if proposal.server_id is None and server_id is not None:
                proposal.server_id = server_id
            audit_service.record_privileged_action(
                db,
                user_id=active_user.id,
                action="ai.action.executed",
                target_type="server",
                target_id=server_id,
                details={
                    "proposal_id": row_id,
                    "tool": tool_name,
                    "confirmed": True,
                    "succeeded": not queued,
                    **({"queued": True} if queued else {}),
                    **({"task_id": task_id} if task_id else {}),
                },
                origin="ai",
                correlation_id=correlation_id,
            )
            db.commit()
            db.refresh(proposal)
            return proposal, result
        except Exception as exc:
            db.rollback()
            failed = db.get(AiActionProposal, row_id)
            if failed is not None:
                failed.status = "failed"
                failed.error_code = (
                    exc.code if isinstance(exc, AiActionStateError) else "AI_ACTION_EXECUTION_FAILED"
                )
                failed.executed_at = datetime.now(timezone.utc)
                audit_service.record_privileged_action(
                    db,
                    user_id=active_user.id,
                    action="ai.action.executed",
                    target_type="server",
                    target_id=server_id,
                    details={
                        "proposal_id": row_id,
                        "tool": tool_name,
                        "confirmed": True,
                        "succeeded": False,
                        "error_code": failed.error_code,
                    },
                    origin="ai",
                    correlation_id=correlation_id,
                )
                db.commit()
            if isinstance(exc, AiActionStateError):
                raise
            if isinstance(exc, HTTPException) and exc.status_code == 409:
                raise AiActionStateError("AI_ACTION_REVISION_CONFLICT") from exc
            raise AiActionStateError("AI_ACTION_EXECUTION_FAILED") from exc
    finally:
        if lock is not None:
            lock.release()


def reconcile_interrupted_actions(db: Session) -> int:
    rows = db.query(AiActionProposal).filter(AiActionProposal.status == "executing").all()
    for row in rows:
        row.status = "failed"
        row.error_code = "AI_ACTION_INTERRUPTED"
        row.executed_at = datetime.now(timezone.utc)
    if rows:
        db.commit()
    return len(rows)
