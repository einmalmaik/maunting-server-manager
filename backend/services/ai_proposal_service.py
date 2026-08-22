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

from collections.abc import Callable
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
from services.ai_ini_edit import ini_setzen
from services import ai_task_service
from services import server_config_wishes
from services.ai_tool_registry import (
    GLOBAL_WRITE_TOOLS,
    GUARDIAN_HEILUNG_TOOLS,
    WERKZEUGE,
    WRITE_TOOLS,
    aufgaben_tools,
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


@dataclass(frozen=True)
class AufgabenKontext:
    """Der Rahmen eines Laufs, den die Uhr ausgeloest hat — nicht ein Mensch.

    Das Gegenstueck zu `GuardianKontext` und bewusst **anders geschnitten**. Ein
    Heilungslauf gehoert einem Server; ein stehender Auftrag gehoert keinem. Der
    Benutzer hat "sieh nach **meinen Servern**" gesagt, und welche das sind,
    entscheidet seine Rechteliste, nicht der Auftrag. Es gibt hier deshalb keine
    Serverbindung und keine Backup-Schranke.

    Was bleibt, ist die Werkzeugmenge — und die haengt an ``kind``:

    * ``report`` liest, fasst zusammen und meldet.
    * ``act`` darf zusaetzlich handeln, und zwar nur, soweit `autonomy_allows`
      es im Einzelfall zulaesst. Der Rahmen erweitert nichts: er begrenzt.

    ``channel`` und ``title`` tragen nichts zur Schranke bei und stehen
    trotzdem hier. Sie werden am **Ende** des Laufs gebraucht, fuer den Bericht
    — und die Aufgabe kann bis dahin geloescht worden sein. Ein Bericht, der
    seinen eigenen Betreff aus einer Zeile holen muesste, die es nicht mehr
    gibt, waere ein Bericht, der ausgerechnet dann ausfaellt, wenn jemand
    aufgeraeumt hat.
    """

    task_id: str
    kind: str
    channel: str
    title: str


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


#: Welches Recht jede Reparatur verlangt. Die Zuordnung spiegelt die
#: Panel-Routen: Ports aendern verlangt dort `server.network.manage`, den
#: Root-Chown loest das Panel nur innerhalb einer `server.files.write`-Operation
#: aus. Ein Ort fuer beide Stellen, die sie brauchen — `_permission_for` und
#: die Formpruefung in `create_proposal`.
_REPARATUR_RECHTE = {
    "repair_permissions": "server.files.write",
    "reallocate_port": "server.network.manage",
}


def _permission_for(tool_name: str, payload: dict) -> tuple[str, ...]:
    """Die Permission-Keys, die dieses Werkzeug verlangt — alle zugleich.

    Sie stehen in `ai_tool_registry.WERKZEUGE` — dort, wo auch alles andere
    ueber ein Werkzeug steht. Vorher war das hier eine if-Kette: ein zweiter
    Ort, an dem ein neues Werkzeug eingetragen werden musste, und der Ort, an
    dem man es am ehesten vergisst. Ein vergessener Eintrag lieferte die leere
    Menge und damit eine Ablehnung — immerhin die sichere Richtung, aber erst
    bemerkbar, wenn ein Benutzer davorsteht.

    Zwei Ausnahmen bleiben, beide haengen am *Vorgang* statt am Werkzeug und
    lassen sich in einer Tabellenzeile nicht ausdruecken: der Lebenszyklus
    (Starten, Stoppen und Neustarten sind drei verschiedene Rechte) und die
    Reparatur (`_REPARATUR_RECHTE`).

    Eine **unbekannte** Reparatur-Kennung verlangt die Vereinigung beider
    Rechte. Sie ist kein gueltiger Vorgang und wird nie ausgefuehrt — der
    Ausfuehrungszweig weist sie als `AI_ACTION_TOOL_NOT_ALLOWED` ab und der
    Vorschlag endet als `failed`, sichtbar fuer den Menschen davor. Eine leere
    Menge staende dem im Weg: Bestaetigung und Ausfuehrung braechen dann schon
    an der Rechtepruefung mit `AI_ACTION_ACCESS_REVOKED` ab — eine Meldung, die
    von entzogenen Rechten spricht, wo eine manipulierte Nutzlast vorliegt.
    Strenger als jede gueltige Wahl bleibt die Vereinigung trotzdem.
    """
    if tool_name == "propose_server_lifecycle":
        recht = {
            "start": "server.start",
            "stop": "server.stop",
            "restart": "server.restart",
        }.get(str(payload.get("operation")), "")
        return (recht,) if recht else ()
    if tool_name == "propose_server_repair":
        recht = _REPARATUR_RECHTE.get(str(payload.get("action")), "")
        if recht:
            return (recht,)
        return tuple(_REPARATUR_RECHTE.values())
    werkzeug = WERKZEUGE.get(tool_name)
    return (werkzeug.recht,) if werkzeug and werkzeug.recht else ()


def _require_tool_permission(
    db: Session, user: User, server_id: int | None, tool_name: str, payload: dict
) -> None:
    permissions = _permission_for(tool_name, payload)
    if not permissions:
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
        for permission in permissions:
            if not permission_service.has_global_permission(db, user, permission):
                raise AiActionValidationError("AI-Aktion ist nicht erlaubt")
        return

    if server_id is None:
        raise AiActionValidationError("AI-Aktion ist nicht erlaubt")
    for permission in permissions:
        if not permission_service.has_server_permission(db, user, server_id, permission):
            raise AiActionValidationError("AI-Aktion ist nicht erlaubt")
    if tool_name in {
        "propose_config_update",
        "propose_config_patch",
        "propose_config_set",
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


def _config_set_payload(
    db: Session, server: Server, arguments: dict
) -> tuple[dict, dict, str | None]:
    """Prueft ein sektionsbewusstes Setzen und baut Nutzlast und Vorschau.

    **Warum dieses Werkzeug neben dem Patch existiert.** Der Patch sucht Text
    und ersetzt ihn. Fuer eine Formatdatei ist das messbar das falsche
    Verfahren: am 18.08.2026 hing ein ausgefuehrter Patch einen zweiten
    ``[ServerSettings]``-Block ans Dateiende, und ARK liest nur den ersten. Die
    Werte waren richtig, die Wirkung war null. Mit Sektion und Schluessel als
    Argument kann das nicht passieren.

    **Und warum er den Wunsch mitspeichert.** Ein geschriebener Wert haelt nur,
    solange der Prozess ihn laesst, dem die Datei gehoert — gemessen auf Server
    107, wo ein ausgefuehrter Vorschlag vier Tage spaeter nicht mehr in der
    Datei stand. Deshalb ist das Setzen hier zweiteilig: die Datei jetzt, und
    der Wunsch fuer jeden kuenftigen Start.

    ``expected_revision: null`` legt eine fehlende Datei an — derselbe Weg wie
    bei ``propose_config_update``.
    """
    if set(arguments) != {"path", "expected_revision", "entries"}:
        raise AiActionValidationError("Set-Tool hat ungueltige Argumente")
    path = _config_path(arguments["path"])
    expected = arguments["expected_revision"]
    if expected is not None and (
        not isinstance(expected, str)
        or not expected.startswith("sha256:")
        or len(expected) != 71
    ):
        raise AiActionValidationError(
            "expected_revision fehlt oder ist ungueltig. Zuerst read_config aufrufen."
        )

    roh = arguments["entries"]
    if not isinstance(roh, list) or not roh:
        raise AiActionValidationError("Es fehlt mindestens ein Eintrag")
    if len(roh) > MAX_PATCH_EDITS:
        raise AiActionValidationError(
            f"Hoechstens {MAX_PATCH_EDITS} Eintraege je Vorschlag"
        )

    eintraege: list[tuple[str, str, str]] = []
    for nummer, eintrag in enumerate(roh, start=1):
        if not isinstance(eintrag, dict) or set(eintrag) != {"section", "key", "value"}:
            raise AiActionValidationError(
                f"Eintrag {nummer} braucht genau 'section', 'key' und 'value'"
            )
        sektion, schluessel, wert = eintrag["section"], eintrag["key"], eintrag["value"]
        if not isinstance(sektion, str) or not isinstance(schluessel, str):
            raise AiActionValidationError(f"Eintrag {nummer} ist unvollstaendig")
        if not isinstance(wert, str):
            raise AiActionValidationError(f"Eintrag {nummer} hat keinen Wert")
        eintraege.append((sektion, schluessel, wert))

    try:
        current = read_server_text(db, server_id=server.id, relative_path=path)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        current = None
    current_revision = str(current["revision"]) if current is not None else None
    if expected is None and current is not None:
        raise AiActionValidationError(
            "Fuer eine vorhandene Datei ist expected_revision Pflicht"
        )
    if current_revision != expected:
        raise AiActionValidationError("Config wurde seit der Analyse veraendert")

    alt = str(current["content"]) if current is not None else ""
    neu = alt
    for sektion, schluessel, wert in eintraege:
        # `ini_setzen` traegt hier die Geheimnisgrenze und die
        # Struktur-Pruefung; ein unzulaessiger Eintrag wirft, bevor irgendetwas
        # geschrieben wird.
        neu = ini_setzen(neu, sektion, schluessel, wert)

    # Der Wunschspeicher prueft dieselben Werte noch einmal gegen seine eigenen
    # Grenzen (Pfadform, Dateiendung, Anzahl). Das geschieht **vor** dem
    # Vorschlag, damit ein Wunsch, der spaeter nicht gespeichert werden koennte,
    # gar nicht erst als Vorschlag im Chat steht.
    #
    # Ohne Schalter: ein gesetzter Wert ist immer auch ein gewollter Wert. Ein
    # Feld "dauerhaft ja/nein" waere eine Entscheidung, die das Modell bei jedem
    # Aufruf neu faellen muesste — und beim ersten Vergessen stuende der
    # Benutzer wieder vor einer Aenderung, die still verschwindet.
    server_config_wishes.setze(
        server.config_wishes_json, datei=path, eintraege=eintraege
    )

    diff_lines = list(
        difflib.unified_diff(
            redact_sensitive_text(alt).splitlines(),
            redact_sensitive_text(neu).splitlines(),
            fromfile=f"{path}:vorher",
            tofile=f"{path}:nachher",
            lineterm="",
        )
    )
    gekuerzt = len(diff_lines) > MAX_DIFF_LINES
    preview = {
        "path": path,
        "change": "create" if current is None else "set",
        "diff": "\n".join(diff_lines[:MAX_DIFF_LINES])[:MAX_DIFF_CHARS],
        "diff_truncated": gekuerzt,
        "entries": len(eintraege),
        # Der Wert wird beim naechsten Start erneut geschrieben, deshalb ist ein
        # Neustart hier kein Nebensatz, sondern der Weg, auf dem die Aenderung
        # wirksam wird.
        "restart_required": True,
        "persistent": True,
    }
    return (
        {
            "path": path,
            "entries": [
                {"section": s, "key": k, "value": v} for s, k, v in eintraege
            ],
        },
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


#: Die fuenf Abschnitte, an denen `is_guardian_enabled` haengt.
#:
#: Sie stehen hier ein zweites Mal, weil `is_guardian_enabled` ein
#: `Blueprint`-Objekt liest und die Ableitung ein Dict baut. Wer einen sechsten
#: Abschnitt einfuehrt, muss beide Stellen anfassen — deshalb nennt der Test
#: `test_guardian_overrides` die Mengen ausdruecklich gegeneinander.
_GUARDIAN_ABSCHNITTE = ("health", "logs", "diagnostics", "recovery", "backups")


def _blueprint_change_payload(
    db: Session, arguments: dict, *, reparatur: bool = False
) -> tuple[dict, dict]:
    """Baut den abgeleiteten Blueprint **schon beim Vorschlagen**.

    Nicht erst beim Ausfuehren, und das ist der Punkt: der Mensch soll sehen,
    was herauskommt, bevor er zustimmt — nicht eine Liste von Aenderungen, deren
    Zusammenwirken er im Kopf nachvollziehen muesste. Ein Vorschlag, dessen
    Ergebnis das Schema verletzt, entsteht damit gar nicht erst; sonst
    scheiterte er nach der Bestaetigung, und jemand haette einer Aenderung
    zugestimmt, die es nicht gibt.

    Die Session dieses Requests geht an `derived_payload` weiter, weil dort der
    Ueberschreibschutz haengt: zeigt ``new_id`` auf einen Community-Blueprint,
    auf dem Server liegen, ist das keine Ableitung mehr, sondern eine Aenderung
    an diesen Servern. Wer das zaehlt, muss denselben Bestand sehen wie dieser
    Request.

    ``reparatur`` heisst: der Vorschlag entsteht in einem Reparaturlauf. Dann
    muss das Ergebnis Guardian mitbringen — sonst ist die Ableitung ein Ziel,
    auf dem der Wachmann blind waere.

    Das kann die Ableitung nicht selbst verschulden: `AENDERBARE_PFADE` kennt
    fuenf Pfade, alle unter `meta` und `runtime`, und alles uebrige wird aus der
    Vorlage tief kopiert — ein abgeleiteter Blueprint traegt die Guardian-Bloecke
    seiner Vorlage immer. Was diese Zeilen abfangen, ist deshalb die
    **guardianlose Vorlage**: leitet ein Reparaturlauf von ihr ab und stellt den
    Server anschliessend darauf um, meldet der Agent nie wieder etwas ueber
    diesen Server — und die Kampagne wartet auf einen Nachweis
    (`wirkung_belegt`), den es dann nicht mehr geben kann. Der Vorfall waere
    nicht behoben, sondern unbeobachtbar geworden.
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
            db=db,
        )
    except HTTPException as exc:
        raise AiActionValidationError(str(exc.detail)) from exc

    mit_guardian = any(nutzlast.get(name) is not None for name in _GUARDIAN_ABSCHNITTE)
    if reparatur and not mit_guardian:
        raise AiActionValidationError(
            f"'{arguments['source_id']}' bringt keine Guardian-Ueberwachung mit. "
            "Eine Ableitung davon koennte diesen Server nicht mehr beobachten — "
            "waehle eine Vorlage mit Guardian."
        )

    quelle = blueprint_service.blueprint_view(str(arguments["source_id"]))["blueprint"]
    payload = {"blueprint": nutzlast}
    preview = {
        "operation": "blueprint_change",
        "source_id": arguments["source_id"],
        "new_id": arguments["new_id"],
        # Ob der abgeleitete Blueprint ueberwacht wird, steht auf der Karte.
        # Ein Mensch, der einer Ableitung zustimmt, soll nicht nachrechnen
        # muessen, ob er dabei den Wachmann verliert.
        "guardian_enabled": mit_guardian,
        # Was sich wirklich unterscheidet — die Zeile, die der Bestaetigende
        # liest. `changes` allein waere die Absicht, nicht das Ergebnis.
        "env_before": (quelle.get("runtime") or {}).get("env") or {},
        "env_after": (nutzlast.get("runtime") or {}).get("env") or {},
        "image_before": (quelle.get("runtime") or {}).get("image"),
        "image_after": (nutzlast.get("runtime") or {}).get("image"),
        # Die Startzeile steht hier aus demselben Grund wie Image und Umgebung:
        # seit `runtime.startup` aenderbar ist, entscheidet sie mit, was der
        # Container spaeter wirklich ausfuehrt. Fehlte sie in dieser
        # Gegenueberstellung, bestaetigte ein Mensch einen Startbefehl, den er
        # nie zu sehen bekommen hat.
        "startup_before": (quelle.get("runtime") or {}).get("startup"),
        "startup_after": (nutzlast.get("runtime") or {}).get("startup"),
        "restart_required": False,
    }
    return payload, preview


def _blueprint_delete_payload(db: Session, arguments: dict) -> tuple[dict, dict]:
    """Prueft das Loeschen eines Community-Blueprints schon beim Vorschlagen."""
    from services import blueprint_service
    from models import Server

    if set(arguments) != {"blueprint_id"}:
        raise AiActionValidationError("Blueprint-Delete-Tool hat ungueltige Argumente")
    blueprint_id = str(arguments["blueprint_id"])
    try:
        ansicht = blueprint_service.blueprint_view(blueprint_id)
    except HTTPException as exc:
        raise AiActionValidationError(str(exc.detail)) from exc
    if ansicht.get("origin") == "native" or not ansicht.get("editable"):
        raise AiActionValidationError("Native Blueprints koennen nicht geloescht werden")

    anzahl = db.query(Server).filter(Server.game_type == blueprint_id).count()
    if anzahl > 0:
        raise AiActionValidationError(
            f"Blueprint '{blueprint_id}' wird noch von {anzahl} Server(n) verwendet und kann nicht geloescht werden."
        )

    payload = {"blueprint_id": blueprint_id}
    preview = {
        "operation": "blueprint_delete",
        "blueprint_id": blueprint_id,
        "blueprint_name": (ansicht.get("blueprint") or {}).get("meta", {}).get("name") or blueprint_id,
        # `path` ist das eine Vorschaufeld, das die Bestaetigungskarte immer
        # rendert — unabhaengig von ihrer Tatsachenliste. Ein Loeschvorschlag
        # ohne Ziel auf der Karte waere die Frage "loeschen?" ohne das Objekt;
        # deshalb steht die ID hier ein zweites Mal unter diesem Namen.
        "path": blueprint_id,
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
            # Ein leeres Feld ist eine Aussage und kein vergessener Wert — was
            # es aussagt, haengt inzwischen aber am Feld ab. Bei den
            # Kontingenten heisst es "unbegrenzt"; bei `max_memory_entries`
            # heisst es "nichts hinterlegt", und welche Zahl daraus beim Merken
            # wird, entscheidet allein
            # `ai_limit_service.resolve_scope_memory_limit`.
            # Hier bleibt das bewusst ohne Fallunterscheidung: dieser Bau
            # schreibt weiter genau das, was der Betreiber gesagt hat. Dass das
            # Modell den Unterschied kennt, *bevor* es `null` setzt, leistet der
            # Werkzeugtext in `ai_action_service` — er ist die einzige Stelle,
            # an der ein "unbegrenztes Gedaechtnis" noch abbiegen kann.
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

    allowed_keys = {"workshop_id", "action", "name"}
    if not (set(arguments).issubset(allowed_keys) and {"workshop_id", "action"}.issubset(set(arguments))):
        raise AiActionValidationError("Mod-Tool hat ungueltige Argumente")
    workshop_id = arguments["workshop_id"]
    if not isinstance(workshop_id, str) or not workshop_id.isdigit() or len(workshop_id) > 20:
        raise AiActionValidationError("Ungueltige Workshop-Kennung")
    action = arguments["action"]
    if action not in {"install", "update", "reinstall"}:
        raise AiActionValidationError("Ungueltige Mod-Aktion")
    name = str(arguments.get("name") or "")[:256] or None

    plugin = get_plugin(server.game_type)
    if plugin is None or not getattr(plugin, "supports_mods", False):
        raise AiActionValidationError("Dieses Spiel unterstuetzt keine Workshop-Mods")

    existing = (
        db.query(Mod)
        .filter(Mod.server_id == server.id, Mod.workshop_id == workshop_id)
        .first()
    )
    payload = {"workshop_id": workshop_id, "action": action}
    if name:
        payload["name"] = name
    bekannt = redact_sensitive_text(
        str((existing.name if existing and existing.name else name) or "")
    )[:128] or None
    preview = {
        "operation": f"mod_{action}",
        "workshop_id": workshop_id,
        "known_name": bekannt,
        # `path` ist das, was die Karte als Ueberschrift zeigt und was die
        # Rueckfrage einsetzt ("Die Mod „{{path}}“ ... einspielen?"). Ohne den
        # Schluessel stand dort ein leeres Paar Anfuehrungszeichen: der
        # Bestaetigende sollte zustimmen, ohne zu lesen, wozu. Der Name ist die
        # Auskunft, die Kennung der Rueckfall — eine frisch entdeckte Mod hat
        # noch keinen Namen im Panel.
        "path": bekannt or workshop_id,
        "already_installed": existing is not None,
        # **Installiert heisst nicht aktiv.** Eine vorhandene, aber
        # ausgeschaltete Mod laedt der Installationspfad zwar herunter, in die
        # Startzeile kommt sie trotzdem nicht — der Server startet ohne sie,
        # und eine Erfolgsmeldung waere dann falsch. Der Zustand steht deshalb
        # in der Vorschau, damit das Modell danach `propose_mod_toggle`
        # nachlegen kann. Ihn hier still mitzusetzen waere eine zweite
        # Wirkung unter einem fremden Werkzeugnamen.
        "currently_enabled": bool(existing.enabled) if existing is not None else True,
        "current_status": server.status,
        # Eine Mod wird beim Start geladen — ohne Neustart wirkt sie nicht.
        "restart_required": True,
    }
    return payload, preview


def _mod_toggle_payload(db: Session, server: Server, arguments: dict) -> tuple[dict, dict]:
    """Erwartet die Argumente *ohne* Begruendung und ohne `server_id`.

    Die Mod muss es geben: einen Schalter an etwas umzulegen, das nicht
    installiert ist, ergibt keinen Vorschlag, sondern einen Hinweis — und der
    ist als Formfehler die guenstigere Auskunft als ein Vorschlag, der bei der
    Ausfuehrung scheitert.
    """
    from games import get_plugin
    from models import Mod

    allowed_keys = {"workshop_id", "enabled"}
    if set(arguments) != allowed_keys:
        raise AiActionValidationError("Mod-Schalter hat ungueltige Argumente")
    workshop_id = arguments["workshop_id"]
    if not isinstance(workshop_id, str) or not workshop_id.isdigit() or len(workshop_id) > 20:
        raise AiActionValidationError("Ungueltige Workshop-Kennung")
    enabled = arguments["enabled"]
    if not isinstance(enabled, bool):
        raise AiActionValidationError("`enabled` muss true oder false sein")

    plugin = get_plugin(server.game_type)
    if plugin is None or not getattr(plugin, "supports_mods", False):
        raise AiActionValidationError("Dieses Spiel unterstuetzt keine Workshop-Mods")

    vorhanden = (
        db.query(Mod)
        .filter(Mod.server_id == server.id, Mod.workshop_id == workshop_id)
        .first()
    )
    if vorhanden is None:
        raise AiActionValidationError(
            "Diese Mod ist auf dem Server nicht installiert — erst "
            "propose_mod_install, dann schalten"
        )

    payload = {"workshop_id": workshop_id, "enabled": enabled}
    bekannt = redact_sensitive_text(str(vorhanden.name or ""))[:128] or None
    preview = {
        "operation": "mod_enable" if enabled else "mod_disable",
        "workshop_id": workshop_id,
        "known_name": bekannt,
        # Siehe `_mod_install_payload`: die Rueckfrage der Karte setzt `path`
        # ein, nicht `known_name`.
        "path": bekannt or workshop_id,
        "was_enabled": bool(vorhanden.enabled),
        "current_status": server.status,
        # Die aktive Modliste wird beim Bau des Containers in die Startzeile
        # gerendert (`games/base.active_mod_ids`). Vorher aendert sich nichts.
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


def _blueprint_startwerte(server: Server) -> tuple[int, int]:
    """Ruhezeit und Timeout, wie der Compiler sie ohne Uebersteuerung setzt.

    Dieselben Rueckfallwerte wie in `compile_guardian_config` (30/300): eine
    Blueprint ohne Startfenster bekommt dort genau diese Zahlen, also sind sie
    auch hier die Wahrheit.
    """
    from games import get_plugin

    plugin = get_plugin(server.game_type)
    blueprint = plugin.get_blueprint() if plugin else None
    health = blueprint.health if blueprint else None
    start = health.startup if health and health.startup else None
    if start is None:
        return 30, 300
    return int(start.grace_period_seconds), int(start.timeout_seconds)


def _guardian_tuning_payload(server: Server, arguments: dict) -> tuple[dict, dict]:
    """Nutzlast fuer `propose_guardian_tuning` — Zahlen aus einer festen Menge.

    Der Fall dahinter: die Blueprint gilt fuer **jeden** Server ihres Spiels und
    kann nicht wissen, dass ausgerechnet auf dieser Node zwoelf Instanzen um
    acht Gigabyte streiten. Guardian sieht dort einen Server, der nicht in
    dreissig Sekunden hochkommt, startet ihn neu, sieht es wieder — und nach
    drei Anlaeufen steht er in Quarantaene, obwohl nichts kaputt ist ausser der
    Erwartung.

    Die Enge ist wieder der Zweck. Erlaubt sind ausschliesslich die Schluessel
    aus `GUARDIAN_STELLSCHRAUBEN`, alle Werte sind ganze Zahlen mit Ober- und
    Untergrenze, und ausserhalb des Bereichs wird **abgewiesen** statt geklemmt:
    das Modell soll erfahren, dass es danebenlag, statt stillschweigend etwas
    anderes zu bekommen, als es vorgeschlagen hat. Geklemmt wird trotzdem noch
    einmal im Compiler — dort gegen alles, was nicht durch dieses Werkzeug kam.

    `reset` ist der Rueckweg und schliesst alles andere aus. Eine Nutzlast, die
    zugleich zuruecksetzt und setzt, haette zwei Bedeutungen und keine davon
    ganz.
    """
    from services.guardian_runtime_compiler import (
        GUARDIAN_STELLSCHRAUBEN,
        gelesene_uebersteuerung,
    )

    # `reason` und `expected_effect` sind hier schon abgetrennt (`rest`), wie
    # bei jedem Payload-Bauer.
    unbekannt = set(arguments) - (set(GUARDIAN_STELLSCHRAUBEN) | {"reset"})
    if unbekannt:
        raise AiActionValidationError(
            "Unbekannte Guardian-Stellschraube: " + ", ".join(sorted(unbekannt))
        )

    zuruecksetzen = bool(arguments.get("reset"))
    werte: dict[str, int] = {}
    for name, (unten, oben) in GUARDIAN_STELLSCHRAUBEN.items():
        if name not in arguments:
            continue
        roh = arguments[name]
        if isinstance(roh, bool) or not isinstance(roh, (int, float)):
            raise AiActionValidationError(f"{name} muss eine Zahl sein")
        wert = int(roh)
        if wert < unten or wert > oben:
            raise AiActionValidationError(
                f"{name} liegt ausserhalb von {unten}..{oben}"
            )
        werte[name] = wert

    if zuruecksetzen and werte:
        raise AiActionValidationError(
            "Zuruecksetzen und Setzen schliessen sich aus"
        )
    if not zuruecksetzen and not werte:
        raise AiActionValidationError("Keine Guardian-Stellschraube angegeben")

    vorher = gelesene_uebersteuerung(server)
    # Der Nachtrag ist ein Nachtrag: was das Modell nicht nennt, bleibt stehen.
    # Sonst hiesse jede Anpassung einer einzelnen Zahl, alle anderen zu
    # verlieren — und das Modell muesste sie in jedem Aufruf mitschreiben.
    nachher: dict[str, int] = {} if zuruecksetzen else {**vorher, **werte}

    # Der Agent-Vertrag verlangt startup timeout > grace. Geprueft wird gegen
    # die **wirksamen** Werte: was die Uebersteuerung nicht setzt, kommt aus
    # der Blueprint — genau wie beim Kompilieren. Nur so faellt auch der Fall
    # auf, in dem das Modell einen Timeout unter eine Ruhezeit senkt, die
    # allein in der Blueprint steht; sonst wuerde der Compiler die explizite
    # Absenkung stillschweigend wieder hochziehen. Die Abweisung ist die
    # Rueckmeldung, aus der das Modell den naechsten Versuch baut. Geprueft
    # wird nur, wenn dieser Aufruf eine der beiden Schrauben anfasst — ein
    # Altbestand ist Sache der Compiler-Klemmung, nicht dieser Aenderung.
    if not zuruecksetzen and (
        "startup_grace_period_seconds" in werte or "startup_timeout_seconds" in werte
    ):
        grace = nachher.get("startup_grace_period_seconds")
        timeout = nachher.get("startup_timeout_seconds")
        if grace is None or timeout is None:
            blueprint_grace, blueprint_timeout = _blueprint_startwerte(server)
            grace = blueprint_grace if grace is None else grace
            timeout = blueprint_timeout if timeout is None else timeout
        if timeout <= grace:
            raise AiActionValidationError(
                "startup_timeout_seconds muss groesser sein als "
                f"startup_grace_period_seconds — wirksam waeren timeout={timeout} "
                f"und grace={grace} (nicht gesetzte Werte kommen aus der "
                "Blueprint); der Agent lehnt die Kombination ab"
            )
    payload = {"overrides": nachher, "reset": zuruecksetzen}
    preview = {
        "operation": "guardian_tuning",
        "description": (
            "Guardian-Einstellungen dieses Servers auf die Blueprint zuruecksetzen"
            if zuruecksetzen
            else "Guardian fuer diesen Server anders einstellen"
        ),
        # Beide Staende in der Karte: wer bestaetigt, soll sehen, was sich
        # aendert, und nicht nur, was danach gilt.
        "before": vorher,
        "after": nachher,
        "changed": sorted(werte) if not zuruecksetzen else sorted(vorher),
        "current_status": server.status,
        "restart_required": False,
    }
    return payload, preview


def _aktuelle_restart_zeiten(server: Server) -> list[str]:
    """Die heute gesetzten festen Neustartzeiten — für die Vorher-Spalte der Karte."""
    roh = server.restart_times_utc or server.restart_time_utc or ""
    return [teil.strip() for teil in roh.split(",") if teil.strip()]


def _restart_schedule_payload(server: Server, arguments: dict) -> tuple[dict, dict]:
    """Nutzlast für `propose_restart_schedule_set` — die Panel-Felder, nichts sonst.

    Dieselben Grenzen wie am Panel-Endpunkt (`schemas/server.py`): Intervall
    1–168 Stunden, höchstens 12 feste Zeiten, striktes ``HH:MM`` beim
    Speichern. Gelesen wird nachsichtig (``"8:00"`` ist eindeutig,
    `ai_task_service.uhrzeit_pruefen`), gespeichert streng — ein Formfehler
    kostet das Modell eine Runde, nie den Zeitplan.

    Intervall und feste Zeiten schließen sich aus, wie überall sonst: der
    Vorschlag verlangt **genau eines** von beiden, solange eingeschaltet wird.
    Beim Ausschalten bleibt der Plan stehen (Wiedereinschalten erinnert ihn),
    aber neue Planangaben wären eine Aussage ohne Wirkung und werden abgewiesen.
    """
    unbekannt = set(arguments) - {"enabled", "interval_hours", "times"}
    if unbekannt:
        raise AiActionValidationError(
            "Unbekanntes Feld im Neustart-Zeitplan: " + ", ".join(sorted(unbekannt))
        )
    enabled = arguments.get("enabled")
    if not isinstance(enabled, bool):
        raise AiActionValidationError("enabled muss wahr oder falsch sein")

    intervall = arguments.get("interval_hours")
    zeiten = arguments.get("times")
    if enabled:
        if (intervall is None) == (zeiten is None):
            raise AiActionValidationError(
                "Gib genau eines an: interval_hours (alle N Stunden) oder "
                "times (feste Uhrzeiten)."
            )
    elif intervall is not None or zeiten is not None:
        raise AiActionValidationError(
            "enabled:false schaltet den Auto-Neustart aus — ohne Planangaben. "
            "Der bisherige Plan bleibt für ein späteres Einschalten stehen."
        )

    times_csv: str | None = None
    if intervall is not None:
        if isinstance(intervall, bool) or not isinstance(intervall, int):
            raise AiActionValidationError("interval_hours muss eine ganze Zahl sein")
        if not 1 <= intervall <= 168:
            raise AiActionValidationError(
                "Das Neustart-Intervall muss zwischen 1 und 168 Stunden liegen"
            )
    if zeiten is not None:
        if not isinstance(zeiten, list) or not zeiten:
            raise AiActionValidationError("times muss eine Liste von 'HH:MM'-Zeiten sein")
        normalisiert = [ai_task_service.uhrzeit_pruefen(zeit) for zeit in zeiten]
        from schemas.server import _validate_restart_times

        try:
            times_csv = _validate_restart_times(",".join(normalisiert))
        except ValueError as exc:
            raise AiActionValidationError(str(exc)) from exc
        if not times_csv:
            raise AiActionValidationError("times muss mindestens eine Uhrzeit enthalten")

    payload: dict = {"enabled": enabled}
    if intervall is not None:
        payload["interval_hours"] = intervall
    if times_csv is not None:
        payload["times_csv"] = times_csv

    preview = {
        "operation": "restart_schedule",
        "enabled": enabled,
        "mode": "interval" if intervall is not None else ("fixed" if times_csv else None),
        "interval_hours": intervall,
        "times": times_csv.split(",") if times_csv else [],
        # Beide Stände auf der Karte: wer bestätigt, soll sehen, was sich
        # ändert — nicht nur, was danach gilt.
        "before": {
            "enabled": bool(server.auto_restart),
            "interval_hours": server.restart_interval_hours,
            "times": _aktuelle_restart_zeiten(server),
        },
        "current_status": server.status,
        "restart_required": False,
    }
    return payload, preview


def _backup_schedule_payload(server: Server, arguments: dict) -> tuple[dict, dict]:
    """Nutzlast für `propose_backup_schedule_set` — ein Nachtrag, kein Vollbild.

    Was das Modell nicht nennt, bleibt stehen: „Aufbewahrung auf 10" soll
    nicht verlangen, dass es Intervall und Vor-Start-Schalter fehlerfrei
    mitschreibt. Dieselben Grenzen wie am Panel-Endpunkt
    (`routers/backups.py::BackupSettingsRequest`): Intervall 0–720 Stunden
    (0 = aus), Aufbewahrung 1–100.
    """
    unbekannt = set(arguments) - {"backup_on_start", "interval_hours", "retention_count"}
    if unbekannt:
        raise AiActionValidationError(
            "Unbekanntes Feld im Backup-Zeitplan: " + ", ".join(sorted(unbekannt))
        )
    if not arguments:
        raise AiActionValidationError(
            "Keine Änderung angegeben. Nenne mindestens eines von "
            "backup_on_start, interval_hours, retention_count."
        )

    payload: dict = {}
    if "backup_on_start" in arguments:
        if not isinstance(arguments["backup_on_start"], bool):
            raise AiActionValidationError("backup_on_start muss wahr oder falsch sein")
        payload["backup_on_start"] = arguments["backup_on_start"]
    if "interval_hours" in arguments:
        wert = arguments["interval_hours"]
        if isinstance(wert, bool) or not isinstance(wert, int):
            raise AiActionValidationError("interval_hours muss eine ganze Zahl sein")
        if not 0 <= wert <= 720:
            raise AiActionValidationError(
                "Das Backup-Intervall muss zwischen 0 (aus) und 720 Stunden liegen"
            )
        payload["interval_hours"] = wert
    if "retention_count" in arguments:
        wert = arguments["retention_count"]
        if isinstance(wert, bool) or not isinstance(wert, int):
            raise AiActionValidationError("retention_count muss eine ganze Zahl sein")
        if not 1 <= wert <= 100:
            raise AiActionValidationError(
                "Die Aufbewahrung muss zwischen 1 und 100 Backups liegen"
            )
        payload["retention_count"] = wert

    preview = {
        "operation": "backup_schedule",
        "changes": dict(payload),
        "before": {
            "backup_on_start": bool(server.backup_on_start),
            "interval_hours": server.backup_interval_hours,
            "retention_count": server.backup_retention_count,
        },
        "current_status": server.status,
        "restart_required": False,
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


#: Die Felder, die `propose_task_set` entgegennimmt. `task_id` steht bewusst
#: dabei: sie entscheidet ueber Anlegen oder Aendern und gehoert damit zur
#: Nutzlast, nicht zur Vorschau.
_AUFGABEN_FELDER = frozenset({
    "task_id", "title", "instruction", "kind", "enabled", "plan_kind",
    "time_of_day", "weekdays", "interval_hours", "once_at", "timezone",
    "channel",
})


def _task_set_payload(db: Session, user: User, arguments: dict) -> tuple[dict, dict]:
    """Nutzlast fuer `propose_task_set` — anlegen oder aendern.

    Der Payload-Bau prueft **vollstaendig**: Zeitzone, Plan, Art, Zustellweg,
    Rechte und die autonome Freigabe. Das ist nicht nur fuer die Vorschau da.
    Ein Modell, dessen Vorschlag erst beim Klick scheitert, hat dem Benutzer
    eine Karte hingelegt, die nicht haelt — und im Chat steht dann eine
    Fehlermeldung an der Stelle, an der eine Zusage stand.

    Gespeichert wird hier nichts. `vorschau` arbeitet auf einer losen Aufgabe;
    die eigentliche Aenderung passiert erst in `_execute_task_set`, und dort
    laufen dieselben Pruefungen erneut.
    """
    if set(arguments) - _AUFGABEN_FELDER:
        raise AiActionValidationError("Aufgaben-Tool hat ungueltige Argumente")
    roh = arguments.get("task_id")
    # **Eine leere Kennung heisst dasselbe wie keine: anlegen.** Das Schema sagt
    # "weglassen legt neu an", aber ein Modell kann ein Feld schlecht weglassen,
    # das es gerade gelesen hat — es schickt stattdessen `""`. Die Unterscheidung
    # zwischen "nicht genannt" und "leer genannt" traegt hier nichts und kostete
    # im Betrieb die haeufigste aller Aufgaben: das Anlegen der ersten.
    if isinstance(roh, str) and not roh.strip():
        roh = None
    if roh is not None and not isinstance(roh, str):
        raise AiActionValidationError("task_id muss eine Kennung aus list_tasks sein")
    task_id = roh.strip() if isinstance(roh, str) else None

    felder = {name: wert for name, wert in arguments.items() if name != "task_id"}
    if task_id is not None and not felder:
        raise AiActionValidationError(
            "Es wurde nichts genannt, das geaendert werden soll"
        )

    preview = ai_task_service.vorschau(db, user=user, felder=felder, task_id=task_id)
    return {"task_id": task_id, "felder": felder}, preview


def _task_delete_payload(db: Session, user: User, arguments: dict) -> tuple[dict, dict]:
    """Nutzlast fuer `propose_task_delete`.

    Die Aufgabe wird **jetzt** aufgeschlagen, damit auf der Karte ihr Name und
    ihr Zeitplan stehen und nicht nur eine Kennung. "Aufgabe
    a3f2c1…-… loeschen?" ist keine Frage, die jemand beantworten kann.
    """
    if set(arguments) != {"task_id"}:
        raise AiActionValidationError("Aufgaben-Tool hat ungueltige Argumente")
    aufgabe = ai_task_service.eigene_aufgabe(
        db, user=user, task_id=arguments["task_id"]
    )
    return (
        {"task_id": aufgabe.id},
        {
            "operation": "task_delete",
            "task_id": aufgabe.id,
            "title": aufgabe.title,
            "plan": ai_task_service.plan_text(aufgabe),
            "kind": aufgabe.kind,
            "enabled": bool(aufgabe.enabled),
        },
    )


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
    # Verzoegert wegen des Importzyklus: der Stream-Service importiert dieses
    # Modul beim Laden.
    from services.ai_stream_service import GuardianRahmenUnlesbar, guardian_aus_zustand

    run = db.get(AiRun, run_id)
    if run is None:
        return None
    try:
        # **Derselbe** Parser wie in jeder Laufrunde, keine zweite Auslegung.
        # Hier stand eine Abschrift mit eigener Semantik, und sie war bereits
        # gedriftet: ein vorhandener, aber nicht-dict Rahmen galt hier als
        # „kein Guardian" und liess `execute_proposal` ohne Backup-Nachweis
        # und ohne Serverbindung weiterlaufen — waehrend dieselbe Lage im
        # Stream ausdruecklich wirft, weil der Verlust des Rahmens die
        # gefaehrliche Richtung ist.
        return guardian_aus_zustand(ai_run_service.zustand_lesen(run) or {})
    except GuardianRahmenUnlesbar as exc:
        # Ein unlesbarer Rahmen ist kein Freibrief. Er heisst: dieser Vorschlag
        # stammt aus einem Lauf, dessen Bedingungen nicht mehr feststellbar sind
        # — und dann wird nicht ausgefuehrt.
        raise AiActionStateError("AI_BACKUP_UNVERIFIED") from exc


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


#: Der Payload-Bau der **globalen** Schreibwerkzeuge — Werkzeugname → Bauer.
#:
#: Hier standen acht `elif`-Zweige mit woertlich demselben Vier-Zeilen-Rumpf,
#: in dem nur die Payload-Funktion variierte. Die beiden dokumentierten
#: Vergangenheitsfehler dieser Kette (eine Sammelklausel schickte jedes zweite
#: Werkzeug in die falsche Payload; die Rechtepruefung stand einmal **hinter**
#: dem Payload-Bau und machte die Ablehnung zum Orakel ueber fremden Bestand)
#: musste jeder neue Zweig aufs Neue vermeiden. Die Tabelle macht beides
#: strukturell: einen Eintrag ohne eigenen Bauer gibt es nicht, und die eine
#: Aufrufstelle prueft das Recht **vor** jedem Bau.
#:
#: Jeder Bauer bekommt dieselben fuenf Groessen; was er nicht braucht, laesst
#: er liegen. Zwei Feinheiten sind Absicht und keine Nachlaessigkeit:
#: `propose_server_create` liest `arguments` (mit `reason`/`expected_effect`),
#: alle anderen `rest` — ohne die beiden Schluessel behalten deren
#: Schluesselmengenpruefungen ihre exakte Form. Und nur der Blueprint-Wechsel
#: fragt nach dem Guardian-Rahmen: in einer Reparatur ist er ein anderer
#: Vorgang.
_GLOBALE_PAYLOADS: dict = {
    "propose_blueprint_change": lambda db, user, rest, arguments, guardian: (
        _blueprint_change_payload(db, rest, reparatur=guardian is not None)
    ),
    "propose_blueprint_delete": lambda db, user, rest, arguments, guardian: (
        _blueprint_delete_payload(db, rest)
    ),
    "propose_server_create": lambda db, user, rest, arguments, guardian: (
        _server_create_payload(db, arguments)
    ),
    "propose_hoster_integration": lambda db, user, rest, arguments, guardian: (
        _hoster_integration_payload(db, user, rest)
    ),
    "propose_hoster_product": lambda db, user, rest, arguments, guardian: (
        _hoster_product_payload(db, user, rest)
    ),
    "propose_ai_tarif_role": lambda db, user, rest, arguments, guardian: (
        _ai_tarif_role_payload(db, user, rest)
    ),
    "propose_task_set": lambda db, user, rest, arguments, guardian: (
        _task_set_payload(db, user, rest)
    ),
    "propose_task_delete": lambda db, user, rest, arguments, guardian: (
        _task_delete_payload(db, user, rest)
    ),
}


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
    aufgabe: "AufgabenKontext | None" = None,
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
    if aufgabe is not None and tool_name not in aufgaben_tools(aufgabe.kind):
        # Dieselbe Durchsetzung an derselben Stelle. Bei `kind='report'` faellt
        # hier **jedes** Schreibwerkzeug heraus: eine Aufgabe, die berichten
        # sollte, kann nichts anfassen, auch wenn das Modell es versucht und
        # auch dann, wenn der Benutzer die autonome Freigabe erteilt hat.
        raise AiActionValidationError(
            "Dieses Werkzeug steht in einer geplanten Aufgabe nicht zur Verfuegung"
        )
    reason, expected_effect = _rationale(arguments, fallback=rationale_fallback)
    rest = {key: value for key, value in arguments.items() if key not in {"reason", "expected_effect"}}

    server: Server | None = None
    bauer = _GLOBALE_PAYLOADS.get(tool_name)
    if bauer is not None:
        # **Das Recht vor der Nutzlast**, fuer jeden Tabelleneintrag an genau
        # dieser einen Stelle: die Bauer lesen den Bestand, ueber den sie
        # urteilen, und ihre Fehlermeldungen reichen ihn woertlich durch. Ohne
        # diese Reihenfolge unterschiede ein Benutzer ohne `blueprints.manage`
        # vorhandene von erfundenen Blueprint-Kennungen an der Meldung, und
        # "Unbekannte Node" waere eine Auskunft an jemanden ohne
        # `servers.create`.
        _require_tool_permission(db, user, None, tool_name, rest)
        payload, preview = bauer(db, user, rest, arguments, guardian)
        expected_revision = None
    elif tool_name in GLOBAL_WRITE_TOOLS:
        # **Der Waechter hinter der Tabelle.** Hier stand frueher
        # `elif tool_name in GLOBAL_WRITE_TOOLS: _server_create_payload(...)`.
        # Das las sich wie eine Mengenzugehoerigkeit, meinte aber genau ein
        # Werkzeug — und jedes zweite globale Schreibwerkzeug waere still in
        # der Servererstellung gelandet und mit "Servererstellung hat
        # ungueltige Argumente" gescheitert, einer Meldung, die auf die
        # falsche Stelle zeigt. Ein neues globales Schreibwerkzeug bekommt
        # einen Eintrag in `_GLOBALE_PAYLOADS`; wer das vergisst, faellt hier
        # auf, statt in der falschen Payload zu landen.
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
        # Fuer Lebenszyklus und Reparatur haengt das Recht am Vorgang, deshalb
        # wird deren Formpruefung hier vorgezogen — sonst bekaeme ein
        # ungueltiger Vorgang die Rechte-Ablehnung statt der Formmeldung, die
        # dem Modell weiterhilft. Beide Pruefungen lesen keinen Zustand; sie
        # verraten also nichts, was die Rechtepruefung schuetzen muesste.
        if tool_name == "propose_server_lifecycle" and rest.get("operation") not in {
            "start", "stop", "restart",
        }:
            raise AiActionValidationError("Ungueltige Lifecycle-Aktion")
        if tool_name == "propose_server_repair":
            if set(rest) != {"action"}:
                raise AiActionValidationError("Reparatur-Tool hat ungueltige Argumente")
            if rest["action"] not in REPARATUREN:
                raise AiActionValidationError("Unbekannte Reparatur")
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
        elif tool_name == "propose_mod_toggle":
            payload, preview = _mod_toggle_payload(db, server, rest)
            expected_revision = None
        elif tool_name == "propose_config_patch":
            payload, preview, expected_revision = _config_patch_payload(db, server.id, rest)
        elif tool_name == "propose_config_set":
            payload, preview, expected_revision = _config_set_payload(db, server, rest)
        elif tool_name == "propose_config_update":
            payload, preview, expected_revision = _config_payload(db, server.id, rest)
        elif tool_name == "propose_server_repair":
            payload, preview = _server_repair_payload(server, rest)
            expected_revision = None
        elif tool_name == "propose_guardian_tuning":
            payload, preview = _guardian_tuning_payload(server, rest)
            expected_revision = None
        elif tool_name == "propose_restart_schedule_set":
            payload, preview = _restart_schedule_payload(server, rest)
            # Kommt der Vorschlag aus einem stehenden Auftrag, wird der Server
            # mit ihm verknüpft: das Panel zeigt dann „Von der KI verwaltet
            # (Aufgabe X)", und eine manuelle Änderung deaktiviert genau X.
            if aufgabe is not None:
                payload["ai_task_id"] = aufgabe.task_id
            expected_revision = None
        elif tool_name == "propose_backup_schedule_set":
            payload, preview = _backup_schedule_payload(server, rest)
            if aufgabe is not None:
                payload["ai_task_id"] = aufgabe.task_id
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
    # Später Import wie beim Anlegen: `ai_autonomy_service` liest
    # `ALWAYS_CONFIRM_TOOLS` aus diesem Modul, am Dateikopf wäre das ein Zirkel.
    from services.ai_autonomy_service import autonomie_grundlage

    # **Die Autonomie wird beim Anlegen entschieden, aber nicht eingefroren.**
    # Zwischen dem Vorschlag und diesem Punkt liegt ein Zeitfenster ohne
    # Obergrenze — ein Vorschlag im Status 'proposed' altert nicht. Ohne diese
    # Prüfung überlebte eine erteilte Autonomie ihren eigenen Widerruf: der
    # Betreiber nimmt `ai.autonomous.use` weg oder schaltet die Freigabe für
    # diesen Server ab, und die bereits angelegte Aktion liefe trotzdem noch
    # ohne Rückfrage. Dieselbe Überlegung wie beim zweiten Backup-Nachweis in
    # `execute_proposal`, nur eine Ebene früher.
    if (
        autonomie_grundlage(
            db, user=user, server_id=proposal.server_id, tool_name=proposal.tool_name
        )
        is None
    ):
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
        name = payload.get("name")
        max_order = db.query(Mod).filter(Mod.server_id == server_id).count()
        db.add(Mod(
            server_id=server_id,
            workshop_id=workshop_id,
            name=name,
            load_order=max_order,
            install_status="pending",
        ))
        db.commit()
    elif payload.get("name") and not existing.name:
        existing.name = payload.get("name")
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


def _execute_mod_toggle(db: Session, *, server_id: int, payload: dict) -> dict:
    """Legt den Schalter an einer installierten Mod um.

    Die drei Zeilen stehen hier und nicht in einem neuen Dienst: der Weg des
    Panels (`routers.mods.update_mod`) haengt an FastAPI-Abhaengigkeiten und
    laesst sich nicht wie `install_mod_bg` einfach aufrufen. Eine
    Dienstschicht fuer einen zweiten Aufrufer waere eine Abstraktion auf
    Vorrat; sie lohnt sich, wenn ein dritter kommt.

    `update_modlist` gehoert trotzdem dazu: bei dateibasierten Mods schreibt
    es die `.disabled`-Marken bzw. die Modlisten-Datei. Bei Spielen, die ihre
    Mods als Startparameter uebergeben (ARK), ist der Aufruf wirkungslos —
    dort entsteht die Liste erst beim Bau des Containers, also mit dem
    naechsten Start.
    """
    from games import get_plugin
    from models import Mod, Server

    workshop_id = str(payload["workshop_id"])
    enabled = bool(payload["enabled"])

    mod = (
        db.query(Mod)
        .filter(Mod.server_id == server_id, Mod.workshop_id == workshop_id)
        .first()
    )
    if mod is None:
        # Zwischen Vorschlag und Klick kann jemand die Mod entfernt haben.
        raise AiActionStateError("AI_ACTION_EXECUTION_FAILED")

    mod.enabled = enabled
    db.commit()

    server = db.get(Server, server_id)
    plugin = get_plugin(server.game_type) if server is not None else None
    if server is not None and plugin is not None:
        try:
            plugin.update_modlist(server)
        except Exception:
            # Die Wahrheit steht in der Spalte, und die ist geschrieben. Ein
            # gescheitertes Nachziehen der Dateien darf den Vorgang nicht als
            # fehlgeschlagen ausweisen — sonst schaltet die KI erneut und
            # kippt den Zustand zurueck.
            logger.warning(
                "Modliste nach dem Schalten nicht aktualisiert server_id=%s",
                server_id,
            )

    return {
        "server_id": server_id,
        "workshop_id": workshop_id,
        "enabled": enabled,
        "restart_required": True,
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


def _execute_guardian_tuning(
    db: Session,
    *,
    server_id: int,
    payload: dict,
    user: User,
    correlation_id: str,
    incident_id: int | None = None,
) -> dict:
    """Schreibt die Uebersteuerung — und nimmt sie zurueck, wenn sie nicht ankommt.

    Der Rueckweg ist der eigentliche Inhalt dieser Funktion. Ohne ihn haengt die
    Guardian-Synchronisation dieses Servers dauerhaft in einem gespeicherten
    Fehler: `compile_and_sync_desired_state` erhoeht die Generation, der Agent
    lehnt die Nutzlast ab oder kann sie nicht, und jeder folgende
    Reconcile-Takt versucht dieselbe abgelehnte Konfiguration erneut. Der Server
    bekaeme von da an gar keine Guardian-Aktualisierung mehr — auch keine
    richtige.

    Deshalb: Stand merken, schreiben, synchronisieren, und bei einem Fehlschlag
    den gemerkten Stand wiederherstellen. Danach steht wieder, was vorher stand,
    und die naechste Synchronisation traegt das hinaus.

    `mark_guardian_configuration_changed` und nicht bloss ein erhoehtes
    `desired_state_generation`: die Funktion setzt zusaetzlich
    `guardian_config_hash` auf NULL, und ohne das haelt der Compiler die
    Konfiguration fuer unveraendert und schickt gar nichts.
    """
    import json as _json

    from models import ChangeEvent
    from services import audit_service, guardian_state_service
    from services.server_lifecycle_service import sync_desired_state_to_agent

    server = db.query(Server).filter(Server.id == server_id).first()
    if server is None:
        raise AiActionStateError("AI_ACTION_TARGET_MISSING")

    neu = payload.get("overrides") or {}
    if not isinstance(neu, dict):
        raise AiActionStateError("AI_ACTION_TOOL_NOT_ALLOWED")
    vorher = server.guardian_overrides_json

    server.guardian_overrides_json = (
        _json.dumps(neu, sort_keys=True, separators=(",", ":")) if neu else None
    )
    guardian_state_service.mark_guardian_configuration_changed(server)
    db.commit()
    db.refresh(server)

    # Ein Server ohne Node hat keinen Agenten, der etwas quittieren koennte —
    # das ist kein Fehlschlag, sondern ein Server, der noch nirgends laeuft.
    if server.node_id is not None and not sync_desired_state_to_agent(db, server):
        server.guardian_overrides_json = vorher
        guardian_state_service.mark_guardian_configuration_changed(server)
        db.commit()
        logger.info(
            "Guardian-Uebersteuerung zurueckgerollt server_id=%s", server_id
        )
        raise AiActionStateError("AI_ACTION_GUARDIAN_SYNC_FAILED")

    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="guardian.overrides.set",
        target_type="server",
        target_id=server.id,
        # Nur die Zahlen, keine Begruendung des Modells: der Audit-Eintrag soll
        # sagen, was gilt, und nicht, was jemand dazu gedacht hat.
        details={"overrides": neu, "generation": server.desired_state_generation},
        correlation_id=correlation_id,
    )
    db.add(ChangeEvent(
        server_id=server.id,
        event_type="guardian_overrides",
        description=(
            "Guardian-Einstellungen dieses Servers auf die Blueprint zurueckgesetzt"
            if not neu
            else "Guardian fuer diesen Server anders eingestellt"
        ),
        # Die Chronikzeile ist zugleich die Herkunftsangabe im Guardian-Reiter
        # (`routers/guardian._herkunft`). Deshalb steht der Vorfall hier: ohne
        # ihn koennte der Reiter zwar sagen "von der KI gesetzt", aber nicht,
        # woraufhin — und genau das ist die Frage, die jemand stellt, der eine
        # unerwartete Zahl sieht.
        details=_json.dumps(
            {"overrides": neu, "source": "ai", "incident_id": incident_id},
            sort_keys=True,
            separators=(",", ":"),
        ),
    ))
    db.commit()
    return {"overrides": neu, "generation": server.desired_state_generation}


@dataclass(frozen=True)
class _AusfuehrungsRahmen:
    """Die gemeinsamen Groessen einer bestaetigten Ausfuehrung — ein Rahmen.

    Jede Ausfuehrungsfunktion bekommt denselben Rahmen und laesst liegen, was
    sie nicht braucht. Die Felder sind die festen Kopien aus
    `execute_proposal` (dort angelegt, damit die Fehlerbehandlung nach einem
    Rollback nicht auf ein abgelaufenes ORM-Objekt greifen muss) plus der
    handelnde Benutzer und der Guardian-Rahmen des Laufs. Die Session geht
    daneben als eigener Parameter mit: sie ist kein Wert des Vorschlags,
    sondern der Ort, an dem dieser Request arbeitet.
    """

    payload: dict
    server_id: int | None
    active_user: User
    correlation_id: str | None
    expected_revision: str | None
    row_id: str
    guardian: GuardianKontext | None
    tool_name: str


@dataclass(frozen=True)
class _Ausgefuehrt:
    """Das Ergebnis einer Ausfuehrung — vollstaendig per Konstruktion.

    In der frueheren elif-Kette waren `result`, `task_id` und `queued`
    nirgends vorinitialisiert; jeder Zweig musste alle drei setzen, und ein
    vergessenes Feld fiel erst beim Bestaetigen als `NameError` auf. Hier
    erzwingt der Konstruktor `result`, und die uebrigen Felder tragen die
    Werte, die fast alle Zweige meinen: nur der Lifecycle reiht ein
    (`queued`, `task_id`), und nur die Servererstellung liefert mit
    `neuer_server_id` einen frisch vergebenen Server zurueck.
    """

    result: dict
    task_id: str | None = None
    queued: bool = False
    neuer_server_id: int | None = None


def _ausfuehren_server_lifecycle(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    from services.server_action_service import request_lifecycle_operation

    result = request_lifecycle_operation(
        db,
        server_id=rahmen.server_id,
        operation=str(rahmen.payload["operation"]),
        actor=ActorContext.for_user(
            rahmen.active_user, origin="ai", correlation_id=rahmen.correlation_id
        ),
        idempotency_key=rahmen.row_id,
    )
    # Start/Stop/Restart laufen in einem Hintergrund-Thread weiter.
    # Zum Zeitpunkt dieser Antwort ist die Aktion nur eingereiht,
    # nicht ausgefuehrt. Der Vorschlag bleibt deshalb "executing";
    # den Endzustand setzt `finish_lifecycle_task`, sobald der
    # Vorgang wirklich fertig ist. Ein bereits abgeschlossener Task
    # (Wiederverwendung derselben Idempotency-ID) bleibt terminal.
    return _Ausgefuehrt(
        result=result,
        task_id=result.get("task_id"),
        queued=result.get("status") == "queued",
    )


def _ausfuehren_backup(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    from services.backup_orchestrator import create_server_backup

    backup = create_server_backup(
        rahmen.server_id,
        db,
        # Ohne eigenen Namen bleibt der bisherige Standard stehen:
        # er sagt in der Backup-Liste wenigstens, woher der Eintrag
        # stammt.
        name=str(rahmen.payload.get("name") or "AI-confirmed snapshot"),
    )
    return _Ausgefuehrt(result={"backup_id": backup.id})


def _ausfuehren_backup_restore(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    # Derselbe Aufruf wie der Panel-Endpunkt. Die Reihenfolge darin
    # ist der Grund, warum die KI keinen eigenen Weg bekommt:
    # S3-Download und Entschluesselung laufen **vor** dem
    # Container-Stop, damit ein falsches Passwort den Server
    # unberuehrt laesst.
    from services.backup_restore_service import restore_server_backup

    result = restore_server_backup(
        db,
        server_id=rahmen.server_id,
        backup_id=int(rahmen.payload["backup_id"]),
        actor=ActorContext.for_user(
            rahmen.active_user, origin="ai", correlation_id=rahmen.correlation_id
        ),
    )
    return _Ausgefuehrt(result=result)


def _ausfuehren_server_blueprint_switch(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
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

    server_row = db.query(Server).filter(Server.id == rahmen.server_id).first()
    if server_row is None:
        raise AiActionStateError("AI_ACTION_TARGET_MISSING")
    try:
        result = switch_server_blueprint(
            db,
            server_row,
            str(rahmen.payload["blueprint_id"]),
            user_id=rahmen.active_user.id,
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
            rahmen.server_id, kennung,
        )
        raise AiActionStateError(
            "AI_ACTION_SERVER_BUSY"
            if kennung == "server_must_be_stopped"
            else "AI_ACTION_BLUEPRINT_SWITCH_FAILED"
        ) from exc
    return _Ausgefuehrt(result=result)


def _ausfuehren_server_delete(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    # Derselbe Aufruf, den der Panel-Router und die Hoster-Anbindung
    # nehmen. `delete_server_completely` prueft `servers.delete`
    # selbst noch einmal — die dritte Pruefung nach `_resolve_server`
    # beim Vorschlagen und `_require_tool_permission` beim
    # Bestaetigen. Eine davon zu ueberspringen, waere ein eigener
    # Loeschpfad fuer die KI, und genau den soll es nicht geben.
    from services.server_deletion_service import delete_server_completely

    result = delete_server_completely(
        db,
        server_id=rahmen.server_id,
        actor=ActorContext.for_user(
            rahmen.active_user, origin="ai", correlation_id=rahmen.correlation_id
        ),
    )
    return _Ausgefuehrt(result=result)


def _ausfuehren_config_update(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    result = write_server_text(
        db,
        user=rahmen.active_user,
        server_id=rahmen.server_id,
        relative_path=str(rahmen.payload["path"]),
        content=str(rahmen.payload["content"]),
        expected_revision=rahmen.expected_revision,
        create_only=bool(rahmen.payload.get("create_only")),
    )
    return _Ausgefuehrt(result=result)


def _ausfuehren_config_patch(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    # Erneut anwenden statt den fertigen Inhalt mitzuschleppen. Es
    # kommt dasselbe heraus: `expected_revision` laesst nur genau
    # den Stand zu, auf dem die Ersetzungen beim Vorschlagen schon
    # einmal aufgegangen sind — und dieselbe Revision geht gleich
    # noch einmal in `write_server_text`, das den Schreibvorgang
    # unter der Dateisperre gegen sie prueft.
    pfad = str(rahmen.payload["path"])
    aktuell = read_server_text(db, server_id=rahmen.server_id, relative_path=pfad)
    ersetzungen = [
        (str(e["find"]), str(e["replace"])) for e in rahmen.payload["edits"]
    ]
    try:
        neu = apply_edits(str(aktuell["content"]), ersetzungen)
    except EditNotApplicable as exc:
        raise AiActionStateError("AI_ACTION_FILE_CHANGED") from exc
    result = write_server_text(
        db,
        user=rahmen.active_user,
        server_id=rahmen.server_id,
        relative_path=pfad,
        content=neu,
        expected_revision=rahmen.expected_revision,
    )

    # **Auch eine Teilaenderung ist dauerhaft.** Ohne das haette die
    # Bestaendigkeit am gewaehlten Werkzeug gehangen: derselbe Wert, per
    # `propose_config_set` gesetzt, ueberlebt den naechsten Autosave — per
    # Patch gesetzt nicht. Ein Unterschied, den kein Benutzer sehen kann und
    # den niemand erklaeren koennte.
    #
    # Ein Fehlschlag hier darf die bereits geschriebene Datei nicht
    # zurueckdrehen: der Wert steht, nur seine Wiederherstellung beim naechsten
    # Start fehlt. Das ist der schwaechere von zwei Zustaenden, aber ein
    # ehrlicher — und der Grund steht im Konsolenlog.
    server = db.get(Server, rahmen.server_id) if rahmen.server_id else None
    if server is not None:
        try:
            server.config_wishes_json = server_config_wishes.setze_text(
                server.config_wishes_json, datei=pfad, ersetzungen=ersetzungen
            )
            db.flush()
        except AiActionValidationError as fehler:
            logger.info("Dauerhafter Wert nicht hinterlegt (%s): %s", pfad, fehler)
    return _Ausgefuehrt(result=result)


def _ausfuehren_config_set(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    """Schreibt die Werte — und merkt sie fuer jeden kuenftigen Start.

    Beides gehoert zusammen: die Datei allein haelt nur, solange der
    Spielprozess sie laesst (gemessen auf Server 107, wo ein ausgefuehrter
    Vorschlag nach vier Tagen verschwunden war), und der Wunsch allein wirkt
    erst beim naechsten Start.

    Die Reihenfolge ist nicht beliebig. Erst die Datei, dann der Wunsch: schlaegt
    das Schreiben fehl, steht kein Wunsch in der Datenbank, der bei jedem Start
    etwas verspricht, das nie ankam. Andersherum entstuende genau diese
    Geisterzusage.
    """
    pfad = str(rahmen.payload["path"])
    eintraege = [
        (str(e["section"]), str(e["key"]), str(e["value"]))
        for e in rahmen.payload["entries"]
    ]
    # Ein serverbezogenes Werkzeug ohne Server ist ein Programmierfehler, kein
    # Benutzerfehler — `_resolve_server` hat ihn beim Anlegen laengst gesetzt.
    if rahmen.server_id is None:
        raise AiActionStateError("AI_ACTION_INVALID")
    server_id = rahmen.server_id

    if rahmen.expected_revision is None:
        alt = ""
    else:
        alt = str(
            read_server_text(db, server_id=server_id, relative_path=pfad)["content"]
        )
    neu = alt
    for sektion, schluessel, wert in eintraege:
        neu = ini_setzen(neu, sektion, schluessel, wert)

    result = write_server_text(
        db,
        user=rahmen.active_user,
        server_id=server_id,
        relative_path=pfad,
        content=neu,
        expected_revision=rahmen.expected_revision,
        create_only=rahmen.expected_revision is None,
    )

    server = db.get(Server, server_id)
    if server is not None:
        server.config_wishes_json = server_config_wishes.setze(
            server.config_wishes_json, datei=pfad, eintraege=eintraege
        )
        db.flush()

    # Nachweis statt Behauptung: `write_server_text` meldet Erfolg, sobald der
    # Schreibaufruf durchlief. Wo ein fremder Prozess dieselbe Datei besitzt,
    # ist das keine Aussage ueber ihren Inhalt. Einmal zuruecklesen kostet einen
    # Lesevorgang und erspart dem Benutzer das Nachsehen im Dateimanager, das er
    # sich bisher nicht sparen konnte.
    try:
        nachher = read_server_text(db, server_id=server_id, relative_path=pfad)
        inhalt = str(nachher["content"])
        result = dict(result)
        result["verifiziert"] = all(
            f"{schluessel}={wert}" in inhalt for _, schluessel, wert in eintraege
        )
    except HTTPException:
        result = dict(result)
        result["verifiziert"] = False
    return _Ausgefuehrt(result=result)


def _ausfuehren_bind_ip_update(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    result = _execute_bind_ip_update(
        db, server_id=rahmen.server_id, payload=rahmen.payload
    )
    return _Ausgefuehrt(result=result)


def _ausfuehren_mod_install(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    # Anders als beim Lifecycle gibt es fuer den Mod-Download keinen
    # Rueckkanal, der den Vorschlag spaeter abschliesst. Ein
    # dauerhaftes "executing" waere deshalb kein ehrlicherer Zustand,
    # sondern ein fuer immer offener Vorgang. Abgeschlossen ist hier
    # das, was der Vorschlag zugesagt hat: die Installation ist
    # angestossen. Ihren Ausgang traegt die Mod-Zeile.
    result = _execute_mod_install(db, server_id=rahmen.server_id, payload=rahmen.payload)
    return _Ausgefuehrt(result=result)


def _ausfuehren_mod_toggle(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    result = _execute_mod_toggle(db, server_id=rahmen.server_id, payload=rahmen.payload)
    return _Ausgefuehrt(result=result)


def _ausfuehren_server_repair(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    result = _execute_server_repair(
        db, server_id=rahmen.server_id, payload=rahmen.payload,
        user=rahmen.active_user, correlation_id=rahmen.correlation_id,
    )
    return _Ausgefuehrt(result=result)


def _ausfuehren_guardian_tuning(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    result = _execute_guardian_tuning(
        db, server_id=rahmen.server_id, payload=rahmen.payload,
        user=rahmen.active_user, correlation_id=rahmen.correlation_id,
        incident_id=rahmen.guardian.incident_id if rahmen.guardian else None,
    )
    return _Ausgefuehrt(result=result)


def _ausfuehren_file_delete(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    result = _execute_file_delete(
        db, user=rahmen.active_user, server_id=rahmen.server_id,
        payload=rahmen.payload, expected_revision=rahmen.expected_revision,
    )
    return _Ausgefuehrt(result=result)


def _ausfuehren_server_create(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    # Ebenso wie beim Mod-Download: `provision_server` kehrt zurueck, sobald
    # der Server existiert und die Installation laeuft — exakt der Punkt, an
    # dem auch `POST /api/servers` dem Panel antwortet. Der weitere Verlauf
    # haengt an der Operation-Task, deren ID mitgegeben wird.
    result, created_server_id, task_id = _execute_server_create(
        db, user=rahmen.active_user, payload=rahmen.payload,
        correlation_id=rahmen.correlation_id, proposal_id=rahmen.row_id,
    )
    # Nur dieses Werkzeug setzt `neuer_server_id`: `execute_proposal`
    # uebernimmt damit die frisch vergebene Nummer — daran haengen der
    # Fixup-Block des Erstellungsvorschlags und das Audit.
    return _Ausgefuehrt(
        result=result, task_id=task_id, neuer_server_id=created_server_id
    )


def _ausfuehren_blueprint_change(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    # Gespeichert wird die Nutzlast, die beim **Vorschlagen**
    # entstanden ist — nicht eine neu berechnete. Der Mensch hat
    # genau dieses Ergebnis gesehen und bestaetigt; zwischenzeitlich
    # geaenderte Vorlagen duerfen daran nichts mehr drehen.
    from services import blueprint_service

    try:
        blueprint_id = blueprint_service.save_community_blueprint(
            dict(rahmen.payload["blueprint"])
        )
    except HTTPException as exc:
        logger.info("Blueprint-Vorschlag abgelehnt: %s", exc.detail)
        raise AiActionStateError("AI_ACTION_BLUEPRINT_REJECTED") from exc
    return _Ausgefuehrt(result={"blueprint_id": blueprint_id})


def _ausfuehren_blueprint_delete(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    # Die Session dieses Requests geht mit. `delete_community_blueprint`
    # zaehlt vor dem Loeschen die Server, die den Blueprint noch
    # verwenden — und zwar erneut, denn zwischen Vorschlag und Klick
    # kann ein Server angelegt worden sein. Diese Zaehlung muss den
    # Stand sehen, auf dem dieser Request arbeitet; eine eigene
    # Verbindung daneben antwortete auf eine andere Frage als die,
    # die hier gestellt wird.
    from services import blueprint_service

    try:
        blueprint_service.delete_community_blueprint(
            str(rahmen.payload["blueprint_id"]), db=db
        )
    except HTTPException as exc:
        logger.info("Blueprint-Loeschvorschlag abgelehnt: %s", exc.detail)
        raise AiActionStateError("AI_ACTION_BLUEPRINT_DELETE_REJECTED") from exc
    return _Ausgefuehrt(
        result={"deleted": True, "blueprint_id": str(rahmen.payload["blueprint_id"])}
    )


def _ausfuehren_hoster_schreiben(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    result = _execute_hoster_write(
        db, user=rahmen.active_user, tool_name=rahmen.tool_name, payload=rahmen.payload
    )
    return _Ausgefuehrt(result=result)


def _ausfuehren_restart_schedule_set(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    """Setzt den eingebauten Auto-Neustart-Zeitplan — derselbe Weg wie das Panel.

    Normalisierung und Scheduler-Sync sind wörtlich die des Panel-PATCH
    (`routers/servers.py`): erst `normalize_server_restart_mode` (Intervall
    und feste Zeiten schließen sich aus), dann `sync_server_restart_schedule`
    **vor** dem Commit, damit Datenbank und APScheduler nicht driften.
    Zusätzlich bekommt der Server das „Von der KI verwaltet"-Abzeichen; die
    Aufgaben-Kennung kommt mit, wenn der Vorschlag aus einem stehenden
    Auftrag stammt.
    """
    if rahmen.server_id is None:
        raise AiActionStateError("AI_ACTION_INVALID")
    server = db.get(Server, rahmen.server_id)
    if server is None:
        raise AiActionStateError("AI_ACTION_INVALID")

    p = rahmen.payload
    server.auto_restart = bool(p.get("enabled"))
    if p.get("interval_hours") is not None:
        server.restart_interval_hours = int(p["interval_hours"])
    elif p.get("times_csv"):
        zeiten = str(p["times_csv"])
        server.restart_times_utc = zeiten
        # Legacy-Spiegel wie im Panel: die erste Zeit landet zusätzlich im
        # Einzelfeld, das ältere Leser noch kennen.
        server.restart_time_utc = zeiten.split(",")[0]
        server.restart_interval_hours = None

    from services.server_provisioning_service import normalize_server_restart_mode

    normalize_server_restart_mode(server)
    server.restart_ai_managed = True
    server.restart_ai_task_id = str(p["ai_task_id"]) if p.get("ai_task_id") else None

    from services.scheduler_service import sync_server_restart_schedule

    sync_server_restart_schedule(server)
    db.flush()
    return _Ausgefuehrt(result={
        "enabled": bool(server.auto_restart),
        "interval_hours": server.restart_interval_hours,
        "times": [
            teil for teil in (server.restart_times_utc or "").split(",") if teil
        ],
        "ai_managed": True,
    })


def _ausfuehren_backup_schedule_set(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    """Setzt den eingebauten Auto-Backup-Zeitplan — derselbe Weg wie das Panel.

    Ein Nachtrag: nur die Felder aus der Nutzlast werden angefasst. Der
    Scheduler wird bei einer Intervall-Änderung sofort synchronisiert, wie am
    Panel-Endpunkt (`routers/backups.py`).
    """
    if rahmen.server_id is None:
        raise AiActionStateError("AI_ACTION_INVALID")
    server = db.get(Server, rahmen.server_id)
    if server is None:
        raise AiActionStateError("AI_ACTION_INVALID")

    p = rahmen.payload
    if "backup_on_start" in p:
        server.backup_on_start = bool(p["backup_on_start"])
    if "interval_hours" in p:
        wert = int(p["interval_hours"])
        server.backup_interval_hours = wert if wert > 0 else None
        from services.scheduler_service import remove_job, schedule_backup

        if server.backup_interval_hours:
            schedule_backup(
                server.id,
                interval_hours=server.backup_interval_hours,
                job_id=f"backup_server_{server.id}",
            )
        else:
            remove_job(f"backup_server_{server.id}")
    if "retention_count" in p:
        server.backup_retention_count = int(p["retention_count"])

    server.backup_ai_managed = True
    server.backup_ai_task_id = str(p["ai_task_id"]) if p.get("ai_task_id") else None
    db.flush()
    return _Ausgefuehrt(result={
        "backup_on_start": bool(server.backup_on_start),
        "interval_hours": server.backup_interval_hours,
        "retention_count": server.backup_retention_count,
        "ai_managed": True,
    })


def _ausfuehren_task_set(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    # **Die Felder werden hier erneut geprueft**, nicht nur
    # angewandt. Zwischen Vorschlag und Bestaetigung liegt ein
    # Zeitfenster ohne Obergrenze, und in ihm kann der Betreiber die
    # autonome Freigabe zurueckgenommen haben. Ohne die zweite
    # Pruefung entstuende hier eine handelnde Aufgabe auf Grundlage
    # einer Freigabe, die es nicht mehr gibt — und sie liefe von da
    # an jede Nacht.
    #
    # `ai_task_service` prueft beides in `_anwenden`; deshalb steht
    # hier nur der Aufruf und keine eigene Kette.
    gemerkt = rahmen.payload.get("task_id")
    felder = dict(rahmen.payload.get("felder") or {})
    if gemerkt:
        aufgabe = ai_task_service.aendern(
            db, user=rahmen.active_user, task_id=str(gemerkt), felder=felder
        )
    else:
        aufgabe = ai_task_service.anlegen(
            db, user=rahmen.active_user, felder=felder
        )
    # `task_id` im Ergebnis ist die ID der **KI-Aufgabe**. Das gleichnamige
    # Feld von `_Ausgefuehrt` bleibt bewusst leer: es meint die
    # Operation-Task eines Lifecycles, und eine KI-Aufgabe ist keine.
    return _Ausgefuehrt(result={
        "task_id": aufgabe.id,
        "title": aufgabe.title,
        "plan": ai_task_service.plan_text(aufgabe),
        "enabled": bool(aufgabe.enabled),
        "next_run": (
            ai_task_service.utc(aufgabe.next_run_at).isoformat()
            if aufgabe.next_run_at is not None else None
        ),
    })


def _ausfuehren_task_delete(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    geloescht = ai_task_service.loeschen(
        db, user=rahmen.active_user, task_id=str(rahmen.payload["task_id"])
    )
    return _Ausgefuehrt(result={"deleted": True, "title": geloescht})


#: Die Ausfuehrung der bestaetigten Schreibwerkzeuge — Werkzeugname → Funktion.
#:
#: Hier stand eine Kette aus 19 `elif`-Zweigen in `execute_proposal`, und jeder
#: Zweig musste `result`, `task_id` und `queued` selbst setzen —
#: vorinitialisiert war nichts, ein vergessenes Feld fiel erst beim Bestaetigen
#: als `NameError` auf. Die Tabelle macht das strukturell, nach demselben
#: Muster wie `_GLOBALE_PAYLOADS`: einen Eintrag ohne benannte Funktion gibt es
#: nicht, und `_Ausgefuehrt` erzwingt die Vollstaendigkeit per Konstruktion.
#:
#: Ein unbekannter Name faellt an der einen Aufrufstelle weiterhin in
#: `AI_ACTION_TOOL_NOT_ALLOWED` — derselbe Waechter, der vorher der letzte
#: `else`-Zweig der Kette war.
_AUSFUEHRUNGEN: dict[str, Callable[[Session, _AusfuehrungsRahmen], _Ausgefuehrt]] = {
    "propose_server_lifecycle": _ausfuehren_server_lifecycle,
    "propose_backup": _ausfuehren_backup,
    "propose_backup_restore": _ausfuehren_backup_restore,
    "propose_server_blueprint_switch": _ausfuehren_server_blueprint_switch,
    "propose_server_delete": _ausfuehren_server_delete,
    "propose_config_update": _ausfuehren_config_update,
    "propose_config_patch": _ausfuehren_config_patch,
    "propose_config_set": _ausfuehren_config_set,
    "propose_bind_ip_update": _ausfuehren_bind_ip_update,
    "propose_mod_install": _ausfuehren_mod_install,
    "propose_mod_toggle": _ausfuehren_mod_toggle,
    "propose_server_repair": _ausfuehren_server_repair,
    "propose_guardian_tuning": _ausfuehren_guardian_tuning,
    "propose_restart_schedule_set": _ausfuehren_restart_schedule_set,
    "propose_backup_schedule_set": _ausfuehren_backup_schedule_set,
    "propose_file_delete": _ausfuehren_file_delete,
    "propose_server_create": _ausfuehren_server_create,
    "propose_blueprint_change": _ausfuehren_blueprint_change,
    "propose_blueprint_delete": _ausfuehren_blueprint_delete,
    # Die drei Shop-Einrichtungswerkzeuge teilen sich eine Funktion; welche
    # der drei gemeint ist, sagt `rahmen.tool_name`.
    "propose_hoster_integration": _ausfuehren_hoster_schreiben,
    "propose_hoster_product": _ausfuehren_hoster_schreiben,
    "propose_ai_tarif_role": _ausfuehren_hoster_schreiben,
    "propose_task_set": _ausfuehren_task_set,
    "propose_task_delete": _ausfuehren_task_delete,
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
            ausfuehrung = _AUSFUEHRUNGEN.get(tool_name)
            if ausfuehrung is None:
                raise AiActionStateError("AI_ACTION_TOOL_NOT_ALLOWED")
            ausgefuehrt = ausfuehrung(
                db,
                _AusfuehrungsRahmen(
                    payload=payload,
                    server_id=server_id,
                    active_user=active_user,
                    correlation_id=correlation_id,
                    expected_revision=expected_revision,
                    row_id=row_id,
                    guardian=guardian,
                    tool_name=tool_name,
                ),
            )
            result = ausgefuehrt.result
            task_id = ausgefuehrt.task_id
            queued = ausgefuehrt.queued
            # Nur die Servererstellung traegt eine neue Server-ID zurueck. Ab
            # hier meint `server_id` den frisch angelegten Server — daran
            # haengen der Fixup-Block gleich unten und das Audit.
            if ausgefuehrt.neuer_server_id is not None:
                server_id = ausgefuehrt.neuer_server_id

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
            _ausfuehrung_protokollieren(
                db,
                user_id=active_user.id,
                server_id=server_id,
                row_id=row_id,
                tool_name=tool_name,
                correlation_id=correlation_id,
                succeeded=not queued,
                extra={
                    **({"queued": True} if queued else {}),
                    **({"task_id": task_id} if task_id else {}),
                },
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
                _ausfuehrung_protokollieren(
                    db,
                    user_id=active_user.id,
                    server_id=server_id,
                    row_id=row_id,
                    tool_name=tool_name,
                    correlation_id=correlation_id,
                    succeeded=False,
                    extra={"error_code": failed.error_code},
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


def _ausfuehrung_protokollieren(
    db: Session,
    *,
    user_id: int,
    server_id: int | None,
    row_id: str,
    tool_name: str,
    correlation_id: str | None,
    succeeded: bool,
    extra: dict | None = None,
) -> None:
    """Der Audit-Eintrag einer Ausfuehrung — Erfolg und Fehlschlag, eine Form.

    Stand als Zehn-Zeilen-Paar zweimal in `execute_proposal`, unterschieden
    nur durch `succeeded` und die Zusatzfelder. Ein neues Detail-Feld musste
    zweimal ergaenzt werden; vergisst man eines, erzaehlen Erfolgs- und
    Fehlerprotokoll verschieden viel.
    """
    audit_service.record_privileged_action(
        db,
        user_id=user_id,
        action="ai.action.executed",
        target_type="server" if server_id is not None else "ai_action",
        target_id=server_id,
        details={
            "proposal_id": row_id,
            "tool": tool_name,
            "confirmed": True,
            "succeeded": succeeded,
            **(extra or {}),
        },
        origin="ai",
        correlation_id=correlation_id,
    )


def reconcile_interrupted_actions(db: Session) -> int:
    rows = db.query(AiActionProposal).filter(AiActionProposal.status == "executing").all()
    for row in rows:
        row.status = "failed"
        row.error_code = "AI_ACTION_INTERRUPTED"
        row.executed_at = datetime.now(timezone.utc)
    if rows:
        db.commit()
    return len(rows)
