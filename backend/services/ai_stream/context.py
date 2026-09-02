# -*- coding: utf-8 -*-
"""Kontext- und Zustandsextraktion fuer AI-Runs."""

from __future__ import annotations

from datetime import datetime
import logging
from uuid import UUID

from models import AiProvider, AiRun, User
from database import SessionLocal
from services import ai_model_catalog, ai_reasoning, permission_service
from services.ai_proposal_service import AufgabenKontext, GuardianKontext
from services.ai_stream.types import GuardianRahmenUnlesbar, _Vorbereitung

logger = logging.getLogger(__name__)


def guardian_aus_zustand(zustand: dict) -> GuardianKontext | None:
    """Baut den Guardian-Rahmen aus dem Arbeitsgedaechtnis des Laufs.

    Er wird beim Start hineingeschrieben und bei **jeder** Runde daraus wieder
    hergestellt — nicht einmal ermittelt und in einer Variablen gehalten. Der
    Grund ist derselbe wie bei `reasoning_effort`: ein Lauf ueberlebt den
    Prozess nicht, aber er ueberlebt Minuten und Fortsetzungen, und was mitten
    in einer Aufgabe gilt, muss aus derselben Quelle kommen wie am Anfang.

    Kein Schluessel ``guardian`` heisst: ein Mensch hat getippt, es gilt der
    gewoehnliche Chatlauf.

    Ein **vorhandener, aber unlesbarer** Rahmen ist etwas anderes und wirft.
    Hier stand zuerst, ``None`` sei auch dafuer die sichere Richtung — "ohne
    Rahmen greifen die Verschaerfungen nicht, aber es wird auch nichts erlaubt,
    was sonst verboten waere". Das war falsch herum gedacht: in einer Heilung ist
    die Werkzeugmenge **enger** als im Chat, der Server ist fest, und vor jedem
    Eingriff steht ein Backup-Nachweis. Faellt der Rahmen weg, faellt all das
    weg — und zwar in einem Lauf, in dem niemand mitliest, im Namen des
    Freigebers und mit dessen Rechten. Der Verlust des Rahmens ist die
    gefaehrliche Richtung, nicht die sichere.
    """
    roh = zustand.get("guardian")
    if roh is None:
        return None
    if not isinstance(roh, dict):
        raise GuardianRahmenUnlesbar("Guardian-Rahmen ist kein Woerterbuch")
    try:
        return GuardianKontext(
            server_id=int(roh["server_id"]),
            incident_id=int(roh["incident_id"]),
            # `backup_anker` ist der Beginn des Heilungslaufs und damit der
            # ehrliche Nachweiszeitpunkt; `incident_created_at` bleibt als
            # Rueckfall fuer Laeufe, die vor dieser Aenderung angelegt wurden.
            incident_created_at=datetime.fromisoformat(
                str(roh.get("backup_anker") or roh["incident_created_at"])
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GuardianRahmenUnlesbar("Guardian-Rahmen im Laufzustand unlesbar") from exc


def aufgabe_aus_zustand(zustand: dict) -> AufgabenKontext | None:
    """Baut den Aufgabenrahmen aus dem Arbeitsgedaechtnis des Laufs.

    Wortgleich zur Ueberlegung bei `guardian_aus_zustand`, und aus demselben
    Grund eine eigene Funktion: die beiden Rahmen schliessen sich nicht
    gegenseitig aus, sie kommen nur nie zusammen vor.

    Ein **vorhandener, aber unlesbarer** Rahmen wirft. Die Richtung ist hier
    dieselbe wie dort: ohne Rahmen faellt die Werkzeugeinengung weg, `ask_user`
    wird wieder moeglich (und parkt den Lauf, den niemand aufweckt), und offene
    Vorschlaege werden geparkt statt zurueckgenommen. Der Verlust des Rahmens
    ist die gefaehrliche Richtung, nicht die sichere.
    """
    roh = zustand.get("aufgabe")
    if roh is None:
        return None
    if not isinstance(roh, dict):
        raise GuardianRahmenUnlesbar("Aufgabenrahmen ist kein Woerterbuch")
    try:
        return AufgabenKontext(
            task_id=str(roh["task_id"]),
            kind=str(roh["kind"]),
            channel=str(roh["channel"]),
            title=str(roh.get("title") or ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GuardianRahmenUnlesbar("Aufgabenrahmen im Laufzustand unlesbar") from exc


def rolle_aus_zustand(zustand: dict) -> str:
    """Die Rolle dieses Laufs — eingefroren wie Denkstufe und Kontextfenster.

    ``lauf_beginnen`` schreibt sie beim Anlegen; ein Lauf ohne den Schluessel
    stammt aus der Zeit davor und ist ein gewoehnlicher Chatlauf ("voll").
    Eingefroren mit Absicht: der Systemprompt in den `provider_messages` ist
    bereits nach dieser Rolle geschnitten, und ein Katalog, der mitten im Lauf
    die Rolle wechselte (weil der Betreiber `worker_model` umgestellt hat),
    passte nicht mehr zu dem Prompt, unter dem der Lauf angefangen hat.

    Ein unbekannter Wert faellt auf "worker" — die **engste** Rolle. Der
    Verlust des Rahmens ist die gefaehrliche Richtung, nicht die sichere
    (dieselbe Ueberlegung wie bei `guardian_aus_zustand`): ein Tippfehler, der
    stillschweigend den vollen Katalog oeffnete, waere genau die Luecke, die
    die Rollentrennung schliessen soll.
    """
    roh = zustand.get("rolle")
    if roh is None:
        return "voll"
    rolle = str(roh)
    if rolle not in ("voll", "gehirn", "worker"):
        return "worker"
    return rolle


def herkunft_aus_zustand(zustand: dict) -> str:
    """Von wo die Bitte kam: aus dem Panel oder aus der Smart-System-App.

    Eingefroren wie die Rolle und aus demselben Grund — der Katalog ist danach
    geschnitten, und ein Lauf, der die Herkunft mitten im Zug wechselte, saehe
    Werkzeuge, unter denen er nicht angefangen hat.

    Ein unbekannter Wert faellt auf "panel", die **engere** Herkunft: sie
    verliert die Desktop-Werkzeuge. Bis zum 21.08.2026 fiel er auf "desktop",
    weil damals dort die Serverwerkzeuge fehlten — mit der umgedrehten Matrix
    (`ai_tool_registry.herkunft_schnitt`) ist "desktop" die weitere Seite, und
    die Rueckfallrichtung dreht mit. Wie bei der Rolle gilt: ein Tippfehler
    darf keinen Rahmen oeffnen, den niemand gesetzt hat.
    """
    return "desktop" if str(zustand.get("herkunft")) == "desktop" else "panel"


def familie_aus_zustand(zustand: dict) -> str | None:
    """**Welches** Geraet den Lauf angestossen hat, als Refresh-Familie.

    Eingefroren neben der Herkunft und aus demselben Grund: der Lauf schlaeft
    zwischen seinen Segmenten in der Datenbank, und wenn er aufwacht, ist von
    der Anfrage, die ihn begonnen hat, nichts mehr da. Die Herkunft sagt
    "App oder Panel", diese hier sagt "welcher Rechner" — und nur mit ihr
    landet ein Auftrag bei dem Geraet, an dem der Mensch sitzt, statt bei dem,
    das zuerst nach Arbeit fragt (`desktop_job_service.naechster`).

    ``None`` heisst "nicht bekannt" und ist kein Rueckfall auf eine engere
    Seite — hier gibt es keine: die Familie entscheidet nichts ueber Rechte
    und nichts ueber den Werkzeugkatalog, sie adressiert nur. Ein Auftrag ohne
    sie ist von jedem Geraet des Benutzers abholbar, so wie vor dieser Spalte.
    """
    roh = zustand.get("familie")
    return str(roh) if roh else None


def worker_aus_zustand(zustand: dict) -> dict | None:
    """Der Worker-Rahmen: Fenster, Titel und Meldekanal des Auftrags.

    Anders als Guardian- und Aufgabenrahmen traegt er keine Verschaerfung —
    die haengt an der Rolle (`rolle_aus_zustand`), die `lauf_beginnen` aus der
    Fensterart ableitet und einfriert. Der Rahmen traegt nur die Zustelldaten
    der Meldung. Ein unlesbarer Rahmen ist deshalb kein Abbruchgrund: der
    Lauf bleibt eingeengt, nur die Meldung faellt auf ihre Rueckfaelle
    (Kanal "chat", Titel des Fensters).
    """
    roh = zustand.get("worker")
    return roh if isinstance(roh, dict) else None


def _modell_fuer(provider: AiProvider, rolle: str) -> str:
    """Das Modell dieses Laufs: Worker arbeiten auf dem Arbeitsmodell.

    `worker_model` ist die vierte Funktion eines Zugangs (docs/agentic-
    framework.md, §5); ``None`` heisst Ein-Modell-Betrieb, und dann faehrt
    auch ein Worker auf `default_model`. Alle vier Stellen, die ein Modell
    nennen (zwei Reservierungen, zwei Nachrichten), und der Katalog-Abgleich
    gehen durch diese eine Funktion — eine vergessene Stelle hiesse: gebucht
    wird das eine Modell, gearbeitet auf dem anderen.
    """
    if rolle == "worker" and provider.worker_model:
        return str(provider.worker_model)
    return str(provider.default_model)


def _denken_am_modell(
    vorbereitung: _Vorbereitung, modell
) -> tuple[bool, str | None]:
    """Die eingefrorene Denkstufe, geprüft gegen das Modell von **jetzt**.

    Zwei Dinge in diesem Lauf haben verschiedene Lebensdauern, und das ist
    Absicht: die Denkstufe kommt aus `AiRun` und bleibt über alle Segmente
    stehen, damit eine Fortsetzung nach einer Bestätigung dieselbe Tiefe
    behält (`_segment_vorbereiten`). Der Zugang dagegen wird je Segment frisch
    gelesen — der Betreiber darf ihn korrigieren, während ein Lauf geparkt ist.

    Beides zusammen ergibt eine Lage, die keiner der beiden Regeln einfällt:
    das ``default_model`` wechselt mitten im Lauf, und die gespeicherte Stufe
    gehört zum alten Modell. ``xhigh`` an einem Modell, das nur ``low`` und
    ``high`` führt, ist ein ``400`` — und zwar bei **jedem** weiteren Segment,
    also ein Lauf, der nie wieder anläuft.

    Deshalb hier eine Prüfung und keine Neuberechnung. Der Unterschied ist
    wichtig: neu geklemmt würde auch ein zwischenzeitlich geänderter
    Rollendeckel mitten in einer Aufgabe wirken, und schlimmer noch, ein
    fehlendes Wort (`None`) würde plötzlich zur Vorgabe des Modells aufgefüllt
    — nach **oben**, am ursprünglichen Deckel vorbei. Angefasst wird also nur,
    was das jetzige Modell nicht annehmen kann, und die eingefrorene Stufe ist
    dabei die Decke: ``deckel=rang(stufe)``. Tiefer geht immer, teurer nie.

    Schweigt der Katalog, bleibt alles wie eingefroren. Eine Stufe wegen einer
    Netzstörung fallen zu lassen wäre dieselbe stille Verteuerung, gegen die
    `ai_reasoning._aus` geschrieben ist.
    """
    aktiv, stufe = vorbereitung.reasoning, vorbereitung.reasoning_effort
    if modell is None:
        return aktiv, stufe
    if not modell.denkt:
        # Getauscht gegen ein Modell ohne Denkvermögen: dort ist jedes
        # ``reasoning_effort`` ein ``400``, ``none`` eingeschlossen.
        return False, None
    if stufe is None or stufe in modell.stufen:
        return aktiv, stufe
    if stufe == ai_reasoning.AUS_STUFE:
        # „Aus“ ist selbst nur ein Wort, und nicht jedes Modell führt es. Beim
        # neuen Modell heißt dasselbe womöglich „gar kein Feld“ — oder, bei
        # Denkzwang, „so flach wie es geht“. Ein Deckel von ``MIN_RANG`` sagt
        # genau das, und zwar in derselben Funktion wie überall sonst.
        return ai_reasoning.klemmen(
            modell, wunsch=None, aktiv=False, deckel=ai_reasoning.MIN_RANG
        )
    return ai_reasoning.klemmen(
        modell, wunsch=stufe, aktiv=aktiv, deckel=ai_reasoning.rang(stufe)
    )


def _rolle_ableiten(
    db, user: User, conversation, provider: AiProvider, unbeaufsichtigt: bool
) -> str:
    """Welche Rolle ein neuer Lauf bekommt (docs/agentic-framework.md, §3/§5).

    Ein Worker-Fenster traegt seine Rolle in der Fensterart — das ist die
    verlaesslichste Quelle, und sie gilt auch fuer die Antwort auf eine
    Rueckfrage (`worker_antwort`). Der Dauerchat wird zum Gehirn, sobald der
    Betreiber ein Arbeitsmodell hinterlegt hat — aber nur fuer Zuege mit
    einem Menschen davor: faellige Auftraege und Heilungen behalten den
    heutigen Voll-Betrieb samt ihrer eigenen Werkzeugschnitte. Ohne
    `worker_model` gilt der Ein-Modell-Betrieb ("voll"), kein Hard-Stop.

    Das Recht `ai.background.use` gehoert mit in die Ableitung, nicht nur in
    das Werkzeugangebot: ein Gehirn, dessen Benutzer keine Worker starten
    darf, haette **gar keinen** Arbeitsweg mehr — sein Katalog schrumpfte
    auf das Gedaechtnis, und jede Sachfrage endete in einer Entschuldigung.
    Wem das Recht fehlt, dessen Chat arbeitet wie bisher in einem Lauf
    (derselbe Fallback wie ohne `worker_model`).
    """
    kind = str(getattr(conversation, "kind", "primary") or "primary")
    if kind == "worker":
        return "worker"
    if kind == "primary" and not unbeaufsichtigt and provider.worker_model:
        from services import permission_service

        if permission_service.has_global_permission(db, user, "ai.background.use"):
            return "gehirn"
    return "voll"

