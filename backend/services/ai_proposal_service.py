"""Der Lebenszyklus eines Vorschlags: pruefen, anlegen, bestaetigen, ausfuehren.

Herausgeloest aus `ai_action_service`, das damit auf die Haelfte schrumpft. Die
beiden Haelften hatten ohnehin kaum Beruehrung — die eine baut den
Werkzeugkatalog und fuehrt Lesezugriffe aus, die andere fuehrt schreibende
Aktionen durch den Bestaetigungsablauf. Geteilt wurden nur zwei Dinge: die
Fehlerarten (jetzt in `ai_action_errors`) und `_resolve_server`.

**Kein Schreibwerkzeug fuehrt hier direkt etwas aus.** Das Modell erzeugt einen
Vorschlag; ausgefuehrt wird er erst, wenn ein Mensch bestaetigt — oder, bei
erteilter Freigabe und nur fuer Werkzeuge ausserhalb von
`ALWAYS_CONFIRM_TOOLS`, autonom. Diese Trennung ist die eigentliche
Sicherheitsgrenze der KI-Schreibseite; der Systemprompt ist es nicht.

Die Nutzlast eines Vorschlags liegt verschluesselt (DIS, AES-256-GCM) mit einer
AAD, die an die Vorschlags-ID gebunden ist: eine in der Datenbank umgeschriebene
Zuordnung macht die Nutzlast unlesbar, statt sie umzuhaengen.
"""

from __future__ import annotations

from datetime import datetime, timezone
import difflib
import hashlib
import hmac
import json
import logging
import secrets
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import AiActionProposal, AiConversation, Server, User
from services import audit_service, permission_service
from services.actor_context import ActorContext
from services.ai_action_errors import AiActionStateError, AiActionValidationError
from services.ai_action_service import (
    CONFIRMATION_TTL,
    MAX_BACKUP_NAME_CHARS,
    MAX_CONFIG_CHARS,
    MAX_DIFF_CHARS,
    MAX_DIFF_LINES,
    MAX_REASON_CHARS,
    _MUTEX_TOOLS,
    _config_path,
    _resolve_server,
    is_binary_text,
)
from services.ai_redaction import redact_sensitive_text
from services.ai_tool_registry import GLOBAL_WRITE_TOOLS, WERKZEUGE, WRITE_TOOLS
from services.dis_client import DisClient
from services.server_file_access_service import read_server_text, write_server_text


logger = logging.getLogger(__name__)

# Fehlte vor der Aufteilung: `logger` war in `ai_action_service` nie definiert,
# die Zeile im Bind-IP-Zweig also ein wartender `NameError`. Statt der sauberen
# Ablehnung "AI_ACTION_BIND_IP_REJECTED" waere ein 500er herausgekommen —
# genau in dem Fall, den die Pruefung abfangen soll. Der Schnitt hat es
# sichtbar gemacht, weil die Datei jetzt klein genug fuer eine Namensanalyse ist.


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
    """Der Permission-Key, den dieses Werkzeug verlangt.

    Er steht in `ai_tool_registry.WERKZEUGE` — dort, wo auch alles andere ueber
    ein Werkzeug steht. Vorher war das hier eine if-Kette: ein zweiter Ort, an
    dem ein neues Werkzeug eingetragen werden musste, und der Ort, an dem man
    es am ehesten vergisst. Ein vergessener Eintrag lieferte den leeren String
    und damit eine Ablehnung — immerhin die sichere Richtung, aber erst
    bemerkbar, wenn ein Benutzer davorsteht.

    Eine Ausnahme bleibt: der Lebenszyklus haengt am *Vorgang*, nicht am
    Werkzeug. Starten, Stoppen und Neustarten sind drei verschiedene Rechte, und
    das laesst sich in einer Tabellenzeile nicht ausdruecken.
    """
    if tool_name == "propose_server_lifecycle":
        return {
            "start": "server.start",
            "stop": "server.stop",
            "restart": "server.restart",
        }.get(str(payload.get("operation")), "")
    werkzeug = WERKZEUGE.get(tool_name)
    return werkzeug.recht if werkzeug and werkzeug.recht else ""


def _require_tool_permission(
    db: Session, user: User, server_id: int | None, tool_name: str, payload: dict
) -> None:
    permission = _permission_for(tool_name, payload)
    if not permission:
        raise AiActionValidationError("AI-Aktion ist nicht erlaubt")

    werkzeug = WERKZEUGE.get(tool_name)
    if werkzeug is not None and werkzeug.recht_global:
        # Manche Rechte sind bewusst global und nicht delegierbar: `servers.create`
        # (es gibt noch keinen Server, auf den sich ein Recht beziehen koennte)
        # und `servers.delete` (destruktiv, nur Admin/Owner).
        #
        # Bei `propose_server_delete` gilt trotzdem **beides**: `_resolve_server`
        # hat vorher `server.view` geprueft, sonst waere die Server-ID ein Weg,
        # die Existenz fremder Server zu erraten. Sehen duerfen und loeschen
        # duerfen sind zwei Huerden, nicht eine.
        if not permission_service.has_global_permission(db, user, permission):
            raise AiActionValidationError("AI-Aktion ist nicht erlaubt")
        return

    if server_id is None:
        raise AiActionValidationError("AI-Aktion ist nicht erlaubt")
    if not permission_service.has_server_permission(db, user, server_id, permission):
        raise AiActionValidationError("AI-Aktion ist nicht erlaubt")
    if tool_name == "propose_config_update" and not permission_service.has_server_permission(
        db, user, server_id, "server.files.read"
    ):
        raise AiActionValidationError("Config-Vorschlag benoetigt Lese- und Schreibrecht")


def _config_payload(db: Session, server_id: int, arguments: dict) -> tuple[dict, dict, str | None]:
    if set(arguments) != {"path", "content", "expected_revision"}:
        raise AiActionValidationError("Config-Tool hat ungueltige Argumente")
    path = _config_path(arguments["path"])
    content = arguments["content"]
    expected = arguments["expected_revision"]
    if not isinstance(content, str) or len(content) > MAX_CONFIG_CHARS:
        raise AiActionValidationError("Datei-Inhalt ist zu gross oder ungueltig")
    if redact_sensitive_text(content) != content:
        raise AiActionValidationError("Dateivorschlag enthaelt moegliche Zugangsdaten")
    if is_binary_text(content):
        # Zweite Schranke neben `read_config`. Dort ist eine Binaerdatei bereits
        # als `editable: false` gekennzeichnet; hier wird sie auch dann
        # abgewiesen, wenn der Vorschlag auf einem anderen Weg entstanden ist.
        # Ein zurueckgeschriebener Ersatzzeichen-Salat ist Datenverlust, kein
        # missglueckter Bearbeitungsversuch.
        raise AiActionValidationError("Dateivorschlag ist kein Text")
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


def _blueprint_change_payload(arguments: dict) -> tuple[dict, dict]:
    """Baut den abgeleiteten Blueprint **schon beim Vorschlagen**.

    Nicht erst beim Ausfuehren, und das ist der Punkt: der Mensch soll sehen,
    was herauskommt, bevor er zustimmt — nicht eine Liste von Aenderungen, deren
    Zusammenwirken er im Kopf nachvollziehen muesste. Ein Vorschlag, dessen
    Ergebnis das Schema verletzt, entsteht damit gar nicht erst; sonst
    scheiterte er nach der Bestaetigung, und jemand haette einer Aenderung
    zugestimmt, die es nicht gibt.
    """
    from services import blueprint_service

    if set(arguments) != {"source_id", "new_id", "changes"}:
        raise AiActionValidationError("Blueprint-Tool hat ungueltige Argumente")
    aenderungen = arguments["changes"]
    if not isinstance(aenderungen, dict) or not aenderungen:
        raise AiActionValidationError("Ein Blueprint-Vorschlag ohne Aenderung ist keiner")
    try:
        nutzlast = blueprint_service.derived_payload(
            str(arguments["source_id"]),
            new_id=str(arguments["new_id"]),
            changes=aenderungen,
        )
    except HTTPException as exc:
        raise AiActionValidationError(str(exc.detail)) from exc

    quelle = blueprint_service.blueprint_view(str(arguments["source_id"]))["blueprint"]
    payload = {"blueprint": nutzlast}
    preview = {
        "operation": "blueprint_change",
        "source_id": arguments["source_id"],
        "new_id": arguments["new_id"],
        # Was sich wirklich unterscheidet — die Zeile, die der Bestaetigende
        # liest. `changes` allein waere die Absicht, nicht das Ergebnis.
        "env_before": (quelle.get("runtime") or {}).get("env") or {},
        "env_after": (nutzlast.get("runtime") or {}).get("env") or {},
        "image_before": (quelle.get("runtime") or {}).get("image"),
        "image_after": (nutzlast.get("runtime") or {}).get("image"),
        "restart_required": False,
    }
    return payload, preview


def _blueprint_switch_payload(server: Server, arguments: dict) -> tuple[dict, dict]:
    """Prueft, ob dieser Server auf diesen Blueprint umgestellt werden kann.

    Zwei Bedingungen, beide aus dem Betrieb begruendet:

    **Der Server muss gestoppt sein.** Ein laufender Container haengt am alten
    Image; ein Wechsel unter ihm weg fuehrt zu einem Zustand, den weder Panel
    noch Guardian einordnen koennen.

    **Die Portrollen muessen uebereinstimmen.** Die vergebenen Ports haengen an
    den Namen (`game`, `query`, `rcon`). Passen sie nicht, bliebe ein belegter
    Port ohne Zuordnung oder ein verlangter ohne Vergabe.
    """
    from services import blueprint_service

    if set(arguments) != {"blueprint_id"}:
        raise AiActionValidationError("Umstell-Tool hat ungueltige Argumente")
    ziel_id = str(arguments["blueprint_id"])
    if ziel_id == server.game_type:
        raise AiActionValidationError("Der Server nutzt diesen Blueprint bereits")
    if server.status != "stopped":
        raise AiActionValidationError(
            "Der Server muss gestoppt sein, bevor er umgestellt werden kann"
        )
    try:
        ziel = blueprint_service.blueprint_view(ziel_id)
        alt = blueprint_service.blueprint_view(server.game_type)
    except HTTPException as exc:
        raise AiActionValidationError(str(exc.detail)) from exc

    hindernis = blueprint_service.switch_incompatibility(
        alt["blueprint"], ziel["blueprint"]
    )
    if hindernis:
        raise AiActionValidationError(hindernis)

    payload = {"blueprint_id": ziel_id}
    preview = {
        "operation": "blueprint_switch",
        "from_blueprint": server.game_type,
        "to_blueprint": ziel_id,
        "env_before": (alt["blueprint"].get("runtime") or {}).get("env") or {},
        "env_after": (ziel["blueprint"].get("runtime") or {}).get("env") or {},
        "current_status": server.status,
        # Der Server bleibt gestoppt. Ihn automatisch zu starten waere ein
        # zweiter Vorgang, den niemand bestaetigt hat.
        "restart_required": True,
    }
    return payload, preview


def _backup_restore_payload(
    db: Session, server: Server, arguments: dict
) -> tuple[dict, dict]:
    """Prueft die Backup-ID und baut die Vorschau fuer die Bestaetigung.

    Die ID wird **hier** gegen den Server aufgeloest, nicht erst beim
    Ausfuehren. Zwei Gruende: ein Vorschlag auf ein Backup eines fremden Servers
    darf gar nicht erst entstehen, und die Vorschau soll nennen, *welchen Stand*
    der Benutzer gleich zurueckholt. "Backup einspielen" ohne Datum ist keine
    Grundlage fuer eine Zustimmung — zwischen dem Backup von gestern und dem von
    letztem Monat liegt der ganze Unterschied.
    """
    if set(arguments) != {"backup_id"}:
        raise AiActionValidationError("Restore-Tool hat ungueltige Argumente")
    backup_id = arguments["backup_id"]
    if not isinstance(backup_id, int) or isinstance(backup_id, bool) or backup_id < 1:
        raise AiActionValidationError("Ungueltige Backup-ID")

    from models import Backup

    backup = (
        db.query(Backup)
        .filter(Backup.id == backup_id, Backup.server_id == server.id)
        .first()
    )
    if backup is None:
        # Bewusst dieselbe Meldung fuer "gibt es nicht" und "gehoert zu einem
        # anderen Server": sonst waere ein Vorschlag ein Weg, fremde Backup-IDs
        # abzuzaehlen.
        raise AiActionValidationError("Backup nicht gefunden")

    payload = {"backup_id": backup.id}
    preview = {
        "operation": "backup_restore",
        "backup_id": backup.id,
        "backup_name": redact_sensitive_text(str(backup.name or ""))[:128] or None,
        "backup_created_at": backup.created_at.isoformat() if backup.created_at else None,
        "size_mb": backup.size_mb,
        "current_status": server.status,
        # Der Server wird gestoppt und **nicht** automatisch wieder gestartet —
        # so verhaelt sich der Restore im Panel auch.
        "restart_required": True,
        "irreversible": True,
    }
    return payload, preview


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
    if tool_name == "propose_blueprint_change":
        payload, preview = _blueprint_change_payload(rest)
        expected_revision = None
    elif tool_name in GLOBAL_WRITE_TOOLS:
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
            if set(rest) - {"name"}:
                raise AiActionValidationError("Backup-Tool hat ungueltige Argumente")
            name = rest.get("name")
            if name is not None and not isinstance(name, str):
                raise AiActionValidationError("Backup-Name ist ungueltig")
            # Der Name ist Modelltext und landet in einer Liste, die Menschen
            # lesen — also redigiert und gekuerzt wie jede andere Modellausgabe.
            sauber = redact_sensitive_text(str(name).strip())[:MAX_BACKUP_NAME_CHARS] if name else ""
            payload = {"name": sauber} if sauber else {}
            preview = {
                "operation": "backup",
                "current_status": server.status,
                "restart_required": False,
                "name": sauber or None,
            }
            expected_revision = None
        elif tool_name == "propose_backup_restore":
            payload, preview = _backup_restore_payload(db, server, rest)
            expected_revision = None
        elif tool_name == "propose_server_blueprint_switch":
            payload, preview = _blueprint_switch_payload(server, rest)
            expected_revision = None
        elif tool_name == "propose_server_delete":
            if rest:
                raise AiActionValidationError("Loesch-Tool akzeptiert keine Argumente")
            # Der Name steht in der Vorschau, nicht in der Nutzlast: er ist das
            # Einzige, woran ein Mensch beim Bestaetigen erkennt, ob der
            # richtige Server gemeint ist. Die Server-ID allein sagt ihm nichts.
            payload = {}
            preview = {
                "operation": "delete",
                "server_name": server.name,
                "current_status": server.status,
                "restart_required": False,
                # Was tatsaechlich verschwindet. Ohne diese Aufzaehlung waere
                # "Server loeschen" eine Zusage, deren Umfang der Bestaetigende
                # raten muesste — Backups und S3-Objekte gehen mit.
                "removes": [
                    "container", "files", "backups", "ports", "database_resources",
                ],
                "irreversible": True,
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

                backup = create_server_backup(
                    server_id,
                    db,
                    # Ohne eigenen Namen bleibt der bisherige Standard stehen:
                    # er sagt in der Backup-Liste wenigstens, woher der Eintrag
                    # stammt.
                    name=str(payload.get("name") or "AI-confirmed snapshot"),
                )
                result = {"backup_id": backup.id}
                task_id = None
                queued = False
            elif tool_name == "propose_backup_restore":
                # Derselbe Aufruf wie der Panel-Endpunkt. Die Reihenfolge darin
                # ist der Grund, warum die KI keinen eigenen Weg bekommt:
                # S3-Download und Entschluesselung laufen **vor** dem
                # Container-Stop, damit ein falsches Passwort den Server
                # unberuehrt laesst.
                from services.backup_restore_service import restore_server_backup

                result = restore_server_backup(
                    db,
                    server_id=server_id,
                    backup_id=int(payload["backup_id"]),
                    actor=ActorContext.for_user(
                        active_user, origin="ai", correlation_id=correlation_id
                    ),
                )
                task_id = None
                queued = False
            elif tool_name == "propose_server_blueprint_switch":
                # Der eigentliche Wechsel ist eine Zeile. Alles, was ihn
                # gefaehrlich machen koennte — laufender Container, nicht
                # passende Ports — wurde beim Vorschlagen geprueft und wird
                # hier erneut geprueft: zwischen Vorschlag und Bestaetigung
                # koennen Minuten liegen, und der Server kann inzwischen
                # gestartet worden sein.
                from services import blueprint_service

                ziel_id = str(payload["blueprint_id"])
                server_row = db.query(Server).filter(Server.id == server_id).first()
                if server_row is None:
                    raise AiActionStateError("AI_ACTION_TARGET_MISSING")
                if server_row.status != "stopped":
                    raise AiActionStateError("AI_ACTION_SERVER_BUSY")
                try:
                    ziel = blueprint_service.blueprint_view(ziel_id)
                    alt = blueprint_service.blueprint_view(server_row.game_type)
                except HTTPException as exc:
                    raise AiActionStateError("AI_ACTION_TARGET_MISSING") from exc
                if blueprint_service.switch_incompatibility(
                    alt["blueprint"], ziel["blueprint"]
                ):
                    raise AiActionStateError("AI_ACTION_BLUEPRINT_INCOMPATIBLE")
                vorher = server_row.game_type
                server_row.game_type = ziel_id
                db.flush()
                result = {"from_blueprint": vorher, "to_blueprint": ziel_id}
                task_id = None
                queued = False
            elif tool_name == "propose_server_delete":
                # Derselbe Aufruf, den der Panel-Router und die Hoster-Anbindung
                # nehmen. `delete_server_completely` prueft `servers.delete`
                # selbst noch einmal — die dritte Pruefung nach `_resolve_server`
                # beim Vorschlagen und `_require_tool_permission` beim
                # Bestaetigen. Eine davon zu ueberspringen, waere ein eigener
                # Loeschpfad fuer die KI, und genau den soll es nicht geben.
                from services.server_deletion_service import delete_server_completely

                result = delete_server_completely(
                    db,
                    server_id=server_id,
                    actor=ActorContext.for_user(
                        active_user, origin="ai", correlation_id=correlation_id
                    ),
                )
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
            elif tool_name == "propose_blueprint_change":
                # Gespeichert wird die Nutzlast, die beim **Vorschlagen**
                # entstanden ist — nicht eine neu berechnete. Der Mensch hat
                # genau dieses Ergebnis gesehen und bestaetigt; zwischenzeitlich
                # geaenderte Vorlagen duerfen daran nichts mehr drehen.
                from services import blueprint_service

                try:
                    blueprint_id = blueprint_service.save_community_blueprint(
                        dict(payload["blueprint"])
                    )
                except HTTPException as exc:
                    logger.info("Blueprint-Vorschlag abgelehnt: %s", exc.detail)
                    raise AiActionStateError("AI_ACTION_BLUEPRINT_REJECTED") from exc
                result = {"blueprint_id": blueprint_id}
                task_id = None
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
