"""Worker: die Auftraege des Gehirns als eigene Hintergrundlaeufe.

docs/agentic-framework.md (Abschnitt 3): Das Gehirn fuehrt nichts aus — es
deklariert. `worker_start` legt je Auftrag ein eigenes Fenster
(``kind='worker'``) samt Lauf an und stoesst ihn an; der Worker arbeitet dann
unter dem vollen Vorschlagsfluss mit den Rechten und aus dem Kontingent des
Benutzers. `worker_cancel` faengt ihn wieder ein.

Beide laufen als ``delegation``-Werkzeuge im Lesepfad des Werkzeug-Dispatches:
eigene Session, Commit danach, 60-Sekunden-Grenze. Deshalb die wichtigste
Regel dieses Moduls: **nie auf den Worker warten.** Anlegen, anstossen,
zurueckkehren — sonst frisst ein einziger Auftrag die Zeitgrenze des
Gehirn-Zugs, und genau die Stille, die v3 abschafft, waere wieder da.

Die Worker-ID ist die Unterhaltungs-ID. Sie bleibt stabil, wenn ein Neustart
den Lauf neu saet (der Lauf wechselt, das Fenster nicht) — und sie ist es,
worueber Rueckfragen und Ergebnisse spaeter geroutet werden.

Der Anbieter wird nicht gewaehlt, sondern geerbt: `anbieter_ohne_auswahl`
liefert den zuletzt benutzten Zugang des Benutzers — das ist der Zugang, ueber
den das Gehirn gerade spricht, denn dessen Lauf ist der juengste. Welches
Modell darauf arbeitet (``worker_model`` oder der Ein-Modell-Fallback),
entscheidet der Sendepfad je Segment, nicht diese Datei.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from sqlalchemy.orm import Session

from models import AiConversation, AiRun, User
from models.ai_run import BEENDET
from models.ai_task import KANAELE

logger = logging.getLogger(__name__)


#: Grenzen der Argumente. Der Auftragstext ist bewusst so lang wie eine
#: Aufgaben-`instruction`: er ist die einzige Wissensquelle des Workers.
MAX_AUFTRAG_CHARS = 2000
MAX_TITEL_CHARS = 120

#: Wie lange `wait_until` einen Lauf hoechstens parkt. Eine Woche deckt
#: "pruef das am Wochenende" ab; was laenger warten soll, ist kein Auftrag
#: mehr, sondern ein stehender Auftrag (`propose_task_set`) — der hat einen
#: Zeitplan und ueberlebt beliebig lange.
WAIT_MIN_MINUTEN = 1
WAIT_MAX_MINUTEN = 7 * 24 * 60


def aktive_worker(db: Session, *, user_id: int) -> list[AiRun]:
    """Die nicht beendeten Worker-Laeufe eines Benutzers.

    Geparkte (`waiting_*`) zaehlen mit: ein wartender Langlaeufer ist ein
    offener Auftrag, kein freier Platz. Genau diese Zaehlung traegt den
    Betreiber-Deckel — und die Worker-Liste der Oberflaeche.
    """
    return (
        db.query(AiRun)
        .join(AiConversation, AiConversation.id == AiRun.conversation_id)
        .filter(
            AiConversation.kind == "worker",
            AiRun.user_id == user_id,
            AiRun.status.notin_(BEENDET),
        )
        .order_by(AiRun.created_at.asc())
        .all()
    )


def _text(arguments: dict, feld: str, maximum: int) -> str | None:
    """Nachsichtig lesen: Rand-Leerzeichen weg, leer heisst „nicht angegeben"."""
    wert = arguments.get(feld)
    if wert is None:
        return None
    text = str(wert).strip()
    if not text:
        return None
    return text[:maximum]


def worker_start(db: Session, *, user: User, arguments: dict) -> dict:
    """Deklariert einen Auftrag: eigenes Fenster, eigener Lauf, sofort zurueck.

    Gibt bei allem, was kein Programmierfehler ist, ein **Ergebnis** zurueck
    statt zu werfen: „Deckel erreicht" oder „kein Anbieter" sind Antworten,
    die das Gehirn dem Menschen erklaeren soll — eine Ausnahme wuerde als
    Werkzeugfehler im Verlauf landen und nichts erklaeren (Nachsicht am
    Werkzeugrand: ein Formfehler kostet eine Runde, nie die Antwort).
    """
    from services import ai_run_service, ai_worker_limits, permission_service
    from services.ai_action_service import AiActionValidationError

    # Dieselbe Pruefung wie am Angebot — hier verbindlich. Das Angebot ist
    # eine Bitte, keine Zusage (Muster der Memory-Handler).
    if not permission_service.has_global_permission(db, user, "ai.background.use"):
        raise AiActionValidationError("Hintergrund-Worker sind nicht erlaubt")

    auftrag = _text(arguments, "auftrag", MAX_AUFTRAG_CHARS)
    if not auftrag:
        raise AiActionValidationError("worker_start braucht einen Auftragstext")
    titel = _text(arguments, "titel", MAX_TITEL_CHARS) or auftrag[:MAX_TITEL_CHARS]
    kanal = _text(arguments, "kanal", 16) or "chat"
    if kanal not in KANAELE:
        raise AiActionValidationError(
            "Unbekannter Meldekanal. Zulässig sind: " + ", ".join(KANAELE)
        )

    # **Zählen und Anlegen unter einer Sperre.** Das Gehirn ruft die Werkzeuge
    # einer Welle nebenläufig auf, bis zu acht gleichzeitig und jedes in einer
    # eigenen Sitzung: zwei `worker_start` derselben Runde sähen ohne diese
    # Zeile beide denselben freien Platz und belegten ihn beide. Dieselbe
    # Zeilensperre wie in `ai_usage_service.reserve_ai_usage` — auf PostgreSQL
    # serialisiert sie den Abschnitt, auf SQLite ist sie wirkungslos, und dort
    # läuft die Welle ohnehin nacheinander.
    db.query(User.id).filter(User.id == user.id).with_for_update().one()
    deckel = ai_worker_limits.max_worker_je_benutzer()
    laufend = aktive_worker(db, user_id=user.id)
    if len(laufend) >= deckel:
        return {
            "started": False,
            "reason": "worker_limit",
            "detail": (
                f"Es laufen bereits {len(laufend)} Aufträge — mehr als {deckel} "
                "gleichzeitig sind nicht vorgesehen. Sag dem Benutzer, welche "
                "laufen, und biete an, einen abzubrechen oder zu warten."
            ),
        }

    from services import ai_chat_service, ai_provider_service

    anbieter = ai_provider_service.anbieter_ohne_auswahl(db, user)
    if anbieter is None:
        return {
            "started": False,
            "reason": "kein_anbieter",
            "detail": (
                "Es ist kein eindeutiger KI-Zugang bestimmbar. Der Auftrag "
                "kann nicht im Hintergrund laufen — erledige ihn in diesem "
                "Gespräch oder bitte den Betreiber, einen Zugang einzurichten."
            ),
        }
    if anbieter.requires_api_key and not anbieter.operator_api_key_encrypted:
        return {
            "started": False,
            "reason": "kein_schluessel",
            "detail": "Am KI-Zugang fehlt der Schlüssel des Betreibers.",
        }

    # Nur geflusht, nicht committet: ein Commit hier gäbe die Benutzersperre
    # frei, bevor der neue Lauf überhaupt existiert — genau in dem Fenster,
    # das die Sperre schließen soll. Den einen Commit setzt `lauf_beginnen`
    # am Ende, und der schließt dann Fenster und Lauf gemeinsam ab.
    fenster = ai_chat_service.worker_unterhaltung_anlegen(db, user, titel)
    db.flush()

    # Spaeter Import: `ai_stream_service` importiert `ai_action_service`, und
    # der Dispatch dort ruft dieses Modul — ein Import am Dateikopf waere der
    # naechste Zyklus.
    from services.ai_stream_service import lauf_beginnen

    # Die Denkstufe der Worker legt der Betreiber am Zugang fest; ``None``
    # heisst „nicht nachdenken" — derselbe Standard wie bei jedem
    # unbeaufsichtigten Lauf. Geklemmt wird sie je Segment gegen das dann
    # geltende Modell (nie teurer, nie unbekannt), nicht hier.
    stufe = anbieter.worker_reasoning_effort
    run, fehler = lauf_beginnen(
        db,
        user=user,
        conversation=fenster,
        provider=anbieter,
        request_id=uuid4(),
        content=auftrag,
        reasoning=bool(stufe),
        reasoning_effort=stufe,
        # Das Fenster des Worker-Modells ist hier nicht ermittelbar (der
        # Katalog ist ein async-Abruf, dieser Handler ein Thread). ``None``
        # heisst „unbekannt" und faellt auf den bewaehrten Rueckfall zurueck.
        context_chars=None,
        # Eine Vorfallsmeldung gehoert ins Gespraech mit dem Menschen, nicht
        # in einen Auftrag — sonst gaelte sie als besprochen, ohne dass je
        # jemand sie gesehen hat (dasselbe Argument wie bei den Aufgaben).
        guardian_briefing_unterdruecken=True,
        unbeaufsichtigt=True,
    )
    if run is None:
        # Das eben angelegte Fenster wieder wegraeumen: ohne Lauf ist es in
        # keiner Liste sichtbar, aber jeder gescheiterte Versuch liesse sonst
        # eine Leiche zurueck. Weil es nur geflusht ist, genügt das
        # Zurücknehmen der Transaktion — ein `db.delete` auf einem Objekt,
        # das `lauf_beginnen` in seinen Fehlerzweigen bereits weggerollt hat,
        # würde stattdessen selbst werfen.
        db.rollback()
        return {
            "started": False,
            "reason": (fehler or ("unbekannt",))[0],
            "detail": (
                "Der Lauf konnte nicht angelegt werden — meist ist das "
                "Kontingent erschoepft. Sag dem Benutzer ehrlich, dass der "
                "Auftrag gerade nicht laufen kann."
            ),
        }

    # **Der Rahmen erst jetzt — nach allem, was noch zurueckrollen kann.**
    # Ginge er bei einem Rollback verloren, liefe der Lauf als gewoehnlicher
    # Chatlauf weiter: voller Werkzeugsatz, `ask_user` erlaubt, und niemand,
    # der je antwortet (dieselbe Reihenfolge wie bei Guardian und Aufgaben).
    zustand = ai_run_service.zustand_lesen(run)
    zustand["worker"] = {
        "conversation_id": fenster.id,
        "titel": titel,
        "kanal": kanal,
    }
    ai_run_service.zustand_schreiben(run, zustand)
    db.commit()

    if not ai_run_service.anlauf(db, run):
        return {
            "started": False,
            "reason": "no_runtime",
            "detail": "Es läuft keine Anwendung, die den Auftrag tragen könnte.",
        }

    logger.info(
        "Worker gestartet (worker_id=%s, run_id=%s, kanal=%s)",
        fenster.id, run.id, kanal,
    )
    return {
        "started": True,
        "worker_id": fenster.id,
        "titel": titel,
        "kanal": kanal,
        "detail": (
            "Der Auftrag läuft jetzt im Hintergrund. Das Ergebnis kommt als "
            "Meldung, sobald es fertig ist — versprich dem Benutzer nichts "
            "über die Dauer."
        ),
    }


def worker_antwort(db: Session, *, user: User, arguments: dict) -> dict:
    """Gibt die Antwort des Benutzers an einen fragenden Auftrag zurueck.

    Mechanisch ist das die Abloesung aus dem Dauerchat, nur im Worker-Fenster:
    `lauf_beginnen` legt dort eine neue Benutzernachricht an,
    `vorgaenger_abloesen` ueberholt den auf ``waiting_user`` geparkten Lauf und
    vererbt ihm die Schleifensignaturen — genau so, wie eine getippte Antwort
    im Dauerchat eine Rueckfrage beantwortet. Der ueberholte Lauf endet als
    ``superseded`` und meldet nichts (die Meldestelle uebergeht diesen Grund).

    Kein Deckel: die Antwort setzt einen bestehenden Auftrag fort, sie
    beginnt keinen neuen.
    """
    from services import ai_run_service, permission_service
    from services.ai_action_service import AiActionValidationError

    if not permission_service.has_global_permission(db, user, "ai.background.use"):
        raise AiActionValidationError("Hintergrund-Worker sind nicht erlaubt")

    worker_id = _text(arguments, "worker_id", 36)
    if not worker_id:
        raise AiActionValidationError("worker_antwort braucht eine worker_id")
    antwort = _text(arguments, "antwort", MAX_AUFTRAG_CHARS)
    if not antwort:
        raise AiActionValidationError("worker_antwort braucht die Antwort des Benutzers")

    fenster = db.get(AiConversation, worker_id)
    if fenster is None or fenster.kind != "worker" or fenster.user_id != user.id:
        raise AiActionValidationError("Worker nicht gefunden")

    juengster = (
        db.query(AiRun)
        .filter(AiRun.conversation_id == fenster.id)
        .order_by(AiRun.created_at.desc(), AiRun.id.desc())
        .first()
    )
    if juengster is None or juengster.status in BEENDET:
        return {
            "delivered": False,
            "worker_id": fenster.id,
            "reason": "schon_beendet",
            "detail": (
                "Dieser Auftrag läuft nicht mehr — die Antwort kann ihn "
                "nicht mehr erreichen. Sag das dem Benutzer ehrlich."
            ),
        }

    # Der Auftrag bleibt auf seinem Zugang. Erst wenn der zwischenzeitlich
    # weg oder abgeschaltet ist, gilt der Erbweg von `worker_start`.
    from models import AiProvider
    from services import ai_provider_service

    anbieter = (
        db.get(AiProvider, juengster.provider_id) if juengster.provider_id else None
    )
    if anbieter is None or not anbieter.enabled:
        anbieter = ai_provider_service.anbieter_ohne_auswahl(db, user)
    if anbieter is None:
        return {
            "delivered": False,
            "worker_id": fenster.id,
            "reason": "kein_anbieter",
            "detail": "Es ist kein KI-Zugang bestimmbar, der die Antwort tragen könnte.",
        }

    # Der Rahmen des Vorgaengers traegt Kanal und Titel — die Antwort aendert
    # daran nichts.
    alter_rahmen = ai_run_service.zustand_lesen(juengster).get("worker")
    if not isinstance(alter_rahmen, dict):
        alter_rahmen = {}

    from services.ai_stream_service import lauf_beginnen

    stufe = anbieter.worker_reasoning_effort
    run, fehler = lauf_beginnen(
        db,
        user=user,
        conversation=fenster,
        provider=anbieter,
        request_id=uuid4(),
        content=antwort,
        reasoning=bool(stufe),
        reasoning_effort=stufe,
        context_chars=None,
        guardian_briefing_unterdruecken=True,
        unbeaufsichtigt=True,
    )
    if run is None:
        return {
            "delivered": False,
            "worker_id": fenster.id,
            "reason": (fehler or ("unbekannt",))[0],
            "detail": (
                "Die Antwort konnte den Auftrag nicht erreichen — meist ist "
                "das Kontingent erschoepft. Sag dem Benutzer ehrlich Bescheid."
            ),
        }

    # Der Rahmen erst nach `lauf_beginnen` — dieselbe Reihenfolge wie bei
    # `worker_start`, und mit denselben Feldern wie beim Vorgaenger.
    zustand = ai_run_service.zustand_lesen(run)
    zustand["worker"] = {
        "conversation_id": fenster.id,
        "titel": str(alter_rahmen.get("titel") or fenster.title or "Auftrag"),
        "kanal": str(alter_rahmen.get("kanal") or "chat"),
    }
    ai_run_service.zustand_schreiben(run, zustand)
    db.commit()

    if not ai_run_service.anlauf(db, run):
        return {
            "delivered": False,
            "worker_id": fenster.id,
            "reason": "no_runtime",
            "detail": "Es läuft keine Anwendung, die den Auftrag tragen könnte.",
        }

    logger.info("Worker-Antwort zugestellt (worker_id=%s, run_id=%s)", fenster.id, run.id)
    return {
        "delivered": True,
        "worker_id": fenster.id,
        "detail": (
            "Die Antwort ist beim Auftrag angekommen; er arbeitet damit "
            "weiter und meldet sich mit dem Ergebnis."
        ),
    }


def worker_cancel(db: Session, *, user: User, arguments: dict) -> dict:
    """Faengt einen Auftrag wieder ein: alle offenen Laeufe seines Fensters enden.

    Der Abbruch ist derselbe wie beim Abloesen eines Vorgaengers: Zeile auf
    ``cancelled``, asyncio-Aufgabe anhalten, Arbeitsgedaechtnis leeren. Ein
    ``waiting_user``-Worker gilt hier **nicht** als beantwortet — der Mensch
    hat abgebrochen, nicht geantwortet.
    """
    from services import ai_run_service, permission_service
    from services.ai_action_service import AiActionValidationError

    if not permission_service.has_global_permission(db, user, "ai.background.use"):
        raise AiActionValidationError("Hintergrund-Worker sind nicht erlaubt")

    worker_id = _text(arguments, "worker_id", 36)
    if not worker_id:
        raise AiActionValidationError("worker_cancel braucht eine worker_id")

    fenster = db.get(AiConversation, worker_id)
    # Eine fremde oder falsche ID bekommt dieselbe Antwort wie eine
    # nichtexistente: wem das Fenster nicht gehoert, dem gegenueber existiert
    # es nicht.
    if fenster is None or fenster.kind != "worker" or fenster.user_id != user.id:
        raise AiActionValidationError("Worker nicht gefunden")

    offene = [
        run
        for run in db.query(AiRun)
        .filter(AiRun.conversation_id == fenster.id, AiRun.status.notin_(BEENDET))
        .all()
    ]
    if not offene:
        return {
            "cancelled": False,
            "worker_id": fenster.id,
            "reason": "schon_beendet",
            "detail": "Dieser Auftrag läuft nicht mehr.",
        }

    for run in offene:
        run.status = "cancelled"
        run.stop_reason = "worker_cancel"
        run.wake_at = None
        ai_run_service.aufgabe_abbrechen(run.id)
        ai_run_service.arbeitsspeicher_leeren(run)
    db.commit()

    logger.info("Worker abgebrochen (worker_id=%s, laeufe=%d)", fenster.id, len(offene))
    return {
        "cancelled": True,
        "worker_id": fenster.id,
        "detail": "Der Auftrag wurde angehalten.",
    }
