from __future__ import annotations

import re
from uuid import uuid4

from database import SessionLocal
from models.user import User
from services import ai_action_service
from services.ai_voice import interactions as voice_interactions
from services.openai_compatible_adapter import ProviderToolCall
from services.ai_tool_registry import RECHTE_SCHREIBEN, WERKZEUGE, WRITE_TOOLS
from services.semantic_tool_router_adapter import SemanticToolRouterAdapter


#: Was der Umweg nie erreicht: sich selbst, die Worker-Steuerung und die
#: Werkzeuge, die einen Gesprächszustand brauchen, den er nicht hat.
_NIE_UEBER_DEN_UMWEG = frozenset({
    "execute_server_action", "worker_start", "worker_cancel", "worker_antwort",
    "ask_user", "wait_until",
})
#: So viele Treffer der Suche sieht das Modell, wenn es wählen soll.
_KANDIDATEN = 5


def _schreibt(name: str) -> bool:
    """Ob ein Werkzeug handelt — dann wählt es nie die Textsuche allein.

    Dazu zählt, was immer bestätigt werden muss (`forget_memory` ist der
    Art nach ein Lesewerkzeug, löscht aber).
    """
    spec = WERKZEUGE.get(name)
    if spec is None:
        return False
    return (
        spec.art in {"server_write", "global_write"}
        or name in WRITE_TOOLS
        or spec.immer_bestaetigen
    )


def _beschreibung(name: str) -> str:
    from services import ai_action_service as katalog

    for eintrag in katalog.provider_tool_definitions():
        funktion = eintrag.get("function", eintrag) if isinstance(eintrag, dict) else {}
        if funktion.get("name") == name:
            text = " ".join(str(funktion.get("description") or "").split())
            return text[:160]
    return ""


def _rueckfrage(action: str, kandidaten: list[str]) -> tuple[dict, None, dict, list]:
    """Die Treffer der Suche zur Auswahl, statt den ersten auszuführen.

    Kein Fehler: das Modell ruft `execute_server_action` noch einmal mit
    `tool_name` auf — oder merkt, dass keiner passt, und sagt es.
    """
    wert = {
        "status": "choose_tool",
        "message": (
            "Nichts ausgeführt. Die Suche nach der Aktion ist nicht eindeutig. "
            "Wähle das passende Werkzeug und rufe execute_server_action erneut mit "
            "tool_name und parameters auf. Passt keines, sag das dem Benutzer."
        ),
        "action": action[:200],
        "candidates": [
            {"tool_name": name, "description": _beschreibung(name)} for name in kandidaten
        ],
    }
    return wert, None, {"tool_name": "execute_server_action"}, []


def _an_den_worker(name: str) -> tuple[dict, str, dict, list]:
    """Rechte anderer Benutzer: der Weg dorthin, statt eines Ersatzwerkzeugs."""
    wert = {
        "error": (
            f"Das passende Werkzeug ist {name}. Rechte und Rollen anderer Benutzer "
            "ändert nur ein Worker. Im Gespräch oder per Stimme: worker_start mit dem "
            f"Auftrag. Als Worker: {name} direkt aufrufen."
        ),
        "tool_name": name,
    }
    return wert, "Nur über einen Worker", {"tool_name": "execute_server_action", "failed": True}, []


def _unterhaltung(user_id: int, conversation_id: str | None) -> str | None:
    """Die Unterhaltung, an der die Karte eines Sprachvorschlags hängt."""
    if conversation_id:
        return conversation_id
    with SessionLocal() as db_conv:
        from models.ai_conversation import AiConversation
        from services.ai_chat_service import get_or_create_primary_conversation
        conv = (
            db_conv.query(AiConversation)
            .filter(AiConversation.user_id == user_id)
            .order_by(AiConversation.updated_at.desc(), AiConversation.id.desc())
            .first()
        )
        if conv:
            return conv.id
        usr = db_conv.get(User, user_id)
        if usr:
            kennung = get_or_create_primary_conversation(db_conv, usr).id
            # `get_or_create_primary_conversation` legt nur an, speichert aber
            # nicht. Ohne Commit verschwände die Zeile mit dieser Sitzung, und
            # die Karte hinge an einer Unterhaltung, die es nicht gibt.
            db_conv.commit()
            return kennung
    return None


def dispatch_voice_action(
    user_id: int,
    arguments: dict,
    *,
    conversation_id: str | None = None,
    herkunft: str = "panel",
    familie: str | None = None,
    schon_freigegeben: bool = False,
    stimme: bool = True,
) -> tuple[object, str | None, dict, list[dict]]:
    """Löst eine per Sprache gewünschte Aktion semantisch über die gesamte Tool-Registry auf.

    Ermöglicht dem Realtime-Sprachmodus Zugriff auf das komplette Werkzeugset
    (Mods, Configs, Ports, Backups, Tasks etc.), ohne dass alle Schemas dauerhaft
    im Sprach-Prompt liegen müssen.

    ``schon_freigegeben``: der Aufruf von `execute_server_action` ist bereits
    durch die Frage eines Chatlaufs gegangen (`ai_stream.engine`: ohne Freigabe
    eine Karte, mit Freigabe autonom). Dann fragt der Dispatcher nur noch, was
    auch im autonomen Modus fragt: das Löschen. Eine zweite Karte für dasselbe
    Vorhaben ließe den bestätigten Aufruf nie laufen (Review vom 23.09.2026).
    Die Stimme ruft den Dispatcher ohne vorherige Frage auf
    (`voice_werkzeug_ausfuehren`); dort ist seine die einzige.

    ``stimme``: der Aufruf kommt aus einer Sprachsitzung. Dann fehlt hier,
    was die Sitzung selbst nicht anbietet (`realtime_session.REALTIME_SCHWER`,
    darunter `propose_server_delete`). Bis zum 25.09.2026 erreichte die Stimme
    über diesen Umweg genau die Werkzeuge, die ihr Katalog weglässt — beim
    Betreibertest schlug die Suche für "Rolle" das Löschen eines Servers vor.
    """
    from services.ai_stream.read_tools import _anzeigeeintrag, _werkzeug_ausfuehren
    from services.ai_stream.write_tools import _persist_write_proposals

    action = str(arguments.get("action") or "").strip()
    explicit_tool = arguments.get("tool_name")
    server_id = arguments.get("server_id")
    raw_params = arguments.get("parameters")
    extra_params = dict(raw_params) if isinstance(raw_params, dict) else {}

    if not action and not explicit_tool:
        wert = {"error": "Keine Aktion angegeben"}
        return wert, "Keine Aktion angegeben", {"tool_name": "execute_server_action", "failed": True}, []

    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            wert = {"error": "Benutzer nicht gefunden"}
            return wert, "Benutzer nicht gefunden", {"tool_name": "execute_server_action", "failed": True}, []
        angeboten = ai_action_service.angebotene_werkzeuge(db, user) - _NIE_UEBER_DEN_UMWEG
    if stimme:
        from services.ai_voice.realtime_session import REALTIME_SCHWER

        angeboten = angeboten - REALTIME_SCHWER
    # Rechte anderer Benutzer aendert nur ein Worker: Gehirn und Stimme
    # uebergeben sie mit `worker_start` (`ai_tool_registry.RECHTE_SCHREIBEN`).
    # Gesucht wird trotzdem ueber sie mit — sonst landet "leg eine Rolle an"
    # beim naechstbesten Werkzeug, das es noch gibt (am 25.09.2026 beim
    # Betreibertest: Tarif-Rolle, Portliste, Server loeschen).
    allowed = angeboten - RECHTE_SCHREIBEN

    if isinstance(explicit_tool, str) and explicit_tool.strip():
        # Ein genannter Name wird genommen oder abgelehnt, nie ersetzt. Bis
        # zum 25.09.2026 fiel ein unbekannter Name still auf die Suche ueber
        # `action` zurueck — und die fand irgendetwas.
        target_tool = explicit_tool.strip()
        if target_tool in RECHTE_SCHREIBEN and target_tool in angeboten:
            return _an_den_worker(target_tool)
        if target_tool not in allowed:
            wert = {"error": f"Werkzeug '{target_tool}' ist hier nicht verfügbar oder nicht berechtigt."}
            return wert, "Aktion nicht verfügbar", {"tool_name": "execute_server_action", "failed": True}, []
    else:
        router = SemanticToolRouterAdapter()
        router.warm(angeboten)
        kandidaten = router.select(action, angeboten, top_k=_KANDIDATEN)
        target_tool = kandidaten[0] if kandidaten else None
        if target_tool in RECHTE_SCHREIBEN:
            return _an_den_worker(target_tool)
        # Eine Suche nach Textaehnlichkeit trifft oft daneben, und ein
        # Schreibwerkzeug handelt dann. Lesen darf sie sofort; geschrieben
        # wird erst, wenn das Modell den Namen aus der Liste nennt.
        if target_tool is not None and _schreibt(target_tool):
            return _rueckfrage(action, kandidaten)

    if not target_tool or target_tool not in allowed:
        wert = {"error": f"Keine passende Aktion für '{action}' verfügbar oder berechtigt."}
        return wert, "Aktion nicht verfügbar", {"tool_name": "execute_server_action", "failed": True}, []

    spec = WERKZEUGE.get(target_tool)
    if spec is None:
        wert = {"error": f"Unbekanntes Zielwerkzeug: {target_tool}"}
        return wert, "Unbekanntes Werkzeug", {"tool_name": "execute_server_action", "failed": True}, []

    target_args: dict[str, object] = dict(extra_params)
    if server_id is not None and "server_id" not in target_args:
        try:
            target_args["server_id"] = int(server_id)
        except (TypeError, ValueError):
            pass

    if "server_id" not in target_args and spec.art in {"server_read", "server_write"}:
        m = re.search(r"server\s*(\d+)", action, re.IGNORECASE)
        if m:
            target_args["server_id"] = int(m.group(1))

    if "query" not in target_args and target_tool in {
        "search_workshop_mods",
        "search_server_files",
        "web_search",
        "email_search",
        "search_docs",
        "search_memory",
    }:
        target_args["query"] = action

    call_id = str(uuid4())
    target_call = ProviderToolCall(id=call_id, name=target_tool, arguments=target_args)

    if spec.art in {"server_write", "global_write"} or target_tool in WRITE_TOOLS:
        if "rationale" not in target_args:
            target_args["rationale"] = (
                f"Per Sprachbefehl angefordert: {action[:100]}"
                if action
                else f"Per Sprachbefehl: {target_tool}"
            )
        if "begruendung" not in target_args:
            target_args["begruendung"] = target_args["rationale"]

        conversation_id = _unterhaltung(user_id, conversation_id)
        if not conversation_id:
            fehler = "Keine aktive Konversation für Schreibvorschlag"
            return {"error": fehler}, fehler, {"tool_name": target_tool, "failed": True}, []

        # Die Ethik-Engine berät über das aufgelöste Werkzeug, nicht über
        # `execute_server_action`: erst hier steht fest, was geschehen soll.
        beratung = voice_interactions.ethik_anstossen(user_id, target_call)
        vorschlaege = _persist_write_proposals(
            user_id=user_id,
            conversation_id=conversation_id,
            tool_calls=[target_call],
            correlation_id=str(uuid4()),
            run_id=None,
        )
        fehler = next((str(v.get("error")) for v in vorschlaege if v.get("error")), None)
        wert = {
            "executed_tool": target_tool,
            "status": "proposal_created",
            "proposals": vorschlaege,
            "message": "Vorschlagskarte wurde im Panel erstellt. Bitte den Benutzer um Bestätigung.",
        }
        if hinweis := voice_interactions.klickhinweis(vorschlaege):
            wert["hinweis"] = hinweis
        ethik = voice_interactions.ethik_abholen(beratung, target_call)
        return (
            voice_interactions.mit_ethik(wert, ethik),
            fehler,
            _anzeigeeintrag(target_call, wert, fehler),
            vorschlaege,
        )

    # Dieselbe Frage wie auf dem geraden Weg: ohne Freigabe erst die Karte.
    # Der Umweg über `execute_server_action` erreicht jedes Werkzeug des
    # Benutzers, auch `forget_memory`; an ihm vorbei durfte keines laufen.
    # Hat ein Chatlauf schon gefragt, bleibt nur das Löschen übrig.
    beratung = voice_interactions.ethik_anstossen(user_id, target_call)
    karte = None
    if not schon_freigegeben or voice_interactions.klick_noetig(target_tool):
        karte = voice_interactions.freigabe_einholen(
            user_id,
            target_call,
            conversation_id=_unterhaltung(user_id, conversation_id),
        )
    if karte is not None:
        karte_wert, fehler, anzeige, vorschlaege = karte
        karte_wert = voice_interactions.mit_ethik(
            karte_wert, voice_interactions.ethik_abholen(beratung, target_call)
        )
        return {"executed_tool": target_tool, **karte_wert}, fehler, anzeige, vorschlaege

    wert, fehler = _werkzeug_ausfuehren(
        user_id, target_call, herkunft=herkunft, familie=familie
    )
    res_wert = voice_interactions.mit_ethik(
        {"executed_tool": target_tool, "data": wert},
        voice_interactions.ethik_abholen(beratung, target_call),
    )
    return res_wert, fehler, _anzeigeeintrag(target_call, wert, fehler), []
