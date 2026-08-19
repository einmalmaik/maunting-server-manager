"""Die Meldestelle: der eine Weg von den Workern zum Menschen.

docs/agentic-framework.md (Abschnitt 4). Vier Zusagen, alle hier und nirgends
sonst:

* **Schwaerzung.** `melden()` ist der Choke-Point: jeder Text laeuft durch
  `redact_sensitive_text`, **bevor** er gespeichert wird. Eine Meldung, die
  woanders entstuende, truege moeglicherweise Klartext aus einem Worker-Lauf,
  der Logdateien fremder Server gelesen hat.
* **Nie ins Gespraech graetschen.** Der Chat-Kanal wartet auf Ruhe: kein
  aktiver Zug im Dauerchat, keine laufende Nutzereingabe, eine Karenz. Die
  Mail des ``email``-Kanals geht dagegen sofort in den Ausgangskorb — sie
  unterbricht niemanden.
* **Buendelung.** Werden mehrere Worker fertig, waehrend der Mensch
  beschaeftigt ist, liefert **ein** Gehirn-Zug alle Meldungen zusammen.
* **Zustellgarantie.** Die Meldung ist eine Zeile (`ai_meldungen`), die Marke
  ``zugestellt_at`` wird **vor** dem Versand committet — kein Doppelversand.
  Der Preis ist benannt: stirbt der Prozess zwischen Marke und Lieferlauf,
  ist diese eine Lieferung verloren; das Ergebnis selbst steht weiterhin in
  der Worker-Unterhaltung.

Das Tipp-Signal lebt im Prozessspeicher — wie der Broker eine bewusste
Betriebsgrenze bei mehreren Backend-Prozessen (Doku, Abschnitt 10). Verloren
gehen kann dadurch nur Zurueckhaltung, nie eine Meldung: im schlimmsten Fall
liefert das Gehirn, waehrend jemand tippt.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from models import AiMeldung, User
from models.ai_meldung import KANAELE, MELDUNGSARTEN
from services.ai_redaction import redact_sensitive_text

logger = logging.getLogger(__name__)


#: Wieviel eine einzelne Meldung sagen darf. Die Meldung ist das Ergebnis,
#: nie der Prozess — wer mehr zu sagen hat, hat es im Worker-Verlauf gesagt.
MAX_MELDUNG_CHARS = 4000

#: Wieviele Meldungen ein Lieferlauf buendelt. Mehr waere keine Wortmeldung
#: mehr, sondern ein Protokoll; der Rest kommt in der naechsten Ruhephase.
MAX_BUENDEL = 10

#: Die Ruhe-Karenz: so viele Sekunden muss die letzte Aktivität des Menschen
#: zurückliegen, bevor das Gehirn von sich aus spricht. Der Wert kommt aus der
#: Panel-Einstellung `ai_meldung_karenz_sekunden`, für die es bewusst **keine**
#: Oberfläche gibt: einen Regler zu bauen, nach dem niemand gefragt hat, kostet
#: Endpunkt, Recht, Maske und zwei Übersetzungen. Ohne Eintrag gelten 15 s; die
#: Grenzen darunter sind die Notbremse gegen einen von Hand verdrehten Wert.
KARENZ_KEY = "ai_meldung_karenz_sekunden"
STANDARD_KARENZ_S = 15
MIN_KARENZ_S = 3
MAX_KARENZ_S = 120


def karenz_sekunden() -> int:
    """Die Karenz aus den Panel-Einstellungen — im Zweifel die Vorgabe."""
    try:
        from services.panel_settings_service import PanelSettingsService

        roh = (PanelSettingsService.get(KARENZ_KEY, "") or "").strip()
    except Exception as exc:
        logger.warning("Ruhe-Karenz nicht lesbar error=%s", type(exc).__name__)
        return STANDARD_KARENZ_S
    try:
        wert = int(roh)
    except ValueError:
        return STANDARD_KARENZ_S
    return wert if MIN_KARENZ_S <= wert <= MAX_KARENZ_S else STANDARD_KARENZ_S


# ── Das Tipp-Signal ───────────────────────────────────────────────────────
#
# Das Frontend meldet, solange das Eingabefeld nicht leer ist oder gerade
# getippt wurde. Feste Sekunden allein sind wackelig (manche tippen langsam) —
# das Signal haelt die Karenz offen, solange es eintrifft.

_TIPPT: dict[int, float] = {}


def tippen_melden(user_id: int) -> None:
    _TIPPT[int(user_id)] = time.monotonic()


def _tippt_gerade(user_id: int, karenz: int) -> bool:
    wann = _TIPPT.get(int(user_id))
    return wann is not None and (time.monotonic() - wann) < karenz


def zuruecksetzen_fuer_tests() -> None:
    _TIPPT.clear()


# ── Melden ────────────────────────────────────────────────────────────────


def melden(
    db: Session,
    *,
    user: User,
    text: str,
    art: str = "ergebnis",
    kanal: str = "chat",
    worker_id: str | None = None,
    worker_titel: str | None = None,
    question: dict | None = None,
) -> AiMeldung:
    """Nimmt eine Wortmeldung an: schwaerzen, speichern, Mail sofort losschicken.

    Der Chat-Kanal wird hier **nicht** bedient — die Zeile wartet auf Ruhe
    (`zustellung_anstossen`). Die Mail geht dagegen sofort in den
    Ausgangskorb: sie unterbricht kein Gespraech, und der Korb traegt ab dort
    Wiederholung und Reihenfolge. ``email`` heisst *zusaetzlich* — die
    Chat-Zeile entsteht bei jedem Kanal.

    Committet selbst: die Zusage „die Meldung ist gespeichert" darf nicht an
    einem spaeteren Commit des Aufrufers haengen (dasselbe Argument wie beim
    Ausgangskorb).
    """
    if art not in MELDUNGSARTEN:
        raise ValueError(f"Unbekannte Meldungsart: {art}")
    if kanal not in KANAELE:
        kanal = "chat"

    inhalt = redact_sensitive_text(str(text or "").strip())[:MAX_MELDUNG_CHARS]
    if not inhalt:
        inhalt = "(leere Meldung)"

    frage_json: str | None = None
    if question is not None:
        # Bereits geprueft durch `question_payload()` beim Abfangen des
        # Werkzeugs; hier wird nur noch geschwaerzt gespeichert.
        frage_json = redact_sensitive_text(
            json.dumps(question, ensure_ascii=False, separators=(",", ":"))
        )

    meldung = AiMeldung(
        id=str(uuid4()),
        user_id=user.id,
        worker_id=worker_id,
        art=art,
        kanal=kanal,
        text=inhalt,
        question_json=frage_json,
    )
    db.add(meldung)
    db.commit()

    if kanal in ("email", "both"):
        _mail_einreihen(db, user=user, meldung=meldung, worker_titel=worker_titel)

    logger.info(
        "Meldung angenommen (user_id=%s, art=%s, kanal=%s, worker_id=%s)",
        user.id, art, kanal, worker_id,
    )
    return meldung


def _mail_einreihen(
    db: Session, *, user: User, meldung: AiMeldung, worker_titel: str | None
) -> None:
    """Der ``email``-Kanal: eine Zeile im Ausgangskorb, nie ein Direktversand."""
    from services import ai_mail
    from services.email_service import EmailService

    rahmen = EmailService.ai_rahmen_worker(
        str(user.username),
        auftrag_titel=str(worker_titel or "Auftrag"),
        frage=meldung.art == "frage",
    )
    betreff, text, html = EmailService.ai_mail_rendern(rahmen, rueckfall=meldung.text)
    ai_mail.zustellen(
        name="ai-worker-meldung",
        db=db,
        user_id=int(user.id),
        betreff=betreff,
        text=text,
        html=html,
        # Der geschwaerzte Meldungstext ist der Stoff, aus dem der Arbeiter am
        # Ausgangskorb die Mail verfasst — KI-erzeugt, nie eine Phrase.
        fakten=meldung.text,
        rahmen=rahmen,
    )


# ── Ruhe und Zustellung ───────────────────────────────────────────────────


def offene_meldungen(db: Session, *, user_id: int) -> list[AiMeldung]:
    return (
        db.query(AiMeldung)
        .filter(AiMeldung.user_id == user_id, AiMeldung.zugestellt_at.is_(None))
        .order_by(AiMeldung.created_at.asc())
        .limit(MAX_BUENDEL)
        .all()
    )


def ruhe(db: Session, *, user: User) -> bool:
    """Hat das Gespraech Ruhe? Drei Bedingungen, alle drei muessen gelten.

    1. Kein aktiver Zug im Dauerchat. Gefragt wird nach ``kind='primary'``:
       ein geparkter Worker ist kein Gespraech, und die Guardian-Heilung
       schreibt in ihr eigenes Fenster.
    2. Kein Tipp-Signal innerhalb der Karenz.
    3. Die letzte Bewegung im Dauerchat liegt mindestens die Karenz zurueck —
       wer gerade eine Antwort gelesen hat, soll nicht im naechsten Atemzug
       die naechste Wortmeldung bekommen.

    Der Sprachmodus ersetzt diese Regel durch den VAD-Zustand „bereit" —
    dort entscheidet die Stimme selbst, wann gesprochen werden darf.
    """
    from models import AiConversation
    from services import ai_run_service

    if ai_run_service.aktiver_lauf(db, user_id=user.id, kind="primary") is not None:
        return False
    karenz = karenz_sekunden()
    if _tippt_gerade(user.id, karenz):
        return False
    # Bewusst eine blosse Abfrage und keine Fabrik: eine Ruhepruefung, die als
    # Nebenwirkung Unterhaltungen anlegt, waere die falsche Sorte Leserin.
    fenster = (
        db.query(AiConversation)
        .filter(AiConversation.user_id == user.id, AiConversation.kind == "primary")
        .first()
    )
    letzte = fenster.updated_at if fenster is not None else None
    if letzte is not None:
        if letzte.tzinfo is None:
            letzte = letzte.replace(tzinfo=timezone.utc)
        alter = (datetime.now(timezone.utc) - letzte).total_seconds()
        if alter < karenz:
            return False
    return True


def _lieferauftrag(meldungen: list[AiMeldung]) -> str:
    """Der Inhalt des Lieferlaufs — eine Panel-Meldung, kein Nutzersatz.

    Aufgebaut wie das Guardian-Briefing: ausdruecklich als Meldung des Panels
    markiert und mit ``untrusted`` versehen — der Text stammt aus Laeufen, die
    unvertrauenswuerdiges Material gelesen haben, auch wenn er geschwaerzt ist.
    Das Gehirn liefert in eigener Stimme; nichts hier ist eine Phrase, die es
    abschreiben soll.
    """
    zeilen = []
    for meldung in meldungen:
        zeile: dict = {
            "worker_id": meldung.worker_id,
            "art": meldung.art,
            "text": meldung.text,
        }
        if meldung.question_json:
            try:
                zeile["frage"] = json.loads(meldung.question_json)
            except ValueError:
                zeile["frage"] = None
        zeilen.append(zeile)
    nutzlast = json.dumps(
        {"untrusted": True, "tool": "worker_meldungen", "data": {"meldungen": zeilen}},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return (
        "Meldung des Panels (nicht vom Benutzer geschrieben): deine "
        "Hintergrund-Aufträge haben berichtet. Liefere dem Benutzer jetzt "
        "die Ergebnisse — in deiner eigenen Stimme, als eine zusammenhängende "
        "Wortmeldung, nicht als Liste von Protokollen. Enthält eine Meldung "
        "eine Frage (art='frage'), stelle sie ihm so, dass er sie beantworten "
        "kann, und nenne dabei, um welchen Auftrag es geht. Erfinde nichts "
        "dazu.\n" + nutzlast
    )


async def zustellung_anstossen(db: Session, *, user: User, ruhe_noetig: bool = True):
    """Liefert offene Meldungen in einem Gehirn-Zug — wenn Ruhe ist.

    ``None`` heisst: nichts geliefert (nichts offen, keine Ruhe, kein
    Anbieter, kein Kontingent). Der Aufrufer — Takt oder Abschlusshaken —
    versucht es einfach spaeter wieder; die Zeilen bleiben stehen.

    ``ruhe_noetig=False`` ist fuer den Sprachmodus: dort ersetzt der
    VAD-Zustand "bereit" die Chat-Ruhe (siehe `ruhe()`), und die Bruecke hat
    ihn **vor** dem Aufruf geprueft — die Chat-Regel wuerde eine offene
    Sprachsitzung sogar faelschlich blockieren, weil jede Aeusserung
    ``conversation.updated_at`` bewegt und die Karenz nie ablaeuft.

    Die Marke faellt **vor** dem Lauf: erst ``zugestellt_at`` committen, dann
    liefern. Scheitert das Anlegen des Laufs kontrolliert (Kontingent), wird
    die Marke zurueckgenommen — das ist kein Absturz, sondern eine Antwort.
    """
    from services import ai_chat_service, ai_run_service, permission_service

    offene = offene_meldungen(db, user_id=user.id)
    if not offene:
        return None
    if not user.is_active:
        return None
    # Ohne Chatrecht gibt es kein Gespraech, in das geliefert werden koennte.
    # Die Zeilen bleiben stehen — kommt das Recht zurueck, kommt die Meldung.
    if not permission_service.has_global_permission(db, user, "ai.chat.use"):
        return None
    client = ai_run_service.http_client()
    if client is None:
        return None
    if ruhe_noetig and not ruhe(db, user=user):
        return None

    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    db.commit()

    flug, anbieter = await ai_run_service.vorflug(client, db, user)
    if flug is None:
        if anbieter is None:
            logger.info("Zustellung ohne Anbieter (user_id=%s)", user.id)
        return None

    # Die Marke vor dem Versand — kein Doppelversand, auch wenn der Prozess
    # gleich stirbt. Siehe Modul-Docstring fuer den benannten Preis.
    jetzt = datetime.now(timezone.utc)
    for meldung in offene:
        meldung.zugestellt_at = jetzt
    db.commit()

    from services.ai_stream_service import lauf_beginnen

    run, fehler = lauf_beginnen(
        db,
        user=user,
        conversation=conversation,
        provider=flug.anbieter,
        request_id=uuid4(),
        content=_lieferauftrag(offene),
        reasoning=flug.denken,
        reasoning_effort=flug.stufe,
        context_chars=flug.fenster.zeichen if flug.fenster.bekannt else None,
        # Offene Vorfaelle haben ihren eigenen Weg; eine Lieferung, die sie
        # nebenbei als besprochen markiert, waere dieselbe Falle wie bei den
        # Aufgaben.
        guardian_briefing_unterdruecken=True,
        unbeaufsichtigt=True,
        # Explizit und nicht abgeleitet: `unbeaufsichtigt=True` ergaebe
        # "voll", aber die Lieferung ist ein Gehirn-Zug — dieselbe Stimme,
        # derselbe Katalog wie im Gespraech, nur ohne Menschen davor.
        rolle="gehirn",
        # **Der Auftrag selbst ist Maschinerie.** Er traegt eine JSON-Nutzlast
        # und eine Anweisung an das Gehirn ("liefere jetzt die Ergebnisse") —
        # beides an den Betreiber adressiert zu sehen, war der Grund fuer
        # diese Marke. Ein Worker arbeitet im Hintergrund; man sieht seine
        # Zettel so wenig wie die eines Assistenten. Der Kontext des Modells
        # bekommt die Zeile weiterhin vollstaendig.
        intern=True,
    )
    if run is None:
        # Kontrolliertes Scheitern, kein Absturz: Marke zurueck, naechste
        # Ruhephase versucht es erneut.
        for meldung in offene:
            meldung.zugestellt_at = None
        db.commit()
        logger.info(
            "Zustellung nicht begonnen (user_id=%s): %s",
            user.id, (fehler or ("unbekannt",))[0],
        )
        return None

    # Der Rahmen nach `lauf_beginnen` — Rollback-Sicherheit wie ueberall.
    zustand = ai_run_service.zustand_lesen(run)
    zustand["meldung"] = {"ids": [meldung.id for meldung in offene]}
    ai_run_service.zustand_schreiben(run, zustand)
    db.commit()

    if not ai_run_service.anlauf(db, run):
        return None
    logger.info(
        "Zustellung begonnen (user_id=%s, meldungen=%d, run_id=%s)",
        user.id, len(offene), run.id,
    )
    return run


#: Endzustaende, die **keine** Meldung sind. `superseded` und `answered`
#: heissen: eine Antwort (`worker_antwort`) oder ein neuer Zug hat den Lauf
#: abgeloest — der Nachfolger meldet, und eine beantwortete Frage ist kein
#: Ergebnis. `worker_cancel` und `berechtigung_entzogen` haben ihre Auskunft
#: bereits an anderer Stelle gegeben. `process_restart` gehoert dem
#: Wiederanlauf (`worker_wiederanlauf_saehen`): entweder saet der einen
#: Nachfolger, oder er meldet selbst, dass es keinen zweiten Versuch gibt.
OHNE_MELDUNG = (
    "superseded", "answered", "worker_cancel", "berechtigung_entzogen",
    "process_restart",
    # `cancelled` setzt der Stream, wenn die asyncio-Aufgabe eines Laufs
    # angehalten wird (`ai_stream_service`, `except asyncio.CancelledError`).
    # Das passiert bei jedem Abloesen: `aufgabe_abbrechen` markiert die Zeile
    # als `superseded` und haelt gleich darauf die Aufgabe an — der Abschluss
    # im Stream ueberschreibt den Grund dann mit `cancelled`.
    #
    # Gemeldet am 18.08.2026: der Betreiber reichte einem laufenden Auftrag
    # Werte nach, alles lief sauber durch, und trotzdem sagte ihm die KI
    # "Der Auftrag zur ASA-Konfiguration wurde abgebrochen und hat keine
    # Zusammenfassung hinterlassen." Der Bestand zeigte, warum:
    #
    #   cancelled/cancelled  MELDET   <- die Falschmeldung
    #   completed/done       MELDET   <- das echte Ergebnis
    #
    # Die erste Meldung war die abgeloeste Runde, die zweite ihr Nachfolger.
    # Das Gehirn hat beide korrekt wiedergegeben — es bekam nur eine Meldung
    # zu viel. Ein angehaltener Lauf hat nichts zu berichten: entweder loest
    # ihn ein Nachfolger ab, der selbst meldet, oder der Mensch hat ihn
    # abgebrochen und weiss es bereits.
    "cancelled",
)


def lauf_beendet(db: Session, *, run, zustand: dict) -> None:
    """Der Abschlusshaken eines Worker-Laufs: Ergebnis als Meldung einreichen.

    Gerufen aus `_bericht_zustellen` im Stream — die Marke `worker_gemeldet`
    ist dort bereits gesetzt und committet, ein Doppelversand ist damit
    ausgeschlossen. Gemeldet wird bei jedem Endzustand, der ein Ergebnis ist:
    auch ein gescheiterter Auftrag sagt, was er geschafft hat und woran er
    scheiterte (docs/agentic-framework.md, §4). Der Meldungstext ist der
    Abschlusstext des Modells — KI-erzeugt, nie eine Phrase; die Zeile
    "Stand laut Panel" darunter ist Auskunft fuer das Gehirn, das daraus in
    eigener Stimme formuliert.
    """
    from models import User as UserModell
    from services.ai_task_report import abschlusstext

    rahmen = zustand.get("worker") or {}
    if str(run.stop_reason or "") in OHNE_MELDUNG:
        return
    user = db.get(UserModell, run.user_id)
    if user is None:
        return

    bericht = abschlusstext(db, run, zustand)
    if not bericht:
        bericht = (
            "Der Auftrag hat keine Zusammenfassung hinterlassen; der Verlauf "
            "steht in der Auftragsansicht."
        )
    geschafft = run.status == "completed"
    stand = "erledigt" if geschafft else (
        f"nicht abgeschlossen ({run.stop_reason or 'unbekannt'})"
    )
    runden = int(zustand.get("rounds") or 0)
    bericht += (
        f"\n[Stand laut Panel: {stand}; Anbieteranfragen in diesem Lauf: "
        f"{runden + 1}]"
    )

    melden(
        db,
        user=user,
        text=bericht,
        art="ergebnis",
        kanal=str(rahmen.get("kanal") or "chat"),
        worker_id=str(rahmen.get("conversation_id") or run.conversation_id),
        worker_titel=str(rahmen.get("titel") or "") or None,
    )


async def faellige_zustellungen(db: Session) -> int:
    """Ein Takt-Handgriff: je Benutzer mit offenen Meldungen eine Lieferung versuchen.

    Klein und wiederholbar: was diesmal keine Ruhe hat, kommt beim naechsten
    Takt wieder dran. Ein Fehler bei einem Benutzer nimmt die anderen nicht mit.
    """
    zeilen = (
        db.query(AiMeldung.user_id)
        .filter(AiMeldung.zugestellt_at.is_(None))
        .distinct()
        .limit(20)
        .all()
    )
    geliefert = 0
    for (user_id,) in zeilen:
        user = db.get(User, user_id)
        if user is None:
            continue
        try:
            if await zustellung_anstossen(db, user=user) is not None:
                geliefert += 1
        except Exception:
            db.rollback()
            logger.warning("Zustellung fehlgeschlagen (user_id=%s)", user_id)
    return geliefert
