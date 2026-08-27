# -*- coding: utf-8 -*-
"""Lebenszyklus, Vorbereitung, Abschluss und Berichte von Segmenten."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from uuid import UUID, uuid4

import httpx

from database import SessionLocal
from models import AiMessage, AiProvider, AiRun, AiUsageEvent, DesktopJob, User
from models.ai_run import BEENDET as AUSGELAUFEN
import services.ai_stream as ai_stream
from services import (
    ai_attachment_service,
    ai_guardian_repair_service,
    ai_guardian_report,
    ai_guardian_service,
    ai_meldestelle,
    ai_model_catalog,
    ai_provider_service,
    ai_reasoning,
    ai_run_broker,
    ai_run_service,
    ai_task_report,
    desktop_job_service,
)
from services.ai_action_service import angebotene_werkzeuge, provider_tool_definitions
from services.ai_context_service import (
    MIN_HISTORY_CHARS,
    anlagenwissen_nachtrag,
    auf_budget_kuerzen,
    build_provider_messages,
    estimate_reserved_tokens,
    message_character_count,
    teilbudgets,
)
from services.ai_proposal_service import AufgabenKontext, GuardianKontext
from services.ai_provider_service import estimate_cost_microunits, resolve_api_key
from services.ai_redaction import redact_sensitive_text
from services.ai_stream.context import (
    _denken_am_modell,
    _modell_fuer,
    aufgabe_aus_zustand,
    familie_aus_zustand,
    guardian_aus_zustand,
    herkunft_aus_zustand,
    rolle_aus_zustand,
    worker_aus_zustand,
)
from services.ai_stream.interactions import (
    _alte_bilder_entwerten,
    _desktopmeldung,
    _sieht_nicht,
)
from services.ai_stream.read_tools import (
    _aufrufnachricht,
    _aussortieren,
    _runde_zaehlen,
    _rundenfehler_nachrichten,
)
from services.ai_stream.types import (
    KEIN_BLICK_GRUND,
    MAX_GLEICHE_AUFRUFE,
    MAX_GLEICHE_POLLING_AUFRUFE,
    MAX_TOOL_ROUNDS,
    POLLING_WERKZEUGE,
    GuardianRahmenUnlesbar,
    _Anlauf,
    _Vorbereitung,
)
from services.ai_stream.write_tools import (
    _aktionsmeldung,
    _vorschlag_ergebnisse,
    _vorschlaege_zuruecknehmen,
)
from services.ai_tool_registry import (
    ASK_TOOLS,
    CHAT_INTERACTION_TOOLS,
    DESKTOP_TOOLS,
    GEHIRN_TOOLS,
    GUARDIAN_HEILUNG_TOOLS,
    NUR_WORKER,
    READ_TOOLS,
    SERVER_READ_TOOLS,
    SKILL_TOOLS,
    WERKZEUGE,
    WORKER_STEUERUNG,
    WRITE_TOOLS,
    aufgaben_tools,
    herkunft_schnitt,
    worker_ausschluss,
)
from services.ai_usage_service import (
    AiQuotaExceeded,
    AiUsageConflict,
    abrechnung,
    complete_ai_usage,
    fail_ai_usage,
    reserve_ai_usage,
)
from services.dis_client import DisSidecarError
from services.openai_compatible_adapter import (
    MAX_REASONING_CHARS,
    StreamUsage,
)

logger = logging.getLogger(__name__)


def _abschnitt_fuer_ablage(abschnitt: dict) -> dict:
    """Ein Abschnitt so, wie er in die Datenbank darf.

    Denkabschnitte werden geschwärzt und gekürzt — **genau wie das Feld
    ``reasoning`` daneben**, das aus ihnen abgeleitet wird. Ohne diese Zeile
    hätte die neue Gliederung die alte Schwärzung ausgehebelt: der Denktext
    läge dann roh in ``sections_json``, und die Oberfläche zeichnet ihn von
    dort. Ein Modell kann in seinen Überlegungen denselben Schlüssel
    wiederholen wie in der Antwort.
    """
    if abschnitt.get("art") != "denken":
        return abschnitt
    roh = str(abschnitt.get("inhalt") or "")
    return {"art": "denken", "inhalt": redact_sensitive_text(roh)[:MAX_REASONING_CHARS]}


def _finalize_stream(
    *,
    message_id: str,
    usage_event_id: int,
    content: str,
    usage: StreamUsage,
    estimated_actual_tokens: int,
    failed: bool,
    had_output: bool,
    token_price_micro_usd_per_million: int | None = None,
    reasoning: str = "",
    question: dict | None = None,
    abschnitte: list[dict] | None = None,
) -> None:
    with SessionLocal() as db:
        message = db.get(AiMessage, message_id)
        usage_event = db.get(AiUsageEvent, usage_event_id)
        if usage_event is None:
            # Ohne Verbrauchszeile gibt es nichts mehr abzurechnen.
            logger.warning("AI usage event missing at finalization message_id=%s", message_id)
            return
        if message is not None:
            # **Die Antwort wird nicht geschwärzt — und das ist Absicht.**
            #
            # Sie ist der einzige Text hier, in dem eine Zuweisung eine
            # *Anleitung* sein kann: auf "wie setze ich das RCON-Passwort"
            # antwortet ein Modell mit `rcon.password=DeinPasswort`, und
            # `[REDACTED]` an dieser Stelle wäre keine Antwort mehr. Die Wege
            # nach draußen sind trotzdem alle dicht: an den Anbieter geht die
            # Historie nur durch `redact_sensitive_text` (ai_context_service),
            # in die Berichtsmails ebenso (ai_task_report, ai_guardian_report),
            # und gelesen wird diese Zeile ausschließlich von dem Benutzer,
            # dessen Unterhaltung sie ist. Wer das ändert, muss die Zeile hier
            # mit ändern — und dann auch `_abschnitt_fuer_ablage` daneben,
            # sonst steht derselbe Text roh in `sections_json`.
            message.content = content
            # Denkschritte werden mitgespeichert, damit der aufklappbare Block
            # nach einem Neuladen der Seite noch da ist. Anders als die Antwort
            # **wird** er geschwärzt: er ist Selbstgespräch und keine Anleitung,
            # niemand liest hier eine Beispielzeile heraus — und ein Modell
            # wiederholt in seinen Überlegungen genauso einen Key wie im Text.
            message.reasoning = (
                redact_sensitive_text(reasoning)[:MAX_REASONING_CHARS] or None
            )
            # Die Rueckfrage gehoert zur Nachricht. Sie ist bereits durch
            # `question_payload()` geprueft, gekuerzt und redigiert — hier wird
            # nur noch abgelegt.
            if question is not None:
                message.question_json = json.dumps(
                    question, ensure_ascii=True, separators=(",", ":")
                )
            # Die Gliederung — Text und Werkzeuge in ihrer Reihenfolge. Sie kommt
            # aus dem Vermittler, weil der sie ohnehin fuehrt; eine zweite Liste
            # im Lauf waere dieselbe Sache ein zweites Mal, und Reihenfolgen
            # laufen dann auseinander.
            #
            # Nur schreiben, wenn es etwas gibt: eine leere Liste hiesse "diese
            # Antwort hatte keine Abschnitte", und das stimmt fuer einen Lauf,
            # dessen Kanal schon abgeraeumt war, gerade nicht.
            if abschnitte:
                message.sections_json = json.dumps(
                    [_abschnitt_fuer_ablage(eintrag) for eintrag in abschnitte],
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
            message.status = "failed" if failed else "complete"
        else:
            # Die Nachricht wurde waehrend des Streams entfernt (z. B. Chat
            # geloescht). Die Reservierung muss trotzdem abgeschlossen werden:
            # sonst bliebe sie dauerhaft "reserved" und wuerde Kontingent sowie
            # einen Nebenlaeufigkeitsplatz des Benutzers permanent blockieren.
            logger.warning("AI message missing at finalization message_id=%s", message_id)
        if failed and not had_output:
            ai_stream.fail_ai_usage(db, usage_event)
        else:
            # Nach partieller Ausgabe darf Verbrauch nicht als null verbucht
            # werden — auch dann nicht, wenn der Lauf gescheitert ist.
            accounted_tokens, accounted_cost, herkunft = ai_stream.abrechnung(
                usage,
                reserved_tokens=usage_event.reserved_tokens,
                estimated_actual_tokens=estimated_actual_tokens,
                failed=failed,
                token_price_micro_usd_per_million=token_price_micro_usd_per_million,
            )
            ai_stream.complete_ai_usage(
                db,
                usage_event,
                actual_tokens=accounted_tokens,
                actual_cost_microunits=accounted_cost,
                aufschluesselung=usage,
                cost_source=herkunft,
            )
        db.commit()


def _segment_beginnen(db, run: AiRun, zustand: dict) -> tuple[str, int, str] | None:
    """Legt Nachricht und Verbrauchszeile fuer ein neues Segment an.

    Jede Fortsetzung nach einer Bestaetigung schreibt eine **eigene** Nachricht.
    Das ist Absicht: "ich stelle um, bitte bestaetigen" und "erledigt, der
    Server laeuft" sind zwei Aussagen. Sie in eine Blase zu zwingen haette
    bedeutet, den Text nachtraeglich zu veraendern — und den Zeitpunkt zu
    verwischen, an dem der Mensch zugestimmt hat.
    """
    provider = db.get(AiProvider, run.provider_id) if run.provider_id else None
    if provider is None:
        return None
    # Das Modell der Rolle, nicht pauschal `default_model`: ein Worker-Segment
    # bucht und beschriftet mit dem Arbeitsmodell des Betreibers.
    modell = _modell_fuer(provider, rolle_aus_zustand(zustand))
    request_id = str(uuid4())
    usage_event = ai_stream.reserve_ai_usage(
        db,
        db.get(User, run.user_id),
        request_id=UUID(request_id),
        estimated_tokens=estimate_reserved_tokens(zustand["provider_messages"]),
        estimated_cost_microunits=estimate_cost_microunits(
            provider, estimate_reserved_tokens(zustand["provider_messages"])
        ),
        server_id=None,
        provider_id=provider.id,
        model=modell,
    )
    message_id = str(uuid4())
    db.add(AiMessage(
        id=message_id,
        conversation_id=run.conversation_id,
        role="assistant",
        content="",
        status="streaming",
        provider_id=provider.id,
        model=modell,
        request_id=request_id,
    ))
    db.flush()
    return message_id, usage_event.id, request_id


def _segment_vorbereiten(run_id: str) -> tuple[_Vorbereitung | None, tuple[str, str] | None]:
    """Holt den Lauf aus der Datenbank und macht ihn lauffaehig.

    Die Sitzung ist absichtlich kurz: waehrend der Anbieter streamt, darf keine
    Transaktion offen stehen. Genau dafuer ist der Zustand persistiert.
    """
    with SessionLocal() as db:
        run = db.get(AiRun, run_id)
        if run is None:
            return None, ("AI_RUN_NOT_FOUND", "ai.chat.errors.notFound")
        if run.status not in {"running"}:
            # Zwischen Planung und Ausfuehrung hat sich etwas geaendert — etwa
            # eine neue Nachricht, die den Lauf ueberholt hat.
            return None, None
        user = db.get(User, run.user_id)
        if user is None or not user.is_active:
            return None, ("AI_ACCESS_REVOKED", "ai.chat.errors.access")
        provider = db.get(AiProvider, run.provider_id) if run.provider_id else None
        if provider is None or not provider.enabled:
            return None, ("AI_RESOURCE_NOT_FOUND", "ai.chat.errors.notFound")

        zustand = ai_run_service.zustand_lesen(run)

        # Fortsetzung: der Lauf erfaehrt, wie der Mensch entschieden hat.
        #
        # Bewusst **kein** zweites Werkzeugergebnis: die `tool_call_id` der
        # geparkten Runde hat ihre Antwort schon bekommen ("wartet auf
        # Bestaetigung"), und das Protokoll erlaubt nur eine je Aufruf. Die
        # Entscheidung ist auch kein Werkzeugergebnis, sondern eine Meldung des
        # Panels — und genau als solche gekennzeichnet. Dasselbe Muster nutzt
        # `ai_context_service` bereits fuer frueher gelesene Werkzeugergebnisse.
        pending = zustand.get("pending")
        if pending:
            ergebnisse = _vorschlag_ergebnisse(db, list(pending.get("proposal_ids", [])))
            zustand["provider_messages"].append(_aktionsmeldung(ergebnisse))
            zustand["pending"] = None

        # Dasselbe fuer den Rechner des Benutzers: was dort passiert ist,
        # erfaehrt das Modell beim Aufwachen — auch der Verfall, wenn der
        # Rechner gar nicht geantwortet hat.
        desktop = zustand.get("desktop")
        if desktop:
            from services import desktop_job_service

            meldung = _desktopmeldung(
                desktop_job_service.ergebnisse(db, list(desktop.get("job_ids", [])))
            )
            # **Vor** dem Anhaengen: nur das juengste Bildschirmfoto bleibt ein
            # Bild. Bei einer Klick-Schleife kaeme sonst jede Runde ein weiteres
            # dazu — teuer, und die aelteren zeigen einen Bildschirm, den es
            # nicht mehr gibt.
            if isinstance(meldung.get("content"), list):
                weg = _alte_bilder_entwerten(zustand["provider_messages"])
                if weg:
                    logger.info(
                        "Aeltere Bildschirmfotos entwertet run_id=%s anzahl=%d",
                        run.id, weg,
                    )
            zustand["provider_messages"].append(meldung)
            zustand["desktop"] = None

        try:
            api_key = resolve_api_key(db, provider, user.id)
        except DisSidecarError:
            return None, ("AI_CREDENTIAL_UNAVAILABLE", "ai.chat.errors.credential")
        if provider.requires_api_key and not api_key:
            return None, ("AI_PROVIDER_KEY_MISSING", "ai.chat.errors.keyMissing")

        if run.message_id:
            nachricht = db.get(AiMessage, run.message_id)
            message_id = run.message_id
            usage_event_id = zustand.get("usage_event_id")
            request_id = zustand.get("request_id") or (
                nachricht.request_id if nachricht is not None else str(uuid4())
            )
            if usage_event_id is None:
                # Der Startpfad legt beides an; fehlt die Verbuchung, ist der
                # Zustand kaputt und ein neues Segment ehrlicher als raten.
                run.message_id = None
        if not run.message_id:
            # `_segment_beginnen` reserviert Verbrauch — und das kann scheitern.
            # Ohne diese Behandlung verliess eine `AiQuotaExceeded` die ganze
            # Funktion und damit auch `segment_ausfuehren`: die asyncio-Aufgabe
            # starb, `_lauf_abschliessen` lief nie, kein `error` ging an den
            # Vermittler. Der Lauf stand danach **fuer immer** auf `running`,
            # und die Oberflaeche zeigte einen tippenden Assistenten, der nie
            # wieder etwas sagt.
            #
            # Der haeufigste Weg dorthin ist kein Sonderfall: ein Lauf parkt auf
            # `waiting_confirmation`, in der Zwischenzeit ist das Tageskontingent
            # aufgebraucht, der Mensch bestaetigt — und genau dann greift die
            # Reservierung ins Leere.
            #
            # Dieselben Fehlerformen wie in `lauf_beginnen`, damit ein
            # erschoepftes Kontingent an beiden Stellen gleich heisst.
            try:
                begonnen = _segment_beginnen(db, run, zustand)
            except AiUsageConflict:
                db.rollback()
                return None, ("AI_REQUEST_CONFLICT", "ai.chat.errors.requestConflict")
            except AiQuotaExceeded as exc:
                db.rollback()
                return None, (f"AI_QUOTA_{exc.reason.upper()}", "ai.chat.errors.quota")
            except Exception as exc:
                db.rollback()
                logger.warning(
                    "AI-Segment konnte nicht beginnen run_id=%s error=%s",
                    run_id, type(exc).__name__,
                )
                return None, ("AI_PREPARATION_FAILED", "ai.chat.errors.unavailable")
            if begonnen is None:
                return None, ("AI_RESOURCE_NOT_FOUND", "ai.chat.errors.notFound")
            message_id, usage_event_id, request_id = begonnen
            run.message_id = message_id
            zustand["usage_event_id"] = usage_event_id
            zustand["request_id"] = request_id

        ai_run_service.zustand_schreiben(run, zustand)
        db.commit()
        db.refresh(provider)
        db.expunge(provider)
        return _Vorbereitung(
            run_id=run_id,
            user_id=run.user_id,
            conversation_id=run.conversation_id,
            provider=provider,
            api_key=api_key,
            message_id=message_id,
            usage_event_id=int(usage_event_id),
            request_id=str(request_id),
            reasoning=bool(run.reasoning),
            # Aus dem Lauf, nicht neu berechnet: eine Fortsetzung nach einer
            # Bestaetigung muss dieselbe Tiefe verwenden wie der erste Zug.
            # Neu klemmen hiesse, dass ein zwischenzeitlich geaenderter
            # Rollendeckel mitten in einer Aufgabe wirkt.
            reasoning_effort=run.reasoning_effort,
            token_price_micro_usd_per_million=provider.token_price_micro_usd_per_million,
            zustand=zustand,
            angebotene_werkzeuge=angebotene_werkzeuge(db, user),
        ), None


def _lauf_abschliessen(
    run_id: str, *, status: str, stop_reason: str, zustand: dict | None = None,
    wake_at: datetime | None = None,
) -> None:
    with SessionLocal() as db:
        run = db.get(AiRun, run_id)
        if run is None:
            return
        if run.status in AUSGELAUFEN:
            # Ein Endzustand ist endgueltig — auch gegenueber dem eigenen Segment,
            # und auch dann, wenn der Zweitschreiber denselben Status meldet.
            #
            # Die Zusatzbedingung `run.status != status`, die hier naheliegt,
            # waere ausgerechnet im Zielfall falsch: nach `vorgaenger_abloesen`
            # steht der Lauf auf 'cancelled/superseded', und der abgebrochene
            # Vorgaenger meldet aus seinem CancelledError-Zweig ebenfalls
            # 'cancelled', nur mit stop_reason 'cancelled'. Der Status waere
            # gleich, der Waechter fiele durch, und 'superseded' ginge verloren
            # — also genau die Unterscheidung, die dieser Waechter schuetzen
            # soll: wurde der Lauf ueberholt, oder fuhr das Panel herunter?
            #
            # Der Fall aus dem Betrieb: der Benutzer schiebt waehrend des Streams
            # eine zweite Nachricht nach. `vorgaenger_abloesen` schreibt diesen
            # Lauf auf 'cancelled/superseded', der Abbruch seiner Aufgabe wird
            # aber erst am naechsten Haltepunkt zugestellt. Brachte sie ihre
            # Runde vorher zu Ende, meldete sie hier 'completed/done' und
            # ueberschrieb den Abbruch: im Protokoll stand ein Lauf als erledigt,
            # den der Benutzer laengst ueberholt hatte.
            #
            # Gemeldet wird der **tatsaechliche** Zustand und nicht der
            # gewuenschte: die Oberflaeche soll den Abbruch sehen und nicht auf
            # eine Antwort warten, die nicht mehr kommt.
            # Die Nachbereitung laeuft trotzdem — und zwar **vor** dem Ausstieg.
            #
            # Hier lag der schwerste Fehler dieser Kopplung. Der Waechter oben
            # ist richtig, aber er sprang bisher an `_lauf_nachbereiten`
            # vorbei, und der haeufigste Weg in diesen Zweig ist ausgerechnet
            # der, den `ai_guardian_service` als Normalfall beschreibt: der
            # Freigeber tippt waehrend einer Heilung etwas in den Chat,
            # `vorgaenger_abloesen` setzt den Lauf direkt in der Datenbank auf
            # 'cancelled/superseded', und das Segment findet beim Abschliessen
            # einen bereits beendeten Lauf vor.
            #
            # Folge war: keine Mail, obwohl `ai_guardian_report` bei **jedem**
            # Endzustand zusagt. Und weil die Notiz mit `mode='healing'` schon
            # beim Start committet wurde, ueberspringt der Ausloeser den Vorfall
            # von da an bei jedem Takt. Ein Server blieb stehen, ein halb
            # umgeschriebenes Konfigurationsfeld blieb liegen, und niemand
            # erfuhr davon.
            #
            # Der Versand ist gegen Doppelung gesichert (`guardian_berichtet`
            # im Zustand), deshalb schadet der zweite Aufruf nicht, wenn beide
            # Wege einmal zusammenfallen.
            ai_stream._lauf_nachbereiten(db, run, None)
            ai_run_broker.veroeffentlichen(
                run_id,
                "run",
                {"run_id": run_id, "status": run.status, "stop_reason": run.stop_reason},
            )
            ai_run_broker.beenden(run_id)
            return
        run.status = status
        run.stop_reason = stop_reason
        if wake_at is not None:
            # Nur beim Parken auf `waiting_wake` gesetzt (dritte Parkstelle,
            # `wait_until`). Der Takt (`faellige_wecken`) liest genau diese
            # Spalte; das Wecken selbst raeumt sie wieder ab.
            run.wake_at = wake_at
        if zustand is not None:
            ai_run_service.zustand_schreiben(run, zustand)
        if status in AUSGELAUFEN:
            # Das Arbeitsgedächtnis wird nicht mehr gebraucht — und es trägt
            # den entschlüsselten Gedächtnisblock des Benutzers im Klartext.
            # Der Rückgabewert ist **dasselbe** Wörterbuch: die Nachbereitung
            # gleich darunter schreibt es beim Setzen einer Berichtsmarke
            # zurück, und mit der alten Fassung wäre der Klartext wieder da.
            zustand = ai_run_service.arbeitsspeicher_leeren(run, zustand)
        if status != "running":
            # Ein geparkter oder beendeter Lauf hat kein laufendes Segment mehr.
            # Die naechste Fortsetzung legt eine neue Nachricht an.
            run.message_id = None
        run.updated_at = datetime.now(timezone.utc)
        db.commit()
        if status in AUSGELAUFEN:
            ai_stream._lauf_nachbereiten(db, run, zustand)
    ai_run_broker.veroeffentlichen(
        run_id, "run", {"run_id": run_id, "status": status, "stop_reason": stop_reason}
    )
    if status in AUSGELAUFEN:
        ai_run_broker.beenden(run_id)


def _lauf_nachbereiten(db, run: AiRun, zustand: dict | None) -> None:
    """Was am Ende eines Laufs ohne Zuschauer noch zu tun ist.

    **Eine** Funktion fuer beide Rahmen, und das ist eine Entscheidung gegen die
    naheliegende Alternative. Ein zweites `_aufgaben_nachbereiten` daneben
    haette zwei Aufrufe an zwei Stellen gebraucht — und genau das Vergessen
    einer dieser Stellen war der schwerste Fehler der Guardian-Kopplung (siehe
    den Waechterzweig weiter oben). Wer beide Faelle in eine Funktion legt, kann
    die zweite Stelle nicht mehr uebersehen.

    Drei Dinge, alle erst **jetzt** — nicht beim Start:

    * Genannte Vorfaelle als besprochen vermerken. Bricht der Lauf vorher ab,
      bleibt der Vorfall vorgemerkt und kommt beim naechsten Mal wieder. Lieber
      zweimal genannt als einmal verschluckt.
    * War es ein Heilungslauf, geht der Bericht per E-Mail hinaus — bei jedem
      Endzustand. "Nicht geschafft" ist fuer den Betreiber die wichtigere
      Nachricht von beiden, und ein Lauf, der still scheitert, waere die
      schlechteste Eigenschaft dieser ganzen Kopplung.
    * War es ein faelliger stehender Auftrag, geht dessen Bericht hinaus — nach
      derselben Regel und mit derselben Begruendung.
    * War es ein Worker, reicht er sein Ergebnis bei der Meldestelle ein —
      wieder dieselbe Regel; nur die Endzustaende, deren Auskunft ein anderer
      gibt, uebergeht `ai_meldestelle.lauf_beendet` selbst.

    Kapselt alles ab: der Lauf ist zu diesem Zeitpunkt fertig und committet.
    Ein Fehler beim Vermerken oder beim Versand darf ihn nicht nachtraeglich in
    einen Fehlschlag verwandeln.

    **Genau einmal.** Die Funktion wird aus zwei Richtungen gerufen — vom
    regulaeren Abschluss und vom Waechter fuer den bereits beendeten Lauf. Beide
    Wege koennen denselben Lauf treffen (ein abgeloester Lauf, dessen Segment
    danach noch seinen eigenen Abschluss meldet). Je Rahmen entscheidet eine
    Marke im Laufzustand, und sie wird **vor** dem Versand gesetzt: eine zweite
    Mail waere schlimmer als eine ausgebliebene Wiederholung, denn der Betreiber
    liest zweimal denselben Vorgang und weiss nicht, ob es zwei waren.
    """
    if zustand is None:
        zustand = ai_run_service.zustand_lesen(run)
    try:
        from services import ai_guardian_service

        gebrieft = [int(x) for x in (zustand.get("guardian_briefed") or [])]
        if gebrieft:
            ai_guardian_service.briefings_abschliessen(
                db, user_id=run.user_id, incident_ids=gebrieft
            )
            db.commit()
    except Exception:
        db.rollback()
        logger.warning("Guardian-Briefing nicht vermerkt run_id=%s", run.id)

    # **Der Auftrag zuerst, dann der Bericht.** Die Reihenfolge ist tragend:
    # `_bericht_zustellen` fragt gleich darunter, ob der Reparaturauftrag noch
    # laeuft, und laese sonst eine Phase, die noch nicht geschrieben ist. Der
    # Betreiber bekaeme eine Mail "nicht behoben" zu einem Auftrag, der zwei
    # Minuten spaeter weitermacht.
    try:
        from services import ai_guardian_repair_service

        ai_guardian_repair_service.lauf_beendet(db, run, zustand)
    except Exception:
        db.rollback()
        logger.warning("Reparaturauftrag nicht fortgeschrieben run_id=%s", run.id)

    from services import ai_guardian_report, ai_meldestelle, ai_task_report

    ai_stream._bericht_zustellen(
        db, run, zustand,
        rahmen="guardian", marke="guardian_berichtet",
        versenden=ai_guardian_report.bericht_versenden,
    )
    ai_stream._bericht_zustellen(
        db, run, zustand,
        rahmen="aufgabe", marke="aufgabe_berichtet",
        versenden=ai_task_report.bericht_versenden,
    )
    # Der dritte Rahmen: ein beendeter Worker meldet sein Ergebnis an die
    # Meldestelle (docs/agentic-framework.md, §4). Dieselbe Marke-vor-Versand-
    # Mechanik wie bei den beiden anderen — und `lauf_beendet` uebergeht
    # selbst die Endzustaende, die kein Ergebnis sind (abgeloest durch eine
    # Antwort, abgebrochen per worker_cancel, Neustart mit anstehendem
    # Wiederanlauf): dort ist die Marke dann gesetzt, ohne dass eine Meldung
    # entsteht, und genau so soll es sein.
    ai_stream._bericht_zustellen(
        db, run, zustand,
        rahmen="worker", marke="worker_gemeldet",
        versenden=ai_meldestelle.lauf_beendet,
    )


def _bericht_zustellen(db, run: AiRun, zustand: dict, *, rahmen: str, marke: str,
                       versenden) -> None:
    """Setzt die Marke, committet sie, und versendet erst dann.

    Die Reihenfolge ist der ganze Inhalt dieser Funktion. Waere sie umgekehrt,
    entstuende bei einem Fehler nach dem Versand eine zweite Mail beim naechsten
    Anlauf — und der Betreiber saehe zwei Berichte ueber einen Vorgang, ohne
    unterscheiden zu koennen, ob es zwei waren.

    Kapselt alles ab: der Lauf ist fertig und committet. Ein Fehler beim Versand
    darf ihn nicht nachtraeglich in einen Fehlschlag verwandeln.
    """
    if not isinstance(zustand.get(rahmen), dict):
        return
    if zustand.get(marke):
        return
    try:
        zustand[marke] = True
        ai_run_service.zustand_schreiben(run, zustand)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Berichtsmarke %s nicht gesetzt run_id=%s", marke, run.id)
        return
    try:
        versenden(db, run=run, zustand=zustand)
    except Exception:
        logger.warning("Bericht %s nicht versendet run_id=%s", rahmen, run.id)


def _werkzeug_signatur(name: str, argumente: dict) -> str:
    return name + "|" + json.dumps(argumente, ensure_ascii=True, sort_keys=True)


async def _segment_anlaufen(
    run_id: str, client: httpx.AsyncClient | None
) -> _Anlauf | None:
    """Vorbereitung, Client und Rahmen eines Segments — oder ``None``.

    ``None`` heisst: hier ist bereits alles geschehen, was geschehen
    musste. Die Fehlerpfade schliessen den Lauf selbst ab (Ereignis und
    Endzustand), und der stille Fall — nichts zu tun — braucht beides
    nicht. Der Orchestrator kehrt dann kommentarlos zurueck.
    """
    # Die Vorbereitung stand ausserhalb jeder Absicherung. Fiel dort etwas um,
    # verliess die Ausnahme diese Koroutine, die asyncio-Aufgabe starb still,
    # und der Lauf blieb auf `running` stehen — ohne Ereignis, ohne Abschluss,
    # ohne Aufraeumen. Ein Lauf, der nie endet, ist schlimmer als einer, der
    # scheitert: der Benutzer sieht einen tippenden Assistenten und wartet.
    #
    # Die bekannten Fehler behandelt `_segment_vorbereiten` selbst und gibt sie
    # als Tupel zurueck. Dieser Block ist fuer das Uebrige da.
    #
    # `to_thread` und nicht direkt: die Vorbereitung ist keine kurze
    # Datenbanktransaktion, wie hier lange stand. Sie öffnet eine Sitzung, macht
    # rund acht Rundreisen und holt sich mittendrin über `resolve_api_key` den
    # Schlüssel beim DIS-Sidecar — und das ist ein **synchrones** `httpx.post`
    # mit 15 Sekunden Frist. Direkt aufgerufen stand der ganze Panelprozess
    # solange still, denn diese Koroutine liegt als Aufgabe auf der Hauptschleife.
    # Derselbe Fehler war für den Laufbeginn schon einmal behoben worden
    # (`lauf_beginnen_nebenher`); das Segment war dabei übersehen worden.
    #
    # Nichts Schleifengebundenes überquert dabei die Threadgrenze: im Rumpf von
    # `_segment_vorbereiten` steht kein einziger `ai_run_broker`-Aufruf, die
    # Sitzung wird dort geöffnet und geschlossen, und der einzige ORM-Wert ist
    # durch `db.refresh` / `db.expunge` abgelöst.
    try:
        vorbereitung, fehler = await asyncio.to_thread(ai_stream._segment_vorbereiten, run_id)
    except Exception as exc:
        logger.exception("AI-Segment-Vorbereitung abgebrochen run_id=%s", run_id)
        ai_run_broker.veroeffentlichen(
            run_id, "error",
            {"code": "AI_PREPARATION_FAILED", "message_key": "ai.chat.errors.unavailable"},
        )
        ai_stream._lauf_abschliessen(
            run_id, status="failed", stop_reason=f"AI_PREPARATION_FAILED:{type(exc).__name__}"
        )
        return
    if vorbereitung is None:
        if fehler is not None:
            code, message_key = fehler
            ai_run_broker.veroeffentlichen(
                run_id, "error", {"code": code, "message_key": message_key}
            )
            ai_stream._lauf_abschliessen(run_id, status="failed", stop_reason=code)
        return

    # Der Client kommt normalerweise aus dem Prozess (beim Start gesetzt). Als
    # Parameter ist er ueberreichbar, damit ein Test das Segment ausfuehren kann,
    # ohne eine ganze Anwendung hochzufahren.
    client = client or ai_run_service.http_client()
    if client is None:
        logger.error("Kein AI-HTTP-Client, Lauf kann nicht arbeiten run_id=%s", run_id)
        ai_run_broker.veroeffentlichen(
            run_id, "error", {"code": "AI_RUNTIME_UNAVAILABLE", "message_key": "ai.chat.errors.unavailable"}
        )
        ai_stream._lauf_abschliessen(run_id, status="failed", stop_reason="AI_RUNTIME_UNAVAILABLE")
        return

    zustand = vorbereitung.zustand
    provider_messages: list[dict] = zustand["provider_messages"]
    conversation_id = vorbereitung.conversation_id
    user_id = vorbereitung.user_id
    message_id = vorbereitung.message_id
    # Aus dem Zustand geholt und nicht uebergeben: jede Fortsetzung dieses Laufs
    # — auch die nach einer Bestaetigung Stunden spaeter — arbeitet unter
    # denselben Verschaerfungen wie der erste Zug.
    try:
        # Der Zustand selbst war nicht zu lesen. Dann steht in ihm auch kein
        # Rahmen mehr, den die beiden Funktionen darunter bemaengeln koennten —
        # sie saehen einen sauberen Chatlauf und schwiegen. Die Marke aus
        # `zustand_lesen` ist der einzige Hinweis darauf, dass hier etwas
        # verlorengegangen ist, und sie fuehrt in denselben Ausstieg wie ein
        # kaputter Rahmen.
        if zustand.get("unlesbar"):
            raise GuardianRahmenUnlesbar("Laufzustand unlesbar")
        guardian = guardian_aus_zustand(zustand)
        aufgabe = aufgabe_aus_zustand(zustand)
    except GuardianRahmenUnlesbar:
        # Ein Lauf, der eine Heilung sein sollte, aber nicht mehr sagen kann,
        # welchen Server er heilt, faehrt nicht weiter. Ohne diesen Ausstieg
        # liefe er als gewoehnlicher Chatlauf weiter — mit dem vollen
        # Werkzeugsatz, ohne Serverbindung, ohne Backup-Pflicht und ohne
        # jemanden, der mitliest. Fuer einen Aufgabenlauf gilt dasselbe: ohne
        # Rahmen faellt die Werkzeugeinengung weg, und `ask_user` wuerde ihn
        # parken, bis er ablaeuft.
        logger.error("Laufrahmen unlesbar, Lauf wird beendet run_id=%s", run_id)
        # Der Grund heißt nicht "guardian_...": hier scheitern beide Rahmen,
        # und ein Aufgabenlauf, der unter dem Namen des Wächters abbricht,
        # schickt den Nachlesenden in die falschen Vorfälle.
        ai_stream._lauf_abschliessen(run_id, status="failed", stop_reason="laufrahmen_unlesbar")
        return

    # Die Rolle und der Worker-Rahmen — aus dem Zustand, wie die beiden
    # Rahmen darueber, und aus demselben Grund: was mitten in einer Aufgabe
    # gilt, kommt aus derselben Quelle wie am Anfang.
    rolle = rolle_aus_zustand(zustand)
    herkunft = herkunft_aus_zustand(zustand)
    worker = worker_aus_zustand(zustand)

    # **Der gemeinsame Nenner beider Rahmen: es sitzt niemand davor.**
    #
    # Drei Stellen im Segment haengen daran, und alle drei galten bisher nur
    # fuer den Guardian: eine Rueckfrage wird abgewiesen, ein bestaetigungs-
    # pflichtiger Vorschlag wird zurueckgenommen statt geparkt, und der Lauf
    # endet, statt auf einen Klick zu warten, den niemand tut. Sie fuer die
    # Aufgabe zu kopieren waere dreimal dieselbe Ueberlegung an drei Orten
    # gewesen — und der erste, den jemand spaeter uebersieht, haengt einen
    # Aufgabenlauf dauerhaft auf 'waiting_user'. Weil `aktiver_lauf` wartende
    # Laeufe mitzaehlt, blockierte er von da an jede weitere Aufgabe dieses
    # Benutzers.
    #
    # Ein Worker zaehlt hier mit — mit zwei Ausnahmen. Seine Rueckfrage laeuft
    # nicht ueber `ask_user`, sondern ueber `worker_frage` und die
    # Meldestelle, und genau dieser Weg wird in `_fragen_behandeln` eigens
    # freigehalten. Und seit dem 22.08.2026 **parkt** ein Worker auf einen
    # bestaetigungspflichtigen Vorschlag, statt den E-Mail-Freigabeweg zu
    # nehmen: unbeaufsichtigt heisst bei ihm "niemand liest mit", nicht
    # "niemand ist da" — jemand hat ihn gerade beauftragt. Die Unterscheidung
    # heisst `niemand_da` und steht in `_schreibrunde_ausfuehren`.
    unbeaufsichtigt = guardian is not None or aufgabe is not None or rolle == "worker"

    return _Anlauf(
        vorbereitung=vorbereitung,
        client=client,
        zustand=zustand,
        provider_messages=provider_messages,
        conversation_id=conversation_id,
        user_id=user_id,
        message_id=message_id,
        guardian=guardian,
        aufgabe=aufgabe,
        rolle=rolle,
        herkunft=herkunft,
        worker=worker,
        unbeaufsichtigt=unbeaufsichtigt,
    )


async def _werkzeuge_und_grenze(
    *,
    client: httpx.AsyncClient,
    vorbereitung: "_Vorbereitung",
    guardian: "GuardianKontext | None",
    aufgabe: "AufgabenKontext | None",
    rolle: str = "voll",
    herkunft: str = "panel",
    zustand: dict,
) -> tuple[list, bool, int, bool, str | None]:
    """Schneidet den Werkzeugkatalog zu und rechnet das Rundenbudget aus.

    Rueckgabe ``(tools, cache_marke, kontextgrenze, denken, denkstufe)`` —
    alles, was die Rundenschleife vom Katalog wissen muss. Einmal je Segment
    und nicht je Runde: nach dem Zuschnitt aendert sich nichts davon mehr, und
    auch die am Modell geklemmte Denkstufe (`_denken_am_modell`) gilt fuer das
    ganze Segment.
    """
    tools = provider_tool_definitions()
    # **Angeboten wird nur, was auch ausgefuehrt wuerde.**
    #
    # Die Schranke ist das nicht — die steht in `_tool_followup_messages`
    # und `create_proposal` und bleibt dort, weil ein Katalog eine Bitte ist
    # und keine Zusage. Das hier ist Fuehrung, und sie hat einen messbaren
    # Preis: ohne sie bekam ein Lauf ohne Zuschauer `ask_user` angeboten,
    # obwohl niemand antwortet. Das Modell ruft es dann auch auf — der
    # Prompt sagt ihm ja, bei Unklarheit zu fragen —, bekommt eine
    # Ablehnung, und die Runde ist verbraucht. Bei einem stehenden Auftrag
    # passiert das jede Nacht aufs Neue.
    #
    # Dasselbe gilt seit der Rechtepruefung fuer den ganzen Katalog: wer
    # kein Hoster-Recht hat, dessen KI kann die Hoster-Werkzeuge ohnehin
    # nicht ausfuehren — angeboten bekam er sie trotzdem, alle 51.
    #
    # **Geschnitten, nicht ersetzt.** `GUARDIAN_HEILUNG_TOOLS` und
    # `aufgaben_tools` sind bewusst ausgeschriebene Aufzaehlungen: was dort
    # nicht steht, soll auch dann nicht in einen unbeaufsichtigten Lauf
    # geraten, wenn der Benutzer das Recht dazu haette. Und umgekehrt darf
    # ein Eintrag dort kein Recht ersetzen, das dem Benutzer fehlt.
    erlaubt = vorbereitung.angebotene_werkzeuge
    # Der Rollenschnitt zuerst (docs/agentic-framework.md, §3): das Gehirn
    # behaelt nur Gedaechtnis und Worker-Steuerung, ein Worker verliert genau
    # diese beiden Gruppen plus `ask_user`, und der heutige Voll-Betrieb
    # verliert alles, was zum Hintergrund-Betrieb gehoert — ohne
    # `worker_model` am Zugang gibt es kein Gehirn und damit auch keine
    # Auftraege. Geschnitten, nicht ersetzt: die Rechtepruefung darueber
    # bleibt die Autoritaet, keine Menge hier ersetzt ein fehlendes Recht.
    if rolle == "gehirn":
        erlaubt = erlaubt & GEHIRN_TOOLS
    elif rolle == "worker":
        erlaubt = erlaubt - worker_ausschluss()
    else:
        erlaubt = erlaubt - (WORKER_STEUERUNG | NUR_WORKER)
    # Danach die Herkunft: aus dem Panel kein Zugriff auf den Rechner des
    # Benutzers (siehe `herkunft_schnitt`; die Gegenrichtung schneidet nichts,
    # die App bekommt alles). Der Schnitt steht **vor** Guardian und Aufgaben,
    # damit deren Aufzaehlungen ihn nicht wieder oeffnen koennen.
    erlaubt = herkunft_schnitt(frozenset(erlaubt), herkunft)
    if guardian is not None:
        erlaubt = erlaubt & GUARDIAN_HEILUNG_TOOLS
    elif aufgabe is not None:
        erlaubt = erlaubt & aufgaben_tools(aufgabe.kind)
    tools = [
        eintrag for eintrag in tools
        if str(eintrag.get("function", {}).get("name")) in erlaubt
    ]
    # Was der Katalog selbst kostet. Er geht in Zeile `tools=tools` neben den
    # Nachrichten über dieselbe Leitung, wurde aber in keinem Budget
    # mitgezählt: `message_character_count` summiert nur `content`. Bei 51
    # Werkzeugen sind das rund 45.000 Zeichen — mehr als das gesamte
    # Nachrichtenbudget eines 32k-Modells. Der Lauf lief damit nicht in einen
    # knapperen Kontext, sondern in eine Absage des Anbieters.
    #
    # Der Wert wird hier einmal genommen und nicht in der Schleife: nach dem
    # Zuschnitt ändert sich `tools` nicht mehr — auch die Schlussrunde
    # schickt den Katalog mit und verbietet nur noch seine Benutzung.
    katalog_zeichen = len(json.dumps(tools, ensure_ascii=False))
    # Zwischenspeichern des Prompts, sofern dieses Modell es ausdruecklich
    # verlangt. Einmal je Segment ermittelt und nicht je Runde: der Katalog
    # antwortet zwar aus seinem eigenen Speicher, aber die Antwort kann sich
    # innerhalb eines Laufs ohnehin nicht aendern.
    #
    # Warum das hier steht und nicht in `_Vorbereitung`: die Frage ist
    # asynchron und braucht den HTTP-Client dieses Laufs; die Vorbereitung
    # ist synchron und läuft in einem Thread. (Hier stand einmal, die
    # Vorbereitung sei „eine kurze Datenbanktransaktion“ — sie geht über
    # `resolve_api_key` selbst über das Netz, und genau deshalb liegt sie
    # inzwischen in `to_thread`.) Warum es nicht am Lauf hängt wie
    # `reasoning_effort`: es ist keine Wahl des Benutzers, die eine
    # Fortsetzung stabil halten muesste, sondern eine Eigenschaft des
    # Modells.
    #
    # Kennt der Katalog das Modell nicht — nicht erreichbar, oder ein Name,
    # den es nicht mehr gibt — geht keine Marke mit. Der Lauf kostet dann,
    # was er vorher auch gekostet hat.
    #
    # Der Schlüssel geht mit, weil er hier ohnehin schon entschlüsselt
    # danebenliegt. Ein schlüsselpflichtiger Katalog käme sonst zwar auch an
    # seinen (`ai_model_catalog.schluesselquelle_setzen`), aber über einen
    # zweiten Gang zum DIS-Sidecar für dasselbe Geheimnis.
    modell = await ai_model_catalog.finde(
        client,
        vorbereitung.provider.provider_kind,
        # Das Modell der Rolle: ein Worker-Segment fragt den Katalog nach dem
        # Arbeitsmodell — Cache-Marke und Denkstufen-Klemme gehoeren zu dem
        # Modell, das gleich wirklich antwortet.
        _modell_fuer(vorbereitung.provider, rolle),
        schluessel=vorbereitung.api_key,
    )
    cache_marke = modell is not None and modell.cache_marke_noetig
    # Ob dieses Modell Bilder lesen kann. Aus demselben Katalog und aus
    # demselben Grund wie die Cache-Marke — eine Eigenschaft des Modells, das
    # gleich antwortet. `None` (Katalog nicht erreichbar, Modell unbekannt)
    # heisst „unbekannt" und niemals „blind": siehe `_sieht_nicht`.
    zustand["sieht"] = modell.sieht if modell is not None else None
    # Die eingefrorene Denkstufe gilt weiter — aber nur, solange sie zu dem
    # Modell passt, das **jetzt** am Zugang steht.
    denken, denkstufe = _denken_am_modell(vorbereitung, modell)
    # Das Budget dieses Laufs. `build_provider_messages` hat es beim Start
    # eingehalten, aber danach waechst die Liste weiter: jede Werkzeugrunde
    # haengt einen Assistentenzug und dessen Ergebnisse an, und ein
    # gelesener Log bringt bis zu 24.000 Zeichen mit. Ohne die Kuerzung vor
    # jedem Ruf laeuft ein langer Lauf mitten in der Arbeit ueber das
    # Fenster — und das ist kein knapperer Kontext, sondern eine Absage.
    #
    # Abzüglich des Werkzeugkatalogs: er fährt in derselben Anfrage mit und
    # gehört deshalb in dieselbe Rechnung. `MIN_HISTORY_CHARS` als Boden,
    # damit ein sehr kleines Fenster nicht in eine negative Grenze fällt —
    # dort passt der Katalog allein schon nicht, und ein leerer Kontext wäre
    # nicht besser als ein knapper.
    kontextgrenze = max(
        teilbudgets(zustand.get("context_chars")).gesamt - katalog_zeichen,
        MIN_HISTORY_CHARS,
    )
    return tools, cache_marke, kontextgrenze, denken, denkstufe


def _runde_filtern(
    *,
    kinds: set[str],
    current_usage: StreamUsage,
    signaturen: dict[str, int],
    zustand: dict,
    run_id: str,
    rundendeckel: int = MAX_TOOL_ROUNDS,
) -> tuple[list, str | None]:
    """Mischrunden-Absage, Schleifenerkennung und der Rundenzaehler.

    Filtert `current_usage.tool_calls` **in place** (dasselbe Objekt wie im
    Orchestrator) und schreibt `signaturen` und `zustand[\"rounds\"]` fort.
    Die Reihenfolge ist tragend: Mischrunden-Absage vor Schleifenerkennung
    vor Rundenzaehlung vor der Leer-Pruefung.

    Rueckgabe `(deferred_calls, signal)`: ``"budget"`` — die Runden sind
    erschoepft, es folgt die letzte Antwort ohne Werkzeuge; ``"fertig"`` —
    nichts mehr zu tun, die Schleife endet; ``None`` — die Lesephase folgt.
    """
    deferred_calls: list = []
    # Der Blick eines Modells, das keine Bilder liest. Steht vor allem
    # anderen, weil ein Aufruf, der ohnehin nicht laufen kann, weder gezaehlt
    # noch als Schleife gewertet werden soll.
    blind = [call for call in current_usage.tool_calls if _sieht_nicht(zustand, call)]
    if blind:
        deferred_calls.extend((call, KEIN_BLICK_GRUND) for call in blind)
        current_usage.tool_calls = [
            call for call in current_usage.tool_calls if call not in blind
        ]

    if kinds == {"read", "write"}:
        # Gemischte Runde: die Lesewerkzeuge laufen, die Schreibaufrufe
        # bekommen eine Absage mit Begruendung und werden nachgeholt.
        #
        # `extend` und nicht `=`: hier stand eine Zuweisung, und die warf die
        # Absagen des blinden Blicks darueber weg. Ein Modell ohne Bildsicht,
        # das in einer Runde hinsieht, liest und schreibt, verlor damit den
        # Blick spurlos — kein Ergebnis, keine Begruendung, kein Hinweis.
        deferred_calls.extend(
            (call, (
                "Schreibaktionen laufen in einer eigenen Runde. Lies "
                "erst zu Ende und rufe die Aktion danach allein auf."
            ))
            for call in current_usage.tool_calls if call.name in WRITE_TOOLS
        )
        current_usage.tool_calls = [
            call for call in current_usage.tool_calls if call.name in READ_TOOLS
        ]

    # Schleifenerkennung — **ueber Runden hinweg**, nicht innerhalb einer.
    #
    # Das ist der ganze Unterschied: neun Statusabfragen nebeneinander
    # sind eine gruendliche Bestandsaufnahme ("laufen alle Server?"),
    # dieselbe Abfrage in der vierten Runde hintereinander ist ein
    # haengendes Modell. Gezaehlt wird deshalb je Runde einmal je
    # Signatur, und geprueft wird gegen die Runden davor.
    wiederholt: list = []
    frisch: list = []
    for call in current_usage.tool_calls:
        gezaehlt = signaturen.get(_werkzeug_signatur(call.name, call.arguments), 0)
        limit = MAX_GLEICHE_POLLING_AUFRUFE if call.name in POLLING_WERKZEUGE else MAX_GLEICHE_AUFRUFE
        if gezaehlt >= limit:
            wiederholt.append((call, (
                "Dieser Aufruf lief mit genau diesen Argumenten bereits in "
                f"{gezaehlt} Runden und liefert nichts Neues. Arbeite mit dem, "
                "was du hast, oder frage den Benutzer."
            )))
            continue
        frisch.append(call)
    for signatur in {
        _werkzeug_signatur(call.name, call.arguments) for call in frisch
    }:
        signaturen[signatur] = signaturen.get(signatur, 0) + 1
    current_usage.tool_calls = frisch
    deferred_calls.extend(wiederholt)

    if _runde_zaehlen(zustand, rundendeckel):
        # Ein Assistent, der abbricht *weil* er gruendlich war, ist
        # schlechter als einer, der mit dem Vorhandenen antwortet. Ab
        # hier gibt es keine Werkzeuge mehr, aber eine Antwort.
        logger.info(
            "AI-Werkzeugrunden erschoepft, letzte Antwort ohne Werkzeuge run_id=%s",
            run_id,
        )
        return deferred_calls, "budget"
    if not current_usage.tool_calls and not deferred_calls:
        return deferred_calls, "fertig"
    return deferred_calls, None

