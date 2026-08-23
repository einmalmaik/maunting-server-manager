"""Auftraege an den Rechner des Benutzers — anlegen, abholen, melden.

Die Richtung ist umgedreht: das Panel legt einen Auftrag hin, die Desktop-App
holt ihn ab. Diese Datei ist die ganze Vermittlung dazwischen. Was auf dem
Rechner passiert, steht hier nicht — das entscheidet die App (Rust), und nur
sie. Das Panel kennt Namen und Argumente, nicht den Rechner. Es kennt aber,
**welchem** Geraet ein Auftrag gehoert (``device_family``): eine Kennung, kein
Wissen ueber den Rechner — und ohne sie landet ein Blick auf den Bildschirm
beim falschen von mehreren gekoppelten Geraeten.

Zwei Fristen halten das Ganze ehrlich:

* ``FRIST_SEKUNDEN`` — solange darf ein Auftrag offen sein. Danach ist er
  verfallen, und der wartende Lauf erfaehrt das als Ergebnis. Ein Rechner, der
  ausgeht, laesst so keinen Lauf haengen.
* ``ABHOLFRIST_SEKUNDEN`` — solange gilt ein abgeholter Auftrag als in Arbeit.
  Stuerzt die App danach ab, faellt er zurueck in die Warteschlange, statt bis
  zum Verfall als "wird gerade gemacht" zu gelten. Auftraege, die auf einen
  Menschen warten, sind davon ausgenommen (siehe `_lange_frist`).

Und eine Aufbewahrungsregel: ``AUFBEWAHRUNG_STUNDEN``. Ein abgeschlossener
Auftrag ist verbraucht — sein Ergebnis steht im Verlauf des Laufs. Die Zeile
traegt aber Bildschirmfotos und Dateiinhalte vom Arbeitsplatz des Benutzers;
die bleiben nicht liegen, nur weil niemand aufraeumt.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.orm import Session

from models import DesktopJob
from models.desktop_job import ABGESCHLOSSEN
from services.dis_client import DisClient

logger = logging.getLogger(__name__)

# Drei Minuten. Lang genug fuer eine Dateiaktion samt Nachfrage im Overlay,
# kurz genug, dass niemand vor einem stehengebliebenen Chat sitzt. Die
# Uebernahme-Bestaetigung hat ihre eigene, laengere Frist (der Mensch soll
# lesen duerfen) — siehe FRIST_BESTAETIGUNG_SEKUNDEN.
FRIST_SEKUNDEN = 180
FRIST_BESTAETIGUNG_SEKUNDEN = 600
ABHOLFRIST_SEKUNDEN = 90

# Wie lange ein **abgeschlossener** Auftrag noch als Zeile stehenbleibt. Er
# wird genau einmal gelesen (`ergebnisse`, beim Wecken des Laufs) und ist
# danach nur noch Bestand: ein Bildschirmfoto des Arbeitsplatzes (bis zu einer
# Million Zeichen) oder der Inhalt einer gelesenen Datei. Verschluesselt zwar,
# aber ein Datenbank-Backup traegt beides mit — "Datenminimierung vor Komfort".
# Ein Tag ist grosszuegig gegen die laengste Auftragsfrist (600 s) und laesst
# jedem Fehlerbild Zeit, sich zu zeigen.
AUFBEWAHRUNG_STUNDEN = 24


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
    if tool_name != "desktop_steuern" or arguments.get("aktion") != "freigabe":
        return False
    # Im autonomen Modus antwortet der Rechner sofort und zeigt gar keine
    # Karte (`auftrag::steuern`). Hier wartet dann niemand, und die lange
    # Frist waere nur eine lange Wartezeit fuer den Fall, dass der Rechner
    # aus ist.
    return not arguments.get("autonom")


def _lange_frist(job: DesktopJob) -> bool:
    """Wartet dieser Auftrag auf einen Menschen — traegt also die lange Frist?

    Am Auftrag selbst ablesbar und ohne Entschluesselung: `anlegen` gibt genau
    den Auftraegen mehr als ``FRIST_SEKUNDEN``, bei denen
    `_wartet_auf_menschen` zustimmt. Die Argumente noch einmal aufzuschliessen,
    nur um dieselbe Frage zu beantworten, waere Arbeit ohne Erkenntnis.
    """
    return (job.expires_at - job.created_at).total_seconds() > FRIST_SEKUNDEN


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
    familie: str | None = None,
) -> DesktopJob:
    """Legt einen Auftrag hin. Der Aufrufer parkt danach den Lauf.

    ``familie`` ist das Geraet, an das der Auftrag gehoert — die Refresh-
    Familie der Sitzung, aus der der Lauf kam. ``None`` heisst "nicht
    bekannt": der Auftrag ist dann von jedem Geraet dieses Benutzers abholbar,
    genau wie der ganze Bestand vor dieser Spalte (siehe
    `models/desktop_job.py`).
    """
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
        device_family=familie,
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


def naechster(
    db: Session, *, user_id: int, familie: str | None = None
) -> DesktopJob | None:
    """Der aelteste offene Auftrag **dieses Geraets** — und markiert ihn als geholt.

    Vorher werden faellige Auftraege aufgeraeumt, damit ein verfallener nie als
    naechster ausgeliefert wird und ein haengengebliebener zurueckfaellt.

    ``familie`` ist der fragende Rechner (`dependencies.session_familie`). Der
    Filter darauf ist der Unterschied zwischen "der Rechner, an dem der Mensch
    sitzt" und "der Rechner, der zuerst gefragt hat": ein Benutzer darf mehrere
    Geraete koppeln, alle fragen im Sekundentakt, und ohne ihn ging ein Blick
    auf den Bildschirm oder eine Uebernahme regelmaessig an den falschen.

    Zwei Nullwerte, zwei Bedeutungen — und beide heissen "kein Schnitt":

    * ``job.device_family is None`` — der Auftrag nennt kein Geraet. Bestand
      aus der Zeit vor der Spalte und Laeufe, deren Einstieg die Familie nicht
      mitgibt. Er bleibt fuer alle abholbar; sonst haetten beim Deploy alle
      wartenden Auftraege bis zu ihrer Frist gehangen.
    * ``familie is None`` — der Fragende nennt kein Geraet, etwa mit einem
      Access-Token von vor dem Anspruch. Auch er bekommt alles; ein Token
      laeuft in Minuten ab, eine haengende Sitzung waere das schlechtere
      Ergebnis.

    Die Zeile wird beim Lesen **gesperrt**, aus demselben Grund wie der
    Kopplungscode in `device_pairing_service.einloesen`: zwischen dem Lesen und
    dem ``taken`` liegt sonst ein Fenster, in dem eine zweite Anfrage dieselbe
    Zeile sieht und denselben Auftrag ein zweites Mal ausliefert. Die App fragt
    im Sekundentakt, und mehrere Arbeitsprozesse sind ausdruecklich vorgesehen
    — ein doppelt ausgelieferter Auftrag hiesse, dass der Rechner ihn zweimal
    ausfuehrt. ``skip_locked``, damit ein Nebenbuhler die naechste Zeile
    bekommt statt in der Schlange zu warten. Unter SQLite faellt die Klausel
    weg (der Dialekt kennt sie nicht); dort serialisiert die Datenbank ohnehin
    jeden Schreibzugriff — deshalb ist der Fehler in den Tests nie aufgefallen.
    """
    _aufraeumen(db, user_id=user_id)
    abfrage = db.query(DesktopJob).filter(
        DesktopJob.user_id == user_id, DesktopJob.status == "pending"
    )
    if familie is not None:
        abfrage = abfrage.filter(
            or_(
                DesktopJob.device_family.is_(None),
                DesktopJob.device_family == familie,
            )
        )
    job = (
        abfrage.order_by(DesktopJob.created_at)
        .with_for_update(skip_locked=True)
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
        # Ein Auftrag, der auf einen Menschen wartet, haengt nicht — er wartet.
        # Bis zum 23.08.2026 galt die Abholfrist auch fuer ihn: die Karte mit
        # zwanzig Loeschpfaden sprang alle 90 Sekunden zurueck, weil `naechster`
        # denselben Auftrag erneut auslieferte und Rust den wartenden Plan durch
        # einen zweiten ersetzte. Bestaetigte der Mensch im falschen Moment,
        # ging die Antwort an einen Auftrag, den es so nicht mehr gab. Diese
        # Auftraege regelt ihr eigenes `expires_at` (600 s); die Erholung nach
        # einem Absturz bleibt fuer alle anderen erhalten.
        if _lange_frist(job):
            continue
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
    deren Auftraege laengst fertig sind, die aber trotzdem schlafen. Dritter:
    `_alte_loeschen` raeumt den Bestand weg.
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
    if faellige:
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
        logger.info(
            "Desktop-Auftraege verfallen anzahl=%d laeufe=%d", len(faellige), len(laeufe)
        )
    _alte_loeschen(db)
    return len(faellige) + nachgeholt


def _alte_loeschen(db: Session) -> None:
    """Abgeschlossene Auftraege nach ``AUFBEWAHRUNG_STUNDEN`` loeschen.

    Nichts im Backend hat `desktop_jobs` bisher je geloescht — die Zustaende
    wurden nur weitergedreht. Damit sammelten sich vollstaendige
    Bildschirmfotos des Arbeitsplatzes und gelesene Dateiinhalte dauerhaft in
    der Panel-Datenbank, obwohl der Lauf sie nach einer einzigen Runde
    verbraucht hat.

    Die ganze Zeile und nicht nur das Ergebnis: auch `payload_encrypted` traegt
    Pfade und Text vom Rechner des Benutzers. Und hier im Takt und nicht beim
    Lesen in `ergebnisse` — das ist eine Lesefunktion ohne eigenen Commit, und
    ein Lauf, der zwischen Lesen und Weckmeldung scheitert, haette sein
    Ergebnis sonst unwiederbringlich verloren.

    Ein Auftrag, dessen Lauf laenger als einen Tag schlaeft, wird beim Wecken
    als ``DESKTOP_JOB_NOT_FOUND`` gemeldet. Das ist der richtige Ausgang: seine
    Frist war 600 Sekunden, und ein Ergebnis von gestern taugt nicht mehr fuer
    eine Antwort von heute.
    """
    geloescht = (
        db.query(DesktopJob)
        .filter(
            DesktopJob.status.in_(ABGESCHLOSSEN),
            DesktopJob.finished_at <= _jetzt() - timedelta(hours=AUFBEWAHRUNG_STUNDEN),
        )
        .delete(synchronize_session=False)
    )
    if geloescht:
        db.commit()
        logger.info("Alte Desktop-Auftraege geloescht anzahl=%d", geloescht)


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
