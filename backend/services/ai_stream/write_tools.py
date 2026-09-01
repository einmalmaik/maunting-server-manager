# -*- coding: utf-8 -*-
"""Verwaltung von Schreibrunden, Vorschlaegen und Freigaben."""

from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID, uuid4

from database import SessionLocal
from models import AiActionProposal, AiRun, AiToolResult, User
import services.ai_stream as ai_stream
from services import (
    ai_approval_service,
    ai_meldestelle,
    ai_provider_service,
    ai_run_broker,
    ai_run_service,
)
from services.ai_action_errors import AiActionStateError, AiActionValidationError
from services.ai_action_service import angebotene_werkzeuge, execute_read_tool
from services.ai_chat_service import get_owned_conversation
from services.ai_proposal_service import (
    AufgabenKontext,
    GuardianKontext,
    create_proposal,
    execute_autonomously,
    proposal_response,
)
from services.ai_redaction import redact_sensitive_text
from services.ai_stream.read_tools import (
    _ablehnung_protokollieren,
    _anzeigeeintrag,
    _aufrufnachricht,
    _aussortieren,
    _runde_zaehlen,
    _rundenfehler_nachrichten,
    _tool_followup_messages,
    _werkzeuge_ansagen,
)
from services.ai_stream.types import (
    MAX_TOOL_CALLS,
    MAX_WRITE_ROUNDS,
    _SchreibrundenErgebnis,
    _Vorbereitung,
)
from services.ai_tool_registry import (
    CHAT_INTERACTION_TOOLS,
    READ_TOOLS,
    WORKER_STEUERUNG,
    WRITE_TOOLS,
)
from services.openai_compatible_adapter import StreamUsage

logger = logging.getLogger(__name__)


def _vorschlag_ereignis(proposal: AiActionProposal) -> dict:
    """Ein Vorschlag als SSE-Nutzlast — derselbe Vertrag wie die REST-Antwort.

    Hier stand frueher ein handgebautes Dict aus sechs Feldern. Der Vertrag hat
    fuenfzehn, und zwei der fehlenden — `reason` und `expected_effect` — stehen
    auf der Karte, mit der ein Mensch einen Schreibvorgang freigibt. Live blieb
    sie deshalb ohne Begruendung; erst ein Neuladen holte sie nach.

    Schwerer wog der zweite Weg: dasselbe Dict landet im Abzug des Laufs
    (`ai_run_broker`), und ein Chat, der sich an einen wartenden Lauf
    wiederanhaengt, **ersetzt** damit den vollstaendigen Vorschlag aus der
    REST-Liste. Die gerade noch sichtbare Begruendung verschwand vor den Augen
    des Benutzers.

    `mode="json"` ist Pflicht: `created_at` ist ein `datetime`, und `sse_event`
    serialisiert mit `json.dumps` — ohne Umwandlung scheitert nicht die Anzeige,
    sondern der Stream.
    """
    return proposal_response(proposal).model_dump(mode="json")


def _persist_write_proposals(
    *, user_id: int, conversation_id: str, tool_calls, correlation_id: str,
    run_id: str | None = None, guardian: GuardianKontext | None = None,
    aufgabe: AufgabenKontext | None = None,
) -> list[dict]:
    """Legt die Vorschlaege einer Schreibrunde an und meldet ihren Ausgang.

    Jeder Eintrag der Rueckgabe traegt neben dem Vorschlagsvertrag ein
    ``call_id``: die Kennung des Aufrufs, aus dem er entstanden ist. Ohne sie
    liess sich der Ausgang nur ueber den Werkzeugnamen zuordnen, und zwei
    gleichnamige Aufrufe in einer Runde (zwei `propose_task_set` fuer zwei
    Aufgaben) bekamen beide beide Ausgaenge — bei globalen Werkzeugen ohne
    `server_id` war danach nicht mehr erkennbar, welcher der beiden gescheitert
    ist. Das Feld gehoert **nicht** in die Vorschlagskarte: sie traegt denselben
    Vertrag wie die REST-Antwort, und der Aufrufer laesst es dort weg.
    """
    if len(tool_calls) > MAX_TOOL_CALLS or any(call.name not in (WRITE_TOOLS | READ_TOOLS | WORKER_STEUERUNG) for call in tool_calls):
        raise AiActionValidationError("Ungueltige Tool-Sequenz")
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            raise AiActionValidationError("AI-Zugriff wurde entzogen")
        conversation = get_owned_conversation(db, conversation_id, user)
        if conversation is None:
            from services.ai_chat_service import get_or_create_conversation
            conversation = get_or_create_conversation(db, user, "primary")
        proposals = []
        # Vorschlaege, die gar nicht erst entstanden sind, weil eine Bedingung
        # der Anlage fehlte — heute nur der fehlende Backup-Nachweis. Sie sind
        # **keine** Fehler des Modells, sondern eine Auskunft, auf die es
        # antworten kann ("dann lege ich erst ein Backup an"). Deshalb reissen
        # sie den Lauf nicht ab, sondern gehen als Ergebnis zurueck.
        abgelehnt: list[dict] = []
        uebersprungen: list[tuple[str, str]] = []
        # Welcher Aufruf welchen Vorschlag ausgeloest hat. Die Zuordnung
        # entsteht hier, wo sie noch eindeutig ist — spaeter gibt es nur noch
        # Werkzeugnamen, und die kommen in einer Runde mehrfach vor.
        aufruf_je_vorschlag: dict[str, str] = {}
        for call in tool_calls:
            if abgelehnt:
                # **Die Runde bricht ab.** Ohne diese Zeile fuehrte ein
                # gescheitertes Backup nicht dazu, dass der Loeschvorgang
                # dahinter unterbleibt — die Schleife lief weiter, und die
                # Reihenfolge "erst sichern, dann anfassen" waere eine
                # Absichtserklaerung statt einer Zusage.
                uebersprungen.append((call.name, call.id))
                continue
            try:
                vorschlag = create_proposal(
                    db,
                    user=user,
                    conversation=conversation,
                    tool_name=call.name,
                    arguments=call.arguments,
                    correlation_id=correlation_id,
                    guardian=guardian,
                    aufgabe=aufgabe,
                )
                proposals.append(vorschlag)
                aufruf_je_vorschlag[vorschlag.id] = call.id
            except AiActionStateError as exc:
                _ablehnung_protokollieren(
                    user_id=user_id,
                    tool_name=call.name,
                    grund=exc.code,
                    correlation_id=correlation_id,
                )
                abgelehnt.append({
                    "call_id": call.id,
                    "tool_name": call.name,
                    "status": "rejected",
                    "autonomous": False,
                    "server_id": None,
                    "error_code": exc.code,
                })
            except AiActionValidationError as exc:
                # **Der Formfehler beendet den Lauf nicht mehr.** Er wird zur
                # Werkzeugantwort, genau wie ein Zustandsfehler eine Zeile
                # darueber.
                #
                # Vorher flog er weiter: der Benutzer sah "Die KI hat einen
                # Werkzeugaufruf gestellt, den das Panel nicht annehmen konnte",
                # verlor die ganze Antwort, und das Modell erfuhr nie, was falsch
                # war. Das war doppelt widersinnig — die Meldungen sind
                # ausdruecklich **an das Modell** geschrieben ("Frag im Zweifel
                # mit ask_user nach", "leg die Aufgabe als reinen Bericht an"),
                # und der Lesepfad macht es seit jeher andersherum: ein einzelner
                # fehlgeschlagener Aufruf ist dort eine Auskunft und kein Grund,
                # dem Benutzer die Antwort wegzunehmen.
                #
                # Aufgefallen ist es an den stehenden Auftraegen, weil deren
                # Werkzeug zwoelf teils bedingte Argumente hat und entsprechend
                # oft danebengreift. Der Fehler war aber nie deren eigener.
                #
                # Ausgefuehrt wird dabei nichts: der Vorschlag ist gar nicht erst
                # entstanden, und die Zeile darueber laesst den Rest der Runde
                # ausfallen. Was das Modell gewinnt, ist genau eine Auskunft.
                #
                # Vorher hinterliess eine Ablehnung keinerlei Spur: das Audit
                # entsteht in `create_proposal` erst nach `db.add`, und die
                # Session hier committet erst nach der ganzen Schleife. Wer den
                # Grund suchte, fand weder einen Vorschlag noch einen
                # Auditeintrag — und selbst ein in derselben Runde bereits
                # gelungener Vorschlag verschwand im Rollback mit.
                _ablehnung_protokollieren(
                    user_id=user_id,
                    tool_name=call.name,
                    grund=str(exc),
                    correlation_id=correlation_id,
                )
                # Dieselbe Zeile, die frueher der aeussere Fehlerzweig schrieb.
                # Sie zieht hierher mit, statt zu verschwinden: der Betreiber
                # findet den Grund weiterhin an derselben Stelle im Panel-Log,
                # und dass der Lauf jetzt weiterlaeuft, aendert daran nichts.
                # Ohne sie waere die Aenderung ein Tausch — das Modell erfaehrt
                # etwas, der Mensch nicht mehr.
                logger.warning(
                    "AI-Werkzeugaufruf abgelehnt run_id=%s werkzeug=%s grund=%s",
                    run_id, call.name, redact_sensitive_text(str(exc))[:200],
                )
                abgelehnt.append({
                    "call_id": call.id,
                    "tool_name": call.name,
                    "status": "rejected",
                    "autonomous": False,
                    "server_id": None,
                    "error_code": "AI_TOOL_ARGUMENTS_INVALID",
                    # Der Grund im Klartext — er ist der ganze Zweck der
                    # Aenderung. Redigiert und gekuerzt aus demselben Grund wie
                    # die Logzeile: bei `propose_blueprint_change` kann ein vom
                    # Modell gewaehlter Pfad woertlich im Text stehen, und
                    # dieses Modell hat seinerseits fremden Logtext gelesen.
                    "error": redact_sensitive_text(str(exc))[:300],
                })
        # Der Rueckweg: welcher Lauf wartet auf diesen Vorschlag. Ohne ihn
        # wuesste der Bestaetigungsknopf spaeter nicht, wen er aufwecken soll.
        for proposal in proposals:
            proposal.run_id = run_id
        # Auch ein Schreibvorschlag belegt den Serverbezug: `create_proposal`
        # geht durch dieselbe `_resolve_server`-Pruefung wie ein Lesewerkzeug.
        # Ohne diese Zeile verloere ein Lauf sein Thema ausgerechnet dann, wenn
        # er am meisten damit vorhat — etwa wenn das Modell eine gelesene
        # Konfiguration direkt aendern will.
        #
        # `propose_server_create` traegt hier noch keine Nummer; sie entsteht
        # erst bei der Ausfuehrung. Das ist kein Verlust: ueber einen Server,
        # den es gerade erst gibt, weiss noch niemand etwas.
        ai_run_service.serverbezug_merken(
            db,
            run_id=run_id,
            server_id=next(
                (p.server_id for p in reversed(proposals) if p.server_id is not None),
                None,
            ),
        )
        db.commit()
        results: list[dict] = []
        # Feste Kopien: `execute_autonomously` committet und rollt bei einem
        # Fehler zurueck. Ein danach noch gehaltenes ORM-Objekt waere abgelaufen.
        #
        # Kopiert wird der **vollstaendige** Vorschlag statt einer Handvoll
        # Felder. Er ist zugleich die Rueckfallebene fuer den Fall, dass die
        # Zeile nach der Ausfuehrung nicht mehr auffindbar ist.
        summaries = [
            (proposal.id, bool(proposal.autonomous), _vorschlag_ereignis(proposal))
            for proposal in proposals
        ]
        for proposal_id, autonomous, vorher in summaries:
            error_code: str | None = None
            if autonomous:
                # Sofort ausfuehren — aber ueber denselben Pfad wie eine
                # bestaetigte Aktion. Scheitert sie, endet das nicht den Stream:
                # der Benutzer soll die Antwort samt Fehlergrund sehen.
                try:
                    execute_autonomously(db, proposal_id=proposal_id, user=user)
                except AiActionStateError as exc:
                    error_code = exc.code
                except Exception:
                    logger.warning("Autonome AI-Aktion fehlgeschlagen id=%s", proposal_id)
                    error_code = "AI_ACTION_EXECUTION_FAILED"
            current = db.get(AiActionProposal, proposal_id)
            # Nach der Ausfuehrung noch einmal serialisieren: Status, `task_id`
            # und — bei einer Servererstellung — die `server_id` entstehen erst
            # dabei. Fehlt die Zeile, bleibt der Abzug von vorher; er ist
            # vollstaendig und traegt nur einen anderen Status.
            ereignis = (
                _vorschlag_ereignis(current) if current is not None
                else {**vorher, "status": "failed"}
            )
            if error_code:
                # Zustandsfehler wie `AI_ACTION_SERVER_BUSY` oder eine
                # abgelaufene Bestaetigung entstehen, **bevor** ueberhaupt
                # ausgefuehrt wird. Sie stehen deshalb nicht an der Zeile und
                # gingen ohne diese Zeile verloren.
                ereignis["error_code"] = error_code
            ereignis["call_id"] = aufruf_je_vorschlag.get(proposal_id)
            results.append(ereignis)
        # Abgelehnte und uebersprungene Aufrufe hinten dran. Sie tragen kein
        # `id`, gehen also nicht als Vorschlagskarte an die Oberflaeche — es
        # gibt nichts zu bestaetigen. Das Modell bekommt sie ueber
        # `_write_followup_messages` und weiss damit, warum sein Aufruf nicht
        # gelaufen ist und was zuerst zu tun waere.
        results.extend(abgelehnt)
        results.extend({
            "call_id": call_id,
            "tool_name": name,
            "status": "skipped",
            "autonomous": False,
            "server_id": None,
            "error_code": "AI_ACTION_ROUND_ABORTED",
        } for name, call_id in uebersprungen)
        return results


def _vorschlaege_zuruecknehmen(proposal_ids: list[str], *, grund: str) -> None:
    """Nimmt offene Vorschlaege zurueck, auf die niemand mehr klicken wird.

    Gebraucht wird das in genau einem Fall: eine Guardian-Heilung erzeugt einen
    Vorschlag, der eine Bestaetigung verlangt, obwohl niemand am Panel sitzt.
    Bliebe er auf 'proposed' stehen, waere er eine Karte, die den naechsten
    Menschen Stunden spaeter bittet, einen Eingriff freizugeben, dessen Anlass
    er nicht mitbekommen hat — und dessen Backup-Nachweis inzwischen von der
    Aufbewahrungsregel abgeraeumt sein kann.

    Gesetzt wird `expired` und nicht ein neuer Status: die Spalte traegt eine
    CHECK-Bedingung mit sechs Werten, und `expired` heisst dort genau das, was
    hier geschehen ist — die Gelegenheit zur Bestaetigung ist vorbei.
    """
    if not proposal_ids:
        return

    try:
        with SessionLocal() as db:
            zeilen = (
                db.query(AiActionProposal)
                .filter(
                    AiActionProposal.id.in_(proposal_ids),
                    AiActionProposal.status.in_(("proposed", "confirmed")),
                )
                .all()
            )
            for zeile in zeilen:
                zeile.status = "expired"
                zeile.confirmation_token_hash = None
                zeile.error_code = grund
            db.commit()
    except Exception:  # noqa: BLE001 - ein Aufraeumfehler beendet keinen Lauf
        logger.warning("Offene Vorschlaege nicht zurueckgenommen: %s", proposal_ids)


def _freigabe_melden(user_id: int, conversation_id: str, zustand: dict) -> None:
    """Sagt an, dass ein Auftrag auf einen Klick wartet.

    Ein Worker parkt seit dem 22.08.2026 auf `waiting_confirmation`, statt
    zurueckzunehmen und zu enden. Das ist richtig — nur ist ein Parken kein
    Endzustand, und die Meldestelle spricht sonst ausschliesslich Ergebnisse
    an. Wer per Stimme arbeitet, saehe die Karte nie und hoerte auch nichts.

    Die Meldung nennt bewusst den Ort ("im Chat"): eine gesprochene Zusage
    bestaetigt nur Vorschlaege des laufenden Gesprächs, nicht die eines
    fremden Fensters.

    Eigene Sitzung wie bei `_vorschlaege_zuruecknehmen`, und ein Fehlschlag
    beendet nichts — der Lauf parkt auch ohne Ansage korrekt.
    """
    from services import ai_meldestelle

    rahmen = zustand.get("worker")
    titel = ""
    kanal = "chat"
    if isinstance(rahmen, dict):
        titel = str(rahmen.get("titel") or "")[:120]
        kanal = str(rahmen.get("kanal") or "chat")
    benannt = f'Der Auftrag "{titel}"' if titel else "Ein Auftrag"

    try:
        with SessionLocal() as db:
            user = db.get(User, user_id)
            if user is None:
                return
            ai_meldestelle.melden(
                db,
                user=user,
                text=(
                    f"{benannt} wartet auf eine Freigabe: der nächste "
                    "Schritt ist vorgeschlagen, aber noch nicht ausgeführt. "
                    "Die Karte dazu steht im Chat und braucht einen Klick."
                ),
                kanal=kanal,
                worker_id=conversation_id,
                worker_titel=titel or None,
            )
    except Exception:  # noqa: BLE001 - eine fehlende Ansage beendet keinen Lauf
        logger.warning(
            "Freigabe-Meldung nicht abgesetzt conversation_id=%s", conversation_id
        )


def _freigabe_per_mail_erbeten(run_id: str, proposal_ids: list[str]) -> bool:
    """Fragt per E-Mail nach — fuer **genau einen** der offenen Vorschlaege.

    Einen, nicht alle: eine Mail je Vorschlag waere ein Postfach voller Links,
    von denen der Empfaenger bei keinem wuesste, ob er noch gilt, und die
    Reihenfolge der Ausfuehrung waere seine Sache. Der erste wartet, die uebrigen
    bleiben offen und werden im selben Lauf weitergefuehrt, sobald die Antwort
    da ist.

    Eigene Sitzung wie bei `_vorschlaege_zuruecknehmen`: dieser Zweig laeuft im
    Streamsegment, also im Ereignisschleifen-Thread, und die Sitzung des Laufs
    gehoert hier nicht her.

    ``False`` heisst "es kann niemand antworten" — dann faellt der Aufrufer auf
    das alte Verhalten zurueck.
    """
    if not proposal_ids:
        return False
    try:
        with SessionLocal() as db:
            from services import ai_approval_service

            run = db.get(AiRun, run_id)
            if run is None:
                return False
            user = db.query(User).filter(User.id == run.user_id).first()
            if user is None:
                return False
            proposal = (
                db.query(AiActionProposal)
                .filter(
                    AiActionProposal.id.in_(proposal_ids),
                    AiActionProposal.status.in_(("proposed", "confirmed")),
                )
                .order_by(AiActionProposal.created_at.asc())
                .first()
            )
            if proposal is None:
                return False
            return ai_approval_service.freigabe_anfordern(
                db, proposal=proposal, user=user, run_id=run_id
            )
    except Exception:  # noqa: BLE001 - ein Mailfehler beendet keinen Lauf
        logger.warning(
            "Freigabe per Mail nicht angefordert run_id=%s", run_id, exc_info=True
        )
        return False


def _ask_refusal_messages(tool_calls, rundentext: str | None = None) -> list[dict]:
    """Die Antwort auf eine Rueckfrage, die in einer Heilung niemand hoert.

    Verworfen wird die **ganze** Runde und nicht nur der `ask`-Aufruf. Das
    Protokoll verlangt zu jeder `tool_call_id` genau eine Antwort; blieben die
    uebrigen Aufrufe derselben Runde unbeantwortet, waere die naechste Anfrage
    an den Anbieter formal kaputt. Und inhaltlich gehoert es so: eine Runde,
    deren Plan auf einer Rueckfrage aufbaut, ist als Ganzes hinfaellig — das
    Modell soll sie neu fassen, nicht die Haelfte davon weiterverwenden.
    """
    return _rundenfehler_nachrichten(
        tool_calls,
        rundentext,
        code="AI_GUARDIAN_NO_HUMAN",
        hinweis=(
            "In einer Guardian-Heilung sitzt niemand am Panel; Rueckfragen sind "
            "nicht moeglich. Diese Runde wurde deshalb vollstaendig verworfen. "
            "Entscheide selbst und rufe die Werkzeuge ohne Rueckfrage erneut "
            "auf, oder beende mit einer Zusammenfassung deiner Vermutung — sie "
            "geht als E-Mail an den Betreiber."
        ),
    )


def _ask_formfehler_messages(
    tool_calls, grund: str, rundentext: str | None = None
) -> list[dict]:
    """Die Antwort auf eine Rueckfrage, deren Argumente die Pruefung reissen.

    Nachsicht am Werkzeugrand: ein Formfehler kostet eine Runde, nie die
    Antwort. `question_payload` ist bewusst streng (Optionen als Strings statt
    ``{label,...}``-Objekte sind eine sehr uebliche Modellausgabe) — lief die
    Ausnahme aber ungefangen bis in den aeusseren Fehlerzweig, endete der ganze
    Lauf als ``AI_TOOL_REJECTED``, und die Nachricht fiel auf ``failed``: aller
    bis dahin gestreamter Text war fuer Neuladen **und** Folgekontext verloren.
    Fuer Schreib- und Lesewerkzeuge ist genau dieses Muster laengst repariert;
    diese Funktion schliesst die verbliebene Luecke am Fragepfad.

    Wie bei `_ask_refusal_messages` wird die **ganze** Runde beantwortet: das
    Protokoll verlangt zu jeder `tool_call_id` genau eine Antwort.
    """
    return _rundenfehler_nachrichten(
        tool_calls,
        rundentext,
        code="AI_ASK_INVALID",
        hinweis=(
            f"Die Rueckfrage wurde nicht gestellt: {grund}. "
            "Stelle sie erneut — `question` als Text, `options` als Liste von "
            "zwei bis vier Objekten mit `label` (und optional `hint`)."
        ),
    )


def _write_followup_messages(
    *, conversation_id: str, tool_calls, proposals: list[dict],
    run_id: str | None = None, rundentext: str | None = None,
) -> list[dict]:
    """Gibt dem Modell zurueck, was aus seinen Schreib-Aufrufen geworden ist.

    Ohne diesen Rueckfluss endete ein Schreibvorgang stumm: das Modell hatte
    nur einen Werkzeugaufruf abgegeben und nie erfahren, ob er durchging. Die
    Antwortnachricht blieb leer ("Keine Antwort erhalten"), und — schwerer
    wiegend — der naechste Zug sah eine Historie ohne jede Spur der Aktion.
    Ein blosses "danke" wirkte dort wie eine noch offene Bitte, und das Modell
    stoppte denselben Server ein zweites Mal.

    Der Ergebnistext wird zusaetzlich als `AiToolResult` abgelegt. Die
    Abschlussrunde koennte auch ohne Text enden; die Zeile stellt sicher, dass
    die Historie den Vorgang trotzdem kennt.

    Zugeordnet wird **je Aufruf** und nicht je Werkzeugname. Vorher bekam bei
    zwei gleichnamigen Aufrufen in einer Runde jeder von beiden beide Ausgaenge:
    bei serverbezogenen Werkzeugen half noch die `server_id`, bei globalen wie
    `propose_task_set` (dort ist sie immer ``None``) war nicht mehr erkennbar,
    welche der beiden Aufgaben angelegt wurde und welche nicht — falsche Daten
    unter richtigem Namen.
    """
    outcome_by_call: dict[str, list[dict]] = {}
    for proposal in proposals:
        outcome_by_call.setdefault(proposal.get("call_id") or "", []).append({
            "status": proposal.get("status"),
            "autonomous": proposal.get("autonomous"),
            "server_id": proposal.get("server_id"),
            **({"error_code": proposal["error_code"]} if proposal.get("error_code") else {}),
            # Ein Code allein sagt "ging nicht". Bei einem Formfehler steht das
            # Brauchbare im Text — welches Feld, welche Form, und oft der
            # naechste Schritt. Ohne ihn haette das Modell nichts, woraus es
            # einen besseren zweiten Versuch bauen koennte.
            **({"error": proposal["error"]} if proposal.get("error") else {}),
        })

    assistant_call = _aufrufnachricht(tool_calls, rundentext)
    messages: list[dict] = [assistant_call]
    for call in tool_calls:
        outcomes = outcome_by_call.get(call.id, [])
        # `succeeded` heisst ausgefuehrt, `proposed` heisst: wartet auf den
        # Menschen. Die Unterscheidung muss beim Modell ankommen, sonst meldet
        # es einen Vorschlag als erledigt.
        payload = {"tool": call.name, "outcomes": outcomes}
        messages.append({
            "role": "tool",
            "tool_call_id": call.id,
            "content": json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        })

    try:
        with SessionLocal() as db:
            # Eine Zeile je Aufruf, nicht je Werkzeugname — dieselbe Aufteilung
            # wie oben, damit die Historie spaeter dasselbe erzaehlt wie der
            # Rueckfluss. Aufrufe ohne Ausgang bekommen keine Zeile: sie gab es
            # vorher auch nicht.
            for call in tool_calls:
                outcomes = outcome_by_call.get(call.id, [])
                if not outcomes:
                    continue
                db.add(AiToolResult(
                    id=str(uuid4()),
                    conversation_id=conversation_id,
                    run_id=run_id,
                    tool_name=call.name,
                    result_json=json.dumps(
                        {"outcomes": outcomes}, ensure_ascii=True, separators=(",", ":")
                    ),
                ))
            db.commit()
    except Exception:
        # Die Spur in der Historie ist wichtig, aber nicht wichtiger als die
        # Antwort. Scheitert das Schreiben, laeuft die Abschlussrunde trotzdem.
        logger.warning(
            "Ergebnis der Schreibaktion nicht persistiert conversation_id=%s", conversation_id
        )
    return messages


def _vorschlag_ergebnisse(db, proposal_ids: list[str]) -> list[dict]:
    """Was aus den Vorschlaegen einer geparkten Runde geworden ist.

    Genau die Auskunft, die das Modell beim Aufwecken braucht: hat der Mensch
    zugestimmt, ist die Aktion gelaufen, ist sie gescheitert. Ohne sie wuesste
    es nur, dass es etwas vorgeschlagen hat.
    """
    ergebnisse: list[dict] = []
    for proposal_id in proposal_ids:
        row = db.get(AiActionProposal, proposal_id)
        if row is None:
            ergebnisse.append({
                "tool_name": "unknown",
                "status": "failed",
                "autonomous": False,
                "server_id": None,
                "error_code": "AI_ACTION_NOT_FOUND",
            })
            continue
        entry = {
            "tool_name": row.tool_name,
            "status": row.status,
            "autonomous": bool(row.autonomous),
            "server_id": row.server_id,
            **({"error_code": row.error_code} if row.error_code else {}),
        }
        if row.status in ("succeeded", "executing"):
            tool_res = (
                db.query(AiToolResult)
                .filter(
                    AiToolResult.conversation_id == row.conversation_id,
                    AiToolResult.run_id == row.run_id,
                    AiToolResult.tool_name == row.tool_name,
                )
                .order_by(AiToolResult.created_at.desc())
                .first()
            )
            if tool_res is not None and tool_res.result_json:
                try:
                    entry["result"] = json.loads(tool_res.result_json)
                except Exception:
                    pass
        ergebnisse.append(entry)
    return ergebnisse


def _aktionsmeldung(ergebnisse: list[dict]) -> dict:
    """Die Entscheidung des Menschen, als Meldung des Panels an das Modell.

    Ausdruecklich als Panel-Meldung beschriftet und nicht als Satz des
    Benutzers: das Modell soll nicht glauben, jemand haette ihm das getippt.
    """
    return {
        "role": "user",
        "content": (
            "Meldung des Panels (nicht vom Benutzer geschrieben): Der Benutzer "
            "hat ueber die vorgeschlagenen Aktionen entschieden. Ergebnis:\n"
            + json.dumps(ergebnisse, ensure_ascii=True, separators=(",", ":"))
            + "\n\nArbeite die Aufgabe von hier aus weiter. Ist sie damit "
            "erledigt, sag es kurz; fehlt noch ein Schritt, mach ihn."
        ),
    }


async def _schreibrunde_ausfuehren(
    *,
    run_id: str,
    user_id: int,
    conversation_id: str,
    vorbereitung: "_Vorbereitung",
    guardian: "GuardianKontext | None",
    aufgabe: "AufgabenKontext | None",
    unbeaufsichtigt: bool,
    rolle: str,
    rundendeckel: int,
    rundentext: str,
    current_usage: StreamUsage,
    provider_messages: list[dict],
    zustand: dict,
    chunks: list[str],
    thoughts: list[str],
    denknaht: str,
) -> _SchreibrundenErgebnis:
    """Eine reine Schreibrunde: Vorschlaege anlegen, parken oder weiterarbeiten.

    Der Supersede-Check am Anfang steht ABSICHTLICH nur hier und nicht in
    einem gemeinsamen Rundenkopf: das ist der einzige Punkt, an dem der
    Lauf etwas veraendert. Listen und `zustand` werden in place
    fortgeschrieben; jeder Ausgang traegt seine Flags selbst — die vier
    Wege unterscheiden sich bewusst darin, was sie setzen.
    """
    # Ein abgeloester Lauf darf die Aussenwelt nicht mehr anfassen.
    #
    # Zwischen dem Beginn dieses Segments und dieser Runde koennen
    # Minuten liegen. Schreibt der Benutzer in der Zwischenzeit etwas
    # Neues, steht dieser Lauf auf 'cancelled' — sein Abbruch wird
    # aber erst am naechsten Haltepunkt zugestellt, und zwischen dem
    # Ende des Anbieterstroms und `_persist_write_proposals` liegt
    # keiner. Ohne diese Frage legte ein ueberholter Lauf noch
    # Vorschlaege in die Unterhaltung und fuehrte autonome Aktionen
    # am Server tatsaechlich aus.
    #
    # Gefragt wird genau hier und nicht in jeder Runde: das ist der
    # einzige Punkt, an dem der Lauf etwas veraendert.
    if ai_run_broker.lauf_status(run_id) != "running":
        return _SchreibrundenErgebnis(denknaht=denknaht, abgeloest=True)
    # **Das Gehirn fasst keine Server an.** Server-Schreibwerkzeuge bleiben dem
    # Worker vorbehalten — dieser Zweig ist der Spiegel dazu im Vorschlagspfad,
    # denn der Katalogschnitt ist eine Bitte und keine Zusage. Kommunikations-
    # und Dialogvorschlaege (E-Mail, Kalender, Pop-up) legt das Gehirn direkt an.
    if rolle == "gehirn":
        unzulässig = [
            call for call in current_usage.tool_calls
            if call.name not in (CHAT_INTERACTION_TOOLS & WRITE_TOOLS)
        ]
        if unzulässig:
            provider_messages.extend(_rundenfehler_nachrichten(
                unzulässig,
                rundentext,
                code="AI_GEHIRN_READONLY",
                hinweis=(
                    "Das Gehirn führt keine direkten Server-Aktionen aus. Der Aufruf lief nicht — "
                    "gib die Server-Arbeit mit worker_start als Auftrag in den Hintergrund."
                ),
            ))
            if _runde_zaehlen(zustand, rundendeckel):
                return _SchreibrundenErgebnis(
                    denknaht=denknaht, budget_erschoepft=True, letzte_runde=True
                )
            return _SchreibrundenErgebnis(denknaht=denknaht)
    # **Dieselbe Ansage wie im Lesepfad**, an derselben Stelle im
    # Ablauf: geprueft ist geprueft, angelegt ist noch nichts.
    # Achtzehn der zweiundfuenfzig gepflegten Verlaufssaetze
    # gehoeren Schreibwerkzeugen und waren ohne diese Zeile
    # unerreichbar — die laengste Stille des Laufs liegt
    # ausgerechnet hier, wo ein Backup laeuft.
    #
    # Sie steht hier und nicht in `_persist_write_proposals`, weil
    # diese Funktion gleich in einem Thread laeuft; `veroeffentlichen`
    # gehoert auf die Ereignisschleife.
    # Ethics Engine Reflexion: Falls konfiguriert und vom Trigger als pruefbeduerftig
    # eingestuft, beraet die Engine das System vor Ausfuehrung der Schreibvorschlaege.
    from services import ai_ethics_service, ai_ethics_trigger, ai_memory_service
    if ai_provider_service.fuer_ethics(vorbereitung.provider):
        for call in current_usage.tool_calls:
            trigger = ai_ethics_trigger.should_trigger_ethics(
                call.name,
                getattr(call, "arguments", None) or {},
                ethics_mode=vorbereitung.provider.ethics_mode or "auto",
                goal=f"Ausführung von {call.name}",
                planned_action=f"{call.name} in Schreibrunde",
            )
            if trigger.should_evaluate:
                with SessionLocal() as db_ethics:
                    benutzer = db_ethics.get(User, user_id)
                    if benutzer is not None:
                        mem_context = ai_memory_service.provider_memory_context(
                            db_ethics, benutzer, query=call.name
                        )
                        try:
                            eval_res = await ai_ethics_service.evaluate_decision(
                                client,
                                db_ethics,
                                vorbereitung.provider,
                                benutzer,
                                trigger.decision_context,
                                relevant_memories=[mem_context] if mem_context else None,
                            )
                            if eval_res.assessment in ("review", "critical"):
                                logger.info(
                                    "Ethics Engine Hinweis für %s: %s (assessment=%s)",
                                    call.name, eval_res.recommendation, eval_res.assessment,
                                )
                        except Exception as fehler:
                            logger.warning("Ethics Engine Auswertung fehlgeschlagen: %s", fehler)

    _werkzeuge_ansagen(run_id, current_usage.tool_calls)
    # **Als Ganzes in einen Thread, innen unveraendert sequenziell.**
    #
    # Hier entstehen die Backups, Neustarts und
    # Konfigurationsaenderungen — die langsamste Arbeit des ganzen
    # Laufs, und bisher lief sie auf der Ereignisschleife. Drei
    # Backups zu drei Sekunden hiessen neun Sekunden, in denen das
    # Panel niemandem antwortete.
    #
    # Was **nicht** nebenlaeufig wird: der Inhalt. Die Reihenfolge
    # "erst sichern, dann anfassen" und der Abbruch der Runde nach
    # einer Ablehnung sind Zusagen an den Benutzer, keine
    # Ablaufdetails. Sie stehen unveraendert in
    # `_persist_write_proposals`.
    proposals = await asyncio.to_thread(
        ai_stream._persist_write_proposals,
        user_id=user_id,
        conversation_id=conversation_id,
        tool_calls=current_usage.tool_calls,
        correlation_id=vorbereitung.request_id,
        run_id=run_id,
        guardian=guardian,
        aufgabe=aufgabe,
    )
    call_by_id = {call.id: call for call in current_usage.tool_calls}
    for proposal in proposals:
        call = call_by_id.get(proposal.get("call_id"))
        if call is not None:
            anzeige = _anzeigeeintrag(
                call,
                {"proposal_id": proposal.get("id"), "autonomous": bool(proposal.get("autonomous"))},
                proposal.get("error"),
            )
            if run_id is not None:
                ai_run_broker.veroeffentlichen(run_id, "tool", anzeige)
        # Nur echte Vorschlaege bekommen eine Karte. Ein abgelehnter
        # Aufruf hat keine Zeile und keine Kennung; ihn als
        # Vorschlagsereignis zu senden waere eine Karte ohne Knopf.
        if not proposal.get("id"):
            continue
        # `call_id` ist die interne Buchfuehrung des Rueckflusses und bleibt
        # draussen: die Karte traegt genau den Vertrag der REST-Antwort, und
        # derselbe Abzug ersetzt beim Wiederanhaengen die vollstaendige Liste.
        karte = {name: wert for name, wert in proposal.items() if name != "call_id"}
        ai_run_broker.veroeffentlichen(
            run_id, "action" if proposal.get("autonomous") else "proposal", karte
        )
    # Das Ergebnis geht **immer** zurueck ans Modell, auch wenn es
    # nur "wartet auf den Menschen" lautet. Das Protokoll verlangt zu
    # jeder `tool_call_id` genau eine Antwort — und das Modell soll
    # den Vorgang in Worte fassen koennen, statt eine leere Blase
    # ueber der Bestaetigungskarte zu hinterlassen.
    provider_messages.extend(_write_followup_messages(
        conversation_id=conversation_id,
        tool_calls=current_usage.tool_calls,
        proposals=proposals,
        run_id=run_id,
        rundentext=rundentext,
    ))
    zustand["write_rounds"] = int(zustand.get("write_rounds", 0)) + 1
    # Dieselbe Rundennaht wie im Lesepfad weiter unten, und sie steht
    # **vor** den vier Ausstiegen dieser Schreibrunde, weil nach
    # jedem von ihnen noch Text gestreamt wird — auch die letzte
    # Runde vor dem Parken schreibt ihren Schlusssatz. Ohne die Naht
    # klebte er an der Ansage dieser Runde: „…ich lege das Backup
    # an.Das Backup ist fertig…“ — im gespeicherten `content`, im
    # Live-Abzug und in den Berichtsmails.
    #
    # Anders als im Lesepfad geht die Naht auch als `delta` hinaus:
    # dort trennt der Werkzeugabschnitt die Textabschnitte des
    # Abzugs ohnehin, hier gibt es keinen — Vorschlaege stehen in
    # `vorschlaege`, nicht in `abschnitte`, und `text_anhaengen`
    # schriebe sonst live denselben Textabschnitt nahtlos weiter.
    if chunks and not chunks[-1].endswith("\n"):
        chunks.append("\n\n")
        ai_run_broker.veroeffentlichen(run_id, "delta", {"content": "\n\n"})
    if thoughts and not thoughts[-1].endswith("\n"):
        denknaht = "\n\n"

    # **Der Punkt, an dem der Lauf frueher endete.** Wartet auch nur
    # ein Vorschlag auf einen Menschen, wird geparkt statt
    # aufgegeben: der Zustand geht in die Datenbank, und die
    # Bestaetigung weckt genau hier wieder auf.
    #
    # Geparkt wird aber erst **nach** einer letzten Runde ohne
    # Werkzeuge. Sonst stuende die Karte im Chat und darueber
    # nichts — der Benutzer soll lesen, was da bestaetigt werden
    # will und warum.
    # `proposal.get("id")` statt `proposal["id"]`: seit ein
    # abgelehnter Aufruf als Ergebnis mitlaeuft, tragen nicht mehr
    # alle Eintraege eine Kennung. Ein `KeyError` genau hier haette
    # den Lauf mitten in einer Schreibrunde abgerissen — nach dem
    # Anlegen der Vorschlaege und vor dem Parken.
    offen = [
        proposal["id"] for proposal in proposals
        if proposal.get("id")
        and proposal.get("status") in {"proposed", "confirmed"}
    ]
    # **Ein Worker hat einen Menschen ueber sich.** Er zaehlt als
    # unbeaufsichtigt, weil ihm `ask_user` fehlt und niemand seinen
    # Verlauf mitliest — aber jemand hat ihn gerade beauftragt und
    # sitzt vor dem Chat. Ohne diese Unterscheidung lief er in den
    # Zweig fuer Heilungen und faellige Aufgaben: erst eine
    # Freigabe-Mail, und ohne Versandweg wurde der Vorschlag
    # zurueckgenommen und der Auftrag beendet. Der Benutzer bekam
    # dann keinen Knopf, sondern eine Erzaehlung darueber, dass
    # etwas haette bestaetigt werden muessen (22.08.2026: „Soll
    # MauntARK trotzdem jetzt neu gestartet werden?").
    #
    # **Faellige Aufgaben sind nicht mitgemeint**, obwohl sie seit
    # dem 20.08.2026 ebenfalls in Fenstern mit `kind='worker'`
    # laufen: `ai_task_service` uebergibt ausdruecklich
    # `rolle="voll"` (die Begruendung steht dort). Um drei Uhr
    # nachts sitzt niemand vor dem Chat — dort bleibt der Mailweg
    # richtig, und er bleibt.
    niemand_da = unbeaufsichtigt and rolle != "worker"
    if offen and niemand_da:
        # Eine Heilung parkt nicht, und eine faellige Aufgabe
        # ebensowenig. Es ist niemand da, der die Karte anklickt.
        #
        # Der Fall ist nicht theoretisch: das Stundenkontingent ist
        # benutzerweit, und `autonomy_allows` faellt bei Erschoepfung
        # ausdruecklich auf Bestaetigungspflicht zurueck statt zu
        # scheitern. Wer vormittags im Chat gearbeitet hat, dessen
        # naechtliche Heilung stiess also mitten im Vorgang an die
        # Grenze. `zustaendiger_freigeber` prueft nur die Obergrenze
        # des Grants, nicht den bereits verbrauchten Stand — und das
        # kann es auch nicht, weil die Grenze waehrend des Laufs
        # kippt.
        #
        # Geparkt bedeutete: Status 'waiting_confirmation', kein
        # Endzustand, also kein Bericht; im Guardian-Reiter dauerhaft
        # "die KI bearbeitet das"; und weil `aktiver_lauf` wartende
        # Laeufe mitzaehlt, keine weitere Heilung dieses Freigebers
        # auf keinem seiner Server. Ein Neustart des Panels hob das
        # nicht auf, denn `unterbrochene_laeufe_abgleichen` fasst
        # 'waiting_*' bewusst nicht an.
        #
        # Ein Test dieses Projekts schreibt die Regel schon fest: ein
        # Freigeber mit Budget 0 kommt gar nicht erst als Akteur in
        # Frage, weil "ein Lauf, der sofort auf eine Bestaetigung
        # wartet, keine Heilung ist, sondern eine Zeile in der
        # Datenbank, die einen Vorfall als versorgt markiert, ohne es
        # zu sein". Genau dieser Zustand entstand hier — nur eben
        # mitten im Lauf statt am Anfang.
        #
        # **Seit es die Freigabe per E-Mail gibt, wird zuerst
        # gefragt.** Alle vier Gruende oben sind einzeln abgeraeumt:
        # `aktiver_lauf` ist nach Unterhaltung gefasst, also
        # blockiert ein geparkter Reparaturlauf keine weitere
        # Heilung; der Takt laeuft in eine Frist und weckt den Lauf,
        # statt ihn ewig stehen zu lassen; die Abschlussmail kommt
        # vom Auftrag, nicht vom einzelnen Lauf; und der
        # Guardian-Reiter zeigt den wartenden Lauf an.
        #
        # Der Rueckfall darunter bleibt trotzdem stehen und ist
        # nicht tot: ohne hinterlegte Adresse, ohne eingerichteten
        # Versandweg oder wenn schon eine Freigabe offen ist, kann
        # niemand antworten. Dann wird zurueckgenommen und beendet,
        # genau wie vorher — ein Lauf, der auf eine Antwort wartet,
        # die nie kommen kann, waere schlimmer als einer, der
        # ehrlich aufhoert.
        if _freigabe_per_mail_erbeten(run_id, offen):
            zustand["pending"] = {"proposal_ids": list(offen)}
            return _SchreibrundenErgebnis(
                denknaht=denknaht, geparkt=True, letzte_runde=True
            )

        # Sonst: die offenen Vorschlaege zuruecknehmen und den
        # Lauf beenden. Der Bericht geht dann hinaus und sagt dem
        # Betreiber ehrlich, dass die KI nicht weiterkonnte.
        _vorschlaege_zuruecknehmen(offen, grund="guardian_unattended")
        logger.info(
            "Lauf ohne Zuhoerer beendet: Vorschlag verlangt "
            "Bestaetigung, niemand ist da run_id=%s anzahl=%d",
            run_id, len(offen),
        )
        return _SchreibrundenErgebnis(
            denknaht=denknaht, budget_erschoepft=True, letzte_runde=True
        )
    if offen:
        zustand["pending"] = {
            "proposal_ids": [
                proposal["id"] for proposal in proposals if proposal.get("id")
            ],
        }
        if rolle == "worker":
            # Ein Worker schreibt in sein eigenes Fenster, das niemand
            # mitliest. Solange er endete, sagte die Meldestelle wenigstens
            # sein Ergebnis an; seit er stattdessen parkt, ist
            # 'waiting_confirmation' kein Endzustand — und damit passierte
            # gar nichts mehr. Wer per Stimme arbeitet oder den Chat
            # zugeklappt hat, wartete auf eine Antwort, die nie kam.
            await asyncio.to_thread(
                _freigabe_melden, user_id, conversation_id, zustand
            )
        return _SchreibrundenErgebnis(
            denknaht=denknaht, geparkt=True, letzte_runde=True
        )
    # Alles ausgefuehrt? Dann darf der Lauf weiterarbeiten. Das ist
    # der Unterschied zwischen "eine Aktion abgeben" und "eine
    # Aufgabe erledigen": erst wenn Schritt eins nachweislich lief,
    # ergibt Schritt zwei ueberhaupt Sinn.
    ausgefuehrt = bool(proposals) and all(
        proposal.get("status") in {"succeeded", "executing"}
        and not proposal.get("error_code")
        for proposal in proposals
    )
    # **Eine Runde, in der gar nichts entstanden ist, darf wiederholt
    # werden.**
    #
    # Ohne diese Zeile war der Rueckfluss des Ablehnungsgrundes eine
    # Geste: das Modell erfuhr, dass `time_of_day` im Format HH:MM
    # stehen muss — und bekam im selben Atemzug die Werkzeuge
    # weggenommen. Es konnte den Fehler nur noch beschreiben, nicht
    # beheben. Der Benutzer las dann "ich konnte die Aufgabe nicht
    # anlegen", obwohl der zweite Versuch durchgegangen waere.
    #
    # Die urspruengliche Regel ("erst wenn Schritt eins nachweislich
    # lief, ergibt Schritt zwei Sinn") bleibt unangetastet, denn sie
    # meint etwas anderes: eine Aktion, die **ausgefuehrt wurde und
    # scheiterte**. Dann ist der Serverzustand unklar, und
    # weiterzumachen waere leichtsinnig. Hier dagegen ist nichts
    # passiert — kein Vorschlag, keine Zeile, kein Eingriff.
    #
    # Begrenzt ist es durch dieselbe Zahl wie zuvor: `write_rounds`
    # zaehlt jede Schreibrunde, auch die abgelehnte.
    nichts_entstanden = not any(
        proposal.get("id") for proposal in proposals
    )
    if not (
        (ausgefuehrt or nichts_entstanden)
        and zustand["write_rounds"] < MAX_WRITE_ROUNDS
    ):
        # Nur wenn die Runde *ausgefuehrt* wurde und trotzdem Schluss
        # ist, lag es am Budget. Endet sie, weil ein Vorschlag offen
        # blieb, wartet der Lauf auf einen Menschen — das ist kein
        # aufgebrauchtes Budget.
        return _SchreibrundenErgebnis(
            denknaht=denknaht, budget_erschoepft=ausgefuehrt, letzte_runde=True
        )
    return _SchreibrundenErgebnis(denknaht=denknaht)

