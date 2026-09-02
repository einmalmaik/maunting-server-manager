"""Stehende Auftraege: anlegen, aendern, auflisten — und ausrechnen, wann.

Der Fachteil hinter `ai_tasks`. Ohne Router: die Aufgaben werden ausschliesslich
im Chat verwaltet, und die Werkzeuge rufen von hier aus.

**Die Faelligkeit wird gerechnet, nicht geplant.** APScheduler haelt seine Jobs
in MSM nur im Speicher; ein Job je Aufgabe waere nach jedem Neustart des Panels
weg und muesste aus der Tabelle wiederhergestellt werden. Also steht der naechste
Termin als `next_run_at` in der Tabelle, und ein einziger Takt fragt ihn ab.

Gerechnet wird trotzdem mit den Triggern von APScheduler — aber als **reine
Funktionen**, ohne sie je zu registrieren. `CronTrigger.get_next_fire_time` kann
alles, was hier gebraucht wird, kennt Zeitzonen und behandelt die
Sommerzeitgrenze richtig. Ein eigener Kalenderrechner waere derselbe Code
nochmal, nur ungeprueft; ein `croniter` waere eine neue Abhaengigkeit fuer etwas,
das schon im Haus ist.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from models import AiTask, User
from models.ai_task import ARTEN, KANAELE, PLANARTEN
from services import permission_service
from services.ai_action_errors import AiActionValidationError
from services.ai_redaction import redact_sensitive_text


logger = logging.getLogger(__name__)

#: Wieviele stehende Auftraege ein Mensch haben darf. Die Grenze ist nicht
#: technisch, sondern eine Kostengrenze: jede Aufgabe ist ein KI-Lauf aus seinem
#: Kontingent, und zwanzig taegliche Auftraege sind zwanzig Anbieteraufrufe, von
#: denen er keinen mitbekommt.
MAX_AUFGABEN_JE_BENUTZER = 20

#: Kuerzestes Intervall. Ein Auftrag "alle fuenf Minuten" waere in einer Nacht
#: knapp 300 Laeufe — das Kontingent ist morgens leer, und der Chat besteht aus
#: Berichten. Wer wirklich engmaschig ueberwacht sein will, hat dafuer Guardian.
MIN_INTERVALL_STUNDEN = 1
MAX_INTERVALL_STUNDEN = 24 * 7

MAX_TITEL_ZEICHEN = 120
MAX_AUFTRAG_ZEICHEN = 2_000


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def utc(wert: datetime | None) -> datetime | None:
    """SQLite gibt zeitzonenlose Werte zurueck, PostgreSQL zeitzonenbehaftete.

    Ein Vergleich zwischen beiden wirft `TypeError` — hier ausgerechnet in der
    Faelligkeitspruefung, also an der Stelle, an der ein Fehler bedeutet, dass
    ueberhaupt keine Aufgabe mehr laeuft. Dieselbe Funktion steht aus demselben
    Grund in `ai_guardian_report`.
    """
    if wert is None:
        return None
    return wert.replace(tzinfo=timezone.utc) if wert.tzinfo is None else wert


# ── Die Zeitzone ──────────────────────────────────────────────────────────


def zone_pruefen(name: object) -> str:
    """Nimmt eine IANA-Zone an oder weist ab. Kein Rueckfall auf UTC.

    Die Pruefung ist die **mechanische** Seite der Zusage, dass die KI vorher
    gefragt hat. Der Prompt sagt ihr, sie solle die Zeitzone erfragen oder aus
    dem Gedaechtnis holen; ein Prompt ist aber keine Schranke. Waere hier ein
    stiller Rueckfall auf UTC, legte ein Modell, das nicht gefragt hat, die
    Aufgabe trotzdem an — und der Betreiber bekaeme seine Mail im Sommer zwei
    Stunden zu frueh, ohne je ein Wort ueber Zeitzonen gelesen zu haben.

    Der Fehlertext nennt ausdruecklich den Weg (`ask_user`), weil er als
    Werkzeugergebnis beim Modell landet und dessen naechsten Versuch bestimmt.
    """
    if not isinstance(name, str) or not name.strip():
        raise AiActionValidationError(
            "Ohne Zeitzone laesst sich keine Uhrzeit anlegen. Frag den Benutzer "
            "mit ask_user, in welcher Zeitzone er lebt (z. B. Europe/Berlin)."
        )
    sauber = name.strip()
    try:
        ZoneInfo(sauber)
    except (ZoneInfoNotFoundError, ValueError, ModuleNotFoundError) as exc:
        raise AiActionValidationError(
            f"Unbekannte Zeitzone {sauber!r}. Erwartet wird eine IANA-Angabe wie "
            "Europe/Berlin oder America/New_York. Frag im Zweifel mit ask_user nach."
        ) from exc
    return sauber


# ── Der Plan ──────────────────────────────────────────────────────────────


def uhrzeit_pruefen(wert: object) -> str:
    """``"HH:MM"`` — dasselbe Format wie `restart_times_utc` am Server.

    **Beim Lesen nachsichtig, beim Speichern streng.** Gespeichert wird immer
    ``"HH:MM"``; angenommen werden auch ``"8:00"`` und ``"08:00:00"``.

    Der Grund steht im Betrieb: das Schema sagt dem Modell ``'HH:MM'``, und
    trotzdem schickt es regelmaessig eine einstellige Stunde oder haengt
    Sekunden an. Beides ist eindeutig — es gibt keine zweite Lesart von
    ``"8:00"``. Die Abweisung kostete den Benutzer aber nicht das Feld, sondern
    die **ganze Antwort**: eine Formmeldung aus dem Vorschlagspfad beendet den
    Lauf, und im Chat stand statt der Aufgabe eine Fehlermeldung.

    Streng bleibt es, wo etwas wirklich mehrdeutig ist — ``"halb neun"`` oder
    ``"8 pm"`` werden weiterhin abgewiesen. Nachsicht heisst hier: dieselbe
    Angabe anders geschrieben, nicht eine andere Angabe.
    """
    if not isinstance(wert, str):
        raise AiActionValidationError("Uhrzeit muss im Format HH:MM stehen")
    teile = wert.strip().split(":")
    if len(teile) not in (2, 3) or not all(teil.strip().isdigit() for teil in teile[:2]):
        raise AiActionValidationError("Uhrzeit muss im Format HH:MM stehen")
    stunde, minute = int(teile[0]), int(teile[1])
    if not (0 <= stunde <= 23 and 0 <= minute <= 59):
        raise AiActionValidationError("Uhrzeit liegt ausserhalb des Tages")
    return f"{stunde:02d}:{minute:02d}"


def wochentage_pruefen(wert: object) -> str | None:
    """ISO-Wochentage (Montag = 1) als sortierte, entdoppelte Liste.

    ``None`` und die vollstaendige Woche bedeuten dasselbe — taeglich — und
    werden auch gleich gespeichert. Zwei Schreibweisen fuer denselben Plan
    waeren zwei Zeilen in der Auflistung, die verschieden aussehen und dasselbe
    tun.
    """
    if wert is None or wert == [] or wert == "":
        return None
    if isinstance(wert, str):
        # Die gespeicherte Form (``"1,3,5"``) wird auch wieder angenommen. Zwei
        # Aufrufer brauchen das, und beide sind echt:
        #
        # * `_planfelder_ergaenzen` reicht den Bestand aus der Datenbank
        #   herein, wenn jemand nur die Uhrzeit verschiebt. Ohne diesen Zweig
        #   scheiterte ausgerechnet die kleinste denkbare Aenderung an einer
        #   Aufgabe, die Wochentage traegt.
        # * Das Modell schickt sie ebenfalls, obwohl das Schema eine Liste
        #   nennt — es hat die Zeichenkette gerade in `list_tasks` gelesen.
        try:
            wert = [int(teil) for teil in wert.split(",") if teil.strip()]
        except ValueError as exc:
            raise AiActionValidationError(
                "Wochentage muessen Zahlen von 1 (Montag) bis 7 (Sonntag) sein"
            ) from exc
    if not isinstance(wert, list):
        raise AiActionValidationError("Wochentage muessen eine Liste von Zahlen sein")
    tage: set[int] = set()
    for eintrag in wert:
        if isinstance(eintrag, bool) or not isinstance(eintrag, int):
            raise AiActionValidationError("Wochentage muessen ganze Zahlen sein")
        if not 1 <= eintrag <= 7:
            raise AiActionValidationError(
                "Wochentage laufen von 1 (Montag) bis 7 (Sonntag)"
            )
        tage.add(eintrag)
    if not tage or len(tage) == 7:
        return None
    return ",".join(str(tag) for tag in sorted(tage))


#: ISO-Wochentag (Montag = 1) auf die Kuerzel von APScheduler. Die Umrechnung
#: passiert genau hier und nirgends sonst: in der Datenbank steht die Zaehlung,
#: die ein Mensch beim Nachsehen erwartet, im Trigger die, die APScheduler
#: versteht (dort ist Montag 0).
_APS_TAGE = {1: "mon", 2: "tue", 3: "wed", 4: "thu", 5: "fri", 6: "sat", 7: "sun"}


def _tagesplan(aufgabe: AiTask) -> CronTrigger:
    stunde, minute = aufgabe.time_of_day.split(":")
    tage = None
    if aufgabe.weekdays:
        tage = ",".join(
            _APS_TAGE[int(teil)] for teil in aufgabe.weekdays.split(",") if teil
        )
    return CronTrigger(
        hour=int(stunde),
        minute=int(minute),
        day_of_week=tage,
        timezone=ZoneInfo(aufgabe.time_zone),
    )


def naechste_faelligkeit(aufgabe: AiTask, *, ab: datetime) -> datetime | None:
    """Der naechste Termin **nach** ``ab``, in UTC. ``None`` heisst: keiner mehr.

    ``ab`` ist ausdruecklich ein Parameter und nicht ``jetzt()``: beim Anlegen
    wird von jetzt gerechnet, beim Weiterschalten vom gerade faellig gewordenen
    Termin. Ein Intervall, das sich immer an ``jetzt`` orientiert, driftet mit
    jedem Lauf ein Stueck weiter nach hinten — derselbe Fehler, den
    `scheduler_service` beim Auto-Neustart einmal hatte.
    """
    ab = utc(ab)
    if aufgabe.plan_kind == "once":
        faellig = utc(aufgabe.once_at)
        return faellig if faellig is not None and faellig > ab else None
    if aufgabe.plan_kind == "interval":
        return ab + timedelta(hours=int(aufgabe.interval_hours or 0))
    naechster = _tagesplan(aufgabe).get_next_fire_time(None, ab)
    return naechster.astimezone(timezone.utc) if naechster is not None else None


def plan_text(aufgabe: AiTask) -> str:
    """Der Plan in einem Satz — fuer die Auflistung, die Vorschau und die Mail.

    Nur **eine** Stelle, an der ein Plan in Worte gefasst wird. Der Text geht an
    drei Orte mit drei verschiedenen Lesern (Modell, Vorschlagskarte, E-Mail);
    drei Formulierungen davon waeren drei Gelegenheiten, verschiedene Dinge ueber
    dieselbe Aufgabe zu behaupten.

    Die Zeitzone steht **immer** dabei. Ohne sie ist "taeglich 08:00" genau die
    Angabe, bei der sich der Betreiber spaeter fragt, warum die Mail um neun kam.
    """
    if aufgabe.plan_kind == "interval":
        stunden = int(aufgabe.interval_hours or 0)
        return f"alle {stunden} Stunden" if stunden != 1 else "stuendlich"
    if aufgabe.plan_kind == "once":
        zeitpunkt = utc(aufgabe.once_at)
        if zeitpunkt is None:
            return "einmalig (Termin abgelaufen)"
        ortszeit = zeitpunkt.astimezone(ZoneInfo(aufgabe.time_zone))
        return f"einmalig am {ortszeit:%d.%m.%Y um %H:%M} ({aufgabe.time_zone})"
    if aufgabe.weekdays:
        namen = {1: "Mo", 2: "Di", 3: "Mi", 4: "Do", 5: "Fr", 6: "Sa", 7: "So"}
        tage = ", ".join(
            namen[int(teil)] for teil in aufgabe.weekdays.split(",") if teil
        )
        return f"{tage} um {aufgabe.time_of_day} ({aufgabe.time_zone})"
    return f"taeglich um {aufgabe.time_of_day} ({aufgabe.time_zone})"


# ── Rechte ────────────────────────────────────────────────────────────────


def darf_verwalten(db: Session, user: User) -> bool:
    return permission_service.has_global_permission(db, user, "ai.tasks.manage")


def darf_handeln(db: Session, user: User) -> bool:
    """Ob dieser Mensch eine Aufgabe der Art ``act`` haben darf.

    Zwei Bedingungen, und die zweite ist der Grund, warum diese Funktion nicht
    einfach eine Rechtepruefung ist: es braucht **auch** eine tatsaechlich
    erteilte Freigabe. Das Recht sagt "darf den autonomen Modus benutzen", die
    Freigabe sagt "hier und jetzt, fuer diesen Server oder panelweit". Ohne die
    zweite erzeugte ein faelliger Lauf um drei Uhr nachts nur einen Vorschlag,
    auf dessen Bestaetigung niemand wartet — und der Betreiber erfuehre erst
    Wochen spaeter, dass sein Backup nie lief.

    Der Vergleich `server_id IS NULL OR = id` fehlt hier bewusst: geprueft wird,
    ob es **irgendeine** aktive Freigabe gibt. Welche fuer den konkreten Server
    gilt, entscheidet spaeter `autonomy_allows` bei jedem einzelnen Werkzeug —
    das ist die eigentliche Schranke, diese hier ist die ehrliche Auskunft beim
    Anlegen.
    """
    from models import AiAutonomyGrant

    if not permission_service.has_global_permission(db, user, "ai.autonomous.use"):
        return False
    return (
        db.query(AiAutonomyGrant.id)
        .filter(
            AiAutonomyGrant.user_id == user.id,
            AiAutonomyGrant.enabled.is_(True),
            AiAutonomyGrant.max_actions_per_hour > 0,
        )
        .first()
        is not None
    )


# ── Anlegen, aendern, loeschen, auflisten ─────────────────────────────────


def eigene_aufgabe(db: Session, *, user: User, task_id: object) -> AiTask:
    """Holt eine Aufgabe dieses Benutzers oder wirft.

    Die Besitzpruefung steht **in der Abfrage** und nicht als `if` danach: eine
    fremde Aufgabe soll gar nicht erst in einer Variablen landen, aus der sie
    versehentlich in eine Vorschau geraet.
    """
    if not isinstance(task_id, str) or not task_id.strip():
        raise AiActionValidationError("Aufgabennummer fehlt")
    aufgabe = (
        db.query(AiTask)
        .filter(AiTask.id == task_id.strip(), AiTask.user_id == user.id)
        .first()
    )
    if aufgabe is None:
        raise AiActionValidationError(
            "Diese Aufgabe gibt es nicht. Ruf list_tasks auf und nimm eine "
            "Nummer von dort, statt sie zu raten."
        )
    return aufgabe


#: Alle Felder, die den Termin formen. Eine Menge und keine Aufzaehlung an drei
#: Stellen: sie entscheidet, **ob** der Plan neu uebernommen wird, **ob** der
#: Termin neu gerechnet wird, und was dabei aus dem Bestand ergaenzt wird.
_PLANFELDER = frozenset({
    "plan_kind", "time_of_day", "weekdays", "interval_hours", "once_at",
})


def _planfelder_ergaenzen(aufgabe: AiTask, felder: dict) -> dict:
    """Fuellt die nicht genannten Planfelder aus dem Bestand auf.

    Der Anlass war ein stiller Fehlschlag: `aendern` uebernahm den Plan nur,
    wenn `plan_kind` genannt war. "verschieb das auf 9 Uhr" nennt es nicht — das
    Werkzeug meldete Erfolg, die Vorschau zeigte neun Uhr, und die Aufgabe lief
    weiter um acht. Ein Zweig, der nichts tut und trotzdem gelingt, ist die
    teuerste Art von Fehler: er sieht bis zum naechsten Morgen richtig aus.

    Wechselt der Benutzer dagegen die **Planart**, wird nichts ergaenzt. Von
    `daily` auf `interval` gibt es nichts zu erben, und eine geerbte Uhrzeit
    waere genau die Angabe, nach der sich danach nichts mehr richtet.
    """
    genannt = felder.get("plan_kind")
    if genannt is not None and genannt != aufgabe.plan_kind:
        return felder
    bestand = {
        "plan_kind": aufgabe.plan_kind,
        "time_of_day": aufgabe.time_of_day,
        "weekdays": aufgabe.weekdays,
        "interval_hours": aufgabe.interval_hours,
        # Zurueck in die Textform, in der `_plan_uebernehmen` sie erwartet —
        # mit Zonenangabe, sonst laege der geerbte Zeitpunkt danach um den
        # Zonenversatz verschoben.
        "once_at": (
            utc(aufgabe.once_at).isoformat() if aufgabe.once_at is not None else None
        ),
    }
    bestand.update({name: felder[name] for name in _PLANFELDER if name in felder})
    return bestand


def _plan_uebernehmen(aufgabe: AiTask, felder: dict) -> None:
    """Setzt die Planfelder und raeumt die der anderen Planarten weg.

    Das Wegraeumen ist nicht Kosmetik. Bleibt beim Wechsel von `daily` auf
    `interval` eine `time_of_day` stehen, zeigt die Auflistung eine Uhrzeit an,
    nach der sich nichts richtet — und beim naechsten Wechsel zurueck gilt
    plotzlich wieder eine Zeit, die der Benutzer nie erneut genannt hat.
    """
    plan_kind = felder["plan_kind"]
    if plan_kind not in PLANARTEN:
        raise AiActionValidationError(
            f"Unbekannte Planart. Moeglich sind: {', '.join(PLANARTEN)}"
        )
    aufgabe.plan_kind = plan_kind
    aufgabe.time_of_day = None
    aufgabe.weekdays = None
    aufgabe.interval_hours = None
    aufgabe.once_at = None

    if plan_kind == "daily":
        aufgabe.time_of_day = uhrzeit_pruefen(felder.get("time_of_day"))
        aufgabe.weekdays = wochentage_pruefen(felder.get("weekdays"))
        return
    if plan_kind == "interval":
        stunden = felder.get("interval_hours")
        if isinstance(stunden, bool) or not isinstance(stunden, int):
            raise AiActionValidationError("interval_hours muss eine ganze Zahl sein")
        if not MIN_INTERVALL_STUNDEN <= stunden <= MAX_INTERVALL_STUNDEN:
            raise AiActionValidationError(
                f"Das Intervall muss zwischen {MIN_INTERVALL_STUNDEN} und "
                f"{MAX_INTERVALL_STUNDEN} Stunden liegen. Kuerzere Abstaende "
                "verbrauchen das KI-Kontingent schneller, als der Benutzer es "
                "bemerken kann."
            )
        aufgabe.interval_hours = stunden
        return

    roh = felder.get("once_at")
    if not isinstance(roh, str) or not roh.strip():
        raise AiActionValidationError(
            "once_at fehlt. Erwartet wird ein Zeitpunkt wie 2026-08-20T08:00."
        )
    try:
        zeitpunkt = datetime.fromisoformat(roh.strip())
    except ValueError as exc:
        raise AiActionValidationError(
            f"Zeitpunkt {roh!r} ist nicht lesbar. Erwartet wird ISO-8601, "
            "zum Beispiel 2026-08-20T08:00."
        ) from exc
    # Ohne Zeitzonenangabe gilt die der Aufgabe — der Benutzer hat sie genannt,
    # und "am 20. um 8" meint seine acht, nicht die von UTC.
    if zeitpunkt.tzinfo is None:
        zeitpunkt = zeitpunkt.replace(tzinfo=ZoneInfo(aufgabe.time_zone))
    zeitpunkt = zeitpunkt.astimezone(timezone.utc)
    if zeitpunkt <= _jetzt():
        raise AiActionValidationError("Der Zeitpunkt liegt in der Vergangenheit")
    aufgabe.once_at = zeitpunkt


def _anwenden(db: Session, *, user: User, ziel: AiTask, felder: dict, neu: bool) -> AiTask:
    """Prueft die Felder und schreibt sie auf ``ziel``. Speichert nichts.

    Der gemeinsame Kern von Anlegen, Aendern **und Vorschau**. Ohne ihn gaebe es
    die Pruefungen dreimal: einmal beim Bau der Vorschlagskarte, einmal beim
    Anlegen und einmal beim Aendern — und die Vorschau wuerde irgendwann etwas
    versprechen, das die Ausfuehrung anders macht.

    ``neu`` unterscheidet die beiden Faelle: beim Anlegen sind die Grundangaben
    Pflicht, beim Aendern wird nur angefasst, was genannt ist. "Pausier das mal"
    ist der haeufigste Fall und darf nicht verlangen, dass das Modell den
    ganzen Plan erneut aufschreibt — was es dabei falsch abschriebe, waere
    danach der Plan.
    """
    if neu or "title" in felder:
        titel = redact_sensitive_text(str(felder.get("title") or "").strip())
        if not titel:
            raise AiActionValidationError("Die Aufgabe braucht einen kurzen Namen")
        ziel.title = titel[:MAX_TITEL_ZEICHEN]
    if neu or "instruction" in felder:
        auftrag = redact_sensitive_text(str(felder.get("instruction") or "").strip())
        if not auftrag:
            raise AiActionValidationError("Die Aufgabe braucht einen Auftragstext")
        ziel.instruction = auftrag[:MAX_AUFTRAG_ZEICHEN]
    if neu or "kind" in felder:
        kind = felder.get("kind")
        if kind not in ARTEN:
            raise AiActionValidationError(
                f"Unbekannte Aufgabenart. Moeglich sind: {', '.join(ARTEN)}"
            )
        # Die Pruefung steht hier und laeuft damit **zweimal**: beim Bau des
        # Vorschlags und noch einmal beim Ausfuehren. Das ist keine Doppelung
        # aus Versehen — zwischen beiden liegt ein Commit und ein Zeitfenster
        # ohne Obergrenze, in dem der Betreiber die autonome Freigabe
        # zurueckgenommen haben kann. Dieselbe Ueberlegung wie bei der
        # Backup-Schranke des Guardian, und dort war die fehlende zweite
        # Pruefung einmal der Weg zu einer geloeschten Datei ohne Backup.
        if kind == "act" and not darf_handeln(db, user):
            raise AiActionValidationError(
                "Eine Aufgabe, die selbst handelt, setzt den autonomen Modus "
                "voraus. Er ist fuer diesen Benutzer nicht freigegeben. Sag "
                "ihm, dass er ihn im KI-Chat einschalten muss, oder leg die "
                "Aufgabe als reinen Bericht an (kind='report')."
            )
        ziel.kind = kind
    # Der Zustellweg ist **keine** Pflichtangabe. Er stand bis hierher unter
    # ``neu`` und wurde damit beim Anlegen verlangt — der Standard ``chat`` aus
    # `_leere_aufgabe` konnte nie greifen. Gekostet hat das eine ganze Rückfrage
    # bei jeder ersten Aufgabe („und wie möchtest du die Ergebnisse erhalten?"),
    # also eine volle Anbieterrunde mit mehreren Sekunden Stille — für eine
    # reine Vorliebe. Der Chat ist ohnehin da, und dort steht das Ergebnis in
    # jedem Fall.
    #
    # Genannt bleibt genannt: ein unbekannter Wert wird weiterhin abgewiesen,
    # nur das Weglassen ist jetzt erlaubt. ``None`` gilt dabei als weggelassen —
    # ein Modell schickt ein Feld, das es kennt, lieber leer als gar nicht.
    if felder.get("channel") is not None:
        channel = felder["channel"]
        if channel not in KANAELE:
            raise AiActionValidationError(
                f"Unbekannter Zustellweg. Moeglich sind: {', '.join(KANAELE)}"
            )
        ziel.channel = channel
    if neu or "timezone" in felder or "time_zone" in felder:
        tz_val = felder.get("timezone") if "timezone" in felder else felder.get("time_zone")
        if tz_val is None and neu:
            tz_val = user.time_zone or "UTC"
        ziel.time_zone = zone_pruefen(tz_val)
    if "enabled" in felder:
        wert = felder["enabled"]
        if not isinstance(wert, bool):
            raise AiActionValidationError("enabled muss wahr oder falsch sein")
        ziel.enabled = wert
    if neu or (_PLANFELDER & set(felder)):
        _plan_uebernehmen(ziel, _planfelder_ergaenzen(ziel, felder))

    # Neu rechnen, sobald etwas den Termin beeinflusst haben kann. Auch beim
    # Wiedereinschalten: eine pausierte Aufgabe traegt einen Termin von vor der
    # Pause, und der liegt in der Vergangenheit.
    if neu or (_PLANFELDER | {"timezone", "time_zone", "enabled"}) & set(felder):
        ziel.next_run_at = (
            naechste_faelligkeit(ziel, ab=_jetzt()) if ziel.enabled else None
        )
        if ziel.enabled and ziel.next_run_at is None:
            raise AiActionValidationError("Dieser Plan ergibt keinen naechsten Termin")
    return ziel


def _leere_aufgabe(user: User) -> AiTask:
    """Der Entwurf, auf dem `_anwenden` arbeitet."""
    return AiTask(
        id=str(uuid4()),
        user_id=user.id,
        title="",
        instruction="",
        kind="report",
        plan_kind="daily",
        time_zone=user.time_zone or "UTC",
        time_of_day="08:00",
        weekdays=None,
        interval_hours=None,
        once_at=None,
        channel="chat",
        enabled=True,
    )


def vorschau(db: Session, *, user: User, felder: dict, task_id: str | None) -> dict:
    """Was auf der Bestaetigungskarte steht — ohne irgendetwas zu speichern.

    Gebaut auf einer **losen** Aufgabe: entweder einer frischen oder einer Kopie
    der vorhandenen. Die vorhandene Zeile selbst zu beschreiben und darauf zu
    hoffen, dass die Sitzung sie nicht speichert, waere ein Vorschlag, der schon
    passiert ist, bevor jemand ihn bestaetigt hat.

    Nichts Geheimes darin: die Vorschau geht unverschluesselt in die Datenbank,
    in die SSE-Nutzlast und in den wiederangehaengten Chat.
    """
    if not darf_verwalten(db, user):
        raise AiActionValidationError(
            "Fuer stehende Aufgaben fehlt diesem Benutzer das Recht ai.tasks.manage"
        )
    if task_id is None:
        _mengengrenze_pruefen(db, user)
        entwurf = _leere_aufgabe(user)
        neu = True
    else:
        vorhanden = eigene_aufgabe(db, user=user, task_id=task_id)
        entwurf = _leere_aufgabe(user)
        for spalte in (
            "title", "instruction", "kind", "plan_kind", "time_of_day",
            "weekdays", "interval_hours", "once_at", "time_zone", "channel",
            "enabled",
        ):
            setattr(entwurf, spalte, getattr(vorhanden, spalte))
        entwurf.next_run_at = vorhanden.next_run_at
        neu = False

    _anwenden(db, user=user, ziel=entwurf, felder=felder, neu=neu)
    return {
        "operation": "task_create" if neu else "task_update",
        "task_id": task_id,
        "title": entwurf.title,
        "instruction": entwurf.instruction,
        "kind": entwurf.kind,
        "plan": plan_text(entwurf),
        "timezone": entwurf.time_zone,
        "channel": entwurf.channel,
        "enabled": bool(entwurf.enabled),
        "next_run": (
            utc(entwurf.next_run_at).isoformat()
            if entwurf.next_run_at is not None else None
        ),
    }


def _mengengrenze_pruefen(db: Session, user: User) -> None:
    anzahl = db.query(AiTask.id).filter(AiTask.user_id == user.id).count()
    if anzahl >= MAX_AUFGABEN_JE_BENUTZER:
        raise AiActionValidationError(
            f"Es gibt bereits {anzahl} Aufgaben; mehr als "
            f"{MAX_AUFGABEN_JE_BENUTZER} sind nicht vorgesehen. Loesche oder "
            "pausiere eine, bevor du eine neue anlegst."
        )


def anlegen(db: Session, *, user: User, felder: dict) -> AiTask:
    """Legt einen stehenden Auftrag an — ohne zu committen.

    Der Commit gehoert dem Aufrufer: das Anlegen laeuft aus der Ausfuehrung
    eines bestaetigten Vorschlags heraus, und dort haengt noch mehr an derselben
    Transaktion.
    """
    if not darf_verwalten(db, user):
        raise AiActionValidationError(
            "Fuer stehende Aufgaben fehlt diesem Benutzer das Recht ai.tasks.manage"
        )
    _mengengrenze_pruefen(db, user)
    aufgabe = _anwenden(
        db, user=user, ziel=_leere_aufgabe(user), felder=felder, neu=True
    )
    db.add(aufgabe)
    db.flush()
    return aufgabe


def aendern(db: Session, *, user: User, task_id: str, felder: dict) -> AiTask:
    """Aendert eine vorhandene Aufgabe. Nur genannte Felder werden angefasst."""
    if not darf_verwalten(db, user):
        raise AiActionValidationError(
            "Fuer stehende Aufgaben fehlt diesem Benutzer das Recht ai.tasks.manage"
        )
    aufgabe = eigene_aufgabe(db, user=user, task_id=task_id)
    _anwenden(db, user=user, ziel=aufgabe, felder=felder, neu=False)
    db.flush()
    return aufgabe


def loeschen(db: Session, *, user: User, task_id: str) -> str:
    """Entfernt die Aufgabe und gibt ihren Namen zurueck — fuer die Rueckmeldung."""
    if not darf_verwalten(db, user):
        raise AiActionValidationError(
            "Fuer stehende Aufgaben fehlt diesem Benutzer das Recht ai.tasks.manage"
        )
    aufgabe = eigene_aufgabe(db, user=user, task_id=task_id)
    name = str(aufgabe.title)
    _server_verweise_aufraeumen(db, task_id=aufgabe.id)
    db.delete(aufgabe)
    db.flush()
    return name


def _server_verweise_aufraeumen(db: Session, *, task_id: str) -> None:
    """Leert die weichen Verweise auf diese Aufgabe an den Servern.

    Die ``*_ai_task_id``-Spalten haben bewusst keinen DB-Fremdschlüssel
    (Migration 20260820_02); dieses Aufräumen ist der Ersatz dafür. Nur die
    Kennung wird geleert — das ``*_ai_managed``-Abzeichen bleibt stehen, denn
    der Zeitplan selbst kam weiterhin von der KI.
    """
    from models import Server

    db.query(Server).filter(Server.restart_ai_task_id == task_id).update(
        {"restart_ai_task_id": None}, synchronize_session=False
    )
    db.query(Server).filter(Server.backup_ai_task_id == task_id).update(
        {"backup_ai_task_id": None}, synchronize_session=False
    )


def ki_zeitplan_verwaltung_aufheben(db: Session, server, *, bereich: str) -> None:
    """Manuelle Änderung gewinnt: nimmt die KI-Verwaltung eines Zeitplans zurück.

    Wird von den Panel-Endpunkten gerufen, sobald ein Mensch den Auto-Neustart-
    oder Auto-Backup-Zeitplan eines Servers anfasst. Der verknüpfte stehende
    Auftrag wird dabei **deaktiviert** (nicht gelöscht): der Benutzer soll in
    der Aufgabenliste sehen, was da nicht mehr läuft, und es bewusst wieder
    einschalten können. Committet nichts — die Transaktion gehört dem Endpunkt.
    """
    if bereich == "restart":
        task_id = server.restart_ai_task_id
        server.restart_ai_managed = False
        server.restart_ai_task_id = None
    elif bereich == "backup":
        task_id = server.backup_ai_task_id
        server.backup_ai_managed = False
        server.backup_ai_task_id = None
    else:  # pragma: no cover - Programmierfehler, kein Laufzeitfall
        raise ValueError(f"Unbekannter Bereich {bereich!r}")
    if not task_id:
        return
    aufgabe = db.query(AiTask).filter(AiTask.id == task_id).first()
    if aufgabe is not None and aufgabe.enabled:
        aufgabe.enabled = False
        aufgabe.next_run_at = None
        aufgabe.updated_at = _jetzt()
        logger.info(
            "KI-Aufgabe deaktiviert, Zeitplan manuell geändert (task_id=%s, server_id=%s)",
            task_id, server.id,
        )


def auflisten(db: Session, *, user: User) -> list[dict]:
    """Alle Aufgaben dieses Benutzers, in **einem** Aufruf.

    Vollstaendig und nicht seitenweise: Ergebnisse von Lesewerkzeugen fliessen
    nur aus dem juengsten Lauf in den Folgekontext (`ai_context_service`), und
    ein Modell, das blaettern muesste, haette beim zweiten Aufruf den ersten
    schon vergessen. Die Obergrenze von zwanzig Aufgaben je Benutzer macht das
    unbedenklich.
    """
    zeilen = (
        db.query(AiTask)
        .filter(AiTask.user_id == user.id)
        .order_by(AiTask.created_at.asc())
        .all()
    )
    return [eintrag(aufgabe) for aufgabe in zeilen]


def eintrag(aufgabe: AiTask) -> dict:
    """Eine Aufgabe als Wörterbuch — für `list_tasks` und die Aufgabenliste.

    Eine Form für beide Leser (Modell und Oberfläche): zwei Serialisierer
    wären zwei Gelegenheiten, verschiedene Dinge über dieselbe Aufgabe zu
    behaupten. `conversation_id` ist das Hintergrundfenster der Aufgabe —
    die Oberfläche verlinkt damit auf den Verlauf (?ansicht=worker&id=…).
    """
    return {
        "task_id": aufgabe.id,
        "title": aufgabe.title,
        "instruction": aufgabe.instruction,
        "kind": aufgabe.kind,
        "plan": plan_text(aufgabe),
        "plan_kind": aufgabe.plan_kind,
        "time_of_day": aufgabe.time_of_day,
        "weekdays": aufgabe.weekdays,
        "interval_hours": aufgabe.interval_hours,
        "once_at": (
            utc(aufgabe.once_at).isoformat() if aufgabe.once_at is not None else None
        ),
        "timezone": aufgabe.time_zone,
        "channel": aufgabe.channel,
        "enabled": bool(aufgabe.enabled),
        "conversation_id": aufgabe.conversation_id,
        "next_run": (
            utc(aufgabe.next_run_at).isoformat()
            if aufgabe.next_run_at is not None else None
        ),
        "last_started": (
            utc(aufgabe.last_started_at).isoformat()
            if aufgabe.last_started_at is not None else None
        ),
    }


# ── Der faellige Lauf ─────────────────────────────────────────────────────


def _auftragstext(aufgabe: AiTask) -> str:
    """Was der KI als Auftrag in den Chat geschrieben wird.

    Anders als beim Guardian steht hier **Benutzertext** drin — und das ist der
    Unterschied, nicht die Nachlaessigkeit: der Auftrag ist genau das, was der
    Betreiber selbst diktiert hat. Er ist beim Anlegen durch
    `redact_sensitive_text` gegangen und stammt aus einer Unterhaltung, die
    dieser Mensch selbst gefuehrt hat. Die Beschreibung eines Guardian-Vorfalls
    stammt dagegen von einem Agenten auf einem Server, auf dem Fremde spielen.

    Alles um den Auftragstext herum kommt aus dem Panel: Name, Plan, Art. Nichts
    wird aus der Aufgabe zitiert, was nicht beim Anlegen geprueft wurde.
    """
    name = redact_sensitive_text(str(aufgabe.title or ""))[:MAX_TITEL_ZEICHEN]
    auftrag = str(aufgabe.instruction or "")[:MAX_AUFTRAG_ZEICHEN]
    gelesen = (
        " und als E-Mail." if aufgabe.channel in ("email", "both") else "."
    )
    zeilen = [
        f'Faelliger stehender Auftrag "{name}" ({plan_text(aufgabe)}).',
        "",
        "Der Benutzer hat dich frueher im Chat darum gebeten:",
        auftrag,
        "",
        "Niemand sitzt gerade davor. Rueckfragen sind nicht moeglich — "
        "entscheide selbst oder melde ehrlich Fehlanzeige. Fasse am Ende in "
        "wenigen Saetzen zusammen, was du festgestellt oder getan hast; diese "
        "Zusammenfassung liest der Benutzer spaeter im Chat" + gelesen,
    ]
    if aufgabe.kind != "act":
        zeilen.append(
            "Dieser Auftrag ist ein Bericht: sieh nach und schreib auf, aber "
            "veraendere nichts."
        )
    return "\n".join(zeilen)


def _stilllegen(db: Session, aufgabe: AiTask, *, grund: str) -> None:
    """Schaltet die Aufgabe ab, weil ihre Voraussetzung weggefallen ist.

    Ausgeschaltet und **nicht** geloescht: der Betreiber soll im Chat sehen
    koennen, was da nicht mehr laeuft, und es nach dem Umlegen des Schalters
    wieder einschalten. Eine geloeschte Aufgabe muesste er neu diktieren.

    `next_run_at` wird geleert, sonst suchte der Takt dieselbe Zeile bei jedem
    Durchlauf wieder heraus — der Index liegt auf `(enabled, next_run_at)`, aber
    ein ewig faelliger Termin an einer abgeschalteten Aufgabe ist trotzdem eine
    Unwahrheit in der Tabelle.
    """
    aufgabe.enabled = False
    aufgabe.next_run_at = None
    aufgabe.updated_at = _jetzt()
    db.commit()
    logger.info("Aufgabe stillgelegt (task_id=%s): %s", aufgabe.id, grund)


async def aufgabenlauf_starten(db: Session, *, aufgabe: AiTask):
    """Startet den Lauf zu einer faelligen Aufgabe — oder gibt ``None`` zurueck.

    Baut nach, was sonst der Streamendpunkt tut: Unterhaltung holen, Anbieter
    waehlen, Denkstufe und Kontextfenster ermitteln, Lauf anlegen, starten.
    Der Ablauf ist derselbe wie in `ai_guardian_service.heilungslauf_starten`
    und bleibt trotzdem eine eigene Funktion: die beiden unterscheiden sich in
    jedem zweiten Schritt — kein Vorfall, keine Notiz, keine Serverbindung,
    dafuer eine Rechtepruefung, die die Aufgabe abschalten kann. Ein
    gemeinsames Geruest haette sechs Schalter. Nur die zwei woertlich
    identischen Segmente wohnen in `ai_run_service`: der Vorflug
    (`vorflug` — Anbieter, Denkstufe, Fenster) und der Anlauf-Schwanz
    (`anlauf` — Kanal eroeffnen, Segment planen, Rueckfall auf 'failed').
    Das ist die Grenze; alles dazwischen bleibt hier.

    ``None`` heisst immer: es wurde nichts angelegt und nichts verbraucht. Ob
    der Termin danach verfaellt oder gleich erneut versucht wird, entscheidet
    der Takt — nicht diese Funktion.
    """
    from services import ai_chat_service, ai_run_service
    from services.ai_stream_service import lauf_beginnen

    user = db.get(User, aufgabe.user_id)
    if user is None or not user.is_active:
        # Der Mensch ist weg oder gesperrt. Die Aufgabe laeuft nicht unter
        # seinem Namen weiter — und sie bleibt als abgeschaltete Zeile stehen,
        # damit sie nach einer Reaktivierung wieder eingeschaltet werden kann.
        _stilllegen(db, aufgabe, grund="benutzer_inaktiv")
        return None

    client = ai_run_service.http_client()
    if client is None:
        # Keine laufende Anwendung — also auch keine Ereignisschleife, auf der
        # ein Segment laufen koennte. Gar nicht erst anfangen ist ehrlicher als
        # ein Lauf, der nie loslaeuft.
        logger.debug("Aufgabenlauf uebersprungen: keine Laufzeit (task_id=%s)", aufgabe.id)
        return None

    if not permission_service.has_global_permission(db, user, "ai.chat.use"):
        _stilllegen(db, aufgabe, grund="kein_chatrecht")
        return None
    if not darf_verwalten(db, user):
        _stilllegen(db, aufgabe, grund="kein_aufgabenrecht")
        return None
    if not darf_handeln(db, user):
        # Alle Aufgaben laufen im Hintergrund. Gemäß Maunting Studios Grundsatz
        # ("Sicherheit braucht Vertrauen") und den Anforderungen des Autonomie-Modus
        # können geplante Aufgaben im Hintergrund nur ausgeführt werden, wenn der
        # Autonomie-Modus für den Benutzer aktiv ist.
        _stilllegen(db, aufgabe, grund="autonomie_entzogen")
        return None

    # **Ein stehender Auftrag laeuft im Hintergrund.** Hier stand bis zum
    # 20.08.2026 der Dauerchat samt Vertagen, solange der Mensch dort arbeitete
    # — der Betreiber hat es umgedreht: im Dauerchat steht nur, was der Mensch
    # schreibt, und ein faelliger Auftrag unterbricht nie das Gespraech. Die
    # Aufgabe bekommt ein eigenes Fenster (kind='worker', wiederverwendet ueber
    # alle Laeufe) und meldet ihr Ergebnis ueber die Meldestelle, sobald der
    # Chat Ruhe hat. Vertagen braucht es damit nicht mehr.
    conversation = None
    if aufgabe.conversation_id:
        from models import AiConversation

        conversation = db.get(AiConversation, aufgabe.conversation_id)
    if conversation is None:
        conversation = ai_chat_service.worker_unterhaltung_anlegen(
            db, user, f"Aufgabe: {aufgabe.title}"
        )
        aufgabe.conversation_id = conversation.id
    db.commit()

    flug, anbieter = await ai_run_service.vorflug(client, db, user)
    if flug is None:
        if anbieter is None:
            logger.info("Aufgabenlauf ohne Anbieter (task_id=%s)", aufgabe.id)
        else:
            logger.info(
                "Aufgabenlauf ohne API-Schluessel (provider_id=%s)", anbieter.id
            )
        return None

    run, fehler = lauf_beginnen(
        db,
        user=user,
        conversation=conversation,
        provider=flug.anbieter,
        request_id=uuid4(),
        content=_auftragstext(aufgabe),
        reasoning=flug.denken,
        reasoning_effort=flug.stufe,
        context_chars=flug.fenster.zeichen if flug.fenster.bekannt else None,
        guardian_briefing_unterdruecken=True,
        unbeaufsichtigt=True,
        rolle="voll",
    )
    if run is None:
        # Kontingent erschoepft, Schluessel nicht lesbar, Anfragekonflikt. Alles
        # Gruende, die beim naechsten Termin anders liegen koennen — deshalb
        # bleibt die Aufgabe eingeschaltet.
        logger.info(
            "Aufgabenlauf nicht begonnen (task_id=%s): %s",
            aufgabe.id, (fehler or ("unbekannt",))[0],
        )
        return None

    # **Der Rahmen erst jetzt — nach allem, was noch zurueckrollen kann.**
    #
    # Dieselbe Reihenfolge wie in der Guardian-Heilung, und aus demselben Grund:
    # ginge der Rahmen bei einem Rollback verloren, liefe der Lauf als
    # gewoehnlicher Chatlauf weiter. Voller Werkzeugsatz, `ask_user` erlaubt —
    # und niemand, der die Rueckfrage je beantwortet.
    zustand = ai_run_service.zustand_lesen(run)
    zustand["aufgabe"] = {
        "task_id": aufgabe.id,
        "kind": aufgabe.kind,
        "channel": aufgabe.channel,
        "title": aufgabe.title,
    }
    # Zusaetzlich der Worker-Rahmen: ueber ihn reicht `_lauf_nachbereiten` das
    # Ergebnis bei der Meldestelle ein (Zustellung in den Dauerchat, sobald
    # dort Ruhe ist), und ueber ihn erscheint der Lauf in der Worker-Leiste.
    # `kanal` bleibt fest "chat": die E-Mail des Zustellwegs `email`/`both`
    # verschickt der Aufgabenbericht (`ai_task_report`), nicht die Meldestelle
    # — sonst kaemen zwei Mails zu einem Lauf.
    zustand["worker"] = {
        "conversation_id": conversation.id,
        "titel": f"Aufgabe: {aufgabe.title}"[:120],
        "kanal": "chat",
    }
    ai_run_service.zustand_schreiben(run, zustand)
    aufgabe.last_run_id = run.id
    db.commit()

    if not ai_run_service.anlauf(db, run):
        return None
    return run


# ── Der Takt ──────────────────────────────────────────────────────────────
#
# Warum ein Takt und **kein** APScheduler-Auftrag je Aufgabe: der Jobstore des
# Panels liegt rein im Speicher (kein `SQLAlchemyJobStore`). Ein Auftrag je
# Aufgabe muesste nach jedem Neustart aus der Tabelle wiederhergestellt werden —
# dann ist die Tabelle ohnehin die Wahrheit und der Auftrag nur die Ausfuehrung.
# Ein Takt macht daraus eine Abfrage statt einer Synchronisation, und eine
# Abfrage kann nicht auseinanderlaufen.

#: Wieviele faellige Zeilen ein Durchlauf ueberhaupt ansieht.
MAX_ZEILEN_JE_DURCHLAUF = 20

#: Wieviele Laeufe ein Durchlauf hoechstens beginnt. Deutlich kleiner als die
#: Zeilenzahl: jeder Lauf ist ein Anbieteraufruf, und der Takt schlaegt jede
#: Minute erneut zu.
MAX_AUFGABEN_JE_DURCHLAUF = 5

#: Ab wann ein verpasster Termin nicht mehr nachgeholt, sondern uebersprungen
#: wird. Das Panel war aus — ein um elf Uhr nachgeholtes Nachtbackup ist
#: schlechter als gar keines, und eine Wetterauskunft von heute Nacht ist um
#: elf schlicht falsch.
MAX_VERZUG_MINUTEN = 60


def _anspruch_nehmen(
    db: Session, aufgabe: AiTask, *, gelesen: datetime | None, neu: datetime | None
) -> bool:
    """Schaltet den Termin weiter — **atomar und vor dem Lauf**.

    Die Bedingung `next_run_at = <gelesen>` ist die eigentliche Aussage: nur wer
    genau den Termin vorfindet, den er gelesen hat, hat ihn auch. Zwei
    gleichzeitige Durchlaeufe starten so einen Lauf und nicht zwei.

    MSM laeuft heute mit `--workers 1` (`msm.service.template`, `install.sh`),
    zwei Durchlaeufe koennen sich also nur ueber `max_instances=1` des Auftrags
    ueberhaupt begegnen — was der Scheduler verhindert. Die Bedingung kostet
    trotzdem nichts und haelt, falls sich das aendert.

    Der zweite und wichtigere Grund steht unabhaengig davon: **vor** dem Lauf
    weitergeschaltet zu haben ist die Schranke gegen eine heisse Schleife. Faellt
    der Prozess mitten im Lauf, findet der naechste Durchlauf einen Termin in der
    Zukunft und nicht denselben faelligen Termin ein weiteres Mal.
    """
    geschrieben = (
        db.query(AiTask)
        .filter(AiTask.id == aufgabe.id, AiTask.next_run_at == gelesen)
        .update(
            {"next_run_at": neu, "last_started_at": _jetzt(), "updated_at": _jetzt()},
            synchronize_session=False,
        )
    )
    db.commit()
    db.expire(aufgabe)
    return geschrieben == 1


async def faellige_aufgaben_bearbeiten(db: Session) -> int:
    """Ein Durchlauf: faellige Aufgaben ansehen und je einen Lauf beginnen.

    Gibt zurueck, wieviele Laeufe tatsaechlich begonnen wurden.

    Der Auftrag im Scheduler ruft das jede Minute. Alles ist je Aufgabe
    abgesichert — eine kaputte darf die uebrigen nicht mitnehmen, und nach oben
    darf gar nichts durchschlagen.
    """
    from services import ai_run_service

    if ai_run_service.http_client() is None:
        # Keine laufende Anwendung. Jeder einzelne Start wuerde daran scheitern;
        # die Termine bleiben stehen und werden gleich erneut angesehen.
        return 0

    jetzt = _jetzt()
    zeilen = (
        db.query(AiTask)
        .filter(
            AiTask.enabled.is_(True),
            AiTask.next_run_at.isnot(None),
            AiTask.next_run_at <= jetzt,
        )
        .order_by(AiTask.next_run_at.asc())
        .limit(MAX_ZEILEN_JE_DURCHLAUF)
        .all()
    )

    begonnen = 0
    for aufgabe in zeilen:
        if begonnen >= MAX_AUFGABEN_JE_DURCHLAUF:
            break
        try:
            begonnen += await _eine_aufgabe_bearbeiten(db, aufgabe, jetzt=jetzt)
        except Exception as exc:
            db.rollback()
            logger.warning(
                "Aufgabe fehlgeschlagen (task_id=%s): %s",
                getattr(aufgabe, "id", "?"), type(exc).__name__,
            )
    return begonnen


async def _eine_aufgabe_bearbeiten(db: Session, aufgabe: AiTask, *, jetzt: datetime) -> int:
    """Eine faellige Aufgabe: ueberspringen oder starten.

    Hier stand bis zum 20.08.2026 ein Vertagungsgrund: solange im Dauerchat ein
    Lauf aktiv war, blieb der Termin stehen. Seit die Aufgaben in einem eigenen
    Hintergrundfenster laufen, koennen sie niemandem mehr ins Wort fallen — der
    Grund ist mitsamt seiner Wartezeit entfallen.
    """
    gelesen = aufgabe.next_run_at
    faellig = utc(gelesen)
    if faellig is None:
        return 0

    # Weitergerechnet wird vom **faellig gewordenen Termin**, nicht von jetzt.
    # Sonst driftet ein Achtstundenplan mit jedem Lauf ein Stueck nach hinten:
    # der Takt schlaegt bis zu einer Minute spaet zu, und aus acht Stunden
    # werden ueber eine Woche fast neun. Genau diesen Fehler hatte der
    # Auto-Neustart in `scheduler_service` einmal.
    #
    # Liegt das Ergebnis immer noch in der Vergangenheit — das Panel war einen
    # Tag aus —, wird von jetzt gerechnet. Sonst arbeitete sich der Takt Termin
    # fuer Termin durch den Rueckstand, einen je Minute.
    neu = naechste_faelligkeit(aufgabe, ab=faellig)
    if neu is not None and utc(neu) <= jetzt:
        neu = naechste_faelligkeit(aufgabe, ab=jetzt)
    if not _anspruch_nehmen(db, aufgabe, gelesen=gelesen, neu=neu):
        # Ein anderer Durchlauf war schneller. Nicht protokollieren: mit
        # `max_instances=1` kann das gar nicht vorkommen, und eine Warnung ueber
        # einen unmoeglichen Fall waere Rauschen.
        return 0

    verzug = (jetzt - faellig).total_seconds() / 60
    if verzug > MAX_VERZUG_MINUTEN:
        # **Uebersprungen, nicht nachgeholt.** Der Termin ist bereits
        # weitergeschaltet; diese Aufgabe laeuft das naechste Mal planmaessig.
        logger.info(
            "Aufgabe uebersprungen, Termin %.0f Minuten alt (task_id=%s)",
            verzug, aufgabe.id,
        )
        return 0

    run = await aufgabenlauf_starten(db, aufgabe=aufgabe)
    if run is not None:
        return 1

    # Nichts angelegt und nichts verbraucht — kein Anbieter, kein Kontingent,
    # kein Schluessel. Bei einem wiederkehrenden Plan ist der naechste Termin
    # ohnehin schon gesetzt; bei einem **einmaligen** waere der Auftrag damit
    # still verschwunden, und genau darauf hat sich jemand verlassen ("erinnere
    # mich morgen um drei"). Der bekommt seinen Termin zurueck und wird bis zur
    # Verzugsgrenze weiter versucht — danach greift die Regel oben, und dann ist
    # er ehrlich vorbei statt ewig im Kreis.
    db.refresh(aufgabe)
    if aufgabe.enabled and aufgabe.plan_kind == "once":
        aufgabe.next_run_at = gelesen
        db.commit()
    return 0
