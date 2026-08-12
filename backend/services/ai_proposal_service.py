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

from dataclasses import dataclass
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

from models import AiActionProposal, AiConversation, HosterIntegration, Role, Server, User
from schemas.ai_action import AiActionProposalResponse
from services import audit_service, permission_service
from services.actor_context import ActorContext
from services.ai_action_errors import AiActionStateError, AiActionValidationError
from services.ai_action_service import (
    CONFIRMATION_TTL,
    MAX_BACKUP_NAME_CHARS,
    MAX_CONFIG_CHARS,
    MAX_DIFF_CHARS,
    MAX_DIFF_LINES,
    MAX_PATCH_CHUNK_CHARS,
    MAX_PATCH_EDITS,
    MAX_READ_CONFIG_CHARS,
    MAX_REASON_CHARS,
    _MUTEX_TOOLS,
    _config_path,
    _resolve_server,
    is_binary_text,
)
from services.file_edit_service import EditNotApplicable, apply_edits
# Die Grenze des Versionsspeichers steht dort, wo sie gilt, und wird hier nicht
# abgeschrieben: `propose_file_delete` lehnt ab, was `file_history_service`
# nicht sichern kann, und beide muessen dieselbe Zahl meinen.
from services.file_history_service import MAX_HISTORY_EDIT_SIZE
from services.ai_redaction import redact_sensitive_text
from services.ai_tool_registry import (
    GLOBAL_WRITE_TOOLS,
    GUARDIAN_HEILUNG_TOOLS,
    WERKZEUGE,
    WRITE_TOOLS,
)
from services.dis_client import DisClient
from services.server_file_access_service import read_server_text, write_server_text


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GuardianKontext:
    """Der Rahmen eines Laufs, den ein Vorfall ausgeloest hat — nicht ein Mensch.

    Er wird beim Start des Heilungslaufs gebildet, liegt im Arbeitsgedaechtnis
    des Laufs (`ai_runs.state_json`) und wird bei jeder Runde daraus wieder
    hergestellt. Drei Angaben, und jede traegt eine Schranke:

    * ``server_id`` — der **einzige** Server, an dem dieser Lauf arbeiten darf.
      Im gewoehnlichen Chat nennt das Modell die Server-ID selbst; das ist dort
      richtig, weil ein Mensch mitliest. Hier liest niemand mit, und die Eingabe
      des Modells stammt teilweise aus Serverlogs — also aus Text, den ein
      Spieler geschrieben haben kann. Der Bezug wird deshalb vorgegeben.
    * ``incident_id`` — welcher Vorfall gemeint ist. Fuer die Notiz-Zeile, den
      Bericht und das Audit.
    * ``incident_created_at`` — ab wann ein Backup als Nachweis taugt. Ein
      Backup von gestern liegt vor der Stoerung und beweist nichts ueber den
      Zustand, den die KI gleich anfasst.
    """

    server_id: int
    incident_id: int
    incident_created_at: datetime


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
    if tool_name in {
        "propose_config_update",
        "propose_config_patch",
    } and not permission_service.has_server_permission(
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
        # Eine vorhandene Datei zu ersetzen, ohne zu sagen, welchen Stand man
        # ersetzt, ist immer ein Fehler — egal wie der Vorschlag entstanden ist.
        raise AiActionValidationError(
            "Fuer eine vorhandene Datei ist expected_revision Pflicht"
        )
    if current_revision != expected:
        raise AiActionValidationError("Config wurde seit der Analyse veraendert")
    old_content = str(current["content"]) if current is not None else ""
    # **Die Schranke der Vollersetzung.** Sie steht hier und nicht mehr indirekt
    # darin, dass `read_config` fuer eine gekuerzte Sicht die Revision
    # zurueckhielt.
    #
    # Der Unterschied ist der Punkt: die alte Absicherung glaubte dem Modell,
    # dass es die Datei gesehen hat, sobald es eine Revision vorzeigen konnte.
    # Diese hier misst die Datei selbst. Was das Modell gesehen zu haben
    # behauptet, spielt keine Rolle mehr — und dadurch darf `read_config` die
    # Revision jetzt ehrlich immer ausgeben, was die Teilaenderung ueberhaupt
    # erst moeglich macht.
    if len(redact_sensitive_text(old_content)) > MAX_READ_CONFIG_CHARS:
        raise AiActionValidationError(
            "Diese Datei ist zu gross, um sie als Ganzes zu ersetzen — der "
            "Vorschlag wuerde alles loeschen, was nicht gelesen werden konnte. "
            "Nutze propose_config_patch fuer die einzelne Stelle."
        )
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


# Zeilen Umgebung je Ersetzung in der Vorschau. Drei sind genug, um zu erkennen,
# *wo* in einer Datei etwas passiert — bei einer XML-Konfiguration steht das
# umschliessende Element dann mit da.
_PATCH_CONTEXT_LINES = 3


def _zeilenbereich(text: str, start: int, ende: int, umgebung: int) -> tuple[int, int, int]:
    """Dehnt einen Zeichenbereich auf ganze Zeilen samt Umgebung aus.

    Liefert Anfang, Ende und die Nummer der ersten Zeile — letztere ist das,
    woran ein Mensch in der Vorschau erkennt, an welcher Stelle der Datei er
    gerade schaut.
    """
    zeilenanfang = text.rfind("\n", 0, start) + 1
    zeilenende = text.find("\n", ende)
    if zeilenende < 0:
        zeilenende = len(text)
    nummer = text.count("\n", 0, zeilenanfang) + 1
    for _ in range(umgebung):
        if zeilenanfang == 0:
            break
        zeilenanfang = text.rfind("\n", 0, zeilenanfang - 1) + 1
        nummer -= 1
    for _ in range(umgebung):
        if zeilenende >= len(text):
            break
        weiter = text.find("\n", zeilenende + 1)
        zeilenende = len(text) if weiter < 0 else weiter
    return zeilenanfang, zeilenende, nummer


def _patch_diff(content: str, edits: list[tuple[str, str]], path: str) -> tuple[str, bool]:
    """Baut die Vorschau einer Teilaenderung aus den Ersetzungen selbst.

    Bewusst **kein** `difflib`-Lauf ueber die ganze Datei. Der wuerde hier zwei
    Megabyte-Strings vergleichen, um am Ende drei geaenderte Zeilen zu zeigen —
    und das an der Stelle, an der ein Mensch auf eine Bestaetigung wartet. Wo
    etwas passiert, ist ohnehin genau bekannt: `find` steht laut Pruefung genau
    einmal in der Datei. Aus dieser Fundstelle und ein paar Zeilen Umgebung
    entsteht dieselbe Darstellung in linearer Zeit.

    Die Zeilennummer gilt zum Zeitpunkt der jeweiligen Ersetzung. Aendert ein
    frueherer Eintrag die Zeilenzahl, verschiebt sich die Nummer der spaeteren —
    dieselbe Reihenfolgeabhaengigkeit, die auch `apply_edits` hat.
    """
    zeilen = [f"--- {path}:vorher", f"+++ {path}:nachher"]
    arbeitsstand = content
    for find, replace in edits:
        stelle = arbeitsstand.find(find)
        if stelle < 0:
            # Kann nach `apply_edits` nicht vorkommen; eine stillschweigend
            # falsche Vorschau waere aber schlimmer als eine fehlende Zeile.
            continue
        ende = stelle + len(find)
        von, bis, nummer = _zeilenbereich(arbeitsstand, stelle, ende, _PATCH_CONTEXT_LINES)
        vorher = arbeitsstand[von:bis]
        nachher = arbeitsstand[von:stelle] + replace + arbeitsstand[ende:bis]
        zeilen.append(f"@@ ab Zeile {nummer} @@")
        zeilen.extend(f"-{z}" for z in redact_sensitive_text(vorher).splitlines())
        zeilen.extend(f"+{z}" for z in redact_sensitive_text(nachher).splitlines())
        arbeitsstand = arbeitsstand[:stelle] + replace + arbeitsstand[ende:]

    gekuerzt = len(zeilen) > MAX_DIFF_LINES
    diff = "\n".join(zeilen[:MAX_DIFF_LINES])
    return diff[:MAX_DIFF_CHARS], gekuerzt or len(diff) > MAX_DIFF_CHARS


def _config_patch_payload(
    db: Session, server_id: int, arguments: dict
) -> tuple[dict, dict, str]:
    """Prueft eine Teilaenderung und baut Nutzlast und Vorschau.

    Der Unterschied zu `_config_payload` ist nicht die Berechtigung — es ist
    dasselbe `server.files.write` — sondern die Reichweite. Eine Vollersetzung
    setzt voraus, dass das Modell die Datei ganz gesehen hat, und scheitert
    deshalb an jeder Datei ueber 24.000 Zeichen. Eine Teilaenderung setzt nur
    voraus, dass es *die eine Stelle* kennt: alles Uebrige bleibt Byte fuer Byte
    stehen, weil es hier gar nicht durchlaeuft.

    Daraus folgt auch die gelockerte Geheimnisregel. `_config_payload` weist
    jede Datei ab, in der irgendwo Zugangsdaten stehen — richtig, denn sie
    schreibt die ganze Datei neu und wuerde das echte Passwort durch den
    Platzhalter ersetzen, den das Modell gesehen hat. Hier genuegt, dass
    **die beruehrte Stelle** geheimnisfrei ist. Ein Passwort drei Zeilen weiter
    wird nicht angefasst, und getroffen werden kann es auch nicht: das Modell
    kennt von dort nur den Platzhalter, und der steht so nicht in der Datei.
    Ohne diese Lockerung waere eine `serverconfig.xml` dauerhaft nur von Hand
    aenderbar — der Fall, aus dem die ganze Aenderung entstanden ist.
    """
    if set(arguments) != {"path", "expected_revision", "edits"}:
        raise AiActionValidationError("Patch-Tool hat ungueltige Argumente")
    path = _config_path(arguments["path"])
    expected = arguments["expected_revision"]
    if (
        not isinstance(expected, str)
        or not expected.startswith("sha256:")
        or len(expected) != 71
    ):
        raise AiActionValidationError(
            "expected_revision fehlt oder ist ungueltig. Zuerst read_config aufrufen."
        )

    roh = arguments["edits"]
    if not isinstance(roh, list) or not roh:
        raise AiActionValidationError("Es fehlt mindestens eine Ersetzung")
    if len(roh) > MAX_PATCH_EDITS:
        raise AiActionValidationError(
            f"Hoechstens {MAX_PATCH_EDITS} Ersetzungen je Vorschlag"
        )
    edits: list[tuple[str, str]] = []
    for nummer, eintrag in enumerate(roh, start=1):
        if not isinstance(eintrag, dict) or set(eintrag) != {"find", "replace"}:
            raise AiActionValidationError(
                f"Ersetzung {nummer} braucht genau 'find' und 'replace'"
            )
        find, replace = eintrag["find"], eintrag["replace"]
        if not isinstance(find, str) or not find:
            raise AiActionValidationError(f"Ersetzung {nummer} hat keinen Suchtext")
        if not isinstance(replace, str):
            raise AiActionValidationError(f"Ersetzung {nummer} hat keinen Ersatztext")
        if len(find) > MAX_PATCH_CHUNK_CHARS or len(replace) > MAX_PATCH_CHUNK_CHARS:
            raise AiActionValidationError(f"Ersetzung {nummer} ist zu gross")
        # Die Umsetzung der gelockerten Geheimnisregel, und zugleich der Schutz
        # davor, dass ein Vorschlag ein Passwort *einbaut*.
        if redact_sensitive_text(find) != find or redact_sensitive_text(replace) != replace:
            raise AiActionValidationError(
                f"Ersetzung {nummer} enthaelt moegliche Zugangsdaten und wird "
                "nicht angewandt"
            )
        edits.append((find, replace))

    try:
        current = read_server_text(db, server_id=server_id, relative_path=path)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        raise AiActionValidationError(
            "Diese Datei gibt es nicht. Eine Teilaenderung braucht eine "
            "vorhandene Datei; zum Anlegen ist propose_config_update da."
        ) from exc
    if str(current["revision"]) != expected:
        raise AiActionValidationError("Datei wurde seit dem Lesen veraendert")

    old_content = str(current["content"])
    if is_binary_text(old_content):
        raise AiActionValidationError("Diese Datei ist kein Text und wird nicht geaendert")

    try:
        new_content = apply_edits(old_content, edits)
    except EditNotApplicable as exc:
        # Die Trefferzahl gehoert in die Meldung: bei null stimmt der Suchtext
        # nicht, bei mehreren fehlt ihm Kontext. Das Modell kann daraus im
        # naechsten Zug etwas machen — aus "hat nicht geklappt" nicht.
        nummer = exc.index + 1
        grund = (
            f"Der Suchtext von Ersetzung {nummer} kommt in der Datei nicht vor"
            if exc.count == 0
            else f"Der Suchtext von Ersetzung {nummer} kommt {exc.count}-mal vor "
            "und ist damit nicht eindeutig. Nimm mehr Umgebung mit hinein."
        )
        raise AiActionValidationError(grund) from exc
    if new_content == old_content:
        # Derselbe Gedanke wie bei den Trefferzahlen oben: das Modell muss aus
        # der Absage etwas machen koennen. "Aendert nichts" klang nach einem
        # Fehler im Vorschlag und war eine Sackgasse — im Betrieb brach die
        # Anfrage an dieser Stelle ab, obwohl die Lage voellig harmlos war: der
        # gewuenschte Wert stand bereits so in der Datei.
        raise AiActionValidationError(
            "Die Datei saehe danach genau aus wie jetzt — der gewuenschte Wert "
            "steht also schon so darin. Das ist kein Fehler: sag dem Benutzer, "
            "dass nichts zu aendern ist, statt es erneut zu versuchen."
        )
    if is_binary_text(new_content):
        raise AiActionValidationError("Das Ergebnis waere keine Textdatei mehr")

    diff, diff_gekuerzt = _patch_diff(old_content, edits, path)
    preview = {
        "path": path,
        "change": "patch",
        "diff": diff,
        "diff_truncated": diff_gekuerzt,
        "edits": len(edits),
        "restart_required": True,
    }
    # Die Nutzlast sind die Ersetzungen, nicht der fertige Inhalt: bei einer
    # Datei von einem Megabyte laege der sonst verschluesselt in der Datenbank,
    # fuer eine Aenderung von drei Zeilen. Angewandt wird beim Ausfuehren erneut
    # — auf denselben Stand, denn `expected_revision` laesst keinen anderen zu.
    return (
        {"path": path, "edits": [{"find": f, "replace": r} for f, r in edits]},
        preview,
        expected,
    )


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


def _hoster_integration_payload(db: Session, user: User, arguments: dict) -> tuple[dict, dict]:
    """Integration anlegen oder aendern — validiert, bevor jemand bestaetigt.

    Alles, was `create_integration` spaeter ohnehin prueft, wird hier schon
    geprueft: Slug-Form, Dienstbenutzer, Webhook-Ziel, Kuendigungsfrist. Ein
    Vorschlag, der erst nach der Bestaetigung scheitert, hat den Menschen
    umsonst zustimmen lassen — und die Meldung kommt dann aus einer Schicht, die
    ihn nicht mehr erreicht.

    **Die Vorschau traegt Panel-Tatsachen.** Die Karte zeigt dem Bestaetigenden
    sonst nur `reason` und `expected_effect`, und beides ist vom Modell
    verfasster Text: er wuerde bestaetigen, was das Modell ueber seinen eigenen
    Vorschlag behauptet. Der Name des Dienstbenutzers steht deshalb aufgeloest
    darin, nicht nur seine Nummer.
    """
    from services import hoster_integration_service

    erlaubt = {
        "integration_id", "name", "slug", "service_user_id",
        "webhook_url", "terminate_grace_days", "enabled",
    }
    if set(arguments) - erlaubt:
        raise AiActionValidationError("Hoster-Integration hat ungueltige Argumente")

    integration_id = arguments.get("integration_id")
    vorhanden = None
    if integration_id is not None:
        vorhanden = (
            db.query(HosterIntegration)
            .filter(HosterIntegration.id == integration_id)
            .first()
        )
        if vorhanden is None:
            raise AiActionValidationError(f"Integration {integration_id} gibt es nicht")

    name = str(arguments.get("name") or "").strip()
    if not name or len(name) > 128:
        raise AiActionValidationError("Name der Integration fehlt oder ist zu lang")
    try:
        slug = hoster_integration_service.normalize_slug(str(arguments.get("slug") or ""))
        webhook_url = hoster_integration_service.validate_webhook_url(
            arguments.get("webhook_url")
        )
        service_user = hoster_integration_service.require_service_user(
            db, int(arguments.get("service_user_id") or 0)
        )
    except hoster_integration_service.HosterConfigurationError as exc:
        raise AiActionValidationError(str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise AiActionValidationError("Dienstbenutzer fehlt oder ist ungueltig") from exc

    tage = arguments.get("terminate_grace_days")
    if isinstance(tage, bool) or not isinstance(tage, int) or not 0 <= tage <= 365:
        raise AiActionValidationError("Kuendigungsfrist muss zwischen 0 und 365 Tagen liegen")

    # Ein fremder Slug faellt sonst erst beim `flush` auf — dann als
    # IntegrityError mitten in der Ausfuehrung statt als Formmeldung.
    kollision = (
        db.query(HosterIntegration.id)
        .filter(HosterIntegration.slug == slug)
        .filter(HosterIntegration.id != (vorhanden.id if vorhanden else -1))
        .first()
    )
    if kollision is not None:
        raise AiActionValidationError(f"Der Slug {slug!r} ist bereits vergeben")

    aktiv = arguments.get("enabled")
    aktiv = True if aktiv is None else bool(aktiv)

    payload = {
        "integration_id": vorhanden.id if vorhanden else None,
        "name": name,
        "slug": slug,
        "service_user_id": service_user.id,
        "webhook_url": webhook_url,
        "terminate_grace_days": tage,
        "enabled": aktiv,
    }
    preview = {
        "operation": "hoster_integration_update" if vorhanden else "hoster_integration_create",
        "path": slug,
        "name": name,
        "slug": slug,
        # Aufgeloest, nicht als Nummer: der Mensch bestaetigt anhand dessen,
        # was er lesen kann.
        "service_user": service_user.username,
        "webhook_url": webhook_url,
        "webhook_secret_will_be_created": bool(
            webhook_url and not (vorhanden.webhook_secret_encrypted if vorhanden else None)
        ),
        "terminate_grace_days": tage,
        "enabled": aktiv,
        # Der Schluessel steht **nicht** in der Vorschau. `preview_json` liegt
        # im Klartext in der Datenbank und geht bei jedem `listActions()` erneut
        # an den Browser. Er entsteht erst beim Ausfuehren und geht ueber
        # `result` genau einmal an die Karte.
        "api_key_shown_once": not vorhanden,
        "restart_required": False,
    }
    return payload, preview


def _hoster_product_payload(db: Session, user: User, arguments: dict) -> tuple[dict, dict]:
    """Produktzuordnung — mit **beiden** Rollenschranken.

    `ensure_role_is_delegatable` prueft gegen den Dienstbenutzer der
    Integration, `ensure_actor_may_grant_role` gegen den Menschen, der hier
    gerade schreibt. Beide gelten zusammen; die erste allein ist wertlos, weil
    der Akteur den Dienstbenutzer selbst aussucht. Ohne die zweite waere der
    KI-Weg schwaecher als der Panel-Knopf — und das ist er nie.
    """
    from games import get_plugin
    from services import hoster_integration_service, role_service

    erlaubt = {
        "integration_id", "external_product_key", "game_type", "ram_limit_mb",
        "cpu_limit_percent", "disk_limit_gb", "node_id", "backup_interval_hours",
        "role_id", "enabled",
    }
    if set(arguments) - erlaubt:
        raise AiActionValidationError("Hoster-Produkt hat ungueltige Argumente")

    integration = (
        db.query(HosterIntegration)
        .filter(HosterIntegration.id == arguments.get("integration_id"))
        .first()
    )
    if integration is None:
        raise AiActionValidationError(
            f"Integration {arguments.get('integration_id')} gibt es nicht"
        )

    try:
        key = hoster_integration_service.normalize_external_id(
            str(arguments.get("external_product_key") or ""), label="Produktkennung"
        )
    except hoster_integration_service.HosterConfigurationError as exc:
        raise AiActionValidationError(str(exc)) from exc

    game_type = str(arguments.get("game_type") or "").strip()
    if not game_type or get_plugin(game_type) is None:
        raise AiActionValidationError(f"Unbekannter Blueprint: {game_type!r}")

    grenzen: dict[str, int | None] = {}
    for feld, maximum in (
        ("ram_limit_mb", 4_194_304),
        ("cpu_limit_percent", 10_000),
        ("disk_limit_gb", 1_048_576),
        ("backup_interval_hours", 8_760),
        ("node_id", None),
        ("role_id", None),
    ):
        wert = arguments.get(feld)
        if wert is None:
            grenzen[feld] = None
            continue
        if isinstance(wert, bool) or not isinstance(wert, int) or wert < 1:
            raise AiActionValidationError(f"'{feld}' muss eine positive ganze Zahl sein")
        if maximum is not None and wert > maximum:
            raise AiActionValidationError(f"'{feld}' liegt ausserhalb des erlaubten Bereichs")
        grenzen[feld] = wert

    rolle = None
    if grenzen["role_id"] is not None:
        try:
            hoster_integration_service.ensure_actor_may_grant_role(
                db, actor=user, role_id=grenzen["role_id"]
            )
            rolle = hoster_integration_service.ensure_role_is_delegatable(
                db, integration=integration, role_id=grenzen["role_id"]
            )
        except hoster_integration_service.HosterConfigurationError as exc:
            raise AiActionValidationError(str(exc)) from exc

    aktiv = arguments.get("enabled")
    aktiv = True if aktiv is None else bool(aktiv)

    payload = {
        "integration_id": integration.id,
        "external_product_key": key,
        "game_type": game_type,
        **grenzen,
        "enabled": aktiv,
    }
    preview = {
        "operation": "hoster_product_save",
        "path": f"{integration.slug}/{key}",
        "integration": integration.name,
        "external_product_key": key,
        "game_type": game_type,
        "ram_limit_mb": grenzen["ram_limit_mb"],
        "cpu_limit_percent": grenzen["cpu_limit_percent"],
        "disk_limit_gb": grenzen["disk_limit_gb"],
        "node_id": grenzen["node_id"],
        "backup_interval_hours": grenzen["backup_interval_hours"],
        # Name **und** Rechte der Rolle: "welche Rolle bekommt jeder Kaeufer"
        # ist die eigentliche Frage dieses Vorschlags, und eine Nummer
        # beantwortet sie nicht.
        "role": rolle.name if rolle is not None else None,
        "role_permissions": (
            sorted(role_service.role_permission_keys(db, rolle.id))
            if rolle is not None else []
        ),
        "enabled": aktiv,
        "restart_required": False,
    }
    return payload, preview


def _ai_tarif_role_payload(db: Session, user: User, arguments: dict) -> tuple[dict, dict]:
    """Eine Tarifrolle: leere Rechteliste, nur ein KI-Kontingent.

    Die leere Rechteliste ist der Sicherheitsentwurf, nicht eine Sparmassnahme.
    Eine Rolle ohne Permission-Keys kann ueber `ensure_actor_may_grant_role` nie
    mehr vergeben, als der Akteur selbst hat — die Fehlmenge ist immer leer.
    Eskalation ist damit **strukturell** ausgeschlossen und nicht durch eine
    Pruefung verhindert, die jemand kuenftig umgehen koennte. Wer einer
    Tarifrolle Rechte geben will, tut das in der Rollenverwaltung.

    Zwei Rechte, ein Feld: die Registry traegt `roles.manage` und wird von
    `_require_tool_permission` geprueft. Das KI-Kontingent haengt aber an
    `panel.settings.write` (siehe `routers/ai_settings.py`), und ein Werkzeug,
    das ueber die KI mehr darf als ueber das Panel, ist genau der Fehler, den
    die Registry sonst verhindert.
    """
    from services import ai_limit_service, permission_service
    from services.permission_catalog import SYSTEM_ROLE_NAMES

    erlaubt = {"name", "description", *ai_limit_service.LIMIT_FIELDS}
    if set(arguments) - erlaubt:
        raise AiActionValidationError("Tarifrolle hat ungueltige Argumente")

    if not permission_service.has_global_permission(db, user, "panel.settings.write"):
        raise AiActionValidationError(
            "Fuer das KI-Kontingent einer Rolle fehlt das Recht 'panel.settings.write'"
        )

    name = str(arguments.get("name") or "").strip()
    if not name or len(name) > 64:
        raise AiActionValidationError("Rollenname fehlt oder ist zu lang")
    if name in SYSTEM_ROLE_NAMES:
        raise AiActionValidationError(f"{name!r} ist ein reservierter Rollenname")
    if db.query(Role.id).filter(Role.id.isnot(None), Role.name == name).first() is not None:
        raise AiActionValidationError(f"Die Rolle {name!r} gibt es schon")

    beschreibung = arguments.get("description")
    if beschreibung is not None and not isinstance(beschreibung, str):
        raise AiActionValidationError("Beschreibung muss Text sein")
    beschreibung = redact_sensitive_text(str(beschreibung or "").strip())[:255] or None

    limits: dict[str, int | None] = {}
    for feld in ai_limit_service.LIMIT_FIELDS:
        wert = arguments.get(feld)
        if wert is None:
            # `None` heisst in `resolve_effective_limits` ausdruecklich
            # **unbegrenzt**. Das ist kein fehlender Wert, sondern eine Aussage.
            limits[feld] = None
            continue
        maximum = ai_limit_service.LIMIT_MAXIMA[feld]
        if isinstance(wert, bool) or not isinstance(wert, int) or not 0 <= wert <= maximum:
            raise AiActionValidationError(f"Ungueltiger Wert fuer {feld}")
        limits[feld] = wert

    payload = {"name": name, "description": beschreibung, "limits": limits}
    preview = {
        "operation": "ai_tarif_role_create",
        "path": name,
        "name": name,
        "description": beschreibung,
        # Ausgeschrieben, damit in der Karte steht, was die Rolle **nicht** kann.
        "permissions": [],
        "ai_limits": limits,
        "restart_required": False,
    }
    return payload, preview


def _blueprint_switch_payload(server: Server, arguments: dict) -> tuple[dict, dict]:
    """Bereitet den Wechsel vor — mit dem, was dabei wirklich passiert.

    Der erste Entwurf hat hier zwei schwere Fehler gemacht, beide aus derselben
    Ursache: er kannte den vorhandenen Panel-Weg nicht
    (`server_lifecycle_service.switch_server_blueprint`) und hat einen zweiten
    erfunden.

    **Erstens war die Vorschau unwahr.** Sie nannte nur die geaenderten
    Umgebungsvariablen und "Server bleibt gestoppt". In Wirklichkeit ist ein
    Wechsel der destruktivste Vorgang neben dem Loeschen: er legt ein
    Pflicht-Backup an, **loescht das gesamte Serververzeichnis**, vergibt die
    Ports neu und installiert das neue Spiel. Der Server steht danach auf
    "installing", nicht auf "stopped". Wer eine Bestaetigungskarte liest, die
    das verschweigt, stimmt etwas zu, das er nicht kennt.

    **Zweitens verlangte er uebereinstimmende Portrollen.** Der Panel-Weg
    vergibt die Ports neu, kennt also keine solche Bedingung. Die Pruefung hat
    Wechsel abgelehnt, die ueber den Knopf funktionieren — eine erfundene
    Einschraenkung.

    Geblieben sind die Vorbedingungen, die der Panel-Weg selbst prueft; sie
    stehen hier nur frueher, damit ein aussichtsloser Vorschlag gar nicht erst
    im Chat landet. Verbindlich geprueft werden sie weiterhin dort.
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
    except HTTPException as exc:
        raise AiActionValidationError(str(exc.detail)) from exc
    try:
        alt = blueprint_service.blueprint_view(server.game_type)
    except HTTPException:
        # Der **alte** Blueprint darf fehlen. Ein Server, dessen Community-
        # Vorlage geloescht wurde, ist genau der Fall, in dem man ihn umstellen
        # will — ihn deswegen abzuweisen waere die Falle zugeschnappt. Der
        # Panel-Knopf prueft die Quelle ebenfalls nicht, nur das Ziel.
        alt = None

    payload = {"blueprint_id": ziel_id}
    preview = {
        "operation": "blueprint_switch",
        "from_blueprint": server.game_type,
        "to_blueprint": ziel_id,
        "env_before": (
            (alt["blueprint"].get("runtime") or {}).get("env") or {}
        ) if alt is not None else None,
        "env_after": (ziel["blueprint"].get("runtime") or {}).get("env") or {},
        "current_status": server.status,
        # Was tatsaechlich geschieht. Ohne diese Aufzaehlung waere "Blueprint
        # wechseln" eine Zusage, deren Umfang der Bestaetigende raten muesste.
        "creates_backup": True,
        "wipes_server_files": True,
        "reallocates_ports": True,
        "reinstalls": True,
        "irreversible": True,
        # Der Server bleibt **nicht** gestoppt: die Neuinstallation startet
        # sofort und setzt den Status auf "installing".
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


#: Die Reparaturen, die `propose_server_repair` kennt. Die Liste steht **hier**
#: und im JSON-Schema des Werkzeugs, und beide Orte sind gewollt: das Schema
#: fuehrt das Modell, diese Menge entscheidet. Ein Modell, das trotz Schema
#: etwas anderes schickt — und Modelle tun das —, faellt hier auf.
#:
#: Es sind **zwei**, und das ist das Ergebnis einer Pruefung, nicht der erste
#: Entwurf. Der hatte vier:
#:
#: * ``recreate_container`` — gestrichen, weil es das schon gibt. `run_container`
#:   entfernt einen vorhandenen Container und baut ihn aus dem Blueprint neu
#:   auf; ein Neustart ueber `propose_server_lifecycle` **ist** der Neuaufbau.
#:   Ein zweites Werkzeug mit demselben Aufruf haette dem Modell zwei Namen fuer
#:   eine Handlung gegeben und der Oberflaeche zwei Karten fuer denselben Vorgang.
#: * ``repair_network`` — gestrichen, weil es nichts wiederherzustellen gibt.
#:   Ein gewoehnlicher Gameserver haengt an der Default-Bridge mit
#:   veroeffentlichten Ports und hat gar kein eigenes Docker-Netz; `extra_networks`
#:   ist nur bei verwalteter Postgres-Datenbank belegt, und dieses Netz legt bis
#:   heute ausschliesslich der Agent an. Der Aufruf waere der erste Netzanlage-
#:   Aufruf des Panels ueberhaupt gewesen — ein neuer Weg an Docker heran, statt
#:   eines vorhandenen.
REPARATUREN = ("repair_permissions", "reallocate_port")

#: Was jede Reparatur dem Menschen ankuendigt. Ohne diese Zeile stuende in der
#: Bestaetigungskarte nur eine Kennung, und "reallocate_port" sagt niemandem,
#: dass Spieler danach eine andere Portnummer brauchen.
_REPARATUR_FOLGEN = {
    "repair_permissions": ("Besitzrechte am Serververzeichnis werden berichtigt", False),
    "reallocate_port": ("Belegte Ports werden neu vergeben", False),
}


def _server_repair_payload(server: Server, arguments: dict) -> tuple[dict, dict]:
    """Nutzlast fuer `propose_server_repair` — eine Kennung, sonst nichts.

    Die Enge ist der Zweck. Das Modell liefert genau ein Wort aus `REPARATUREN`,
    und dieses Wort wird geprueft, bevor irgendetwas daraus gemacht wird. Es gibt
    keinen Pfad, kein Kommando und keinen Containernamen in dieser Nutzlast — der
    Containername entsteht spaeter aus `container_name_for(server_id)` und nie
    aus einer Eingabe.

    Damit hat ein Modell, das durch eine praeparierte Logzeile ueberredet wurde,
    hier keinen Hebel: es kann hoechstens die falsche der beiden Reparaturen
    anstossen, und jede davon stellt einen Zustand her, den MSM ohnehin kennt.

    `operation` traegt die **gewaehlte Kennung**, nicht das Wort "repair". Die
    Oberflaeche zeigt genau dieses Feld an und interpoliert es in den
    Bestaetigungsdialog; stuende dort fuer beide Reparaturen dasselbe, muesste
    der Bestaetigende raten, welche er gerade freigibt.
    """
    if set(arguments) != {"action"}:
        raise AiActionValidationError("Reparatur-Tool hat ungueltige Argumente")
    aktion = arguments.get("action")
    if aktion not in REPARATUREN:
        raise AiActionValidationError("Unbekannte Reparatur")
    beschreibung, neustart = _REPARATUR_FOLGEN[aktion]
    payload = {"action": aktion}
    preview = {
        "operation": aktion,
        "description": beschreibung,
        "current_status": server.status,
        "restart_required": neustart,
    }
    return payload, preview


def _file_delete_payload(db: Session, server: Server, arguments: dict) -> tuple[dict, dict, str]:
    """Nutzlast fuer `propose_file_delete` — genau eine vorhandene Datei.

    Der Pfad laeuft durch `_config_path` (Formpruefung) und danach durch
    `read_server_text`, das ihn zusaetzlich durch `safe_path` schickt. Zwei
    Wirkungen, beide gewollt:

    * Ein Verzeichnis, ein Platzhalter oder ein Ausbruch nach oben scheitert,
      bevor irgendetwas gespeichert wird.
    * Die Datei muss **existieren**. Ein Loeschvorschlag auf einen geratenen
      Namen kommt gar nicht erst in den Chat, und das Modell bekommt eine
      Antwort, mit der es weiterarbeiten kann, statt einer spaeteren
      Fehlermeldung beim Ausfuehren.

    Die Revision wird mitgefuehrt wie beim Schreiben. Aendert sich die Datei
    zwischen Vorschlag und Bestaetigung, ist es nicht mehr dieselbe — und dann
    soll sie nicht geloescht werden, weil die Begruendung dann nicht mehr gilt.
    """
    if set(arguments) != {"path"}:
        raise AiActionValidationError("Loesch-Tool hat ungueltige Argumente")
    pfad = _config_path(arguments["path"])
    ergebnis = read_server_text(db, server_id=server.id, relative_path=pfad)
    inhalt = str(ergebnis["content"])
    # Beides sagt `delete_server_text` beim Ausfuehren ohnehin ab. Es hier schon
    # abzuweisen, ist keine doppelte Pruefung um ihrer selbst willen: das Modell
    # bekommt eine Antwort, mit der es weiterarbeiten kann, statt einen
    # Vorschlag, der im Chat steht und beim Klick scheitert — und in einer
    # unbeaufsichtigten Heilung klickt niemand, dort waere der Vorschlag
    # schlicht das Ende des Weges.
    if is_binary_text(inhalt):
        raise AiActionValidationError(
            "Binaere Dateien lassen sich nicht mit Versionsschnappschuss loeschen"
        )
    if len(inhalt.encode("utf-8")) > MAX_HISTORY_EDIT_SIZE:
        raise AiActionValidationError(
            "Diese Datei ist zu gross fuer einen Versionsschnappschuss und wird "
            "deshalb nicht geloescht"
        )
    payload = {"path": pfad}
    preview = {
        "operation": "file_delete",
        "path": pfad,
        "current_status": server.status,
        "restart_required": False,
        # Wieviel dabei verschwindet. Eine Sperrdatei hat null Zeilen, eine
        # Weltkonfiguration hunderte — der Unterschied entscheidet darueber, ob
        # jemand das ohne Nachsehen bestaetigt.
        "lines": len(inhalt.splitlines()),
        "binary": False,
    }
    return payload, preview, str(ergebnis["revision"])


def _verlangt_gesichertes_backup(
    db: Session, server_id: int, tool_name: str, *, seit: datetime | None
) -> None:
    """Die Schranke: kein Eingriff ohne nachweislich geglecktes Backup.

    Gilt **nur** im von Guardian ausgeloesten Heilungslauf. Im gewoehnlichen
    Chat entscheidet weiterhin der Mensch mit seinem Klick; ihn zum Backup zu
    zwingen waere eine Aenderung, um die niemand gebeten hat, und sie wuerde
    jede kleine Korrektur zu einem Minutenvorgang machen.

    Der Nachweis ist `Backup.verified_at`, nicht das blosse Vorhandensein einer
    Zeile. Der Unterschied ist der ganze Punkt: eine Zeile entsteht auch dann,
    wenn der Remote-Agent-Pfad sie vor der Arbeit des Agenten anlegt, und
    `size_mb` ist fuer jedes Archiv unter einem Megabyte 0. `verified_at` wird
    ausschliesslich gesetzt, nachdem die Datei nachgemessen wurde.

    ``seit`` ist der Zeitpunkt des Vorfalls. Ein Backup von gestern beweist
    nichts ueber den Zustand, den die KI gleich anfasst — es liegt vor der
    Stoerung, und was seitdem passiert ist, holt es nicht zurueck.

    Der Fehler ist ein `AiActionStateError` und keine Validierungsmeldung: es
    ist kein Formfehler des Modells, sondern eine Bedingung der Anlage. Das
    Modell erfaehrt sie ueber den `error_code` und kann darauf antworten, indem
    es zuerst `propose_backup` aufruft.
    """
    from models import Backup
    from services.ai_tool_registry import GUARDIAN_BACKUP_PFLICHT_TOOLS

    if tool_name not in GUARDIAN_BACKUP_PFLICHT_TOOLS:
        return
    abfrage = db.query(Backup.id).filter(
        Backup.server_id == server_id,
        Backup.verified_at.isnot(None),
    )
    if seit is not None:
        abfrage = abfrage.filter(Backup.created_at >= seit)
    if abfrage.first() is None:
        raise AiActionStateError("AI_BACKUP_UNVERIFIED")


def guardian_aus_lauf(db: Session, run_id: str | None) -> "GuardianKontext | None":
    """Holt den Guardian-Rahmen eines Vorschlags aus seinem Lauf zurueck.

    `execute_proposal` bekommt keinen Rahmen uebergeben — es wird aus dem Router
    gerufen, wenn ein Mensch auf "Bestaetigen" klickt, und das kann Stunden nach
    dem Anlegen sein. Der Rahmen lebt im Arbeitsgedaechtnis des Laufs, und der
    Vorschlag traegt dessen Kennung; damit ist er wiederherstellbar, ohne dass
    eine Spalte an `ai_action_proposals` noetig waere.

    Genau diese Luecke machte die zugesagte doppelte Pruefung zur Behauptung: die
    Registry sagt zu `propose_file_delete` zu, der Backup-Nachweis werde "beim
    Anlegen **und** vor der Ausfuehrung" geprueft, `_verlangt_gesichertes_backup`
    hatte aber genau einen Aufrufer. Der Abstand zwischen beiden Punkten ist kein
    Detail: dazwischen liegt ein Commit und ein unbegrenztes Zeitfenster, in dem
    `cleanup_old_backups` das nachgewiesene Archiv abraeumen kann. Dieselbe
    Begruendung laesst die Rechtepruefung dreimal laufen.
    """
    if not run_id:
        return None
    from models import AiRun
    from services import ai_run_service

    run = db.get(AiRun, run_id)
    if run is None:
        return None
    rahmen = (ai_run_service.zustand_lesen(run) or {}).get("guardian")
    if not isinstance(rahmen, dict):
        return None
    anker = rahmen.get("backup_anker") or rahmen.get("incident_created_at")
    try:
        return GuardianKontext(
            server_id=int(rahmen["server_id"]),
            incident_id=int(rahmen["incident_id"]),
            incident_created_at=datetime.fromisoformat(str(anker)),
        )
    except (KeyError, TypeError, ValueError):
        # Ein unlesbarer Rahmen ist kein Freibrief. Er heisst: dieser Vorschlag
        # stammt aus einem Lauf, dessen Bedingungen nicht mehr feststellbar sind
        # — und dann wird nicht ausgefuehrt.
        raise AiActionStateError("AI_BACKUP_UNVERIFIED")


def proposal_response(proposal: AiActionProposal) -> AiActionProposalResponse:
    """Ein Vorschlag als Vertrag nach aussen — die **einzige** Serialisierung.

    Sie stand vorher im Router, und der Stream baute sich daneben ein eigenes
    Dict aus sechs Feldern. Das war kein Schoenheitsfehler: `reason` und
    `expected_effect` fehlten damit genau auf der Karte, mit der ein Mensch
    einen Schreibvorgang freigibt. Live erschien sie ohne Begruendung, und erst
    ein Neuladen holte sie ueber die REST-Liste nach.

    Deshalb liegt sie jetzt beim Vorschlag selbst, und beide Wege rufen sie auf.
    Ein neues Feld am Vorschlag kann so nicht mehr auf nur einem der beiden
    Wege ankommen.

    `preview_json` wird bewusst defensiv gelesen: die Vorschau ist Anzeige, kein
    Sicherheitsmerkmal. Eine kaputte Zeile darf die ganze Liste nicht unlesbar
    machen — sie meldet sich als `unavailable`.
    """
    try:
        preview = json.loads(proposal.preview_json)
    except (TypeError, json.JSONDecodeError):
        preview = {"unavailable": True}
    if not isinstance(preview, dict):
        preview = {"unavailable": True}
    return AiActionProposalResponse(
        id=proposal.id,
        conversation_id=proposal.conversation_id,
        server_id=proposal.server_id,
        tool_name=proposal.tool_name,
        preview=preview,
        expected_revision=proposal.expected_revision,
        requires_confirmation=proposal.requires_confirmation,
        autonomous=bool(proposal.autonomous),
        reason=proposal.reason,
        expected_effect=proposal.expected_effect,
        status=proposal.status,
        task_id=proposal.task_id,
        error_code=proposal.error_code,
        run_id=proposal.run_id,
        created_at=proposal.created_at,
    )


def create_proposal(
    db: Session,
    *,
    user: User,
    conversation: AiConversation,
    tool_name: str,
    arguments: dict,
    correlation_id: str,
    rationale_fallback: tuple[str, str] | None = None,
    guardian: "GuardianKontext | None" = None,
) -> AiActionProposal:
    """Legt einen Vorschlag an.

    ``guardian`` ist gesetzt, wenn dieser Lauf von einem Guardian-Vorfall
    ausgeloest wurde und nicht von einem Menschen. Er aendert drei Dinge, und
    alle drei sind Verschaerfungen:

    * die Werkzeugmenge wird auf `GUARDIAN_HEILUNG_TOOLS` eingeengt,
    * eingreifende Werkzeuge verlangen ein nachweislich geglecktes Backup,
    * das Audit vermerkt `origin="system"` statt `"ai"`.

    Nichts daran erweitert Rechte. Der handelnde Benutzer ist derselbe wie
    sonst — der, der die Freigabe erteilt hat —, und `_require_tool_permission`
    laeuft unveraendert.
    """
    if tool_name not in WRITE_TOOLS:
        raise AiActionValidationError("Tool ist in diesem Kontext nicht erlaubt")
    if guardian is not None and tool_name not in GUARDIAN_HEILUNG_TOOLS:
        # Die Menge steht in der Registry und wird hier durchgesetzt, nicht im
        # Prompt. Ein Modell, das aus einer praeparierten Logzeile heraus etwas
        # anderes versucht, kommt nicht bis zum Payload-Bau.
        raise AiActionValidationError(
            "Dieses Werkzeug steht in einer Guardian-Heilung nicht zur Verfuegung"
        )
    reason, expected_effect = _rationale(arguments, fallback=rationale_fallback)
    rest = {key: value for key, value in arguments.items() if key not in {"reason", "expected_effect"}}

    server: Server | None = None
    if tool_name == "propose_blueprint_change":
        payload, preview = _blueprint_change_payload(rest)
        expected_revision = None
    elif tool_name == "propose_server_create":
        payload, preview = _server_create_payload(db, arguments)
        expected_revision = None
    elif tool_name == "propose_hoster_integration":
        # `rest` statt `arguments`: ohne `reason`/`expected_effect` behalten die
        # Schluesselmengenpruefungen darunter ihre exakte Form.
        _require_tool_permission(db, user, None, tool_name, rest)
        payload, preview = _hoster_integration_payload(db, user, rest)
        expected_revision = None
    elif tool_name == "propose_hoster_product":
        _require_tool_permission(db, user, None, tool_name, rest)
        payload, preview = _hoster_product_payload(db, user, rest)
        expected_revision = None
    elif tool_name == "propose_ai_tarif_role":
        _require_tool_permission(db, user, None, tool_name, rest)
        payload, preview = _ai_tarif_role_payload(db, user, rest)
        expected_revision = None
    elif tool_name in GLOBAL_WRITE_TOOLS:
        # **Nicht als Sammelklausel schreiben.** Hier stand frueher
        # `elif tool_name in GLOBAL_WRITE_TOOLS: _server_create_payload(...)`.
        # Das las sich wie eine Mengenzugehoerigkeit, meinte aber genau ein
        # Werkzeug — und jedes zweite globale Schreibwerkzeug waere still in
        # der Servererstellung gelandet und mit "Servererstellung hat
        # ungueltige Argumente" gescheitert, einer Meldung, die auf die
        # falsche Stelle zeigt. Ein neues globales Schreibwerkzeug bekommt
        # einen eigenen `elif` darueber; wer das vergisst, faellt hier auf.
        raise AiActionValidationError(f"Kein Payload-Bau fuer Werkzeug: {tool_name}")
    else:
        # Dieselbe zentrale Rechtepruefung wie bei den Lesewerkzeugen. `rest`
        # verliert dabei die `server_id`, damit die nachfolgenden
        # Argumentpruefungen ihre exakten Schluesselmengen behalten.
        server, rest = _resolve_server(db, user, rest)

        # Ein Heilungslauf gehoert **einem** Server. `_resolve_server` prueft
        # nur, ob der Benutzer den genannten sehen darf — und der Freigeber darf
        # in aller Regel mehrere sehen. Ohne diese Zeile koennte ein Modell,
        # das aus einer Logzeile heraus in die Irre gefuehrt wurde, einen
        # Vorfall auf Server A zum Anlass nehmen, an Server B zu schreiben.
        if guardian is not None and server.id != guardian.server_id:
            raise AiActionValidationError(
                "In einer Guardian-Heilung ist nur der betroffene Server erlaubt"
            )

        # **Das Recht vor der Nutzlast.** Frueher stand diese Pruefung erst
        # hinter dem Payload-Bau — und der liest den Zustand, ueber den er
        # urteilt: `_config_patch_payload` holt den Dateiinhalt, um zu zaehlen,
        # wie oft der Suchtext darin vorkommt.
        #
        # Damit war die Ablehnung selbst eine Auskunft. Ein Benutzer mit
        # `server.view` und ohne `server.files.read` bekam auf einen erfundenen
        # Patch die Antwort "kommt 3-mal vor" — ein Orakel, mit dem sich der
        # Inhalt einer Datei Zeichen fuer Zeichen erraten laesst, ohne sie je
        # lesen zu duerfen. Der Vorschlag wurde nie gespeichert und nichts
        # geschrieben; das Leck lag allein in der Reihenfolge.
        #
        # Die Zusage ist "die KI kann nur, was der Benutzer kann". Sie gilt erst,
        # wenn schon der *Versuch* nichts verraet.
        #
        # Fuer den Lebenszyklus haengt das Recht am Vorgang, deshalb wird der
        # hier vorgezogen — sonst bekaeme ein ungueltiger Vorgang die
        # Rechte-Ablehnung statt der Formmeldung, die dem Modell weiterhilft.
        if tool_name == "propose_server_lifecycle" and rest.get("operation") not in {
            "start", "stop", "restart",
        }:
            raise AiActionValidationError("Ungueltige Lifecycle-Aktion")
        _require_tool_permission(db, user, server.id, tool_name, rest)

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
        elif tool_name == "propose_config_patch":
            payload, preview, expected_revision = _config_patch_payload(db, server.id, rest)
        elif tool_name == "propose_config_update":
            payload, preview, expected_revision = _config_payload(db, server.id, rest)
        elif tool_name == "propose_server_repair":
            payload, preview = _server_repair_payload(server, rest)
            expected_revision = None
        elif tool_name == "propose_file_delete":
            payload, preview, expected_revision = _file_delete_payload(db, server, rest)
        else:
            # Dieselbe Falle wie oben bei den globalen Werkzeugen, nur eine
            # Ebene tiefer: hier stand ein namenloses `else`, das jedes neue
            # serverbezogene Schreibwerkzeug als Konfigurationsaenderung gebaut
            # haette. Der Vorschlag waere entstanden, haette plausibel
            # ausgesehen und beim Ausfuehren etwas anderes getan, als sein Name
            # sagt.
            raise AiActionValidationError(f"Kein Payload-Bau fuer Werkzeug: {tool_name}")

    preview["reason"] = reason
    preview["expected_effect"] = expected_effect
    server_id = server.id if server is not None else None
    # Fuer serverbezogene Werkzeuge die zweite Pruefung — die erste lief vor dem
    # Payload-Bau. Sie bleibt trotzdem stehen: hier steht die kanonische
    # Nutzlast, und die globalen Werkzeuge kommen nur an dieser Stelle vorbei.
    _require_tool_permission(db, user, server_id, tool_name, payload)
    # Erst das Recht, dann der Nachweis. Die Reihenfolge zaehlt: wer den Server
    # gar nicht anfassen darf, soll nicht erfahren, ob es dort ein Backup gibt.
    if guardian is not None:
        _verlangt_gesichertes_backup(
            db, guardian.server_id, tool_name, seit=guardian.incident_created_at
        )
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
            **({"guardian_incident_id": guardian.incident_id} if guardian else {}),
        },
        # "ai" heisst: ein Mensch hat die KI darum gebeten. "system" heisst: ein
        # Ereignis hat sie geweckt, und niemand sass davor. Im Protokoll ist das
        # der wichtigste Unterschied ueberhaupt — wer spaeter fragt, warum um
        # 03:14 Uhr eine Datei geaendert wurde, findet die Antwort in diesem
        # einen Wort. Der Wert stand in `AUDIT_ORIGINS` bereits bereit.
        origin="system" if guardian is not None else "ai",
        correlation_id=proposal.correlation_id,
    )
    return proposal


def owned_proposal(db: Session, proposal_id: str, user: User) -> AiActionProposal | None:
    """Der Vorschlag, sofern es ihn gibt und er diesem Benutzer gehoert.

    Zwei Ausgaenge, und die Unterscheidung ist der Punkt:

    - ``None`` heisst **gibt es nicht** — unbrauchbare Kennung, oder die Zeile
      gehoert jemand anderem. Beides fuehrt zu 404, und das ist richtig so: ob
      ein fremder Vorschlag existiert, ist selbst schon eine Auskunft.
    - ``AI_ACTION_ACCESS_REVOKED`` heisst **darfst du nicht mehr** — die Zeile
      ist da und gehoert dem Anrufer, ihm fehlt nur das Recht zur Sache.

    Frueher lief beides in dasselbe ``None`` und damit in dieselbe Meldung
    "Aktionsvorschlag nicht gefunden". Wer daraufhin suchte, suchte an der
    falschen Stelle: nach einer verschwundenen Zeile statt nach einem entzogenen
    Recht. Das Vokabular dafuer gibt es laengst, `confirm_proposal` benutzt es.

    Die Existenz fremder Zeilen bleibt geschuetzt, weil die ``user_id``-Bedingung
    schon in der Abfrage steht — geworfen wird nur fuer Zeilen, die der Anrufer
    ohnehin besitzt.
    """
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
        # Kein Server, gegen den sich `server.view` pruefen liesse. Das trifft
        # zwei Faelle: ein Erstellungsvorschlag, dessen Server noch nicht
        # existiert — und seit dem `SET NULL` auch ein erledigter Vorschlag,
        # dessen Server es nicht mehr gibt.
        #
        # Welches Recht dann gilt, steht in der Werkzeugtabelle und nicht hier.
        # Fest verdrahtet stand hier `servers.create`; fuer einen abgeschlossenen
        # Loeschvorschlag waere das sachfremd gewesen. `_require_tool_permission`
        # zieht dieselbe Grenze beim Vorschlagen — zwei Orte mit zwei Antworten
        # sind genau die Sorte Abweichung, die niemand bemerkt.
        werkzeug = WERKZEUGE.get(proposal.tool_name)
        if werkzeug is not None and werkzeug.recht_global and werkzeug.recht:
            if not permission_service.has_global_permission(db, user, werkzeug.recht):
                raise AiActionStateError("AI_ACTION_ACCESS_REVOKED")
        # Ein serverbezogenes Werkzeug ohne globales Recht — etwa ein
        # Konfigvorschlag, dessen Server spaeter geloescht wurde. Es gibt kein
        # Recht mehr zu pruefen und nichts mehr zu verraten; der Beleg der
        # eigenen Unterhaltung bleibt sichtbar.
        return proposal
    if not permission_service.has_server_permission(
        db, user, proposal.server_id, "server.view"
    ):
        raise AiActionStateError("AI_ACTION_ACCESS_REVOKED")
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


def _execute_hoster_write(db: Session, *, user: User, tool_name: str, payload: dict) -> dict:
    """Fuehrt die drei Shop-Einrichtungsvorschlaege ueber den Panel-Pfad aus.

    Ein Weg, kein KI-Sonderweg: gerufen werden `create_integration`,
    `upsert_product`, `create_role` und `set_role_limit` — dieselben Funktionen
    wie hinter den Knoepfen in `routers/hoster_admin.py` und
    `routers/ai_settings.py`.

    **Der API-Key geht ueber den Rueckgabewert und nirgendwo sonst hin.**
    `AiActionExecuteResponse.result` wird nicht persistiert, steht nicht im
    Audit und fliesst nicht zum Modell zurueck — der Rueckfluss besteht
    ausschliesslich aus `status`, `autonomous`, `server_id` und `error_code`.
    Er darf deshalb hier stehen, aber niemals in `preview_json` (Klartext in der
    Datenbank) und niemals im Antworttext.

    Auf die Redaktion ist dabei kein Verlass, und das ist gemessen:
    `redact_sensitive_text` ist namensgebunden, und ein
    `secrets.token_urlsafe(32)` passt auf keines ihrer Muster. Ein Modell kann
    einen Schluessel nur dann nicht ausplaudern, wenn es ihn nie gesehen hat.
    """
    from services import ai_limit_service, hoster_integration_service, role_service

    try:
        if tool_name == "propose_ai_tarif_role":
            rolle = role_service.create_role(
                db, payload["name"], payload.get("description"), []
            )
            ai_limit_service.set_role_limit(db, rolle.id, dict(payload["limits"]))
            db.commit()
            return {"role_id": rolle.id, "name": rolle.name, "permissions": []}

        if tool_name == "propose_hoster_product":
            integration = (
                db.query(HosterIntegration)
                .filter(HosterIntegration.id == payload["integration_id"])
                .first()
            )
            if integration is None:
                raise AiActionStateError("AI_ACTION_HOSTER_REJECTED")
            # Beide Schranken erneut, nicht nur die des Dienstbenutzers: die
            # Rechte des Akteurs koennen zwischen Vorschlag und Bestaetigung
            # geschrumpft sein, und `upsert_product` kennt nur die Integration.
            hoster_integration_service.ensure_actor_may_grant_role(
                db, actor=user, role_id=payload.get("role_id")
            )
            produkt = hoster_integration_service.upsert_product(
                db,
                integration=integration,
                external_product_key=payload["external_product_key"],
                game_type=payload["game_type"],
                ram_limit_mb=payload.get("ram_limit_mb"),
                cpu_limit_percent=payload.get("cpu_limit_percent"),
                disk_limit_gb=payload.get("disk_limit_gb"),
                node_id=payload.get("node_id"),
                backup_interval_hours=payload.get("backup_interval_hours"),
                role_id=payload.get("role_id"),
                enabled=bool(payload.get("enabled", True)),
            )
            db.commit()
            return {
                "product_id": produkt.id,
                "external_product_key": produkt.external_product_key,
            }

        # propose_hoster_integration
        integration_id = payload.get("integration_id")
        if integration_id is not None:
            integration = (
                db.query(HosterIntegration)
                .filter(HosterIntegration.id == integration_id)
                .first()
            )
            if integration is None:
                raise AiActionStateError("AI_ACTION_HOSTER_REJECTED")
            service_user = hoster_integration_service.require_service_user(
                db, int(payload["service_user_id"])
            )
            integration.name = payload["name"]
            integration.slug = hoster_integration_service.normalize_slug(payload["slug"])
            integration.service_user_id = service_user.id
            integration.webhook_url = hoster_integration_service.validate_webhook_url(
                payload.get("webhook_url")
            )
            integration.terminate_grace_days = int(payload["terminate_grace_days"])
            integration.enabled = bool(payload.get("enabled", True))
            api_key = None
        else:
            integration, api_key = hoster_integration_service.create_integration(
                db,
                name=payload["name"],
                slug=payload["slug"],
                enabled=bool(payload.get("enabled", True)),
                service_user_id=int(payload["service_user_id"]),
                webhook_url=payload.get("webhook_url"),
                terminate_grace_days=int(payload["terminate_grace_days"]),
            )

        # Ein Webhook-Ziel ohne Secret stellt nichts zu. Das faellt sonst erst
        # auf, wenn der Shop auf die erste Statusmeldung wartet, die nie kommt.
        webhook_secret = None
        if integration.webhook_url and not integration.webhook_secret_encrypted:
            webhook_secret = hoster_integration_service.set_webhook_secret(db, integration)
        db.commit()
        db.refresh(integration)
    except hoster_integration_service.HosterConfigurationError as exc:
        db.rollback()
        # Ein eigener Code statt des groben AI_ACTION_EXECUTION_FAILED: aus
        # "ging nicht" wird "Slug ist bereits vergeben", und damit kann das
        # Modell im naechsten Zug etwas anfangen.
        logger.info("Hoster-Vorschlag abgelehnt: %s", exc)
        raise AiActionStateError("AI_ACTION_HOSTER_REJECTED") from exc
    except ValueError as exc:
        db.rollback()
        logger.info("Hoster-Vorschlag abgelehnt: %s", exc)
        raise AiActionStateError("AI_ACTION_HOSTER_REJECTED") from exc

    ergebnis: dict = {
        "integration_id": integration.id,
        "slug": integration.slug,
        "api_key_hint": integration.api_key_hint,
    }
    # `secrets` ist der einzige Schluessel, der dieses Modul verlaesst — auf dem
    # Weg zur Karte, die ihn genau einmal anzeigt, und auf keinem anderen.
    geheimnisse = []
    if api_key:
        geheimnisse.append({"label": "API-Key", "value": api_key})
    if webhook_secret:
        geheimnisse.append({"label": "Webhook-Secret", "value": webhook_secret})
    if geheimnisse:
        ergebnis["secrets"] = geheimnisse
    return ergebnis


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


def _execute_file_delete(
    db: Session, *, user: User, server_id: int, payload: dict,
    expected_revision: str | None,
) -> dict:
    """Loescht die eine Datei aus der Nutzlast — ueber den Panel-Pfad.

    Kein eigener Loeschweg fuer die KI: `delete_server_text` ist derselbe
    Dienst, den auch der Dateimanager benutzt, mit derselben Sandbox, derselben
    Revisionspruefung und demselben Versionsschnappschuss.

    Ein `HTTPException` von dort wird in einen Zustandsfehler uebersetzt. Die
    Unterscheidung ist fuer das Modell wichtig: 409 heisst "die Datei hat sich
    geaendert, sieh sie dir neu an", 404 heisst "es gibt sie nicht mehr, du bist
    fertig". Ein pauschales AI_ACTION_EXECUTION_FAILED wuerde beide zu
    demselben Ratespiel machen.
    """
    from services.server_file_access_service import delete_server_text

    try:
        return delete_server_text(
            db,
            user=user,
            server_id=server_id,
            relative_path=str(payload["path"]),
            expected_revision=expected_revision,
        )
    except HTTPException as exc:
        if exc.status_code == 404:
            raise AiActionStateError("AI_ACTION_TARGET_MISSING") from exc
        if exc.status_code == 409:
            raise AiActionStateError("AI_ACTION_FILE_CHANGED") from exc
        raise AiActionStateError("AI_ACTION_EXECUTION_FAILED") from exc


def _execute_server_repair(
    db: Session, *, server_id: int, payload: dict, user: User, correlation_id: str
) -> dict:
    """Fuehrt genau eine der Reparaturen aus `REPARATUREN` aus.

    Jeder Zweig ruft eine Funktion, die es im Panel schon gibt und die dort von
    einem Knopf ausgeloest wird. Es entsteht kein neuer Weg an Docker heran —
    das waere ein zweiter Ort, an dem Containernamen, Netznamen und Rechte
    richtig sein muessten.

    Der Containername kommt in **jedem** Zweig aus `container_name_for(server_id)`
    und nie aus der Nutzlast. Das ist die mechanische Seite der Zusage, dass ein
    Jailbreak hier nichts erreicht: es gibt keine Zeichenkette aus dem Modell,
    die bis zu Docker durchkommt.
    """
    from services import docker_service
    from services.server_lifecycle_service import guardian_recovery_suspension_lease
    from services.server_network_service import (
        PortReassignmentFailed,
        reassign_conflicting_ports,
    )

    aktion = str(payload["action"])
    if aktion not in REPARATUREN:
        # Die Nutzlast ist verschluesselt und wurde beim Anlegen geprueft. Trotzdem
        # hier erneut: zwischen Vorschlag und Ausfuehrung liegt ein Commit, und
        # eine Pruefung, die nur einmal laeuft, ist keine Invariante.
        raise AiActionStateError("AI_ACTION_TOOL_NOT_ALLOWED")

    server = db.query(Server).filter(Server.id == server_id).first()
    if server is None:
        raise AiActionStateError("AI_ACTION_TARGET_MISSING")

    # Waehrend der Reparatur haelt die Guardian-Engine still. Ohne diese Pacht
    # laeuft `_trigger_guardian_auto_restart` in derselben Reconcile-Runde gegen
    # uns: das Panel startet den Server neu, waehrend wir seine Ports gerade
    # umschreiben. Denselben Schutz nehmen `create_server_backup` und
    # `queue_lifecycle_operation` bereits.
    with guardian_recovery_suspension_lease(db, server, "ai-repair"):
        if aktion == "repair_permissions":
            if not server.install_dir:
                raise AiActionStateError("AI_ACTION_TARGET_MISSING")
            # `repair_bind_mount_permissions` kennt keinen `node`-Parameter und
            # laeuft immer auf dem Panel-Host. Bei einem Server auf einem
            # entfernten Node repariert sie also das falsche Verzeichnis — oder
            # findet es nicht und meldet einen Fehlschlag, obwohl nichts kaputt
            # ist. Denselben Schutz zieht `games/base.py` vor jedem Start.
            node = getattr(server, "node", None)
            if node is not None and not getattr(node, "is_local", False):
                raise AiActionStateError("AI_ACTION_REPAIR_NOT_LOCAL")
            ergebnis = docker_service.repair_bind_mount_permissions(server.install_dir)
            if not ergebnis.get("ok"):
                raise AiActionStateError("AI_ACTION_EXECUTION_FAILED")
            return {"action": aktion, "repaired": True}

        # reallocate_port
        try:
            gewechselt = reassign_conflicting_ports(db, server)
        except PortReassignmentFailed as exc:
            logger.info("Portneuvergabe abgelehnt server_id=%s: %s", server_id, exc)
            raise AiActionStateError("AI_ACTION_PORT_REASSIGN_FAILED") from exc
        # Keine Aenderung ist kein Fehlschlag, sondern eine Auskunft: die Ports
        # sind frei, das Problem liegt woanders. Das Modell soll das erfahren
        # und weitersuchen, statt einen Fehler zu sehen und es zu wiederholen.
        return {"action": aktion, "changed": bool(gewechselt), "ports": gewechselt}


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

    # **Der Backup-Nachweis, ein zweites Mal.** Genau hier fehlte er.
    #
    # Zwischen dem Anlegen des Vorschlags und diesem Punkt liegt ein Commit und
    # ein Zeitfenster ohne Obergrenze: ein Vorschlag im Status 'proposed' altert
    # nicht, und `cleanup_old_backups` raeumt nach `backup_retention_count` auch
    # die verifizierte Zeile ab, auf die sich die erste Pruefung gestuetzt hat.
    # Der Betreiber konnte das Archiv sogar von Hand loeschen — der Endpunkt
    # kennt keine Regel, die das letzte nachgewiesene Backup schuetzt.
    #
    # Ohne diese Zeilen loeschte ein Klick auf "Bestaetigen" die Datei, obwohl
    # der Nachweis, mit dem der Vorschlag ueberhaupt entstehen durfte, nicht mehr
    # existierte. Die Zusage in `ai_tool_registry` — geprueft beim Anlegen **und**
    # vor der Ausfuehrung — war bis hierher eine Behauptung.
    guardian = guardian_aus_lauf(db, proposal.run_id)
    if guardian is not None:
        _verlangt_gesichertes_backup(
            db, guardian.server_id, tool_name, seit=guardian.incident_created_at
        )
        # Und die Serverbindung ebenso: ein Vorschlag, dessen Lauf an Server A
        # gebunden war, darf auch nach Stunden nicht auf Server B ausgefuehrt
        # werden. Die Zeile kostet nichts und schliesst den Weg, auf dem eine
        # spaetere Aenderung am Vorschlagspfad hier unbemerkt vorbeikaeme.
        if server_id is not None and int(server_id) != guardian.server_id:
            raise AiActionStateError("AI_ACTION_ACCESS_REVOKED")

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
                # Derselbe Aufruf, den der Panel-Knopf "Spiel / Blueprint
                # wechseln" nimmt.
                #
                # Der erste Entwurf setzte hier `server.game_type = ziel_id` und
                # war damit fertig. Das war kein vereinfachter Weg, sondern ein
                # kaputter: der echte Wechsel legt ein Pflicht-Backup an,
                # **loescht das Serververzeichnis**, vergibt die Ports neu und
                # installiert das neue Spiel. Ein Server, bei dem nur die Spalte
                # umgeschrieben wird, traegt danach das Image des neuen
                # Blueprints ueber den Dateien des alten — Datenbank und
                # Wirklichkeit laufen auseinander, und niemand merkt es bis zum
                # naechsten Start.
                from services.server_lifecycle_service import switch_server_blueprint

                server_row = db.query(Server).filter(Server.id == server_id).first()
                if server_row is None:
                    raise AiActionStateError("AI_ACTION_TARGET_MISSING")
                try:
                    result = switch_server_blueprint(
                        db,
                        server_row,
                        str(payload["blueprint_id"]),
                        user_id=active_user.id,
                    )
                except HTTPException as exc:
                    # Die Vorbedingungen werden dort verbindlich geprueft —
                    # zwischen Vorschlag und Bestaetigung koennen Minuten
                    # liegen, und der Server kann inzwischen gestartet worden
                    # sein. Ein fehlgeschlagenes Pflicht-Backup bricht ebenfalls
                    # hier ab, **bevor** Dateien geloescht werden.
                    kennung = exc.detail.get("code") if isinstance(exc.detail, dict) else None
                    logger.info(
                        "Blueprint-Wechsel abgelehnt server_id=%s code=%s",
                        server_id, kennung,
                    )
                    raise AiActionStateError(
                        "AI_ACTION_SERVER_BUSY"
                        if kennung == "server_must_be_stopped"
                        else "AI_ACTION_BLUEPRINT_SWITCH_FAILED"
                    ) from exc
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
            elif tool_name == "propose_config_patch":
                # Erneut anwenden statt den fertigen Inhalt mitzuschleppen. Es
                # kommt dasselbe heraus: `expected_revision` laesst nur genau
                # den Stand zu, auf dem die Ersetzungen beim Vorschlagen schon
                # einmal aufgegangen sind — und dieselbe Revision geht gleich
                # noch einmal in `write_server_text`, das den Schreibvorgang
                # unter der Dateisperre gegen sie prueft.
                pfad = str(payload["path"])
                aktuell = read_server_text(db, server_id=server_id, relative_path=pfad)
                try:
                    neu = apply_edits(
                        str(aktuell["content"]),
                        [(str(e["find"]), str(e["replace"])) for e in payload["edits"]],
                    )
                except EditNotApplicable as exc:
                    raise AiActionStateError("AI_ACTION_FILE_CHANGED") from exc
                result = write_server_text(
                    db,
                    user=active_user,
                    server_id=server_id,
                    relative_path=pfad,
                    content=neu,
                    expected_revision=expected_revision,
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
            elif tool_name == "propose_server_repair":
                result = _execute_server_repair(
                    db, server_id=server_id, payload=payload,
                    user=active_user, correlation_id=correlation_id,
                )
                task_id = None
                queued = False
            elif tool_name == "propose_file_delete":
                result = _execute_file_delete(
                    db, user=active_user, server_id=server_id, payload=payload,
                    expected_revision=expected_revision,
                )
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
            elif tool_name in {
                "propose_hoster_integration",
                "propose_hoster_product",
                "propose_ai_tarif_role",
            }:
                result = _execute_hoster_write(
                    db, user=active_user, tool_name=tool_name, payload=payload
                )
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
            #
            # Ausdruecklich **nur** dort. Ohne die Einschraenkung auf das
            # Erstellen wuerde diese Zeile nach einem Loeschen genau das
            # rueckgaengig machen, was die Datenbank gerade richtig getan hat:
            # `SET NULL` loest den Bezug auf einen Server, den es nicht mehr
            # gibt — `server_id` waere hier wieder `None`, die lokale Kopie
            # `server_id` traegt aber noch die alte Nummer, und der Commit
            # scheiterte an der Fremdschluesselpruefung.
            if (
                tool_name == "propose_server_create"
                and proposal.server_id is None
                and server_id is not None
            ):
                proposal.server_id = server_id
            audit_service.record_privileged_action(
                db,
                user_id=active_user.id,
                action="ai.action.executed",
                target_type="server" if server_id is not None else "ai_action",
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
                    target_type="server" if server_id is not None else "ai_action",
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
