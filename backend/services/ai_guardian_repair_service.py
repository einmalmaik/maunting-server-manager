"""Der Reparaturauftrag: ein Vorfall, so viele Anlaeufe wie noetig.

Was dieses Modul aendert, laesst sich in einem Satz sagen: **ein erschoepftes
Rundenbudget ist kein Ergebnis.** Bis hierher war es eines. Ein Heilungslauf
endete nach achtundvierzig Leserunden mit ``stop_reason='budget'``, wurde als
``status='completed'`` verbucht, die Notiz mit ``mode='healing'`` stand seit dem
*Start* in der Datenbank, und beide Filter des Takts uebersprangen den Vorfall
von da an bei jedem Durchlauf. Der Server blieb stehen, die Mail sagte "nicht
behoben", und nichts fasste ihn je wieder an.

Die Aufgabenteilung, damit sie nicht verschwimmt:

* ``ai_guardian_service``       — wer ist zustaendig, und was steht im Auftragstext.
* ``ai_guardian_repair_service`` — wann laeuft was, in welcher Phase, wie lange noch.
* ``ai_guardian_report``        — was der Betreiber am Ende liest.

Die drei Regeln, aus denen sich alles Weitere ergibt
----------------------------------------------------

**Die Phase kommt von aussen.** ``diagnose`` → ``eingriff`` → ``beobachtung``.
Nicht das Modell entscheidet, wann es genug untersucht hat — es hat im Betrieb
zweierlei getan: gelesen, geredet und aufgehoert, ohne etwas zu tun; und einen
Vorfall fuer erledigt erklaert, sobald ein Container lief. Eine Phase, die der
Auftrag setzt, laesst beides nicht zu.

**Beobachtet wird zwischen den Laeufen, nicht in einem.**
``MAX_GLEICHE_POLLING_AUFRUFE`` schneidet acht gleiche Abfragen hintereinander
ab; "eine Stunde zusehen" gibt es innerhalb eines Segments nicht. Der Lauf
endet in ``beobachtung`` absichtlich, und ``next_run_at`` traegt das Zusehen —
Minuten spaeter, ohne einen einzigen Token dazwischen.

**Erledigt ist, was die Anlage zeigt.** Nicht, was das Modell schreibt. Die
Und-Verknuepfung aus ``ai_guardian_report`` (Lauf sauber beendet **und** Vorfall
``resolved``) wandert damit auf die Ebene des Auftrags und bekommt zwei weitere
Glieder: der Server muss den Zustand tragen, den der Betreiber will, und er darf
nicht in Quarantaene stehen.

Was die Kontinuitaet traegt
---------------------------

Nicht das Arbeitsgedaechtnis. ``arbeitsspeicher_leeren`` wirft
``provider_messages`` bei jedem Endzustand weg, und das ist Absicht — dort steht
der entschluesselte Gedaechtnisblock des Benutzers im Klartext. Ein beendeter
Lauf laesst sich deshalb **nie** fortsetzen.

Stattdessen traegt ``AiGuardianRepair.erkenntnisse`` den Abschlusstext des
letzten Laufs weiter, geschwaerzt und gedeckelt, und der naechste Auftragstext
zitiert ihn als das, was er ist: die eigene Notiz aus dem vorigen Anlauf.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from models import (
    AiActionProposal,
    AiGuardianRepair,
    AiRun,
    ChangeEvent,
    Incident,
    Server,
    User,
)
from models.ai_guardian_repair import ENDPHASEN
from models.ai_run import WARTEND
from services import audit_service
from services.ai_redaction import redact_sensitive_text


logger = logging.getLogger(__name__)

#: Wie lange ein Auftrag hoechstens laeuft. Danach ist Schluss, egal wie weit er
#: gekommen ist. Sechs Stunden sind die Zeit, die eine Nacht hergibt: wer um
#: zwei Uhr anfaengt, ist zum Fruehstueck fertig — mit einem Ergebnis oder mit
#: einer ehrlichen Mail.
FRIST_STUNDEN = 6

#: Wieviele Laeufe ein Auftrag hoechstens verbraucht. Jeder ist ein
#: Anbieteraufruf mit eigenen Kosten, und acht Anlaeufe, die nichts bewirkt
#: haben, sind kein Argument fuer einen neunten.
MAX_VERSUCHE = 8

#: Der Abstand zwischen zwei Beobachtungen. Kuerzer waere Aktionismus — ein
#: Server, der nach zwei Minuten laeuft, kann nach zehn immer noch abstuerzen,
#: und genau das soll die Phase herausfinden.
BEOBACHTUNG_MINUTEN = 10

#: Der Abstand zwischen zwei Arbeitsphasen. Kurz genug, dass eine Reparatur
#: zuegig vorangeht; lang genug, dass der Takt dazwischen atmet.
WIEDERANLAUF_SEKUNDEN = 90

#: Wie lange ein begonnener Lauf den Auftrag fuer sich hat. Der Weckruf wird
#: beim Anspruch **vor** dem Lauf weitergeschaltet — faellt der Prozess mitten
#: im Lauf, findet der naechste Durchgang einen Termin in der Zukunft und nicht
#: denselben faelligen ein weiteres Mal.
LAUFENDER_LAUF_SEKUNDEN = 300

#: Wieviel vom Abschlusstext in den naechsten Anlauf mitgeht. Der Text landet in
#: der Benutzernachricht des naechsten Laufs, also an der Stelle mit dem meisten
#: Gewicht — und dort ist Laenge kein Vorteil, sondern Verdraengung.
MAX_ERKENNTNISSE = 2_000

#: Wie lange die Guardian-Leiter waehrend eines Eingriffs stillsteht. Der Deckel
#: in ``guardian_state_service`` sind vier Stunden plus fuenf Minuten
#: Uhrendrift; hier wird bei **jedem** Weckruf neu gesetzt, also ist eine kurze
#: Aussetzung die sicherere: faellt das Panel aus, laeuft sie von selbst ab und
#: der Agent macht weiter, statt einen Server stundenlang sich selbst zu
#: ueberlassen.
AUSSETZUNG_MINUTEN = 30

#: Wieviele Zeilen ein Durchlauf ueberhaupt ansieht.
MAX_ZEILEN_JE_DURCHLAUF = 20

#: Wieviele Laeufe ein Durchlauf hoechstens beginnt. Deutlich kleiner als die
#: Zeilenzahl: jeder Lauf ist ein Anbieteraufruf, und der Takt schlaegt jede
#: Minute erneut zu.
MAX_LAEUFE_JE_DURCHLAUF = 3

#: Der Grund, unter dem die Guardian-Leiter ausgesetzt wird. Muss dem Muster
#: ``^[a-z][a-z0-9_-]*$`` genuegen — ``set_recovery_suspension`` prueft das.
AUSSETZUNGSGRUND = "ai_repair"


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _utc(wert: datetime | None) -> datetime | None:
    if wert is None:
        return None
    return wert.replace(tzinfo=timezone.utc) if wert.tzinfo is None else wert


# ── Anlegen und Nachschlagen ──────────────────────────────────────────────


def auftrag_zu_vorfall(db: Session, *, incident_id: int) -> AiGuardianRepair | None:
    """Gibt es zu diesem Vorfall schon einen Auftrag — von wem auch immer?

    Bewusst **ohne** Benutzerfilter, obwohl die Eindeutigkeit ein Paar ist. Zwei
    Freigeber, die denselben Vorfall gleichzeitig reparieren lassen, waeren zwei
    Laeufe auf einem Server: doppelte Kosten, zwei Mails, und zwei Modelle, die
    sich gegenseitig die Datei unter den Haenden wegschreiben.
    """
    return (
        db.query(AiGuardianRepair)
        .filter(AiGuardianRepair.incident_id == int(incident_id))
        .first()
    )


def auftrag_anlegen(
    db: Session, *, vorfall: Incident, server: Server, user: User
) -> AiGuardianRepair | None:
    """Legt den Auftrag an — faellig sofort. ``None`` heisst: gab es schon.

    Der Auftrag wird angelegt und **nicht** gleich gestartet. Das Starten ist
    Sache von `faellige_bearbeiten`, und zwar aus einem Grund: es gibt dann
    genau einen Weg, auf dem ein Reparaturlauf entsteht, mit genau einer
    Anspruchnahme davor. Zwei Wege waeren zwei Stellen, an denen der Anspruch
    vergessen werden kann.
    """
    from sqlalchemy.exc import IntegrityError

    jetzt = _jetzt()
    auftrag = AiGuardianRepair(
        id=str(uuid4()),
        incident_id=int(vorfall.id),
        server_id=int(server.id),
        user_id=int(user.id),
        phase="diagnose",
        attempt=0,
        next_run_at=jetzt,
        deadline_at=jetzt + timedelta(hours=FRIST_STUNDEN),
        created_at=jetzt,
        updated_at=jetzt,
    )
    try:
        with db.begin_nested():
            db.add(auftrag)
            db.flush()
    except IntegrityError:
        # Ein anderer Durchlauf war schneller. Die Eindeutigkeit liegt in der
        # Datenbank, nicht in einer Pruefung davor — sie haelt auch dann, wenn
        # das Panel je mit mehreren Arbeitsprozessen laeuft.
        return None
    db.commit()
    return auftrag


def auftrag_aus_zustand(db: Session, zustand: dict) -> AiGuardianRepair | None:
    """Der Auftrag, zu dem dieser Lauf gehoert — oder ``None``.

    ``None`` heisst bei einem Guardian-Rahmen ohne ``repair_id``: ein Lauf aus
    der Zeit vor dieser Aenderung. Er wird abgeschlossen wie frueher, mit einer
    Mail je Lauf. Alte Laeufe nachtraeglich in Auftraege zu zwingen waere eine
    Migration von Zustandsdaten, und die kann nur schiefgehen.
    """
    rahmen = zustand.get("guardian")
    if not isinstance(rahmen, dict):
        return None
    kennung = rahmen.get("repair_id")
    if not kennung:
        return None
    return db.get(AiGuardianRepair, str(kennung))


def kampagne_laeuft_noch(db: Session, zustand: dict) -> bool:
    """Wartet an diesem Lauf noch ein Auftrag auf einen naechsten Anlauf?

    Die Frage entscheidet, ob die Abschlussmail hinausgeht. **Eine Mail je
    Auftrag, nicht je Lauf** — sonst bekommt der Betreiber acht Mails ueber
    einen Server, von denen sieben "nicht behoben" sagen und die achte ihm
    widerspricht.
    """
    auftrag = auftrag_aus_zustand(db, zustand)
    if auftrag is None:
        return False
    return auftrag.phase not in ENDPHASEN


# ── Der Anspruch ──────────────────────────────────────────────────────────


def _anspruch_nehmen(
    db: Session, auftrag: AiGuardianRepair, *, gelesen: datetime | None, neu: datetime | None
) -> bool:
    """Schaltet den Weckruf weiter — **atomar und vor dem Lauf**.

    Woertlich die Konstruktion aus `ai_task_service._anspruch_nehmen`, und aus
    denselben zwei Gruenden. Die Bedingung ``next_run_at = <gelesen>`` ist die
    eigentliche Aussage: nur wer genau den Termin vorfindet, den er gelesen hat,
    hat ihn auch. Und **vor** dem Lauf weitergeschaltet zu haben ist die
    Schranke gegen eine heisse Schleife — faellt der Prozess mitten im Lauf,
    findet der naechste Durchgang einen Termin in der Zukunft.

    Der Versuchszaehler steigt hier mit, nicht am Ende des Laufs. Ein Lauf, der
    den Prozess mit sich reisst, hat trotzdem einen Versuch gekostet; sonst
    zaehlte ausgerechnet der teuerste Fall nicht mit.
    """
    jetzt = _jetzt()
    geschrieben = (
        db.query(AiGuardianRepair)
        .filter(
            AiGuardianRepair.id == auftrag.id,
            AiGuardianRepair.next_run_at == gelesen,
        )
        .update(
            {
                "next_run_at": neu,
                "attempt": AiGuardianRepair.attempt + 1,
                "last_started_at": jetzt,
                "updated_at": jetzt,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    db.expire(auftrag)
    return geschrieben == 1


def _abschliessen(
    db: Session,
    auftrag: AiGuardianRepair,
    *,
    phase: str,
    grund: str,
) -> None:
    """Setzt eine Endphase und raeumt auf, was der Auftrag noch haelt.

    Aufraeumen heisst zweierlei.

    Erstens die Guardian-Leiter wieder freigeben. Eine Aussetzung, die niemand
    mehr aufhebt, ist die schlimmste Hinterlassung dieses Moduls — der Agent
    sieht einen Server abstuerzen und darf nicht eingreifen, bis der Deckel von
    vier Stunden von selbst ablaeuft.

    Zweitens eine noch offene E-Mail-Freigabe entwerten. Der Link gilt 24
    Stunden, die Frist des Auftrags sind sechs — ohne diese Zeile koennte
    jemand am naechsten Morgen auf "Freigeben" tippen und einen Eingriff
    ausloesen, der zu einer Kampagne gehoert, die es nicht mehr gibt, gegen
    einen Vorfall, der inzwischen ein anderer ist.
    """
    auftrag.phase = phase
    auftrag.next_run_at = None
    auftrag.updated_at = _jetzt()
    db.commit()
    _aussetzung_freigeben(db, auftrag)
    _wartende_freigabe_entwerten(db, auftrag)
    logger.info(
        "Reparaturauftrag beendet repair_id=%s phase=%s grund=%s versuche=%s",
        auftrag.id, phase, grund, auftrag.attempt,
    )


def _wartet_auf_freigabe(db: Session, auftrag: AiGuardianRepair) -> bool:
    """Steht dieser Auftrag an einer Frage, die per Mail hinausging?

    Gemessen wird an der Freigabezeile und nicht am Laufzustand:
    ``waiting_confirmation`` entsteht auch, wenn ein Mensch im Panel eine Karte
    offen hat, und das ist ein anderer Fall — dort sitzt jemand davor.
    """
    from models import AiActionApproval

    if not auftrag.last_run_id:
        return False
    return (
        db.query(AiActionApproval)
        .filter(
            AiActionApproval.run_id == str(auftrag.last_run_id),
            AiActionApproval.consumed_at.is_(None),
        )
        .first()
        is not None
    )


def _wartende_freigabe_entwerten(db: Session, auftrag: AiGuardianRepair) -> None:
    """Nimmt die offene E-Mail-Freigabe dieses Auftrags zurueck.

    Sie gilt laenger als der Auftrag, und das ist Absicht: der Link soll auch
    dann noch funktionieren, wenn jemand ihn erst am Abend liest. Endet der
    Auftrag aber, ist die Frage hinfaellig — und ein Link, der eine hinfaellige
    Frage beantwortet, fuehrt einen Eingriff aus, den niemand mehr gewollt hat.

    Entwertet wird ueber ``consumed_at``, nicht durch Loeschen: die Zeile war
    da, jemand haette antworten koennen, und ``decision IS NULL`` sagt genau
    das — es wurde nie entschieden.

    Der Lauf dazu wird nicht angefasst. Er steht auf ``waiting_confirmation``,
    und `_schlussbericht` liest ihn gleich noch, um die Abschlussmail zu
    verschicken; ihn hier zu beenden hiesse, dem Bericht seine Quelle
    wegzuziehen. Aufgeraeumt wird er vom Verfallslauf der Laeufe, wie jeder
    andere wartende auch.
    """
    from models import AiActionApproval, AiActionProposal

    zeilen = (
        db.query(AiActionApproval)
        .filter(
            AiActionApproval.user_id == int(auftrag.user_id),
            AiActionApproval.consumed_at.is_(None),
        )
        .all()
    )
    if not zeilen:
        return
    jetzt = _jetzt()
    for zeile in zeilen:
        if auftrag.last_run_id and str(zeile.run_id or "") != str(auftrag.last_run_id):
            # Eine Freigabe, die zu einem anderen Lauf gehoert, geht diesen
            # Auftrag nichts an.
            continue
        zeile.consumed_at = jetzt
        vorschlag = (
            db.query(AiActionProposal)
            .filter(AiActionProposal.id == zeile.proposal_id)
            .first()
        )
        if vorschlag is not None and vorschlag.status in ("proposed", "confirmed"):
            vorschlag.status = "expired"
            vorschlag.confirmation_token_hash = None
            vorschlag.error_code = "AI_APPROVAL_CAMPAIGN_ENDED"
    db.commit()


# ── Die Guardian-Leiter anhalten und wieder loslassen ─────────────────────


def _aussetzung_halten(db: Session, server: Server, auftrag: AiGuardianRepair) -> None:
    """Haelt Guardians eigene Heilungsleiter waehrend eines Eingriffs an.

    Ohne das arbeiten zwei gegeneinander: die KI schreibt eine Konfiguration um,
    und der Agent startet den Container mittendrin neu, weil seine Probe
    fehlschlaegt. Danach ist nicht mehr feststellbar, was gewirkt hat.

    Die ``operation_id`` ist die Kennung des Auftrags selbst — sie ist bereits
    eine kanonische UUID, und eine zweite Spalte dafuer waere eine zweite
    Kennung fuer denselben Vorgang. `clear_recovery_suspension` gibt nur frei,
    was dieselbe Kennung traegt; eine Aussetzung, die zu einer laufenden
    Serveroperation gehoert, bleibt damit unberuehrt.
    """
    from services import guardian_state_service

    try:
        guardian_state_service.set_recovery_suspension(
            db,
            server,
            operation_id=auftrag.id,
            reason=AUSSETZUNGSGRUND,
            suspend_until=_jetzt() + timedelta(minutes=AUSSETZUNG_MINUTEN),
        )
    except Exception as exc:  # noqa: BLE001 - eine Aussetzung ist kein Grund, den Lauf zu lassen
        logger.warning(
            "Guardian-Aussetzung nicht gesetzt repair_id=%s: %s",
            auftrag.id, type(exc).__name__,
        )


def _aussetzung_freigeben(db: Session, auftrag: AiGuardianRepair) -> None:
    """Gibt die Leiter wieder frei — in ``beobachtung`` und an jedem Ende.

    In ``beobachtung`` ist das kein Aufraeumen, sondern der Sinn der Phase: es
    sollen die Proben des Agenten entscheiden, ob der Eingriff gehalten hat, und
    nicht die Behauptung des Modells. Ein ausgesetzter Guardian koennte gar
    nichts mehr melden.
    """
    from services import guardian_state_service

    if auftrag.server_id is None:
        return
    server = db.get(Server, int(auftrag.server_id))
    if server is None:
        return
    try:
        guardian_state_service.clear_recovery_suspension(
            db, server, operation_id=auftrag.id
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Guardian-Aussetzung nicht freigegeben repair_id=%s: %s",
            auftrag.id, type(exc).__name__,
        )


# ── Quarantaene: nur gegen Nachweis ───────────────────────────────────────


def _vom_auftrag(
    db: Session, auftrag: AiGuardianRepair, vorschlag: AiActionProposal
) -> bool:
    """Gehoert dieser Vorschlag zu einem Lauf **dieses** Auftrags?

    Fenster, Server und Zeitraum reichen als Eingrenzung nicht: zwei parallele
    Auftraege am selben Server (verschiedene Vorfaelle, je ein Fingerprint)
    teilen sich alle drei. Die einzige Verbindung vom Vorschlag zum Auftrag
    fuehrt ueber seinen Lauf — jeder Vorschlag aus einem Lauf traegt dessen
    ``run_id``, und der Laufzustand traegt die ``repair_id``, ueber die auch
    `auftrag_aus_zustand` den Auftrag findet.
    """
    from services import ai_run_service

    if not vorschlag.run_id:
        return False
    run = db.get(AiRun, str(vorschlag.run_id))
    if run is None:
        return False
    rahmen = ai_run_service.zustand_lesen(run).get("guardian")
    if not isinstance(rahmen, dict):
        return False
    return str(rahmen.get("repair_id") or "") == str(auftrag.id)


def _vorschlaege_des_auftrags(
    db: Session, auftrag: AiGuardianRepair, *, status: str
) -> list[AiActionProposal]:
    """Alle Vorschlaege dieses Auftrags in einem Status, juengste zuerst.

    Fenster, Server und Zeitraum sind der billige SQL-Vorfilter; die ehrliche
    Zuordnung zum Auftrag macht `_vom_auftrag` je Kandidat. Die Kandidatenmenge
    ist klein — hoechstens eine Handvoll Schreibaktionen je Auftrag.
    """
    from models import AiConversation

    if auftrag.server_id is None:
        return []
    fenster = (
        db.query(AiConversation.id)
        .filter(
            AiConversation.user_id == int(auftrag.user_id),
            AiConversation.kind == "guardian",
        )
        .scalar()
    )
    if fenster is None:
        return []
    kandidaten = (
        db.query(AiActionProposal)
        .filter(
            AiActionProposal.conversation_id == str(fenster),
            AiActionProposal.server_id == int(auftrag.server_id),
            AiActionProposal.status == status,
            AiActionProposal.created_at >= _utc(auftrag.created_at),
        )
        .order_by(AiActionProposal.created_at.desc())
        .all()
    )
    return [zeile for zeile in kandidaten if _vom_auftrag(db, auftrag, zeile)]


def _eingriff_nachweisen(db: Session, auftrag: AiGuardianRepair) -> AiActionProposal | None:
    """Der Beleg, dass dieser Auftrag ueberhaupt etwas veraendert hat.

    Gesucht wird eine **ausgefuehrte** Schreibaktion aus einem Lauf **dieses**
    Auftrags. Nicht ein Satz des Modells, nicht ein Werkzeugaufruf, der
    abgelehnt wurde — eine Zeile, die sagt: hier wurde etwas getan.

    Die Zuordnung ueber den Lauf ist der Punkt: ohne sie zaehlte eine Aktion,
    die der Betreiber nebenher im Chat ausgeloest hat oder die ein **paralleler
    Auftrag** am selben Server ausgefuehrt hat, als Nachweis fuer den Eingriff
    dieses Auftrags — die Quarantaene fiele wegen einer Arbeit, die dieser
    Auftrag nie getan hat.

    Das ist der Nachweis, gegen den die Quarantaene aufgehoben wird. Ohne ihn
    hiesse "Quarantaene aufheben" nur, dem Agenten dieselbe Leiter noch einmal
    hochzuschicken, die er schon einmal bis zum Ende gelaufen ist.
    """
    zeilen = _vorschlaege_des_auftrags(db, auftrag, status="succeeded")
    return zeilen[0] if zeilen else None


def _sync_fehlschlaege(db: Session, auftrag: AiGuardianRepair) -> int:
    """Wie oft **dieser Auftrag** schon an der Agent-Synchronisation gescheitert ist.

    Gezaehlt werden ausgefuehrte, aber mit ``AI_ACTION_GUARDIAN_SYNC_FAILED``
    gescheiterte Vorschlaege aus den Laeufen dieses Auftrags — dieselbe
    Eingrenzung wie in `_eingriff_nachweisen`, nur mit umgekehrtem Vorzeichen.
    Der Fehlercode heisst: die Node hat die Konfiguration nicht quittiert, und
    die Uebersteuerung wurde zurueckgerollt.

    Bewusstes Restrisiko: der Zaehler unterscheidet nicht zwischen einer
    deterministischen Ablehnung und zwei zufaellig aufeinanderfolgenden
    transienten Fehlern (Netz, Node-Neustart). Auch dann ist Aufgeben
    vertretbar — der Vorfall bleibt offen und wird weiter gebrieft, und die
    Abschlussmail sagt ehrlich "nicht behoben".
    """
    return sum(
        1
        for zeile in _vorschlaege_des_auftrags(db, auftrag, status="failed")
        if zeile.error_code == "AI_ACTION_GUARDIAN_SYNC_FAILED"
    )


def _quarantaene_aufheben(db: Session, auftrag: AiGuardianRepair, server: Server) -> bool:
    """Bittet den Agenten, es noch einmal zu versuchen — mit Spur.

    ``quarantined`` ist der Zustand, in dem die Guardian-Engine aufgegeben hat,
    und von allein wechselt er nie. Solange er steht, ruehrt der Agent den
    Server nicht mehr an: kein Neustart, keine Probe, keine Vorfallaufloesung.
    Ein reparierter Server bliebe damit fuer immer als tot verbucht — und der
    Auftrag koennte nie belegen, dass sein Eingriff gewirkt hat.

    Aufgehoben wird deshalb genau dann, wenn zwei Dinge zusammenkommen: der
    Server steht in Quarantaene, **und** dieser Auftrag hat nachweislich etwas
    veraendert. Der Vorgang bekommt Audit-Eintrag und ChangeEvent wie der
    Panelknopf, denn es ist derselbe Vorgang — nur hat ihn niemand geklickt.
    """
    from services import guardian_state_service

    beleg = _eingriff_nachweisen(db, auftrag)
    if beleg is None:
        logger.info(
            "Quarantaene bleibt: kein ausgefuehrter Eingriff repair_id=%s", auftrag.id
        )
        return False

    if not guardian_state_service.prepare_quarantine_clear(server, operation_id=auftrag.id):
        # Schon angefordert — dieselbe Kennung, dieselbe Absicht. Kein zweiter
        # Audit-Eintrag fuer denselben Vorgang.
        return False

    audit_service.record_privileged_action(
        db,
        user_id=int(auftrag.user_id),
        action="guardian.quarantine.clear",
        target_type="server",
        target_id=int(server.id),
        details={
            "operation_id": auftrag.id,
            # Woher die Anforderung kommt, steht in den Details und nicht in
            # einem eigenen Aktionsnamen: es ist derselbe Vorgang wie am
            # Panelknopf, und wer nach ``guardian.quarantine.clear`` sucht, soll
            # beide finden.
            "source": "ai_guardian_repair",
            "incident_id": int(auftrag.incident_id),
            "proposal_id": str(beleg.id),
        },
        correlation_id=auftrag.id,
    )
    db.add(
        ChangeEvent(
            server_id=int(server.id),
            event_type="guardian_quarantine_clear",
            description=(
                "Der Assistent hat die Guardian-Quarantaene nach einem "
                "ausgefuehrten Eingriff zur Freigabe angefordert."
            ),
            details=json.dumps(
                {"operation_id": auftrag.id, "source": "ai_guardian_repair"},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )
    db.commit()
    db.refresh(server)
    # **Kein `sync_desired_state_to_agent` von hier aus.** Der Panelknopf ruft
    # es, weil er in einem Request-Thread sitzt; dieser Takt laeuft dagegen als
    # asyncio-Auftrag auf der Ereignisschleife der Anwendung, und ein
    # blockierender HTTPS-Aufruf zur Node hielte dort alles an — auch jeden
    # laufenden SSE-Strom.
    #
    # Noetig ist er auch nicht: `prepare_quarantine_clear` hat
    # `desired_state_generation` erhoeht, und die Guardian-Reconciliation traegt
    # den neuen Stand alle dreissig Sekunden zur Node — in einem eigenen Thread
    # (`asyncio.to_thread` in `reconcile_guardian_servers`). Dieselbe
    # Zurueckhaltung uebt die Aussetzung darueber, und ein Auftrag, der danach
    # ohnehin zehn Minuten beobachtet, merkt von einer halben Minute nichts.
    logger.info(
        "Quarantaene zur Freigabe angefordert repair_id=%s server_id=%s",
        auftrag.id, server.id,
    )
    return True


# ── Hat es gewirkt? ───────────────────────────────────────────────────────


def wirkung_belegt(db: Session, auftrag: AiGuardianRepair) -> tuple[bool, str]:
    """Zeigt die **Anlage**, dass der Vorfall erledigt ist?

    Drei Glieder, alle aus Paneldaten und keines aus dem Text des Modells:

    1. der Vorfall steht auf ``resolved`` — das schreibt der Agent, nicht die KI,
    2. der Server steht nicht in Quarantaene,
    3. sein beobachteter Zustand passt zu dem, was der Betreiber will.

    Der dritte Punkt ist der, den ein Modell am liebsten ueberspringt: ein
    ``docker start`` sagt nichts darueber, ob der Dienst danach noch laeuft.
    ``guardian_observed_state`` ist die Auskunft des Agenten nach seiner
    naechsten Probe — deshalb wird ueberhaupt beobachtet und nicht sofort
    geurteilt.

    Der Grund kommt mit zurueck, damit er im Log und im naechsten Auftragstext
    stehen kann. "Nicht belegt" ohne das Warum waere fuer den naechsten Anlauf
    wertlos.
    """
    vorfall = db.get(Incident, int(auftrag.incident_id))
    if vorfall is None:
        # Der Vorfall wurde abgeraeumt. Dann gibt es nichts mehr zu belegen und
        # nichts mehr zu tun.
        return True, "vorfall_entfernt"
    db.refresh(vorfall)
    if str(vorfall.status) != "resolved":
        return False, f"vorfall_{vorfall.status}"

    if auftrag.server_id is None:
        return False, "server_entfernt"
    server = db.get(Server, int(auftrag.server_id))
    if server is None:
        return False, "server_entfernt"
    db.refresh(server)
    if str(server.guardian_quarantine_status or "") == "quarantined":
        return False, "quarantaene"

    gewollt = str(server.desired_power_state or "stopped")
    beobachtet = str(server.guardian_observed_state or "unknown")
    if gewollt == "running" and beobachtet not in ("healthy", "running"):
        return False, f"zustand_{beobachtet}"
    return True, "belegt"


# ── Das Ende eines Laufs ──────────────────────────────────────────────────


def lauf_beendet(db: Session, run: AiRun, zustand: dict) -> None:
    """Traegt das Ergebnis eines Laufs in seinen Auftrag — und plant den naechsten.

    Gerufen aus `_lauf_nachbereiten`, also nach jedem Endzustand eines Laufs und
    **vor** der Abschlussmail. Die Reihenfolge ist tragend: die Mail fragt
    gleich danach, ob der Auftrag noch laeuft, und wuerde sonst eine Phase
    lesen, die noch nicht geschrieben ist.

    Kapselt alles ab. Der Lauf ist zu diesem Zeitpunkt fertig und committet; ein
    Fehler hier darf ihn nicht nachtraeglich in einen Fehlschlag verwandeln.
    """
    try:
        auftrag = auftrag_aus_zustand(db, zustand)
        if auftrag is None or auftrag.phase in ENDPHASEN:
            return
        _erkenntnisse_uebernehmen(db, auftrag, run, zustand)
        _naechste_phase_setzen(db, auftrag, run)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning(
            "Reparaturauftrag nicht fortgeschrieben run_id=%s: %s",
            run.id, type(exc).__name__,
        )


def _erkenntnisse_uebernehmen(
    db: Session, auftrag: AiGuardianRepair, run: AiRun, zustand: dict
) -> None:
    """Nimmt den Abschlusstext des Laufs als Gedaechtnis des Auftrags mit.

    **Von hinten geschnitten**, wie in `ai_task_report.abschlusstext`: vorne
    stehen die Ansagen vor jedem Werkzeugaufruf ("ich sehe mir jetzt die Logs
    an"), hinten das Ergebnis. Ein Reparaturlauf hat mehr Runden als ein
    Chatlauf, also mehr Ansagen — von vorne geschnitten truege der naechste
    Anlauf ausgerechnet die Ankuendigungen mit.

    Geschwaerzt, weil das Modell Logzeilen zitiert haben kann, und die stammen
    von einem Server, auf dem Fremde spielen.
    """
    from services.ai_task_report import abschlusstext

    # `last_run_id` steht schon (gesetzt beim Start) und wird hier **nicht**
    # nachgezogen: sonst zeigte er bei einem Lauf ohne Abschlusstext auf den
    # vorletzten Anlauf, und `_schlussbericht` berichtete ueber den falschen.
    text = abschlusstext(db, run, zustand) or ""
    if not text.strip():
        return
    auftrag.erkenntnisse = redact_sensitive_text(text.strip())[-MAX_ERKENNTNISSE:]
    auftrag.updated_at = _jetzt()
    db.commit()


def _naechste_phase_setzen(db: Session, auftrag: AiGuardianRepair, run: AiRun) -> None:
    """Die Leiter: was nach diesem Lauf kommt.

    Die Reihenfolge der Pruefungen ist die Aussage. **Zuerst** die Frage, ob es
    schon gut ist — ein Auftrag, dessen Wirkung belegt ist, endet auch dann,
    wenn er formal noch Versuche haette. **Danach** Frist und Deckel: sie sind
    die Bremse, nicht das Ziel.

    Der Endzustand des Laufs selbst kommt in dieser Leiter bewusst **nicht**
    vor. ``budget`` heisst "die KI hatte noch etwas vor, durfte aber nicht mehr"
    — das ist ein Grund fuer den naechsten Anlauf und kein Ergebnis. Und
    ``superseded`` heisst, dass jemand dazwischengefunkt hat; auch das sagt
    nichts ueber den Server.
    """
    jetzt = _jetzt()
    belegt, grund = wirkung_belegt(db, auftrag)

    if belegt and auftrag.phase == "beobachtung":
        _abschliessen(db, auftrag, phase="erledigt", grund=grund)
        return
    if belegt and auftrag.phase != "beobachtung":
        # Erledigt sieht es aus — aber noch nicht beobachtet. Genau hier hat das
        # Modell im Betrieb aufgehoert: Container laeuft, Vorfall zu, fertig.
        # Zehn Minuten spaeter stand der Server wieder. Eine Runde Zusehen
        # kostet nichts als Zeit.
        _phase_setzen(db, auftrag, "beobachtung", jetzt + timedelta(minutes=BEOBACHTUNG_MINUTEN))
        return

    if _utc(auftrag.deadline_at) is not None and _utc(auftrag.deadline_at) <= jetzt:
        _abschliessen(db, auftrag, phase="aufgegeben", grund=f"frist_{grund}")
        return
    if int(auftrag.attempt or 0) >= MAX_VERSUCHE:
        _abschliessen(db, auftrag, phase="aufgegeben", grund=f"versuche_{grund}")
        return
    if _sync_fehlschlaege(db, auftrag) >= 2:
        # Zweimal hat die Node dieselbe Konfiguration nicht quittiert. Das ist
        # kein Zustand, den ein weiterer KI-Anlauf beheben kann: die
        # Uebersteuerung wird nach jedem Fehlschlag zurueckgerollt, der
        # naechste Lauf sieht denselben Ausgangszustand und schlaegt dasselbe
        # vor. Die Leiter ist sonst bewusst ergebnisblind (siehe Docstring) —
        # dieser eine Fehlercode ist die Ausnahme, weil er deterministisch
        # ist. Ohne den Riegel liefen bis zu acht zahlungspflichtige Anlaeufe
        # im 13-Minuten-Takt gegen dieselbe Wand (Vorfall 66, 20.08.2026).
        # Der Vorfall bleibt offen und wird weiter gebrieft; nur der teure
        # Heilungsversuch endet.
        _abschliessen(db, auftrag, phase="aufgegeben", grund="agent_sync")
        return

    if auftrag.phase == "diagnose":
        naechste, wann = "eingriff", jetzt + timedelta(seconds=WIEDERANLAUF_SEKUNDEN)
    elif auftrag.phase == "eingriff":
        naechste, wann = "beobachtung", jetzt + timedelta(minutes=BEOBACHTUNG_MINUTEN)
    else:
        # Beobachtet und nicht belegt: der Eingriff hat nicht gehalten. Zurueck
        # an die Arbeit — mit dem, was der Auftrag inzwischen weiss.
        naechste, wann = "eingriff", jetzt + timedelta(seconds=WIEDERANLAUF_SEKUNDEN)
    _phase_setzen(db, auftrag, naechste, wann)
    logger.info(
        "Reparaturauftrag geht weiter repair_id=%s phase=%s grund=%s versuch=%s/%s",
        auftrag.id, naechste, grund, auftrag.attempt, MAX_VERSUCHE,
    )


def _phase_setzen(
    db: Session, auftrag: AiGuardianRepair, phase: str, wann: datetime
) -> None:
    auftrag.phase = phase
    auftrag.next_run_at = wann
    auftrag.updated_at = _jetzt()
    db.commit()
    if phase == "beobachtung":
        # Jetzt sollen die Proben des Agenten entscheiden. Ein ausgesetzter
        # Guardian koennte gar nichts melden — und der Auftrag wartete zehn
        # Minuten auf eine Auskunft, die er selbst verhindert hat.
        _aussetzung_freigeben(db, auftrag)


# ── Der Takt ──────────────────────────────────────────────────────────────


async def faellige_bearbeiten(db: Session) -> int:
    """Ein Durchlauf: faellige Auftraege wecken. Gibt zurueck, wieviele liefen.

    Laeuft im selben Sechzig-Sekunden-Auftrag wie `vorfaelle_bearbeiten`, direkt
    danach: ein frisch angelegter Auftrag ist sofort faellig und faengt damit im
    selben Takt an, statt eine Minute zu warten.

    Alles ist je Zeile abgesichert — ein kaputter Auftrag darf die uebrigen
    nicht mitnehmen, und nach oben darf gar nichts durchschlagen: der Takt
    laeuft neben der Guardian-Reconciliation, und ein abgebrochener
    Scheduler-Auftrag zieht keine Vorfaelle mehr ein.
    """
    from services import ai_run_service

    if ai_run_service.http_client() is None:
        # Keine laufende Anwendung, also keine Ereignisschleife, auf der ein
        # Segment laufen koennte. Die Termine bleiben stehen.
        return 0

    jetzt = _jetzt()
    zeilen = (
        db.query(AiGuardianRepair)
        .filter(
            AiGuardianRepair.phase.notin_(ENDPHASEN),
            AiGuardianRepair.next_run_at.isnot(None),
            AiGuardianRepair.next_run_at <= jetzt,
        )
        .order_by(AiGuardianRepair.next_run_at.asc())
        .limit(MAX_ZEILEN_JE_DURCHLAUF)
        .all()
    )

    begonnen = 0
    for auftrag in zeilen:
        if begonnen >= MAX_LAEUFE_JE_DURCHLAUF:
            break
        try:
            begonnen += await _einen_auftrag_bearbeiten(db, auftrag, jetzt=jetzt)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.warning(
                "Reparaturauftrag fehlgeschlagen (repair_id=%s): %s",
                getattr(auftrag, "id", "?"), type(exc).__name__,
            )
    return begonnen


async def _einen_auftrag_bearbeiten(
    db: Session, auftrag: AiGuardianRepair, *, jetzt: datetime
) -> int:
    """Ein faelliger Auftrag: beenden, vertagen oder einen Lauf beginnen."""
    from services import ai_guardian_service

    gelesen = auftrag.next_run_at

    # **Frist und Deckel vor dem Anspruch.** Ein Auftrag, der ohnehin nicht mehr
    # laufen darf, soll keinen Versuch verbrauchen — der Zaehler haengt am
    # Anspruch, und ein abgelaufener Auftrag saehe sonst so aus, als haette er
    # einen Anlauf mehr gehabt, als er hatte.
    if _utc(auftrag.deadline_at) is not None and _utc(auftrag.deadline_at) <= jetzt:
        # **`eskaliert` und nicht `aufgegeben`, wenn eine Frage offen war.**
        # Die beiden Ausgaenge sagen etwas Verschiedenes: "aufgegeben" heisst,
        # die KI kam nicht weiter; "eskaliert" heisst, sie kam bis zu einer
        # Entscheidung, die ein Mensch treffen musste, und er hat sie in der
        # Frist nicht getroffen. Fuer den Betreiber ist der Unterschied der
        # ganze Inhalt der Mail — im einen Fall ist die Anlage das Problem, im
        # anderen sein eigenes Postfach.
        wartete = _wartet_auf_freigabe(db, auftrag)
        _abschliessen(
            db, auftrag,
            phase="eskaliert" if wartete else "aufgegeben",
            grund="frist_freigabe" if wartete else "frist",
        )
        _schlussbericht(db, auftrag)
        return 0
    if int(auftrag.attempt or 0) >= MAX_VERSUCHE:
        _abschliessen(db, auftrag, phase="aufgegeben", grund="versuche")
        _schlussbericht(db, auftrag)
        return 0

    server = db.get(Server, int(auftrag.server_id)) if auftrag.server_id else None
    vorfall = db.get(Incident, int(auftrag.incident_id))
    if server is None or vorfall is None:
        # Der Server oder der Vorfall wurde geloescht, waehrend der Auftrag
        # lief. Ordentlich beenden statt die Zeile liegen zu lassen — sonst
        # sieht der Takt sie in jeder Minute erneut.
        _abschliessen(db, auftrag, phase="abgebrochen", grund="ziel_entfernt")
        return 0

    user = db.get(User, int(auftrag.user_id))
    if user is None or not user.is_active:
        _abschliessen(db, auftrag, phase="abgebrochen", grund="freigeber_entfernt")
        return 0

    # **Die Freigabe wird bei jedem Anlauf neu geprueft**, nicht einmal beim
    # Anlegen. Ein Betreiber, der den Autonom-Schalter um drei Uhr umlegt, hat
    # damit ab drei Uhr recht — und nicht erst, wenn der Auftrag von selbst
    # ausgeht. Geprueft wird ueber dieselbe Funktion wie beim ersten Mal, damit
    # es keine zweite Auslegung derselben Frage gibt.
    if ai_guardian_service.zustaendiger_freigeber(db, server) is None:
        _abschliessen(db, auftrag, phase="abgebrochen", grund="freigabe_zurueckgenommen")
        _schlussbericht(db, auftrag)
        return 0

    # Laeuft der vorige Lauf noch? Dann nur die Leine verlaengern. Das ist der
    # Normalfall bei einem langen Lauf: der Anspruch setzt den naechsten Weckruf
    # fuenf Minuten voraus, und ein Lauf darf laenger dauern.
    if _lauf_laeuft_noch(db, auftrag):
        _anspruch_nehmen(
            db, auftrag,
            gelesen=gelesen,
            neu=jetzt + timedelta(seconds=LAUFENDER_LAUF_SEKUNDEN),
        )
        return 0

    if not _anspruch_nehmen(
        db, auftrag,
        gelesen=gelesen,
        neu=jetzt + timedelta(seconds=LAUFENDER_LAUF_SEKUNDEN),
    ):
        # Ein anderer Durchlauf war schneller.
        return 0

    if auftrag.phase == "eingriff":
        # Erst die Leiter anhalten, dann eingreifen. Andersherum startet der
        # Agent den Container mitten in einer halb geschriebenen Datei neu.
        _aussetzung_halten(db, server, auftrag)
        if str(server.guardian_quarantine_status or "") == "quarantined":
            _quarantaene_aufheben(db, auftrag, server)

    run = await ai_guardian_service.heilungslauf_starten(
        db, server=server, vorfall=vorfall, user=user, auftrag=auftrag
    )
    if run is None:
        # Kein Anbieter, kein Kontingent, oder es laeuft schon eine andere
        # Reparatur dieses Freigebers. `heilungslauf_starten` sagt zu: ``None``
        # heisst, es wurde nichts angelegt und nichts verbraucht.
        #
        # Dann darf es auch **keinen Versuch** gekostet haben. Der Zaehler ist
        # beim Anspruch mitgestiegen, weil ein Lauf, der den Prozess mit sich
        # reisst, sonst gratis waere; hier geht er wieder zurueck. Ohne das
        # frisst ein Panel ohne eingerichteten Anbieter den Deckel von acht
        # Anlaeufen in acht Minuten auf, ohne je einen Token ausgegeben zu
        # haben — und meldet danach "aufgegeben".
        #
        # Faellig bleibt er **sofort**: der naechste Takt ist in sechzig
        # Sekunden, und genau das ist der richtige Abstand fuer einen Grund,
        # der bis dahin wegfallen kann. Ein Nachschlag von neunzig Sekunden
        # liesse ihn stattdessen einen ganzen Takt aussetzen.
        auftrag.attempt = max(0, int(auftrag.attempt or 0) - 1)
        auftrag.next_run_at = jetzt
        auftrag.updated_at = _jetzt()
        db.commit()
        return 0

    # **Der Verweis auf den laufenden Anlauf — hier und nicht spaeter.**
    #
    # Er traegt zwei Dinge: `_lauf_laeuft_noch` erkennt daran, dass es noch
    # nicht Zeit fuer den naechsten ist, und `_schlussbericht` weiss, ueber
    # welchen Lauf er berichten soll, wenn die Frist im Takt ablaeuft. Wuerde er
    # erst beim Abschluss des Laufs gesetzt, faende der Takt in der Zwischenzeit
    # einen Auftrag ohne Lauf — und startete den naechsten obendrauf.
    auftrag.last_run_id = run.id
    auftrag.updated_at = _jetzt()
    db.commit()
    return 1


def _lauf_laeuft_noch(db: Session, auftrag: AiGuardianRepair) -> bool:
    """Arbeitet der zuletzt gestartete Lauf dieses Auftrags noch?"""
    if not auftrag.last_run_id:
        return False
    run = db.get(AiRun, str(auftrag.last_run_id))
    if run is None:
        return False
    return str(run.status) in ("running", *WARTEND)


def _schlussbericht(db: Session, auftrag: AiGuardianRepair) -> None:
    """Die Mail, wenn der Auftrag ohne einen letzten Lauf zu Ende geht.

    Der Normalweg ist ein anderer: der letzte Lauf endet, `lauf_beendet` setzt
    die Endphase, und die Nachbereitung dieses Laufs verschickt. Endet der
    Auftrag dagegen **im Takt** — Frist abgelaufen, Freigabe zurueckgenommen —,
    hat kein Lauf mehr die Gelegenheit dazu, und der Betreiber erfuehre nichts.

    Berichtet wird ueber den letzten Lauf, den es gab. Gibt es keinen, gibt es
    auch nichts zu berichten: dann ist nie etwas passiert.
    """
    from services import ai_guardian_report, ai_run_service

    if not auftrag.last_run_id:
        return
    run = db.get(AiRun, str(auftrag.last_run_id))
    if run is None:
        return
    zustand = ai_run_service.zustand_lesen(run)
    if zustand.get("guardian_berichtet"):
        return
    zustand["guardian_berichtet"] = True
    ai_run_service.zustand_schreiben(run, zustand)
    db.commit()
    try:
        ai_guardian_report.bericht_versenden(db, run=run, zustand=zustand)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning(
            "Schlussbericht nicht zugestellt repair_id=%s: %s",
            auftrag.id, type(exc).__name__,
        )


# ── Ein Mensch uebernimmt ─────────────────────────────────────────────────


def uebernehmen(db: Session, *, user: User) -> int:
    """Bricht die laufenden Reparaturen dieses Benutzers ab. Gibt die Anzahl zurueck.

    Der Knopf „Übernehmen" im Guardian-Fenster. Er ist der Grund, warum es dort
    kein Eingabefeld gibt: abbrechen soll man koennen, aber ausdruecklich und
    nicht durch einen Tastendruck.

    Abgebrochen wird der **Auftrag**, nicht nur der Lauf. Nur den Lauf zu
    beenden hiesse, dass der Takt neunzig Sekunden spaeter den naechsten
    startet — der Mensch haette uebernommen und die KI arbeitete weiter.
    """
    auftraege = (
        db.query(AiGuardianRepair)
        .filter(
            AiGuardianRepair.user_id == int(user.id),
            AiGuardianRepair.phase.notin_(ENDPHASEN),
        )
        .all()
    )
    for auftrag in auftraege:
        _abschliessen(db, auftrag, phase="abgebrochen", grund="uebernommen")
    return len(auftraege)
