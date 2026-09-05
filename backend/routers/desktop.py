"""Die zwei Endpunkte, ueber die der Rechner des Benutzers mitarbeitet.

Abholen und melden — mehr braucht die Bruecke nicht. Beide Wege gehoeren
demselben Benutzer: ein Auftrag ist an `user_id` gebunden, und ein fremder
Auftrag ist schlicht nicht zu finden (404, nicht 403 — wer keinen Zugriff hat,
soll nicht erfahren, dass es ihn gibt).

Und beide gehoeren derselben **Herkunft**: nur eine Sitzung aus der App
(`geraet: "desktop"` im Token) erreicht sie. Das ist dieselbe Linie, die schon
den Werkzeugkatalog schneidet (`ai_tool_registry.herkunft_schnitt`) — aus dem
Panel erreicht nichts den Rechner des Benutzers, und diese Bruecke ist der
Rechner. Ohne den Schnitt konnte ein gewoehnlicher Panel-Tab die Nutzlast eines
Auftrags lesen (Pfade, zu tippender Text, Loeschliste) und ein erfundenes
Ergebnis melden.

Sie gehoeren ausserdem demselben **Geraet**: welcher Rechner fragt, steht als
Refresh-Familie im Token (`dependencies.session_familie`) und wird gegen
`desktop_jobs.device_family` gehalten. Wer zwei Rechner gekoppelt hat, soll
den Blick auf "meinen Bildschirm" von dem bekommen, an dem er sitzt — nicht
von dem, der zuerst nach Arbeit fragt. Ein Auftrag ohne Kennung (Bestand aus
der Zeit vor der Spalte) und ein Fragender ohne Kennung (altes Access-Token)
bleiben ungeschnitten; beides heisst "unbekannt", nie "irgendeins".

Warum Abholen und nicht Zustellen: siehe `models/desktop_job.py`. Die App
fragt im Sekundentakt; das kostet weniger als eine gehaltene Verbindung je
Rechner und kann bei mehreren Arbeitsprozessen nicht ins Leere laufen.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from database import get_db
from dependencies import (
    require_global,
    session_familie,
    session_herkunft,
    verify_csrf,
)
from models import DesktopJob, User
from schemas.desktop import DesktopJobResponse, DesktopJobResultRequest
from services import ai_run_service, desktop_job_service, device_pairing_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/desktop", tags=["desktop"])

# Ein Ergebnis, das laenger ist als das, wird als zu gross abgewiesen. Die
# Zahl ist bewusst grosszuegig (eine Quelldatei passt bequem) und trotzdem
# weit unter jedem Kontextfenster.
MAX_ERGEBNIS_ZEICHEN = 200_000

# Das Bildschirmfoto wird **getrennt** gemessen, und das hat einen Anlass:
# bis zum 23.08.2026 lief es unter derselben Grenze, ein PNG des Desktops war
# regelmaessig groesser als 200.000 Zeichen, und jedes Foto wurde abgewiesen.
# Die KI sah nie etwas und meldete "der Bildschirmzugriff ist abgelaufen".
#
# Seit demselben Tag kodiert `bildschirm.rs` als JPEG (gemessen 104.124 Zeichen
# bei 1280x720), womit es auch unter der alten Grenze bliebe. Ein voller
# Bildschirm mit Fotos und vielen Fenstern liegt aber deutlich darueber, und
# eine Grenze, die *meistens* passt, ist genau die Sorte, die erst im Betrieb
# auffaellt. Eine Million Zeichen sind rund 750 KB Bild — mehr, als bei dieser
# Kantenlaenge je entstehen kann.
#
# Getrennt und nicht einfach hochgesetzt, damit die scharfe Grenze fuer alles
# **andere** scharf bleibt: eine gelesene Datei mit 900.000 Zeichen gehoert
# weiterhin nicht in einen Prompt.
MAX_BILD_ZEICHEN = 1_000_000
#: Dasselbe Feld wie in `bildschirm.rs` und `ai_stream_service.BILDFELD`.
BILDFELD = "bild_jpeg_base64"


def _nur_aus_der_app(herkunft: str) -> None:
    """Wer nicht aus der App kommt, findet die Bruecke nicht.

    404 und nicht 403, dieselbe Linie wie beim fremden Auftrag: eine
    Panel-Sitzung soll aus der Antwort nicht lernen, dass hier gerade etwas
    wartet. Der Anspruch kommt aus dem Token und nicht aus dem Koerper — der
    Client kann seine Herkunft seit dem 21.08.2026 nicht mehr selbst erklaeren
    (`dependencies.session_herkunft`).
    """
    if herkunft != "desktop":
        raise HTTPException(status_code=404, detail="ai.errors.codes.DESKTOP_JOB_NOT_FOUND")


def _nur_sein_geraet(job: DesktopJob, familie: str | None) -> None:
    """Quittieren darf nur der Rechner, fuer den der Auftrag gedacht war.

    Der Gegenpart zum Schnitt beim Abholen (`desktop_job_service.naechster`),
    und ohne ihn haette der nur die halbe Wirkung: der Laptop holt den Blick
    auf den Bildschirm ab, der Buerorechner meldet daraufhin *sein* Foto — und
    das Modell haelt es fuer das, wonach es gefragt hat. Dieselbe Vertauschung
    bei einer Uebernahme von Maus und Tastatur waere ein Ergebnis vom falschen
    Arbeitsplatz.

    Dieselben zwei Nullwerte wie beim Abholen, und beide heissen wieder "kein
    Schnitt": ein Auftrag ohne Kennung war fuer jeden abholbar und ist deshalb
    auch von jedem quittierbar, und ein Meldender ohne Kennung (altes
    Access-Token) darf nicht auf einem Ergebnis sitzenbleiben, das er gerade
    erarbeitet hat.

    404 wie ueberall auf dieser Bruecke: kein Wort darueber, dass es den
    Auftrag gibt.
    """
    if job.device_family is None or familie is None:
        return
    if job.device_family != familie:
        raise HTTPException(status_code=404, detail="ai.errors.codes.DESKTOP_JOB_NOT_FOUND")


@router.get("/jobs/next", response_model=DesktopJobResponse | None)
def naechster_auftrag(
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.desktop.use")),
    herkunft: str = Depends(session_herkunft),
    familie: str | None = Depends(session_familie),
) -> DesktopJobResponse | None:
    """Der naechste Auftrag fuer diesen Rechner — oder nichts.

    Kein CSRF-Schutz, und die Herkunft ist der Grund, dass das reicht. Ein GET
    *veraendert* hier naemlich sehr wohl (der Auftrag gilt danach als geholt),
    und das Access-Cookie ist SameSite=Lax — eine Top-Level-Navigation von
    einer fremden Seite genuegte, damit der Browser des Benutzers einen
    wartenden Auftrag abgreift. Lesen koennte der Angreifer die Antwort nicht,
    die Wirkung traete trotzdem ein. Ein Bearer-Token schickt der Browser
    dagegen nie von selbst mit, und nur ein solches traegt `geraet: "desktop"`.

    ``familie`` ist der fragende Rechner und macht "dieser Rechner" im ersten
    Satz erst wahr. Bis zum 23.08.2026 stand die Spalte, stand der Filter, und
    genau diese Uebergabe fehlte — der Auftrag ging weiter an den, der zuerst
    fragte.
    """
    _nur_aus_der_app(herkunft)
    if familie:
        device_pairing_service.aktivitaet_vermerken(familie)
    job = desktop_job_service.naechster(db, user_id=user.id, familie=familie)
    if job is None:
        response.status_code = 204
        return None
    return DesktopJobResponse(
        id=job.id,
        tool_name=job.tool_name,
        arguments=desktop_job_service.argumente(job),
    )


def _zu_gross(ergebnis: dict) -> bool:
    """Zwei Budgets: eines fuer das Bild, eines fuer alles andere.

    Das Bild wird herausgerechnet und gegen `MAX_BILD_ZEICHEN` geprueft, der
    Rest gegen `MAX_ERGEBNIS_ZEICHEN`. Ohne die Trennung faellt jedes
    Bildschirmfoto unter eine Grenze, die fuer Text gedacht war — genau der
    Fehler, den es bis zum 23.08.2026 gab.
    """
    bild = ergebnis.get(BILDFELD)
    if isinstance(bild, str):
        if len(bild) > MAX_BILD_ZEICHEN:
            return True
        rest = {name: wert for name, wert in ergebnis.items() if name != BILDFELD}
    else:
        rest = ergebnis
    return len(str(rest)) > MAX_ERGEBNIS_ZEICHEN


@router.post("/jobs/{job_id}/result", status_code=204)
def ergebnis_melden(
    job_id: str,
    req: DesktopJobResultRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.desktop.use")),
    herkunft: str = Depends(session_herkunft),
    familie: str | None = Depends(session_familie),
    _: None = Depends(verify_csrf),
) -> Response:
    """Nimmt das Ergebnis entgegen und weckt den wartenden Lauf."""
    _nur_aus_der_app(herkunft)
    if familie:
        device_pairing_service.aktivitaet_vermerken(familie)
    job = db.get(DesktopJob, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="ai.errors.codes.DESKTOP_JOB_NOT_FOUND")
    _nur_sein_geraet(job, familie)
    if job.status not in ("pending", "taken"):
        # Doppelte Meldung nach einem Wiederverbinden. Kein Fehler: der Lauf
        # ist laengst geweckt, und ein zweites Ergebnis darf das erste nicht
        # ueberschreiben.
        return Response(status_code=204)

    ergebnis = req.ergebnis
    if _zu_gross(ergebnis):
        desktop_job_service.ergebnis_melden(
            db,
            job=job,
            ok=False,
            ergebnis={},
            error_code="DESKTOP_RESULT_TOO_LARGE",
        )
    else:
        desktop_job_service.ergebnis_melden(
            db,
            job=job,
            ok=req.ok,
            ergebnis=ergebnis,
            error_code=req.error_code,
        )

    # Erst wecken, wenn die ganze Runde beisammen ist — sonst saehe das Modell
    # halbe Ergebnisse. Dieselbe Regel wie bei den Vorschlaegen.
    if desktop_job_service.offene(db, run_id=job.run_id) == 0:
        try:
            ai_run_service.lauf_fortsetzen(db, run_id=job.run_id)
        except Exception:
            logger.warning(
                "AI-Lauf konnte nach dem Desktop-Ergebnis nicht fortgesetzt werden run_id=%s",
                job.run_id,
            )
    return Response(status_code=204)
