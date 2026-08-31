from __future__ import annotations

import logging
import json
import secrets
import hashlib
import hmac
from typing import Callable
from uuid import UUID, uuid4
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from fastapi import HTTPException

from models import AiActionProposal, AiConversation, Server, User, AuditLog
from schemas.ai_action import AiActionProposalResponse
from services import audit_service, permission_service
from services.ai_action_errors import AiActionStateError, AiActionValidationError
from services.ai_redaction import redact_sensitive_text
from services.dis_client import DisClient
from services.ai_tool_registry import (
    GLOBAL_READ_TOOLS,
    GLOBAL_WRITE_TOOLS,
    GUARDIAN_HEILUNG_TOOLS,
    READ_TOOLS,
    SERVER_READ_TOOLS,
    WERKZEUGE,
    WORKER_STEUERUNG,
    WRITE_TOOLS,
    aufgaben_tools,
    bekannt as _werkzeug_bekannt,
)
from services.ai_action_service import (
    _resolve_server,
    CONFIRMATION_TTL,
    MAX_BACKUP_NAME_CHARS,
    _MUTEX_TOOLS,
)
from services.ai_tool_registry import (
    GLOBAL_READ_TOOLS,
    GLOBAL_WRITE_TOOLS,
    GUARDIAN_HEILUNG_TOOLS,
    READ_TOOLS,
    SERVER_READ_TOOLS,
    WERKZEUGE,
    WORKER_STEUERUNG,
    WRITE_TOOLS,
    aufgaben_tools,
)
from services.ai_proposals.base import (
    GuardianKontext,
    AufgabenKontext,
    _aad,
    _json_object,
    _permission_for,
    _require_tool_permission,
    _verlangt_gesichertes_backup,
    guardian_aus_lauf,
    _utc,
    _LIFECYCLE_RECHTE,
    _AusfuehrungsRahmen,
    _Ausgefuehrt,
)
from services.ai_proposals.server_proposals import (
    REPARATUREN,
    _config_payload,
    _config_patch_payload,
    _config_set_payload,
    _rationale,
    _server_create_payload,
    _bind_ip_payload,
    _blueprint_change_payload,
    _blueprint_delete_payload,
    _hoster_integration_payload,
    _hoster_product_payload,
    _ai_tarif_role_payload,
    _blueprint_switch_payload,
    _backup_restore_payload,
    _mod_install_payload,
    _mod_toggle_payload,
    _server_repair_payload,
    _guardian_tuning_payload,
    _restart_schedule_payload,
    _backup_schedule_payload,
    _file_delete_payload,
    _modpack_install_payload,
    _ausfuehren_server_lifecycle,
    _ausfuehren_backup,
    _ausfuehren_backup_restore,
    _ausfuehren_server_blueprint_switch,
    _ausfuehren_server_delete,
    _ausfuehren_config_update,
    _ausfuehren_config_patch,
    _ausfuehren_config_set,
    _ausfuehren_bind_ip_update,
    _ausfuehren_mod_install,
    _ausfuehren_mod_toggle,
    _ausfuehren_server_repair,
    _ausfuehren_guardian_tuning,
    _ausfuehren_file_delete,
    _ausfuehren_server_create,
    _ausfuehren_blueprint_change,
    _ausfuehren_blueprint_delete,
    _ausfuehren_hoster_schreiben,
    _ausfuehren_restart_schedule_set,
    _ausfuehren_backup_schedule_set,
    _ausfuehren_modpack_install,
)
from services.ai_proposals.personal_proposals import (
    _email_send_payload,
    _calendar_event_create_payload,
    _calendar_event_delete_payload,
    _calendar_event_update_payload,
    _note_create_payload,
    _note_update_payload,
    _note_delete_payload,
    _ausfuehren_email_send,
    _ausfuehren_calendar_event_create,
    _ausfuehren_calendar_event_update,
    _ausfuehren_calendar_event_delete,
    _ausfuehren_note_create,
    _ausfuehren_note_update,
    _ausfuehren_note_delete,
)
from services.ai_proposals.network_proposals import (
    _cloudflare_dns_payload,
    _cloudflare_dns_delete_payload,
    _ausfuehren_cloudflare_dns,
    _ausfuehren_cloudflare_dns_delete,
)
from services.ai_proposals.task_proposals import (
    _popup_create_payload,
    _task_set_payload,
    _task_delete_payload,
    _ausfuehren_popup_create,
    _ausfuehren_task_set,
    _ausfuehren_task_delete,
    _ausfuehren_read_tool,
    _ausfuehren_worker_start,
    _ausfuehren_worker_cancel,
)

logger = logging.getLogger(__name__)

def proposal_response(proposal: AiActionProposal) -> AiActionProposalResponse:
    """Ein Vorschlag als Vertrag nach aussen â€” die **einzige** Serialisierung.

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
    machen â€” sie meldet sich als `unavailable`.
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
        proposal_type=getattr(proposal, "proposal_type", None) or "write",
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

#: Der Payload-Bau der **globalen** Schreibwerkzeuge â€” Werkzeugname â†’ Bauer.
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
#: alle anderen `rest` â€” ohne die beiden Schluessel behalten deren
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
    "propose_email_send": lambda db, user, rest, arguments, guardian: (
        _email_send_payload(db, user, rest)
    ),
    "propose_calendar_event_create": lambda db, user, rest, arguments, guardian: (
        _calendar_event_create_payload(db, user, rest)
    ),
    "propose_calendar_event_update": lambda db, user, rest, arguments, guardian: (
        _calendar_event_update_payload(db, user, rest)
    ),
    "propose_calendar_event_delete": lambda db, user, rest, arguments, guardian: (
        _calendar_event_delete_payload(db, user, rest)
    ),
    "propose_note_create": lambda db, user, rest, arguments, guardian: (
        _note_create_payload(db, user, rest)
    ),
    "propose_note_update": lambda db, user, rest, arguments, guardian: (
        _note_update_payload(db, user, rest)
    ),
    "propose_note_delete": lambda db, user, rest, arguments, guardian: (
        _note_delete_payload(db, user, rest)
    ),
    "propose_popup_create": lambda db, user, rest, arguments, guardian: (
        _popup_create_payload(db, user, rest)
    ),
    "propose_cloudflare_dns_record": lambda db, user, rest, arguments, guardian: (
        _cloudflare_dns_payload(rest)
    ),
    "propose_cloudflare_dns_delete": lambda db, user, rest, arguments, guardian: (
        _cloudflare_dns_delete_payload(rest)
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
    sonst â€” der, der die Freigabe erteilt hat â€”, und `_require_tool_permission`
    laeuft unveraendert.
    """
    if tool_name not in WRITE_TOOLS and tool_name not in READ_TOOLS and tool_name not in WORKER_STEUERUNG:
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
    if tool_name not in WRITE_TOOLS and rationale_fallback is None:
        rationale_fallback = (f"AusfÃ¼hrung von {tool_name}", f"Ergebnis von {tool_name}")
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
    elif tool_name in GLOBAL_READ_TOOLS or tool_name in WORKER_STEUERUNG:
        _require_tool_permission(db, user, None, tool_name, rest)
        payload = dict(rest)
        if tool_name == "worker_start":
            auftrag_text = str(rest.get("auftrag") or rest.get("prompt") or "")
            titel = rest.get("title") or (auftrag_text[:60] if auftrag_text else "Worker-Auftrag")
            preview = {
                "operation": "worker_start",
                "title": redact_sensitive_text(str(titel)),
                "kanal": rest.get("kanal", "chat"),
            }
        elif tool_name == "web_search":
            preview = {
                "operation": "web_search",
                "query": redact_sensitive_text(str(rest.get("query", ""))[:120]),
            }
        elif tool_name == "list_my_servers":
            preview = {"operation": "list_my_servers"}
        else:
            preview = {"operation": tool_name}
        expected_revision = None
    elif tool_name in GLOBAL_WRITE_TOOLS:
        # **Der Waechter hinter der Tabelle.** Hier stand frueher
        # `elif tool_name in GLOBAL_WRITE_TOOLS: _server_create_payload(...)`.
        # Das las sich wie eine Mengenzugehoerigkeit, meinte aber genau ein
        # Werkzeug â€” und jedes zweite globale Schreibwerkzeug waere still in
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
        # nur, ob der Benutzer den genannten sehen darf â€” und der Freigeber darf
        # in aller Regel mehrere sehen. Ohne diese Zeile koennte ein Modell,
        # das aus einer Logzeile heraus in die Irre gefuehrt wurde, einen
        # Vorfall auf Server A zum Anlass nehmen, an Server B zu schreiben.
        if guardian is not None and server.id != guardian.server_id:
            raise AiActionValidationError(
                "In einer Guardian-Heilung ist nur der betroffene Server erlaubt"
            )

        # **Das Recht vor der Nutzlast.** Frueher stand diese Pruefung erst
        # hinter dem Payload-Bau â€” und der liest den Zustand, ueber den er
        # urteilt: `_config_patch_payload` holt den Dateiinhalt, um zu zaehlen,
        # wie oft der Suchtext darin vorkommt.
        #
        # Damit war die Ablehnung selbst eine Auskunft. Ein Benutzer mit
        # `server.view` und ohne `server.files.read` bekam auf einen erfundenen
        # Patch die Antwort "kommt 3-mal vor" â€” ein Orakel, mit dem sich der
        # Inhalt einer Datei Zeichen fuer Zeichen erraten laesst, ohne sie je
        # lesen zu duerfen. Der Vorschlag wurde nie gespeichert und nichts
        # geschrieben; das Leck lag allein in der Reihenfolge.
        #
        # Die Zusage ist "die KI kann nur, was der Benutzer kann". Sie gilt erst,
        # wenn schon der *Versuch* nichts verraet.
        #
        # Fuer Lebenszyklus und Reparatur haengt das Recht am Vorgang, deshalb
        # wird deren Formpruefung hier vorgezogen â€” sonst bekaeme ein
        # ungueltiger Vorgang die Rechte-Ablehnung statt der Formmeldung, die
        # dem Modell weiterhilft. Beide Pruefungen lesen keinen Zustand; sie
        # verraten also nichts, was die Rechtepruefung schuetzen muesste.
        #
        # Die SchlÃ¼sselmenge wird hier mitgeprÃ¼ft und nicht noch einmal im
        # Payload-Bau weiter unten. Dieselbe PrÃ¼fung an zwei Stellen hieÃŸe:
        # zwei Wertelisten, die auseinanderlaufen kÃ¶nnen, und eine zweite
        # Meldung fÃ¼r einen Fall, Ã¼ber den die erste schon entschieden hat.
        if tool_name == "propose_server_lifecycle" and (
            set(rest) != {"operation"} or rest.get("operation") not in _LIFECYCLE_RECHTE
        ):
            raise AiActionValidationError("Ungueltige Lifecycle-Aktion")
        if tool_name == "propose_server_repair":
            if set(rest) != {"action"}:
                raise AiActionValidationError("Reparatur-Tool hat ungueltige Argumente")
            if rest["action"] not in REPARATUREN:
                raise AiActionValidationError("Unbekannte Reparatur")
        _require_tool_permission(db, user, server.id, tool_name, rest)

        if tool_name == "propose_server_lifecycle":
            # GeprÃ¼ft ist schon oben, vor der RechteprÃ¼fung â€” hier bleibt der
            # reine Bau.
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
            # lesen â€” also redigiert und gekuerzt wie jede andere Modellausgabe.
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
                # raten muesste â€” Backups und S3-Objekte gehen mit.
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
            # mit ihm verknÃ¼pft: das Panel zeigt dann â€žVon der KI verwaltet
            # (Aufgabe X)", und eine manuelle Ã„nderung deaktiviert genau X.
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
        elif tool_name == "propose_modpack_install":
            payload, preview = _modpack_install_payload(db, server, rest)
            expected_revision = None
        elif tool_name in SERVER_READ_TOOLS:
            _require_tool_permission(db, user, server.id, tool_name, rest)
            payload = dict(rest)
            preview = {
                "operation": tool_name,
                "server_name": server.name,
                "current_status": server.status,
            }
            if "path" in rest:
                preview["path"] = redact_sensitive_text(str(rest["path"]))
            if "lines" in rest:
                preview["lines"] = rest["lines"]
            expected_revision = None
        else:
            raise AiActionValidationError(f"Kein Payload-Bau fuer Werkzeug: {tool_name}")

    preview["reason"] = reason
    preview["expected_effect"] = expected_effect
    server_id = server.id if server is not None else None
    # Fuer serverbezogene Werkzeuge die zweite Pruefung â€” die erste lief vor dem
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

    if tool_name in WORKER_STEUERUNG:
        proposal_type = "worker"
    elif tool_name in SERVER_READ_TOOLS or tool_name in GLOBAL_READ_TOOLS:
        proposal_type = "read"
    else:
        proposal_type = "write"

    autonomous = autonomy_allows(db, user=user, server_id=server_id, tool_name=tool_name)
    proposal = AiActionProposal(
        id=proposal_id,
        conversation_id=conversation.id,
        user_id=user.id,
        server_id=server_id,
        tool_name=tool_name,
        proposal_type=proposal_type,
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
        # der wichtigste Unterschied ueberhaupt â€” wer spaeter fragt, warum um
        # 03:14 Uhr eine Datei geaendert wurde, findet die Antwort in diesem
        # einen Wort. Der Wert stand in `AUDIT_ORIGINS` bereits bereit.
        origin="system" if guardian is not None else "ai",
        correlation_id=proposal.correlation_id,
    )
    return proposal

def owned_proposal(db: Session, proposal_id: str, user: User) -> AiActionProposal | None:
    """Der Vorschlag, sofern es ihn gibt und er diesem Benutzer gehoert.

    Zwei Ausgaenge, und die Unterscheidung ist der Punkt:

    - ``None`` heisst **gibt es nicht** â€” unbrauchbare Kennung, oder die Zeile
      gehoert jemand anderem. Beides fuehrt zu 404, und das ist richtig so: ob
      ein fremder Vorschlag existiert, ist selbst schon eine Auskunft.
    - ``AI_ACTION_ACCESS_REVOKED`` heisst **darfst du nicht mehr** â€” die Zeile
      ist da und gehoert dem Anrufer, ihm fehlt nur das Recht zur Sache.

    Frueher lief beides in dasselbe ``None`` und damit in dieselbe Meldung
    "Aktionsvorschlag nicht gefunden". Wer daraufhin suchte, suchte an der
    falschen Stelle: nach einer verschwundenen Zeile statt nach einem entzogenen
    Recht. Das Vokabular dafuer gibt es laengst, `confirm_proposal` benutzt es.

    Die Existenz fremder Zeilen bleibt geschuetzt, weil die ``user_id``-Bedingung
    schon in der Abfrage steht â€” geworfen wird nur fuer Zeilen, die der Anrufer
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
        # existiert â€” und seit dem `SET NULL` auch ein erledigter Vorschlag,
        # dessen Server es nicht mehr gibt.
        #
        # Welches Recht dann gilt, steht in der Werkzeugtabelle und nicht hier.
        # Fest verdrahtet stand hier `servers.create`; fuer einen abgeschlossenen
        # Loeschvorschlag waere das sachfremd gewesen. `_require_tool_permission`
        # zieht dieselbe Grenze beim Vorschlagen â€” zwei Orte mit zwei Antworten
        # sind genau die Sorte Abweichung, die niemand bemerkt.
        werkzeug = WERKZEUGE.get(proposal.tool_name)
        if werkzeug is not None and werkzeug.recht_global and werkzeug.recht:
            if not permission_service.has_global_permission(db, user, werkzeug.recht):
                raise AiActionStateError("AI_ACTION_ACCESS_REVOKED")
        # Ein serverbezogenes Werkzeug ohne globales Recht â€” etwa ein
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
    aber das bereits geladene Objekt aus der Identity Map zurueck â€” also den
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
    Schritt â€” den Klick des Menschen â€” und keinen einzigen der Schutzmechanismen.
    """
    proposal = owned_proposal(db, proposal_id, user)
    if proposal is None:
        raise AiActionStateError("AI_ACTION_NOT_FOUND")
    if not proposal.autonomous or proposal.requires_confirmation:
        raise AiActionStateError("AI_ACTION_NOT_AUTONOMOUS")
    # SpÃ¤ter Import wie beim Anlegen: `ai_autonomy_service` liest
    # `ALWAYS_CONFIRM_TOOLS` aus diesem Modul, am Dateikopf wÃ¤re das ein Zirkel.
    from services.ai_autonomy_service import autonomie_grundlage

    # **Die Autonomie wird beim Anlegen entschieden, aber nicht eingefroren.**
    # Zwischen dem Vorschlag und diesem Punkt liegt ein Zeitfenster ohne
    # Obergrenze â€” ein Vorschlag im Status 'proposed' altert nicht. Ohne diese
    # PrÃ¼fung Ã¼berlebte eine erteilte Autonomie ihren eigenen Widerruf: der
    # Betreiber nimmt `ai.autonomous.use` weg oder schaltet die Freigabe fÃ¼r
    # diesen Server ab, und die bereits angelegte Aktion liefe trotzdem noch
    # ohne RÃ¼ckfrage. Dieselbe Ãœberlegung wie beim zweiten Backup-Nachweis in
    # `execute_proposal`, nur eine Ebene frÃ¼her.
    if (
        autonomie_grundlage(
            db, user=user, server_id=proposal.server_id, tool_name=proposal.tool_name
        )
        is None
    ):
        raise AiActionStateError("AI_ACTION_NOT_AUTONOMOUS")
    _, token = confirm_proposal(db, proposal_id=proposal_id, user=user)
    import services.ai_proposal_service as _facade
    _exec = getattr(_facade, "execute_proposal", execute_proposal)
    return _exec(
        db, proposal_id=proposal_id, user=user, confirmation_token=token
    )

#: Die Ausfuehrung der bestaetigten Schreib- und Lesewerkzeuge â€” Werkzeugname â†’ Funktion.
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
    "propose_email_send": _ausfuehren_email_send,
    "propose_calendar_event_create": _ausfuehren_calendar_event_create,
    "propose_calendar_event_update": _ausfuehren_calendar_event_update,
    "propose_calendar_event_delete": _ausfuehren_calendar_event_delete,
    "propose_note_create": _ausfuehren_note_create,
    "propose_note_update": _ausfuehren_note_update,
    "propose_note_delete": _ausfuehren_note_delete,
    "propose_popup_create": _ausfuehren_popup_create,
    "propose_cloudflare_dns_record": _ausfuehren_cloudflare_dns,
    "propose_cloudflare_dns_delete": _ausfuehren_cloudflare_dns_delete,
    "propose_modpack_install": _ausfuehren_modpack_install,
    "worker_start": _ausfuehren_worker_start,
    "worker_cancel": _ausfuehren_worker_cancel,
    "worker_antwort": _ausfuehren_read_tool,
    **{name: _ausfuehren_read_tool for name in READ_TOOLS},
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
    import services.ai_proposal_service as _facade
    _hmac = getattr(_facade, "hmac", hmac)
    if not _hmac.compare_digest(proposal.confirmation_token_hash, token_hash):
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
    # Der Betreiber konnte das Archiv sogar von Hand loeschen â€” der Endpunkt
    # kennt keine Regel, die das letzte nachgewiesene Backup schuetzt.
    #
    # Ohne diese Zeilen loeschte ein Klick auf "Bestaetigen" die Datei, obwohl
    # der Nachweis, mit dem der Vorschlag ueberhaupt entstehen durfte, nicht mehr
    # existierte. Die Zusage in `ai_tool_registry` â€” geprueft beim Anlegen **und**
    # vor der Ausfuehrung â€” war bis hierher eine Behauptung.
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
            # hier meint `server_id` den frisch angelegten Server â€” daran
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
            # gibt â€” `server_id` waere hier wieder `None`, die lokale Kopie
            # `server_id` traegt aber noch die alte Nummer, und der Commit
            # scheiterte an der Fremdschluesselpruefung.
            if (
                tool_name == "propose_server_create"
                and proposal.server_id is None
                and server_id is not None
            ):
                proposal.server_id = server_id
            if proposal.conversation_id and proposal.run_id:
                from models import AiToolResult

                db.add(
                    AiToolResult(
                        id=str(uuid4()),
                        conversation_id=proposal.conversation_id,
                        run_id=proposal.run_id,
                        tool_name=tool_name,
                        result_json=json.dumps(
                            result if isinstance(result, dict) else {"result": result},
                            ensure_ascii=True,
                            separators=(",", ":"),
                        ),
                    )
                )
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
    """Der Audit-Eintrag einer Ausfuehrung â€” Erfolg und Fehlschlag, eine Form.

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

def reject_proposal(
    db: Session,
    *,
    proposal_id: str,
    user: User,
) -> AiActionProposal:
    """Lehnt einen offenen Vorschlag ab und vermerkt das im Audit."""
    proposal = owned_proposal(db, proposal_id, user)
    if proposal is None:
        raise AiActionStateError("AI_ACTION_NOT_FOUND")
    proposal = _lock_proposal(db, proposal.id)
    if proposal.status not in ("proposed", "confirmed"):
        raise AiActionStateError("AI_ACTION_NOT_PROPOSED")
    proposal.status = "expired"
    proposal.confirmation_token_hash = None
    proposal.error_code = "AI_ACTION_REJECTED"
    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="ai.action.rejected",
        target_type="server" if proposal.server_id is not None else "ai_action",
        target_id=proposal.server_id,
        details={
            "proposal_id": proposal.id,
            "tool": proposal.tool_name,
        },
        origin="ai",
        correlation_id=proposal.correlation_id,
    )
    db.commit()
    db.refresh(proposal)
    return proposal
