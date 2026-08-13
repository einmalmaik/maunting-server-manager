"""Der Arbeiter am Ausgangskorb — wenige Mails gleichzeitig, keine verloren.

Der Anlass steht in `models/ai_mail_outbox.py`: eine faellige KI-Aufgabe schickte
ihre Mail bisher in einem eigenen Betriebssystem-Thread mit eigener
Ereignisschleife, und zwar **je Mail einen**. Zehntausend Aufgaben auf 18:00
waren damit zehntausend Threads und zehntausend frische SMTP-Verbindungen. Kein
Anbieter nimmt das an, kein Prozess ueberlebt es — und was scheiterte, war weg,
denn der Versand endete in `except Exception: return False`.

Hier steht das Gegenteil davon, und es sind vier Entscheidungen:

**Eine Aufgabe auf der Ereignisschleife der Anwendung, kein Thread je Mail.**
Gestartet im `lifespan`, beendet beim Herunterfahren — dasselbe Muster wie
`ai_model_catalog`, bis hin zum `cancel()` **mit** anschliessendem Abwarten.
Zehntausend Zeilen kosten damit genau eine Aufgabe.

**Eine Schranke, keine Warteschlange ohne Boden.** `GLEICHZEITIG` Zustellungen
laufen zur selben Zeit, der Rest wartet. Die Zahl ist absichtlich klein: sie ist
die Zahl der SMTP-Verbindungen, die MSM gleichzeitig aufmacht, und jeder Anbieter
hat dafuer eine Obergrenze, die er nicht verhandelt.

**Uebernahme auf Zeit statt eines Zustands "laeuft gerade".** Wer eine Zeile
nimmt, schiebt ihr `naechster_versuch_at` um `UEBERNAHME` nach vorn und zaehlt
`versuche` hoch — beides in derselben Transaktion, in der er sie liest. Damit
sieht sie kein zweiter Arbeiter, solange er arbeitet; und stuerzt der Prozess
mitten im Versand ab, faellt die Zeile nach Ablauf der Frist von selbst zurueck
in die Warteschlange. Ein Zustand "laeuft gerade" haette den Absturz ueberlebt
und die Zeile fuer immer blockiert.

`SELECT ... FOR UPDATE SKIP LOCKED` kommt zusaetzlich dazu, wo die Datenbank es
kann. Heute laeuft das Panel als ein Prozess (`uvicorn --workers 1`), also
braeuchte es das nicht — aber der Tag, an dem jemand einen zweiten Prozess
startet, soll nicht der Tag sein, an dem jeder Kunde alles doppelt bekommt.
SQLite kennt beides nicht; dort traegt die befristete Uebernahme allein, und das
genuegt, weil SQLite ohnehin nur einen Schreiber zulaesst.

**Wiederholen mit wachsendem Abstand, dann aufgeben — aber laut.** Ein Anbieter,
der gerade drosselt, ist in einer Minute vielleicht wieder da; ein falsches
Passwort ist es nie. Nach `VERSUCHE_MAX` Anlaeufen steht die Zeile auf
`aufgegeben`, mit dem letzten Fehler daneben und einer Zeile im Log. Das ist
der Unterschied zu vorher: verloren geht nichts, aufgegeben wird sichtbar.

Die Adresse wird **hier** aufgeloest, unmittelbar vor dem Versand, ueber
`ai_mail.empfaenger`. Das ist kein Umweg, sondern die Zusage: wer zwischen dem
Einreihen und der Zustellung seine Benachrichtigungen abschaltet, bekommt keine
Mail mehr.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Callable

from sqlalchemy.orm import Session

from models import AiMailOutbox, User


logger = logging.getLogger(__name__)

#: Wieviele Zustellungen hoechstens gleichzeitig laufen. Das ist die Zahl der
#: gleichzeitigen SMTP-Verbindungen; fuenf ist bei jedem gaengigen Anbieter
#: unauffaellig, und mehr bringt nichts, weil der Engpass die Gegenseite ist.
GLEICHZEITIG = 5
#: Wieviele Zeilen ein Durchgang uebernimmt. Klein genug, dass ein Absturz nur
#: wenige Zeilen in der Uebernahmefrist stehen laesst; gross genug, dass
#: zehntausend faellige Mails in Durchgaengen und nicht in Stunden abfliessen.
STAPEL = 50
#: Wie lange gewartet wird, wenn nichts faellig war. Mails sind Benachrichtigungen
#: und keine Interaktion; eine halbe Minute Verzoegerung faellt niemandem auf,
#: und ein Takt von einer Sekunde waere eine Datenbankabfrage pro Sekunde fuer
#: nichts.
TAKT = 20.0
#: Wie oft eine Mail hoechstens angefasst wird, bevor sie aufgegeben wird.
VERSUCHE_MAX = 5
#: Abstand nach dem ersten Fehlschlag, danach jeweils verdoppelt.
ABSTAND_BASIS = 60.0
ABSTAND_MAX = 3600.0
#: Wie lange eine uebernommene Zeile fuer andere unsichtbar bleibt. Muss laenger
#: sein als ein Versandversuch dauern kann (SMTP-Timeouts liegen bei
#: Zehnersekunden) und kurz genug, dass ein Prozessabsturz nicht zu einer
#: Nachricht fuehrt, die erst am naechsten Tag noch einmal versucht wird.
UEBERNAHME = 300.0


@dataclass(frozen=True)
class _Auftrag:
    """Eine uebernommene Zeile, losgeloest von ihrer Sitzung.

    Bewusst eine Kopie und kein ORM-Objekt: zwischen Uebernahme und Versand
    liegt ein `await`, und ein an eine geschlossene Sitzung gebundenes Objekt
    laedt beim naechsten Attributzugriff nach — auf einer Verbindung, die es
    nicht mehr gibt. Die Kopie traegt ausserdem **keine** Adresse; die wird erst
    im Versand aufgeloest.
    """

    id: str
    user_id: int
    anlass: str
    betreff: str
    text_body: str
    html_body: str | None
    versuche: int


#: Die Sitzungsfabrik. Wird sie nicht gesetzt, kommt sie aus `database` — aber
#: erst beim Aufruf und nicht beim Import, denn die Testsuite tauscht
#: `database.SessionLocal` nach dem Import gegen eine SQLite-Fabrik aus.
_SITZUNGEN: Callable[[], Session] | None = None
#: Die eine laufende Arbeiteraufgabe. Der Verweis liegt hier, damit sie nicht
#: vom Aufraeumer der Ereignisschleife eingesammelt wird — `create_task` allein
#: haelt nichts fest.
_arbeiter: asyncio.Task | None = None


def laufzeit_setzen(sitzungen: Callable[[], Session] | None) -> None:
    """Eine eigene Sitzungsfabrik hinterlegen (oder mit ``None`` zuruecknehmen).

    Gibt es fuer denselben Zweck wie `ai_model_catalog.laufzeit_setzen`: der
    Arbeiter gehoert keiner Anfrage und darf deshalb auch keine Sitzung einer
    Anfrage benutzen. Im Regelfall braucht es den Aufruf nicht — dann nimmt der
    Arbeiter `database.SessionLocal`.
    """
    global _SITZUNGEN
    _SITZUNGEN = sitzungen


def _sitzung() -> Session:
    if _SITZUNGEN is not None:
        return _SITZUNGEN()
    import database

    return database.SessionLocal()


def _kann_ueberspringen(db: Session) -> bool:
    """Beherrscht diese Datenbank ``FOR UPDATE SKIP LOCKED``?

    PostgreSQL ja, SQLite nein. Die Frage wird gestellt, statt sich auf das
    stille Weglassen im SQLite-Dialekt zu verlassen: eine Klausel, von der man
    glaubt, sie wirke, waehrend sie verschwindet, ist schlimmer als eine, die
    man bewusst nicht setzt. Ohne sie traegt die befristete Uebernahme allein —
    was in SQLite ausreicht, weil dort ohnehin nur ein Schreiber zugleich
    arbeitet.
    """
    try:
        return db.get_bind().dialect.name == "postgresql"
    except Exception:  # noqa: BLE001 - eine unbekannte Bindung kann es nicht
        return False


def _uebernehmen(grenze: int) -> list[_Auftrag]:
    """Faellige Zeilen greifen und in einem Zug als "in Arbeit" markieren.

    Beides muss in **derselben** Transaktion passieren. Lesen und erst nach dem
    Versand schreiben hiesse, dass ein zweiter Durchgang dieselben Zeilen noch
    einmal findet — und der Betreiber bekaeme denselben Bericht zweimal.
    """
    jetzt = datetime.now(timezone.utc)
    db = _sitzung()
    try:
        abfrage = (
            db.query(AiMailOutbox)
            .filter(
                AiMailOutbox.status == "offen",
                AiMailOutbox.naechster_versuch_at <= jetzt,
            )
            .order_by(AiMailOutbox.naechster_versuch_at)
            .limit(grenze)
        )
        if _kann_ueberspringen(db):
            abfrage = abfrage.with_for_update(skip_locked=True)
        zeilen = abfrage.all()
        auftraege: list[_Auftrag] = []
        for zeile in zeilen:
            zeile.versuche = (zeile.versuche or 0) + 1
            zeile.naechster_versuch_at = jetzt + timedelta(seconds=UEBERNAHME)
            auftraege.append(
                _Auftrag(
                    id=zeile.id,
                    user_id=zeile.user_id,
                    anlass=zeile.anlass,
                    betreff=zeile.betreff,
                    text_body=zeile.text_body,
                    html_body=zeile.html_body,
                    versuche=zeile.versuche,
                )
            )
        db.commit()
        return auftraege
    finally:
        db.close()


def _adresse(auftrag: _Auftrag) -> str | None:
    """Die Adresse, an die diese Zeile gehen darf — jetzt, nicht beim Einreihen.

    Wirft nicht. Ist der Benutzer geloescht, hat er abbestellt, fehlt der
    Versandweg oder ist der DIS-Sidecar gerade nicht erreichbar, kommt ``None``
    zurueck. Die ersten drei Faelle sind endgueltig, der vierte nicht — der
    Unterschied wird hier bewusst **nicht** gemacht: `ai_mail.empfaenger` fuehrt
    ihn im Log, und eine Mail nach einem Sidecar-Aussetzer aufzugeben ist die
    seltenere und harmlosere Verwechslung als eine Mail an jemanden, der sie
    abbestellt hat.
    """
    from services import ai_mail

    db = _sitzung()
    try:
        user = db.get(User, auftrag.user_id)
        return ai_mail.empfaenger(db, user)
    except Exception as exc:  # noqa: BLE001 - ein Lesefehler verliert keine Zeile
        logger.warning(
            "Empfaenger nicht ermittelbar outbox=%s error=%s",
            auftrag.id,
            type(exc).__name__,
        )
        return None
    finally:
        db.close()


async def _versenden(adresse: str, auftrag: _Auftrag) -> bool:
    """Der eine Punkt, an dem MSM tatsaechlich eine Mail hinausgibt.

    Eine eigene Funktion, obwohl sie nur weiterreicht: sie ist die Naht, an der
    die Tests den Versand abfangen. Ohne sie muesste ein Test entweder
    `EmailService` als Ganzes ersetzen oder wirklich verschicken.
    """
    from services.email_service import EmailService

    return await EmailService.send_email(
        adresse, auftrag.betreff, auftrag.text_body, auftrag.html_body
    )


def _abschliessen(auftrag: _Auftrag) -> None:
    db = _sitzung()
    try:
        zeile = db.get(AiMailOutbox, auftrag.id)
        if zeile is None:
            return
        zeile.status = "zugestellt"
        zeile.sent_at = datetime.now(timezone.utc)
        zeile.letzter_fehler = None
        db.commit()
    finally:
        db.close()


def _fehlschlag(auftrag: _Auftrag, grund: str, *, endgueltig: bool = False) -> None:
    """Zurueck in die Warteschlange — oder aufgeben, aber dann sichtbar.

    ``endgueltig`` ist der Fall, in dem ein weiterer Versuch nichts aendern
    kann: es gibt keinen Empfaenger. Alles andere bekommt einen groesseren
    Abstand und einen weiteren Anlauf.
    """
    db = _sitzung()
    try:
        zeile = db.get(AiMailOutbox, auftrag.id)
        if zeile is None:
            return
        zeile.letzter_fehler = grund[:500]
        if endgueltig or (zeile.versuche or 0) >= VERSUCHE_MAX:
            zeile.status = "aufgegeben"
            logger.warning(
                "KI-Mail aufgegeben outbox=%s anlass=%s versuche=%s grund=%s",
                zeile.id,
                zeile.anlass,
                zeile.versuche,
                zeile.letzter_fehler,
            )
        else:
            abstand = min(
                ABSTAND_MAX, ABSTAND_BASIS * (2 ** max(0, (zeile.versuche or 1) - 1))
            )
            zeile.naechster_versuch_at = datetime.now(timezone.utc) + timedelta(
                seconds=abstand
            )
            logger.info(
                "KI-Mail erneut in der Warteschlange outbox=%s versuch=%s in=%.0fs",
                zeile.id,
                zeile.versuche,
                abstand,
            )
        db.commit()
    finally:
        db.close()


async def _zustellen(auftrag: _Auftrag) -> bool:
    """Eine einzelne Zeile. Wirft nie — ein Fehler ist ein Vermerk, kein Abbruch."""
    adresse = _adresse(auftrag)
    if adresse is None:
        _fehlschlag(
            auftrag,
            "kein Empfaenger: abbestellt, kein Versandweg oder keine Adresse",
            endgueltig=True,
        )
        return False
    try:
        geglueckt = await _versenden(adresse, auftrag)
    except asyncio.CancelledError:
        # Das Herunterfahren ist kein Fehlschlag. Die Zeile bleibt uebernommen
        # und faellt nach `UEBERNAHME` von selbst zurueck in die Warteschlange —
        # den Zaehler hier noch hochzuschreiben waere eine Aussage ueber einen
        # Versuch, den niemand zu Ende gefuehrt hat.
        raise
    except Exception as exc:  # noqa: BLE001 - genau dafuer gibt es diese Tabelle
        _fehlschlag(auftrag, f"{type(exc).__name__}: {exc}")
        return False
    if not geglueckt:
        _fehlschlag(auftrag, "Versand meldete Fehlschlag")
        return False
    _abschliessen(auftrag)
    return True


async def runde(grenze: int | None = None) -> int:
    """Ein Durchgang: uebernehmen, begrenzt zustellen, Ergebnis vermerken.

    Rueckgabe ist die Zahl der **uebernommenen** Zeilen, nicht der geglueckten.
    Genau die braucht die Schleife: solange ein Durchgang voll wird, ist noch
    Arbeit da und der naechste folgt sofort.

    Die Schranke entsteht je Durchgang neu. Ein `asyncio.Semaphore` im Modul
    bindet sich an die Ereignisschleife, in der er zuerst benutzt wird — und die
    Testsuite baut je Test eine neue. Dieselbe Lehre wie bei den Schloessern in
    `ai_model_catalog`.
    """
    auftraege = _uebernehmen(grenze if grenze is not None else STAPEL)
    if not auftraege:
        return 0
    schranke = asyncio.Semaphore(GLEICHZEITIG)

    async def _eine(auftrag: _Auftrag) -> None:
        async with schranke:
            await _zustellen(auftrag)

    await asyncio.gather(*(_eine(auftrag) for auftrag in auftraege))
    return len(auftraege)


async def _schleife() -> None:
    while True:
        try:
            uebernommen = await runde()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - der Arbeiter stirbt nicht an einer Runde
            logger.warning(
                "Ausgangskorb: Durchgang gescheitert error=%s", type(exc).__name__
            )
            uebernommen = 0
        if uebernommen >= STAPEL:
            # Der Stapel war voll, also liegt noch etwas da. Sofort weiter — aber
            # ueber die Ereignisschleife, damit ein Rueckstau von zehntausend
            # Mails den Chat nicht anhaelt.
            await asyncio.sleep(0)
        else:
            await asyncio.sleep(TAKT)


def arbeiter_starten() -> bool:
    """Den Arbeiter auf die laufende Ereignisschleife setzen.

    ``False`` heisst "hier laeuft keine Schleife" — der Zustand in Skripten und
    in der Testsuite. Das ist kein Fehler: Zeilen sammeln sich dann einfach im
    Korb, und der naechste Prozess mit einer Schleife arbeitet sie ab. Genau
    dafuer steht die Nachricht in der Datenbank und nicht in einem Thread.
    """
    global _arbeiter
    if _arbeiter is not None and not _arbeiter.done():
        return True
    try:
        schleife = asyncio.get_running_loop()
    except RuntimeError:
        return False
    _arbeiter = schleife.create_task(_schleife(), name="ai-mail-outbox")
    return True


async def aufraeumen() -> None:
    """Den Arbeiter beenden, bevor die Anwendung schliesst.

    Abbrechen allein genuegt nicht — `cancel()` bittet nur darum. Erst das
    Abwarten stellt sicher, dass kein Versand mehr laeuft, wenn der HTTP- und
    SMTP-Unterbau darunter weggeraeumt wird. Dieselbe Begruendung wie in
    `ai_model_catalog.aufraeumen`, und dieselbe Reihenfolge im `lifespan`.

    Was mitten im Versand abgebrochen wird, ist nicht verloren: die Zeile steht
    weiter auf ``offen`` und faellt nach `UEBERNAHME` zurueck in die
    Warteschlange.
    """
    global _arbeiter
    aufgabe, _arbeiter = _arbeiter, None
    if aufgabe is None or aufgabe.done():
        return
    aufgabe.cancel()
    await asyncio.gather(aufgabe, return_exceptions=True)


def zuruecksetzen_fuer_tests() -> None:
    """Arbeiter und Sitzungsfabrik verwerfen.

    Eine Aufgabe aus einer geschlossenen Ereignisschleife wird nie mehr fertig,
    und eine Sitzungsfabrik aus einem fremden Test zeigt auf eine Datenbank, die
    es nicht mehr gibt.
    """
    global _arbeiter, _SITZUNGEN
    if _arbeiter is not None:
        _arbeiter.cancel()
    _arbeiter = None
    _SITZUNGEN = None
