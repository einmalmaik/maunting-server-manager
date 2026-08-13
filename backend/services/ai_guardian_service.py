"""Guardian meldet, die KI antwortet — mit oder ohne Freigabe.

Dieses Modul ist die Bruecke zwischen zwei Systemen, die sich bisher nicht
kannten. Die Guardian-Engine erkennt Stoerungen und heilt sie nach einer festen
Leiter aus dem Blueprint; was sie nicht versteht, endet in Quarantaene. Die KI
kann lesen, Dateien aendern, Server starten und Backups anlegen — aber nur, wenn
ein Mensch tippt.

Hier laufen beide zusammen, in zwei Zweigen:

* **Ohne Freigabe** — es passiert *nichts*. Kein Lauf, kein Anbieteraufruf, kein
  Token. Der Vorfall wird nur vorgemerkt, und die naechste Frage des Benutzers
  bekommt ihn als Hinweis in den Kontext. Das ist die ausdrueckliche Vorgabe:
  ohne Freigabe hat die KI Lesezugriff und informiert beim naechsten Chat.
* **Mit Freigabe** — ein Heilungslauf startet, ohne dass jemand davorsitzt.

Drei Entscheidungen tragen alles Weitere, und alle drei sind bewusst getroffen:

**Der Akteur ist der Freigeber.** Nicht ein Dienstbenutzer des Betreibers, nicht
der Owner. Eine Rechtepruefung gegen einen Account, den jemand aussuchen kann,
ist keine Schranke — das ist in diesem Projekt schon einmal als Befund
aufgeschlagen. Gehandelt wird als der Mensch, der fuer diesen Server den
Autonom-Schalter umgelegt hat: seine Rechte sind die Grenze, sein Kontingent
wird verbraucht, er bekommt die Mail. Die KI kann damit nie etwas, was dieser
Mensch nicht selbst duerfte und nicht selbst freigegeben hat.

**Der Ausloeser ist ein eigener Auftrag.** Nicht ein Aufruf in
`ingest_incidents_and_ack`: dort laeuft der Benachrichtigungsblock ungeschuetzt
zwischen dem Commit der Vorfaelle und der Quittierung an den Agenten. Eine
Ausnahme an dieser Stelle verhindert das ACK, und der Agent liefert denselben
Vorfall dann fuer immer erneut. Ein KI-Lauf hat in dieser Luecke nichts zu
suchen.

**Geheilt wird im gewoehnlichen Chat.** Es gibt genau eine Unterhaltung je
Benutzer, und eine Heilung schreibt hinein wie ein Mensch. Damit sie sich nicht
gegenseitig abwuergen, startet eine Heilung ausschliesslich dann, wenn dort
gerade nichts laeuft. Umgekehrt gilt die vorhandene Regel unveraendert: schreibt
der Mensch waehrend einer Heilung, loest er sie ab. Das ist richtig so — was die
KI bis dahin getan hat, steht im Verlauf, und ein "mach weiter" genuegt.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from models import (
    AiGuardianNotice,
    AiRun,
    Incident,
    Server,
    User,
)
from services import ai_chat_service, ai_context_window, ai_provider_service
from services import ai_reasoning, ai_run_service
from services import permission_service
from services.ai_autonomy_service import resolve_grant
from services.ai_redaction import redact_sensitive_text


logger = logging.getLogger(__name__)

#: Vorfallzustaende, die noch etwas von jemandem wollen. `resolved` ist erledigt,
#: und `verifying` laeuft gerade — der Agent prueft dort selbst nach, ob seine
#: Massnahme gegriffen hat, und ein Eingriff mittendrin waere ein Rennen.
OFFENE_ZUSTAENDE = ("open", "recovering", "quarantined")

#: Wieviele Vorfaelle ein Durchlauf hoechstens anfasst. Faellt eine ganze Node
#: aus, meldet Guardian im selben Takt dutzende Vorfaelle; jeder davon wuerde
#: einen eigenen Lauf mit eigenen Kosten ausloesen. Die uebrigen bleiben offen
#: und kommen im naechsten Takt dran — nichts geht verloren, es geht nur der
#: Reihe nach.
MAX_VORFAELLE_JE_DURCHLAUF = 5

#: Wieviele Vorfaelle dem Benutzer in einer Chatnachricht genannt werden.
MAX_BRIEFING = 5


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _utc(wert: datetime) -> datetime:
    return wert.replace(tzinfo=timezone.utc) if wert.tzinfo is None else wert


# ── Wer ist zustaendig? ───────────────────────────────────────────────────


def zustaendiger_freigeber(db: Session, server: Server) -> User | None:
    """Der Benutzer, in dessen Namen dieser Server autonom geheilt werden darf.

    Vier Bedingungen, alle gleichzeitig — dieselben, die auch ein getippter
    autonomer Auftrag erfuellen muss, plus die beiden, die sonst nur der Router
    prueft:

    1. eine **aktive** Freigabe, die diesen Server deckt,
    2. `ai.autonomous.use` und `ai.chat.use` (die stehen sonst als
       `require_global` am Endpunkt und liefen hier nie),
    3. `server.view` auf diesen Server,
    4. der Benutzer ist aktiv.

    Die Freigabe wird ueber `resolve_grant` gesucht und **nie** ueber eine eigene
    Abfrage auf `ai_autonomy_grants`. Der Grund steht dort im Docstring: die
    Funktion filtert bewusst nicht auf `enabled`, damit ein gezielt
    abgeschalteter Server-Grant die panelweite Freigabe ueberstimmt. Wer hier
    selbst abfragt und dabei `enabled == True` filtert, macht aus dem "auf diesem
    Server ausdruecklich nicht" ein "dann eben panelweit" — und heilt genau den
    Server autonom, den der Betreiber davon ausgenommen hat.

    Bei mehreren Kandidaten gewinnt der mit der **serverbezogenen** Freigabe: wer
    sie eigens fuer diesen Server erteilt hat, hat konkreter zugestimmt als
    jemand mit einer panelweiten. Bei Gleichstand die kleinste Benutzernummer —
    eine Regel, die keine Rolle spielt, aber jeden Durchlauf gleich entscheidet.
    """
    from models import AiAutonomyGrant

    from sqlalchemy import or_

    kandidaten = (
        db.query(User)
        .join(AiAutonomyGrant, AiAutonomyGrant.user_id == User.id)
        .filter(
            User.is_active.is_(True),
            AiAutonomyGrant.enabled.is_(True),
            # `IS NULL OR = id` und **nicht** `IN (None, id)`.
            #
            # Hier stand `.in_((None, server.id))`. Das liest sich, als deckte
            # es beide Faelle ab, und tut in SQL das Gegenteil: `x IN (NULL, 5)`
            # ist fuer `x = NULL` nicht wahr, sondern unbekannt — die Zeile
            # faellt heraus. `server_id IS NULL` ist aber genau die **panelweite**
            # Freigabe, also die, die der Schalter im KI-Chat standardmaessig
            # setzt (`PANEL_SCOPE` in AiAutonomyButton.tsx).
            #
            # Wirkung: wer die Autonomie panelweit erteilt hatte, bekam nie eine
            # autonome Heilung. Der Schalter stand auf an, das Panel zeigte ihn
            # als an, und es passierte nichts — ohne Log, ohne Fehler. Nur wer
            # sie eigens je Server erteilt hatte, wurde ueberhaupt gefunden.
            #
            # Aufgefallen ist das keinem Baustein-Test, weil alle mit einer
            # serverbezogenen Freigabe arbeiteten, und keiner der drei
            # Pruefungslinsen. Erst ein Durchlauf der ganzen Kette mit einer
            # panelweiten Freigabe hat es gezeigt.
            or_(
                AiAutonomyGrant.server_id.is_(None),
                AiAutonomyGrant.server_id == server.id,
            ),
        )
        .order_by(User.id)
        .distinct()
        .all()
    )

    passend: list[tuple[int, int, User]] = []
    for user in kandidaten:
        grant = resolve_grant(db, user_id=user.id, server_id=server.id)
        # `resolve_grant` kann eine **abgeschaltete** serverbezogene Zeile
        # liefern, obwohl die Abfrage oben nur aktivierte gefunden hat. Genau
        # das ist der Fall, den es zu treffen gilt: panelweit erlaubt, auf
        # diesem Server ausdruecklich nicht.
        if grant is None or not grant.enabled or grant.max_actions_per_hour <= 0:
            continue
        if not permission_service.has_global_permission(db, user, "ai.autonomous.use"):
            continue
        # `ai.chat.use` haengt sonst als `require_global` am Endpunkt. Ohne
        # Request laeuft es nicht, und ein Lauf ohne dieses Recht waere ein
        # Zugang zur KI an der Rechteverwaltung vorbei.
        if not permission_service.has_global_permission(db, user, "ai.chat.use"):
            continue
        if not permission_service.has_server_permission(db, user, server.id, "server.view"):
            continue
        # 0 = eigens fuer diesen Server erteilt, 1 = panelweit.
        passend.append((0 if grant.server_id is not None else 1, user.id, user))

    if not passend:
        return None
    passend.sort(key=lambda eintrag: (eintrag[0], eintrag[1]))
    return passend[0][2]


# ── Notizen: was wurde zu welchem Vorfall schon veranlasst? ───────────────


def _notiz_anlegen(
    db: Session, *, incident_id: int, user_id: int, mode: str, run_id: str | None
) -> bool:
    """Legt die Notiz an. ``False`` heisst: es gab schon eine.

    Die Eindeutigkeit liegt in der Datenbank (`uq_ai_guardian_notices_incident_user`),
    nicht in dieser Pruefung. Sie ist der Grund, warum der Takt einen Vorfall
    nicht alle sechzig Sekunden erneut aufgreift — und sie haelt auch dann, wenn
    das Panel je mit mehreren Arbeitsprozessen laeuft und es den Auftrag mehrfach
    gibt.

    **Eine Ausnahme, und nur diese:** `briefed` laesst sich zu `healing`
    hochstufen. Der Briefingpfad kennt die Freigabe nicht und markiert Vorfaelle
    auch fuer Benutzer, die sie autonom heilen lassen duerften; ohne die
    Hochstufung entschiede die Reihenfolge zweier Ereignisse im
    Sekundenabstand darueber, ob ein Server nachts wieder hochkommt. Umgekehrt
    gilt es nicht: eine begonnene Heilung wird nie zu einer blossen Erwaehnung
    zurueckgestuft.
    """
    from sqlalchemy.exc import IntegrityError

    db.add(AiGuardianNotice(
        incident_id=incident_id, user_id=user_id, mode=mode, run_id=run_id
    ))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        if mode != "healing":
            return False
        vorhanden = (
            db.query(AiGuardianNotice)
            .filter(
                AiGuardianNotice.incident_id == incident_id,
                AiGuardianNotice.user_id == user_id,
            )
            .first()
        )
        if vorhanden is None or vorhanden.mode == "healing":
            return False
        vorhanden.mode = "healing"
        vorhanden.run_id = run_id
        db.flush()
        return True
    return True


def offene_briefings(db: Session, user: User) -> list[Incident]:
    """Vorfaelle, die dieser Benutzer noch nicht genannt bekommen hat.

    Gefragt beim Aufbau des Kontexts einer neuen Chatnachricht. Zurueck kommen
    nur Vorfaelle zu Servern, die der Benutzer sehen darf — die Rechtepruefung
    laeuft je Zeile und nicht als Filter in der Abfrage, weil Sichtbarkeit aus
    Rollenrechten *und* einzeln delegierten Serverrechten entsteht.
    """
    schon = {
        zeile[0]
        for zeile in db.query(AiGuardianNotice.incident_id)
        .filter(AiGuardianNotice.user_id == user.id)
        .all()
    }
    treffer: list[Incident] = []
    zeilen = (
        db.query(Incident)
        .filter(Incident.status.in_(OFFENE_ZUSTAENDE))
        .order_by(Incident.created_at.desc())
        .limit(MAX_BRIEFING * 4)
        .all()
    )
    for vorfall in zeilen:
        if vorfall.id in schon:
            continue
        if not permission_service.has_server_permission(
            db, user, vorfall.server_id, "server.view"
        ):
            continue
        treffer.append(vorfall)
        if len(treffer) >= MAX_BRIEFING:
            break
    return treffer


def briefing_nachricht(db: Session, user: User) -> tuple[str, list[int]] | None:
    """Der Hinweisblock fuer den Kontext einer neuen Chatnachricht.

    Aufgebaut wie `_aktionsmeldung` im Stream: ausdruecklich als Meldung des
    Panels und nicht als Satz des Benutzers, damit das Modell nicht meint, der
    Mensch haette es darum gebeten. Und mit derselben `untrusted`-Markierung wie
    Werkzeugergebnisse.

    Enthalten sind **nur Paneldaten**: Servernummer, Name, Art, Stand, Anzahl,
    Zeitpunkt. Kein Wort aus der Beschreibung des Vorfalls — die stammt vom
    Agenten auf einem Server, auf dem Fremde spielen, und der Kontext einer
    Nachricht ist die Stelle mit dem meisten Gewicht in einem Lauf. Was das
    Modell ueber die Ursache wissen will, holt es sich mit
    `read_guardian_incidents`; dort kommt derselbe Text geschwaerzt und
    ausdruecklich unvertrauenswuerdig an.

    ``None`` heisst: nichts Offenes, kein Block, keine Zeichen im Kontext.
    """
    import json

    vorfaelle = offene_briefings(db, user)
    if not vorfaelle:
        return None
    zeilen = []
    for vorfall in vorfaelle:
        server = db.get(Server, vorfall.server_id)
        zeilen.append({
            "server_id": vorfall.server_id,
            "server": redact_sensitive_text(str(getattr(server, "name", "")))[:64],
            "incident_id": vorfall.id,
            "type": vorfall.type,
            "status": vorfall.status,
            "occurrences": vorfall.occurrences,
            "since": _utc(vorfall.created_at).isoformat() if vorfall.created_at else None,
        })
    nutzlast = json.dumps(
        {"untrusted": True, "tool": "guardian_incidents", "data": {"open": zeilen}},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    text = (
        "Meldung des Panels (nicht vom Benutzer geschrieben): die "
        "Guardian-Engine hat seit dem letzten Gespraech Stoerungen erkannt, die "
        "der Benutzer noch nicht kennt. Nenne sie ihm zu Beginn deiner Antwort "
        "in einem Satz je Server und biete an, sie dir anzusehen. Handle nicht "
        "von dir aus.\n" + nutzlast
    )
    return text, [vorfall.id for vorfall in vorfaelle]


def briefings_abschliessen(db: Session, *, user_id: int, incident_ids: list[int]) -> None:
    """Vermerkt, dass diese Vorfaelle genannt wurden.

    Gerufen wird das erst beim **Ende** des Laufs. Bricht er vorher ab, bleibt
    der Vorfall vorgemerkt und kommt beim naechsten Mal wieder — lieber zweimal
    genannt als einmal verschluckt.
    """
    for incident_id in incident_ids:
        _notiz_anlegen(
            db, incident_id=int(incident_id), user_id=user_id, mode="briefed", run_id=None
        )


# ── Der Heilungslauf ──────────────────────────────────────────────────────


def _auftragstext(server: Server, vorfall: Incident) -> str:
    """Was der KI als Auftrag in den Chat geschrieben wird.

    **Ausschliesslich aus Paneldaten.** Kein Wort aus der Beschreibung des
    Vorfalls, obwohl sie danebensteht — die stammt vom Agenten auf einem Server,
    auf dem Fremde spielen, und ein Auftragstext ist die Stelle mit dem meisten
    Gewicht, die es in einem Lauf gibt. Was das Modell ueber die Ursache wissen
    will, holt es sich selbst mit `read_guardian_incidents` und
    `read_server_logs`; dort kommt es als ausdruecklich unvertrauenswuerdiges
    Werkzeugergebnis an, geschwaerzt und als solches markiert.

    Der Servername ist Betreibertext und wird trotzdem geschwaerzt und gekuerzt —
    er kann aus einer Shop-Bestellung stammen.
    """
    name = redact_sensitive_text(str(server.name or ""))[:64]
    return (
        f"Die Guardian-Engine meldet eine Stoerung auf Server {server.id} "
        f'("{name}"): Vorfall {vorfall.id} vom Typ "{vorfall.type}", '
        f"Status {vorfall.status}, bisher {vorfall.occurrences}-mal aufgetreten.\n\n"
        "Niemand sitzt gerade davor. Untersuche die Ursache mit den "
        "Lesewerkzeugen, sieh dir an, was Guardian selbst schon versucht hat, "
        "und behebe das Problem, wenn du es verstanden hast. Lege vor jedem "
        "Eingriff in Dateien ein Backup an. Pruefe am Ende, ob der Server "
        "wirklich laeuft, und fasse in wenigen Saetzen zusammen, was die Ursache "
        "war und was du getan hast — diese Zusammenfassung geht als E-Mail an "
        "den Betreiber. Kommst du nicht weiter, sag das deutlich und nenne "
        "deine Vermutung."
    )


async def heilungslauf_starten(
    db: Session, *, server: Server, vorfall: Incident, user: User
) -> AiRun | None:
    """Startet einen Lauf zu diesem Vorfall — oder gibt ``None`` zurueck.

    Baut nach, was sonst der Streamendpunkt tut: Unterhaltung holen, Anbieter
    waehlen, Denkstufe und Kontextfenster ermitteln, Lauf anlegen, starten. Die
    Vorbereitung ist bewusst **hier** und nicht in `lauf_beginnen` — die
    Funktion nimmt schon heute nur einfache Objekte und keinen Request, sie
    musste dafuer nicht angefasst werden.

    ``None`` heisst immer: es wurde nichts angelegt und nichts verbraucht.
    """
    from services.ai_stream_service import lauf_beginnen
    from services import ai_run_broker

    client = ai_run_service.http_client()
    if client is None:
        # Keine laufende Anwendung — also auch keine Ereignisschleife, auf der
        # ein Segment laufen koennte. Gar nicht erst anfangen ist ehrlicher als
        # einen Lauf anzulegen, der nie loslaeuft.
        logger.debug("Guardian-Heilung uebersprungen: keine Laufzeit")
        return None

    laufend = ai_run_service.aktiver_lauf(db, user_id=user.id)
    if laufend is not None:
        # Der Mensch arbeitet gerade. Eine Heilung, die jetzt startet, riefe
        # `vorgaenger_abloesen` und braeche ihm mitten im Satz die Antwort ab.
        # Der Vorfall bleibt offen und ohne Notiz — der naechste Takt versucht
        # es erneut.
        logger.debug(
            "Guardian-Heilung vertagt: Lauf %s ist aktiv (user_id=%s)", laufend.id, user.id
        )
        return None

    anbieter = ai_provider_service.anbieter_ohne_auswahl(db, user)
    if anbieter is None:
        logger.info("Guardian-Heilung ohne Anbieter (user_id=%s)", user.id)
        return None
    if anbieter.requires_api_key and not anbieter.operator_api_key_encrypted:
        logger.info("Guardian-Heilung ohne API-Schluessel (provider_id=%s)", anbieter.id)
        return None

    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    db.commit()

    denken, stufe = await ai_reasoning.vorgabe(
        client, db, user=user, provider=anbieter, aktiv=False, wunsch=None
    )
    fenster = await ai_context_window.ermitteln(client, anbieter)

    run, fehler = lauf_beginnen(
        db,
        user=user,
        conversation=conversation,
        provider=anbieter,
        request_id=uuid4(),
        content=_auftragstext(server, vorfall),
        reasoning=denken,
        reasoning_effort=stufe,
        context_chars=fenster.zeichen if fenster.bekannt else None,
        # Sonst berichtete die KI sich selbst von dem Vorfall, an dem sie
        # gerade arbeitet — und markierte ihn dabei als besprochen, obwohl ihn
        # kein Mensch gesehen hat.
        guardian_briefing_unterdruecken=True,
    )
    if run is None:
        # Kontingent erschoepft, Schluessel nicht lesbar, Anfragekonflikt. Alles
        # Gruende, die beim naechsten Takt anders liegen koennen — deshalb keine
        # Notiz, der Vorfall bleibt offen.
        logger.info(
            "Guardian-Heilung nicht begonnen (server_id=%s): %s",
            server.id, (fehler or ("unbekannt",))[0],
        )
        return None

    if not _notiz_anlegen(
        db, incident_id=vorfall.id, user_id=user.id, mode="healing", run_id=run.id
    ):
        # Ein anderer Durchlauf war schneller. Den eigenen Lauf zuruecknehmen,
        # sonst laufen zwei Heilungen auf denselben Vorfall.
        run.status = "cancelled"
        run.stop_reason = "guardian_duplicate"
        db.commit()
        return None

    # **Der Rahmen erst jetzt — nach der Notiz, nicht davor.**
    #
    # `_notiz_anlegen` stuft eine vorhandene `briefed`-Zeile zu `healing` hoch,
    # und dieser Weg fuehrt ueber eine `IntegrityError` mit anschliessendem
    # `db.rollback()`. Stand der Rahmen zu diesem Zeitpunkt schon ungespeichert
    # an `run.state_json`, nahm das Rollback ihn mit: die Zeile wurde
    # hochgestuft, der Lauf gestartet — und lief als **gewoehnlicher** Chatlauf.
    # Voller Werkzeugsatz, keine Serverbindung, keine Backup-Pflicht, und
    # niemand sass davor. Ausgerechnet der Fall, den die Hochstufung retten
    # soll (Vorfall gemeldet, Mensch tippt dazwischen), war damit der
    # gefaehrlichste.
    #
    # Nach der Notiz kann nichts mehr zurueckrollen, was hier geschrieben wird.
    #
    # Ab hier gilt fuer jede Runde dieses Laufs: eingeschraenkte Werkzeugmenge,
    # fester Server, Backup-Pflicht vor jedem Eingriff.
    zustand = ai_run_service.zustand_lesen(run)
    zustand["guardian"] = {
        "server_id": server.id,
        "incident_id": vorfall.id,
        "incident_created_at": _utc(vorfall.created_at).isoformat(),
        # **Der Anker fuer den Backup-Nachweis ist der Beginn dieses Laufs, nicht
        # der Vorfall.**
        #
        # `Incident.created_at` ist der Zeitpunkt des **ersten** Auftretens und
        # wird bei der Gruppierung nie aufgefrischt — `_merge` erhoeht nur
        # `occurrences`, `status`, `attempts` und `description`. Ein Vorfall, der
        # seit Tagen offen steht und den der Betreiber erst jetzt der KI
        # ueberlaesst, haette damit ein tagealtes Nachtbackup als "Nachweis"
        # gelten lassen: die Schranke waere formal erfuellt, und ein Rollback
        # landete auf einem Stand von vorgestern. Genau den Fall schliesst der
        # Test zur Schranke ausdruecklich aus — er hatte ihn nur nicht getroffen,
        # weil er den Vorfall zehn Minuten alt macht.
        #
        # Dazu kommt: `created_at` stammt ungeprueft vom Agenten. Eine
        # nachgehende Uhr auf einer Node haette dieselbe Wirkung.
        #
        # Der Laufbeginn ist der ehrliche Anker. Ein Backup, das juenger ist als
        # er, kann nur waehrend dieser Heilung entstanden sein — und genau das
        # soll bewiesen werden.
        "backup_anker": _jetzt().isoformat(),
    }
    ai_run_service.zustand_schreiben(run, zustand)
    db.commit()

    ai_run_broker.eroeffnen(run.id)
    if not ai_run_service.lauf_starten(run.id):
        # `lauf_starten` hat — anders als `lauf_fortsetzen` — keinen Rueckfall.
        # Ohne diese Zeile stuende der Lauf bis zum naechsten Prozessstart auf
        # 'running' und blockierte jede weitere Heilung dieses Benutzers, weil
        # `aktiver_lauf` ihn fuer beschaeftigt hielte.
        run.status = "failed"
        run.stop_reason = "no_runtime"
        db.commit()
        return None
    return run


# ── Der Takt ──────────────────────────────────────────────────────────────


async def vorfaelle_bearbeiten(db: Session) -> int:
    """Ein Durchlauf: offene Vorfaelle ansehen und je einen Zweig waehlen.

    Gibt zurueck, wieviele Vorfaelle behandelt wurden — gebrieft oder geheilt.

    Jeder Vorfall wird einzeln gekapselt. Ein Fehler bei einem Server darf die
    uebrigen nicht mitnehmen, und der Auftrag darf unter keinen Umstaenden
    durchschlagen: er laeuft neben der Guardian-Reconciliation, und ein
    abgebrochener Scheduler-Auftrag zieht keine Vorfaelle mehr ein.
    """
    behandelt = 0
    vorfaelle = (
        db.query(Incident)
        .filter(Incident.status.in_(OFFENE_ZUSTAENDE))
        .order_by(Incident.created_at.asc())
        .limit(MAX_VORFAELLE_JE_DURCHLAUF * 4)
        .all()
    )
    for vorfall in vorfaelle:
        if behandelt >= MAX_VORFAELLE_JE_DURCHLAUF:
            break
        try:
            if await _einen_vorfall_bearbeiten(db, vorfall):
                behandelt += 1
        except Exception as exc:  # noqa: BLE001 - ein Server darf die uebrigen nicht mitnehmen
            db.rollback()
            logger.warning(
                "Guardian-KI-Ausloeser fehlgeschlagen (incident_id=%s): %s",
                vorfall.id, type(exc).__name__,
            )
    return behandelt


async def _einen_vorfall_bearbeiten(db: Session, vorfall: Incident) -> bool:
    """``True``, wenn fuer diesen Vorfall eine Heilung angelaufen ist.

    Der Zweig **ohne** Freigabe taucht hier nicht auf, und das ist kein
    Versehen. Er braucht keinen Ausloeser: `offene_briefings` holt die noch
    ungenannten Vorfaelle beim Aufbau des Kontexts, wenn der Benutzer das
    naechste Mal schreibt. Nichts vorzumerken heisst nichts zu tun — und der
    Takt soll nicht jede Minute ueber alle Server laufen, um am Ende
    festzustellen, dass er nichts zu tun hatte.
    """
    server = db.get(Server, vorfall.server_id)
    if server is None:
        return False

    freigeber = zustaendiger_freigeber(db, server)
    if freigeber is None:
        return False

    # Nur eine **Heilungsnotiz** sperrt. Eine Briefingnotiz sagt lediglich, dass
    # der Vorfall im Chat erwaehnt wurde, und das darf keine Heilung verhindern.
    #
    # Ohne den `mode`-Filter entschied ein Zufall von sechzig Sekunden: legt
    # Guardian einen Vorfall an und schreibt der Freigeber vor dem naechsten Takt
    # irgendetwas in den Chat, haengt `briefing_nachricht` den Vorfall an — der
    # Briefingpfad kennt die Freigabe naemlich gar nicht —, und beim Abschluss
    # dieses Laufs entsteht die Zeile mit `mode='briefed'`. Der Ausloeser sah
    # danach eine Notiz und liess den Vorfall fuer immer liegen. Der Server blieb
    # stehen, obwohl die Autonomie eingeschaltet war.
    vorhanden = (
        db.query(AiGuardianNotice.id)
        .filter(
            AiGuardianNotice.incident_id == vorfall.id,
            AiGuardianNotice.user_id == freigeber.id,
            AiGuardianNotice.mode == "healing",
        )
        .first()
    )
    if vorhanden is not None:
        return False

    run = await heilungslauf_starten(db, server=server, vorfall=vorfall, user=freigeber)
    if run is None:
        return False
    logger.info(
        "Guardian-Heilung gestartet run_id=%s server_id=%s incident_id=%s",
        run.id, server.id, vorfall.id,
    )
    return True
