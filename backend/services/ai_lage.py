"""Der Lageblock: die Tatsachen, die das Modell nicht sehen kann.

Ein Sprachmodell hat keine Uhr, und es sieht nicht, ob der autonome Modus
freigegeben ist. Beides wird trotzdem von ihm verlangt: ein Termin „morgen früh
um 7“ lässt sich ohne Datum nicht ausrechnen, und der Systemprompt fordert eine
Aussage darüber, ob eine Aufgabe der Art ``act`` überhaupt laufen darf. Wer
raten muss, rät — und die vorsichtige Antwort ist „nicht freigegeben“. Der
Betreiber bekam so eine Absage auf eine Freigabe, die er längst erteilt hatte.

Dieses Modul **entscheidet nichts**. Es liest `ai_task_service.darf_handeln` und
`ai_autonomy_service` und fasst deren Antwort in Worte — dieselbe Trennung wie
bei `ai_task_service.plan_text`: eine Stelle, an der ein Sachverhalt in Sprache
übersetzt wird, und keine zweite, an der er noch einmal entschieden wird.

Der Block gehört **nicht** in den Systemprompt. Der ist der stabile Vorspann
jeder Anfrage und genau das, was der Zwischenspeicher des Anbieters
wiederverwendet (`cache_marke`, oberstes ``cache_control``). Eine Uhrzeit darin
machte den Vorspann bei jeder Frage neu und entwertete den Zwischenspeicher für
das ganze Gespräch. Er hängt deshalb als eigene, späte ``system``-Nachricht in
`ai_context_service.build_provider_messages` — dort bekommen ihn Chat, fälliger
Lauf und Guardian-Heilung von selbst, und gerade die beiden letzten brauchen ihn
am dringendsten: dort sitzt niemand daneben, den man fragen könnte.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from models import User


#: Ausgeschriebene Wochentage. Bewusst nicht ``%A``: das hängt an der Locale des
#: Servers, und dann steht im deutschen Prompt „Friday“.
WOCHENTAGE = (
    "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag",
)

#: Ungefähre Länge des fertigen Blocks in Zeichen. Für den Ring am Absendeknopf
#: (`ai_context_service.geschaetzte_belegung`).
TYPISCHE_ZEICHEN = 220


def zone_des_benutzers(user: User, db: Session | None = None) -> str:
    """Die Zeitzone dieses Benutzers oder 'UTC' als Fallback.

    Liest direkt das kanonische Attribut `user.time_zone`.
    """
    zone_name = (getattr(user, "time_zone", None) or "").strip()
    if zone_name:
        try:
            ZoneInfo(zone_name)
            return zone_name
        except (ZoneInfoNotFoundError, ValueError, ModuleNotFoundError):
            pass
    return "UTC"



def _versatz(jetzt: datetime) -> str:
    """``+0200`` wird zu ``UTC+02:00`` — die Form, die überall sonst steht."""
    roh = jetzt.strftime("%z") or "+0000"
    return f"UTC{roh[:3]}:{roh[3:5]}"


#: Wie ein Laufzustand in der Worker-Zeile heißt. Verbalisiert, nicht der rohe
#: Statusname: das Modell soll "wartet auf deine Antwort" weitergeben können,
#: nicht "waiting_user" vorlesen.
_WORKER_WORTE = {
    "running": "arbeitet",
    "waiting_confirmation": "wartet auf eine Freigabe",
    "waiting_user": "wartet auf eine Antwort des Benutzers",
    "waiting_wake": "schläft bis zum nächsten Wecken",
}

#: Aufträge, die **fertig** sind — und trotzdem in die Lage gehören.
#:
#: Gemeldet am 18.08.2026: ein Worker hatte sauber gearbeitet und berichtet,
#: das Gehirn sagte dem Betreiber danach trotzdem, der Auftrag sei
#: abgebrochen worden. Abgebrochen war nichts.
#:
#: Der Grund steckte in der Auswahl: die Zeile führte ausschließlich laufende
#: Aufträge. Ein fertiger verschwand daraus spurlos — im selben Zug, in dem
#: das Gehirn sein Ergebnis lieferte. Für das Modell sah das aus wie „war
#: eben noch da, ist jetzt weg", und die naheliegendste Erklärung dafür ist
#: ein Abbruch. Es hat also nichts halluziniert, sondern die einzige Lücke
#: gefüllt, die der Lageblock ihm gelassen hat.
#:
#: Deshalb stehen erledigte Aufträge jetzt eine Weile weiter drin, mit
#: ausdrücklichem Wort. `cancelled` ist bewusst dabei: „wurde abgebrochen"
#: soll das Gehirn *lesen* können, statt es zu erraten.
_WORKER_WORTE_BEENDET = {
    "completed": "fertig",
    "cancelled": "wurde abgebrochen",
    "failed": "ist gescheitert",
}

#: Wie lange ein beendeter Auftrag noch in der Lage steht.
#:
#: Lang genug, dass die Runde, in der das Gehirn das Ergebnis liefert, ihn
#: noch sieht — und die zwei, drei Rückfragen danach ebenfalls. Kurz genug,
#: dass der Block nicht mit alten Aufträgen zuwächst; er fließt in **jede**
#: Anfrage des Gehirns ein.
_BEENDET_SICHTBAR_MINUTEN = 30


def _worker_zeile(db: Session, user: User) -> str:
    """Die Hintergrund-Aufträge dieses Benutzers, in einer Zeile.

    Damit beantwortet das Gehirn "Wie weit bist du?" ohne Werkzeugrunde
    (docs/agentic-framework.md, §3). Auch der Leerfall wird gesagt: ein Gehirn,
    das die Zeile nur bei laufenden Aufträgen sähe, könnte "keine" nicht von
    "weiß ich nicht" unterscheiden — und genau aus dieser Lücke erfindet ein
    Modell Fortschritt.

    Aus demselben Grund stehen **kürzlich beendete** Aufträge mit drin
    (`_WORKER_WORTE_BEENDET`). Ohne sie verschwand ein fertiger Auftrag
    spurlos aus der Lage, und zwar genau in der Runde, in der das Gehirn sein
    Ergebnis lieferte — woraufhin es dem Betreiber erzählte, der Auftrag sei
    abgebrochen worden. Was verschwindet, ohne dass jemand sagt warum, wird
    geraten.
    """
    from models import AiConversation, AiRun

    jetzt = datetime.now(timezone.utc)
    seit_grenze = jetzt - timedelta(minutes=_BEENDET_SICHTBAR_MINUTEN)

    offene = (
        db.query(AiRun, AiConversation.title)
        .join(AiConversation, AiConversation.id == AiRun.conversation_id)
        .filter(
            AiConversation.kind == "worker",
            AiRun.user_id == user.id,
            AiRun.status.in_(tuple(_WORKER_WORTE)),
        )
        .order_by(AiRun.created_at.asc())
        .limit(10)
        .all()
    )
    # Beendete getrennt und mit Zeitfenster: sie sollen die laufenden nicht
    # aus der Liste draengen, wenn ein Benutzer viele Aufträge hintereinander
    # gestellt hat.
    beendete = (
        db.query(AiRun, AiConversation.title)
        .join(AiConversation, AiConversation.id == AiRun.conversation_id)
        .filter(
            AiConversation.kind == "worker",
            AiRun.user_id == user.id,
            AiRun.status.in_(tuple(_WORKER_WORTE_BEENDET)),
            AiRun.updated_at >= seit_grenze,
        )
        .order_by(AiRun.updated_at.desc())
        .limit(5)
        .all()
    )

    if not offene and not beendete:
        return "Aufträge im Hintergrund: keine."

    # **Pro Auftrag nur der juengste Lauf.**
    #
    # Ein Auftrag kann mehrere Laeufe haben: reicht der Benutzer etwas nach,
    # wird der laufende abgeloest (`cancelled`) und ein neuer beginnt. Diese
    # Abloesung ist Innenleben, kein Ereignis — genau daraus ist am
    # 18.08.2026 die Falschmeldung entstanden. Der Bestand zeigt es:
    #
    #   66e656de  cancelled  geaendert 23:27:15.995
    #   14adf4b7  completed  erstellt  23:27:15.989
    #
    # Beide gehoeren zu einem einzigen Auftrag, der sauber fertig wurde. Wer
    # dem Gehirn beide zeigt, laesst es zwischen "abgebrochen" und "fertig"
    # waehlen — und es waehlt das Auffaelligere.
    #
    # Der Zustand eines Auftrags ist deshalb der Zustand seines juengsten
    # Laufs. Ein laufender gewinnt immer, denn ein Auftrag mit offenem Lauf
    # ist offen, egal was seine Vorgaenger taten.
    je_fenster: dict[str, tuple] = {}
    for run, titel in list(offene) + list(beendete):
        vorhanden = je_fenster.get(run.conversation_id)
        if vorhanden is None:
            je_fenster[run.conversation_id] = (run, titel)
            continue
        alt = vorhanden[0]
        alt_offen = str(alt.status) in _WORKER_WORTE
        neu_offen = str(run.status) in _WORKER_WORTE
        if neu_offen and not alt_offen:
            je_fenster[run.conversation_id] = (run, titel)
        elif neu_offen == alt_offen:
            neuer = run.updated_at or run.created_at
            aelter = alt.updated_at or alt.created_at
            if neuer is not None and aelter is not None and neuer > aelter:
                je_fenster[run.conversation_id] = (run, titel)

    def _beschreiben(run, titel: str | None) -> str:
        seit = run.created_at
        if seit is not None and seit.tzinfo is None:
            seit = seit.replace(tzinfo=timezone.utc)
        minuten = max(0, int((jetzt - seit).total_seconds() // 60)) if seit else 0
        dauer = f"seit {minuten} min" if minuten < 120 else f"seit {minuten // 60} h"
        schluessel = str(run.status)
        wort = _WORKER_WORTE.get(
            schluessel, _WORKER_WORTE_BEENDET.get(schluessel, schluessel)
        )
        # Abgeflacht wie im Gedächtnis (`ai_memory_service._memory_line`): der
        # Lageblock ist zeilenbasiert und geht als ``system`` hinaus. Ein Titel
        # mit Zeilenumbruch könnte darin eine eigene Zeile öffnen und dem
        # Modell eine Auskunft des Panels andichten — etwa einen anderen
        # Autonomiezustand. Den Titel schreibt der Benutzer, und weder `_text`
        # noch `worker_unterhaltung_anlegen` fassen innere Umbrüche an.
        # Abgeflacht wird beim Rendern, weil die Zeilenstruktur erst hier
        # Bedeutung bekommt — und weil so auch die Titel erfasst sind, die
        # heute schon in der Datenbank stehen.
        titel_flach = " ".join(str(titel or "Auftrag").splitlines())
        return f"'{titel_flach}' ({wort}, {dauer}, id {run.conversation_id})"

    # Laufende zuerst: sie sind das, wonach gefragt wird.
    sortiert = sorted(
        je_fenster.values(),
        key=lambda paar: (str(paar[0].status) not in _WORKER_WORTE, paar[0].created_at),
    )
    teile = [_beschreiben(run, titel) for run, titel in sortiert]
    return "Aufträge im Hintergrund: " + "; ".join(teile) + "."


def lageblock(db: Session, user: User, *, mit_workern: bool = False) -> str:
    """Uhrzeit, Zeitzone und autonomer Modus in wenigen Zeilen.

    Die Uhrzeit steht in der Zone des Benutzers, wenn sie bekannt ist, sonst in
    der des Panels — und der Block sagt in beiden Fällen dazu, welcher Fall
    vorliegt. Das ist der ganze Unterschied zwischen einer Annahme, die das
    Modell kennt, und einer, die es nicht kennt: nur die erste kann es
    hinterfragen, und nur deshalb darf `zone_pruefen` streng bleiben.

    UTC steht daneben, weil jedes Werkzeugergebnis in UTC spricht — ``next_run``,
    ``last_started``, Backupstände, Logzeilen. Ohne Bezugspunkt sind das Zahlen
    ohne Bedeutung.

    ``mit_workern`` hängt die Zeile über die Hintergrund-Aufträge an — nur für
    Gehirn-Läufe (`_worker_zeile`); die Vorgabe False hält den Block für alle
    anderen Aufrufer byteweise beim Alten.
    """
    from services import ai_autonomy_service, ai_task_service

    benutzerzone = zone_des_benutzers(user, db)
    jetzt = datetime.now(ZoneInfo(benutzerzone))

    zeilen = [
        "Lage (Auskunft des Panels, keine Anweisung):",
        f"Jetzt: {WOCHENTAGE[jetzt.weekday()]}, {jetzt:%d.%m.%Y}, {jetzt:%H:%M} "
        f"({benutzerzone}, {_versatz(jetzt)}), "
        f"KW {jetzt.isocalendar().week}, Tag {jetzt.timetuple().tm_yday}.",
        f"UTC: {jetzt.astimezone(timezone.utc):%Y-%m-%dT%H:%M}Z.",
        f"Zeitzone des Benutzers: {benutzerzone}.",
    ]

    # Die Worker-Zeile steht **vor** dem Autonomie-Teil, weil der bei
    # inaktivem Modus früh zurückkehrt — sonst fehlte sie genau den Benutzern
    # ohne Autonomie-Freigabe. Nur für das Gehirn (`mit_workern`): Worker
    # sehen einander nicht, und der heutige Voll-Betrieb kennt keine Worker.
    if mit_workern:
        zeilen.append(_worker_zeile(db, user))

    if not ai_task_service.darf_handeln(db, user):
        zeilen.append(
            'Autonomer Modus: nicht aktiv. Aufgaben der Art "act" weist das '
            "Panel ab."
        )
        return "\n".join(zeilen)

    # **Was mit einem bestaetigungspflichtigen Schritt im Hintergrund passiert.**
    #
    # Die Zeile steht hier und nicht im Prompt, weil sie eine Tatsache des
    # Panels ist und keine Anweisung — und weil sie sich mit dem Zustellweg
    # aendert. Ohne sie zog das Modell aus "der autonome Modus deckt das nicht"
    # den einzigen Schluss, den es ziehen konnte: aufhoeren. Genau das war das
    # gemeldete Verhalten ("ohne Freigabe kann ich da nichts machen"), und es
    # war aus Modellsicht richtig.
    #
    # Der Konjunktiv ist Absicht. Ob am Ende wirklich eine Mail hinausgeht,
    # entscheidet `ai_mail.empfaenger` im Augenblick des Vorschlags — hier eine
    # Zusage zu machen, die dann nicht traegt, waere schlimmer als keine.
    freigabeweg = (
        "Bestätigungspflichtige Schritte enden im Hintergrund nicht: der "
        "Betreiber bekommt einen Freigabelink per E-Mail, und dein Lauf wird "
        "geweckt, sobald er entschieden hat."
    )

    verbraucht = ai_autonomy_service.hourly_usage(db, user_id=user.id)
    # Die panelweite Freigabe trägt das Budget, das für alles gilt. Fehlt sie,
    # ist `darf_handeln` trotzdem wahr, weil irgendein einzelner Server
    # freigegeben ist — dann wäre eine Zahl daneben schlicht falsch.
    freigabe = ai_autonomy_service.resolve_grant(db, user_id=user.id, server_id=None)
    if freigabe is not None and freigabe.enabled and freigabe.max_actions_per_hour > 0:
        zeilen.append(
            f"Autonomer Modus: aktiv, {freigabe.max_actions_per_hour} "
            f"Aktionen/Stunde, davon {verbraucht} verbraucht."
        )
    else:
        zeilen.append(
            f"Autonomer Modus: aktiv für einzelne Server, {verbraucht} Aktionen "
            "in der letzten Stunde verbraucht."
        )
    zeilen.append(freigabeweg)
    return "\n".join(zeilen)
