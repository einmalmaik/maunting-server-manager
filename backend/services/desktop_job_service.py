"""Auftraege an den Rechner des Benutzers — anlegen, abholen, melden.

Die Richtung ist umgedreht: das Panel legt einen Auftrag hin, die Desktop-App
holt ihn ab. Diese Datei ist die ganze Vermittlung dazwischen. Was auf dem
Rechner passiert, steht hier nicht — das entscheidet die App (Rust), und nur
sie. Das Panel kennt Namen und Argumente, nicht den Rechner.

Zwei Fristen halten das Ganze ehrlich:

* ``FRIST_SEKUNDEN`` — solange darf ein Auftrag offen sein. Danach ist er
  verfallen, und der wartende Lauf erfaehrt das als Ergebnis. Ein Rechner, der
  ausgeht, laesst so keinen Lauf haengen.
* ``ABHOLFRIST_SEKUNDEN`` — solange gilt ein abgeholter Auftrag als in Arbeit.
  Stuerzt die App danach ab, faellt er zurueck in die Warteschlange, statt bis
  zum Verfall als "wird gerade gemacht" zu gelten.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from uuid import uuid4

from sqlalchemy.orm import Session

from models import DesktopJob
from services.dis_client import DisClient

logger = logging.getLogger(__name__)

# Drei Minuten. Lang genug fuer eine Dateiaktion samt Nachfrage im Overlay,
# kurz genug, dass niemand vor einem stehengebliebenen Chat sitzt. Die
# Uebernahme-Bestaetigung hat ihre eigene, laengere Frist (der Mensch soll
# lesen duerfen) — siehe FRIST_BESTAETIGUNG_SEKUNDEN.
FRIST_SEKUNDEN = 180
FRIST_BESTAETIGUNG_SEKUNDEN = 600
ABHOLFRIST_SEKUNDEN = 90

def _wartet_auf_menschen(tool_name: str, arguments: dict) -> bool:
    """Kann dieser Auftrag an einer menschlichen Entscheidung haengenbleiben?

    Wenn ja, bekommt er die lange Frist: 180 Sekunden reichen nicht, um eine
    Liste von zwanzig Pfaden zu lesen und zu entscheiden.

    Frueher genuegte dafuer der Werkzeugname (`desktop_takeover_control`).
    Seit die Bitte um die Freigabe eine **Aktion** von `desktop_steuern` ist
    (23.08.2026, Katalogbudget), muessen die Argumente mitgelesen werden —
    sonst bekaeme auch jeder einzelne Klick zehn Minuten Frist, und ein
    Rechner, der zwischendurch ausgeht, liesse den Lauf entsprechend lange
    stehen.

    `desktop_aufraeumen` zeigt seine Karte nur bei ausgeschaltetem autonomem
    Modus, bekommt die lange Frist aber immer: welcher Fall eintritt, hat das
    Panel zwar schon entschieden (`_desktop_argumente` setzt `autonom`), doch
    eine grosszuegige Frist kostet im autonomen Fall nichts — geweckt wird der
    Lauf vom Ergebnis, nicht vom Fristablauf.
    """
    if tool_name == "desktop_aufraeumen":
        return True
    return tool_name == "desktop_steuern" and arguments.get("aktion") == "freigabe"


def _aad(job_id: str) -> str:
    """Bindet den Geheimtext an genau diesen Auftrag (wie bei Vorschlaegen)."""
    return f"msm:desktop_job:{job_id}"


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def anlegen(
    db: Session,
    *,
    user_id: int,
    run_id: str,
    tool_call_id: str,
    tool_name: str,
    arguments: dict,
) -> DesktopJob:
    """Legt einen Auftrag hin. Der Aufrufer parkt danach den Lauf."""
    job_id = str(uuid4())
    frist = (
        FRIST_BESTAETIGUNG_SEKUNDEN
        if _wartet_auf_menschen(tool_name, arguments)
        else FRIST_SEKUNDEN
    )
    job = DesktopJob(
        id=job_id,
        user_id=user_id,
        run_id=run_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        payload_encrypted=DisClient.encrypt(
            json.dumps(arguments, ensure_ascii=True, separators=(",", ":")),
            aad=_aad(job_id),
        ),
        status="pending",
        expires_at=_jetzt() + timedelta(seconds=frist),
    )
    db.add(job)
    db.flush()
    return job


def naechster(db: Session, *, user_id: int) -> DesktopJob | None:
    """Der aelteste offene Auftrag dieses Benutzers — und markiert ihn als geholt.

    Vorher werden faellige Auftraege aufgeraeumt, damit ein verfallener nie als
    naechster ausgeliefert wird und ein haengengebliebener zurueckfaellt.
    """
    _aufraeumen(db, user_id=user_id)
    job = (
        db.query(DesktopJob)
        .filter(DesktopJob.user_id == user_id, DesktopJob.status == "pending")
        .order_by(DesktopJob.created_at)
        .first()
    )
    if job is None:
        return None
    job.status = "taken"
    job.taken_at = _jetzt()
    db.commit()
    return job


def argumente(job: DesktopJob) -> dict:
    roh = DisClient.decrypt(job.payload_encrypted, aad=_aad(job.id))
    daten = json.loads(roh)
    return daten if isinstance(daten, dict) else {}


def ergebnis_melden(
    db: Session,
    *,
    job: DesktopJob,
    ok: bool,
    ergebnis: dict,
    error_code: str | None = None,
) -> None:
    """Nimmt das Ergebnis entgegen. Wecken macht der Aufrufer (Router)."""
    job.status = "done" if ok else "failed"
    job.error_code = None if ok else (error_code or "DESKTOP_JOB_FAILED")
    job.result_encrypted = DisClient.encrypt(
        json.dumps(ergebnis, ensure_ascii=True, separators=(",", ":")),
        aad=_aad(job.id),
    )
    job.finished_at = _jetzt()
    db.commit()


def offene(db: Session, *, run_id: str) -> int:
    """Wie viele Auftraege dieses Laufs noch unterwegs sind.

    Der Lauf wird erst geweckt, wenn das null ist — sonst saehe das Modell
    beim Aufwachen halbe Ergebnisse und wuerde raten. Dieselbe Regel wie
    ``ai_run_service.darf_fortsetzen`` fuer Vorschlaege.
    """
    return (
        db.query(DesktopJob)
        .filter(DesktopJob.run_id == run_id, DesktopJob.status.in_(("pending", "taken")))
        .count()
    )


def ergebnisse(db: Session, job_ids: list[str]) -> list[dict]:
    """Was aus den Auftraegen einer geparkten Runde geworden ist.

    Fehlt ein Auftrag oder ist er verfallen, steht das ausdruecklich da. Ein
    stilles Weglassen waere die schlimmere Antwort: das Modell haelte den
    Schritt fuer erledigt.

    **Auch ein Fehlschlag hat einen Grund, und der geht mit.** Die App legt
    ihn beim Melden ab (`{"fehler": "..."}`, siehe `ergebnis_melden`), gelesen
    wurde er bisher nie: die Bedingung verlangte `status == "done"`. Das
    Modell erfuhr damit nur *dass* etwas schiefging, nie *was* — und konnte es
    dem Benutzer nicht sagen. Der Grund steht unter `grund` und nicht unter
    `ergebnis`, damit ein Fehlschlag nie wie ein Erfolg aussieht.
    """
    ausgabe: list[dict] = []
    for job_id in job_ids:
        job = db.get(DesktopJob, job_id)
        if job is None:
            ausgabe.append({
                "tool_name": "unknown",
                "status": "failed",
                "error_code": "DESKTOP_JOB_NOT_FOUND",
            })
            continue
        eintrag: dict = {"tool_name": job.tool_name, "status": job.status}
        if job.error_code:
            eintrag["error_code"] = job.error_code
        if job.status in ("done", "failed") and job.result_encrypted:
            try:
                daten = json.loads(DisClient.decrypt(job.result_encrypted, aad=_aad(job.id)))
            except Exception:  # noqa: BLE001 — kaputtes Ergebnis ist ein Fehlschlag
                eintrag["status"] = "failed"
                eintrag["error_code"] = "DESKTOP_JOB_RESULT_UNREADABLE"
            else:
                eintrag["ergebnis" if job.status == "done" else "grund"] = daten
        elif job.status == "expired":
            eintrag["error_code"] = "DESKTOP_JOB_EXPIRED"
        ausgabe.append(eintrag)
    return ausgabe


def _aufraeumen(db: Session, *, user_id: int) -> None:
    """Verfallene Auftraege schliessen, haengende zurueck in die Schlange."""
    jetzt = _jetzt()
    geaendert = False
    faellige = (
        db.query(DesktopJob)
        .filter(
            DesktopJob.user_id == user_id,
            DesktopJob.status.in_(("pending", "taken")),
            DesktopJob.expires_at <= jetzt,
        )
        .all()
    )
    for job in faellige:
        job.status = "expired"
        job.error_code = "DESKTOP_JOB_EXPIRED"
        job.finished_at = jetzt
        geaendert = True

    haengende = (
        db.query(DesktopJob)
        .filter(
            DesktopJob.user_id == user_id,
            DesktopJob.status == "taken",
            DesktopJob.taken_at <= jetzt - timedelta(seconds=ABHOLFRIST_SEKUNDEN),
        )
        .all()
    )
    for job in haengende:
        job.status = "pending"
        job.taken_at = None
        geaendert = True

    if geaendert:
        db.commit()


def verfallene_wecken(db: Session) -> int:
    """Schliesst faellige Auftraege panelweit und weckt die wartenden Laeufe.

    Der Takt ruft das: ohne ihn haette nur ein Benutzer mit laufender App
    (der ``_aufraeumen`` ausloest) je einen Verfall bemerkt — ausgerechnet
    der Fall, in dem der Rechner *aus* ist, bliebe damit unbemerkt.

    Zweiter Handgriff derselben Runde: `_verpuffte_wecken` holt Laeufe nach,
    deren Auftraege laengst fertig sind, die aber trotzdem schlafen.
    """
    jetzt = _jetzt()
    faellige = (
        db.query(DesktopJob)
        .filter(
            DesktopJob.status.in_(("pending", "taken")),
            DesktopJob.expires_at <= jetzt,
        )
        .all()
    )
    nachgeholt = _verpuffte_wecken(db)
    if not faellige:
        return nachgeholt
    laeufe = {job.run_id for job in faellige}
    for job in faellige:
        job.status = "expired"
        job.error_code = "DESKTOP_JOB_EXPIRED"
        job.finished_at = jetzt
    db.commit()

    from services import ai_run_service

    for run_id in laeufe:
        if offene(db, run_id=run_id) == 0:
            ai_run_service.lauf_fortsetzen(db, run_id=run_id)
    logger.info("Desktop-Auftraege verfallen anzahl=%d laeufe=%d", len(faellige), len(laeufe))
    return len(faellige) + nachgeholt


def _verpuffte_wecken(db: Session) -> int:
    """Laeufe, deren Auftraege fertig sind, die aber weiterschlafen.

    Der Rechner fragt im Sekundentakt und ist oft schneller als der Lauf: der
    Auftrag ist committet (`_desktop_behandeln`), aber bis zum Parken liegt
    noch das ganze `_finalize_stream` dazwischen. Meldet die App in dieser
    Spanne ihr Ergebnis, ruft der Router `lauf_fortsetzen` — und
    `darf_fortsetzen` weist es ab, weil der Lauf noch auf 'running' steht. Der
    Weckruf ist damit weg, und geweckt haette den Lauf erst die 180-s-Frist
    seines Auftrags. Auf "wie voll ist meine C-Platte" wartete der Betreiber
    so bis zu vier Minuten auf Zahlen, die laengst dalagen.

    Nachgeholt wird es hier und nicht am Parken selbst: dort laege zwischen
    dem Park-Commit und dem Ende der asyncio-Aufgabe ein zusaetzlicher
    `await`, und genau in dem Fenster faende ein Weckruf den Segmentplatz
    belegt (`ai_run_service._platz_belegen`) — der Lauf stuende danach
    dauerhaft auf 'running'. Der Takt hat dieses Problem nicht: dort ist die
    Aufgabe des Laufs laengst beendet.

    Ein Lauf gilt als schlafend, wenn er auf `waiting_wake` mit dem Grund
    `desktop_jobs` steht. `lauf_fortsetzen` prueft danach selbst noch einmal
    alles Weitere (Rechte, Zustand) — hier wird nur ausgewaehlt.
    """
    from models import AiRun
    from services import ai_run_service

    schlafend = (
        db.query(AiRun)
        .filter(AiRun.status == "waiting_wake", AiRun.stop_reason == "desktop_jobs")
        .limit(50)
        .all()
    )
    geweckt = 0
    for run in schlafend:
        if offene(db, run_id=run.id) > 0:
            continue
        if ai_run_service.lauf_fortsetzen(db, run_id=run.id):
            geweckt += 1
            logger.info("Verpuffter Weckruf nachgeholt run_id=%s", run.id)
    return geweckt
