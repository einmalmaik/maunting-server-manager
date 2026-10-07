from __future__ import annotations

import logging
import json
from uuid import uuid4
from sqlalchemy.orm import Session
from fastapi import HTTPException
from models import User
from services import permission_service
from services.ai_action_errors import AiActionValidationError
from services.ai_redaction import redact_sensitive_text
from services.ai_tool_registry import (
    GLOBAL_READ_TOOLS,
    READ_TOOLS,
    WERKZEUGE,
    angebotsrechte,
)
from services.ai_tools.base import (
    _function,
    _resolve_server,
    MAX_QUESTION_OPTIONS,
    MAX_QUESTION_CHARS,
    MAX_OPTION_CHARS,
    MAX_OPTION_HINT_CHARS,
    MAX_DESKTOP_INHALT_CHARS,
    MAX_AUFRAEUM_PFADE,
)

logger = logging.getLogger(__name__)

def _desktop_tool_definitions() -> list[dict]:
    """Der Rechner des Benutzers (Smart System).

    Nur im Katalog, wenn die Bitte aus der Smart-System-App kam
    (`herkunft_schnitt`). Alle vier parken den Lauf, bis der Rechner
    geantwortet hat; das Ergebnis kommt danach als Meldung des Panels.

    **Bewusst die letzten Eintraege des Katalogs** (provider_tool_definitions
    haengt sie ans Ende): so ist der Panel-Katalog ein Byte-Praefix des
    Desktop-Katalogs — wie der Systemprompt, an den der DESKTOP-Block auch nur
    angehaengt wird. Anbieter-Caches arbeiten auf Praefixen; standen die vier
    mitten im Katalog, teilten sich Panel- und App-Laeufe fast nichts
    (test_desktop_werkzeuge_stehen_am_katalogende haelt das fest).
    """
    return [
        _function(
            "desktop_dateien",
            "Arbeitet mit Dateien im Sandbox-Ordner auf dem Benutzer-Rechner "
            "(Pfade immer relativ zur Sandbox). Gelöschtes landet im Papierkorb.",
            {
                "aktion": {
                    "type": "string",
                    "enum": ["auflisten", "lesen", "schreiben", "loeschen", "verschieben"],
                },
                "pfad": {
                    "type": "string",
                    "maxLength": 400,
                    "description": "Relativ zur Sandbox. Leer = Ordner selbst.",
                },
                "ziel": {
                    "type": "string",
                    "maxLength": 400,
                    "description": "Bei verschieben: neuer Pfad.",
                },
                "inhalt": {
                    "type": "string",
                    "maxLength": MAX_DESKTOP_INHALT_CHARS,
                    "description": "Bei schreiben: Dateiinhalt.",
                },
            },
            ["aktion"],
        ),
        _function(
            "desktop_launch_app",
            "Startet Programme oder Web-URLs im Standardbrowser des Benutzers.",
            {
                "programm": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Name des Programms, z. B. 'discord'.",
                },
                "url": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "Web-Adresse (http/https).",
                },
            },
            [],
        ),
        _function(
            "desktop_steuern",
            "Maus und Tastatur. Zuerst aktion='freigabe'. x/y sind Punkte im "
            "letzten Bildschirmfoto. Antwortet mit einem neuen Foto.",
            {
                "aktion": {
                    "type": "string",
                    "enum": [
                        "freigabe", "folge", "klick", "doppelklick", "rechtsklick",
                        "maus_halten", "maus_bewegen", "maus_relativ", "kamera_drehen",
                        "tippen", "taste", "taste_halten", "scrollen", "warten",
                    ],
                },
                "schritte": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {"type": "object"},
                    "description": "Bei folge: Aktionen mit ihren Feldern, der Reihe nach.",
                },
                "anliegen": {
                    "type": "string",
                    "maxLength": 300,
                    "description": "Nur bei freigabe: Grund in einem Satz.",
                },
                "minuten": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 30,
                    "description": "Nur bei freigabe: Geltungsdauer.",
                },
                "x": {"type": "integer", "description": "Bildpunkt X."},
                "y": {"type": "integer", "description": "Bildpunkt Y."},
                "dx": {"type": "integer", "description": "Relative X-Bewegung bei maus_relativ."},
                "dy": {"type": "integer", "description": "Relative Y-Bewegung bei maus_relativ."},
                "knopf": {
                    "type": "string",
                    "enum": ["links", "rechts", "mitte"],
                    "description": "Mausknopf bei maus_halten.",
                },
                "text": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "Text bei tippen; Taste(n) bei taste/taste_halten (z. B. 'w', 'shift+w').",
                },
                "dauer_ms": {
                    "type": "integer",
                    "minimum": 10,
                    "maximum": 10000,
                    "description": "Haltedauer in ms bei taste_halten / maus_halten.",
                },
                "menge": {
                    "type": "integer",
                    "description": "Scroll-Rasten oder Warte-Sekunden.",
                },
            },
            ["aktion"],
        ),
        _function(
            "desktop_system",
            "Sieht den Benutzer-Rechner an (lesend). aktion='laufwerke': Speicherplatz. "
            "aktion='verzeichnis': Ordnerinhalt. aktion='groesste': Platzfresser. "
            "aktion='bildschirm': Screenshot des Hauptbildschirms. aktion='virenscan': Virenprüfung. "
            "Pfade sind absolut.",
            {
                "aktion": {
                    "type": "string",
                    "enum": [
                        "laufwerke", "verzeichnis", "groesste",
                        "bildschirm", "virenscan",
                    ],
                },
                "pfad": {
                    "type": "string",
                    "maxLength": 400,
                    "description": "Absoluter Pfad.",
                },
            },
            ["aktion"],
        ),
        _function(
            "desktop_aufraeumen",
            "Löscht Pfade auf dem Rechner in den Papierkorb (auch außerhalb Sandbox). "
            "'papierkorb' ist Standard. 'endgueltig' nur auf ausdrücklichen Wunsch.",
            {
                "aktion": {
                    "type": "string",
                    "enum": ["papierkorb", "endgueltig", "papierkorb_leeren"],
                },
                "pfade": {
                    "type": "array",
                    "maxItems": MAX_AUFRAEUM_PFADE,
                    "items": {"type": "string", "maxLength": 400},
                    "description": "Absolute Pfade.",
                },
                "grund": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Begründung für den Benutzer.",
                },
            },
            ["aktion", "grund"],
        ),
        _function(
            "desktop_artifact",
            "Desktop-Artefakte (Software, Mods, Installer): download (HTTPS in "
            "Quarantäne), pruefen (SHA-256 und Defender), sandbox (Windows "
            "Sandbox), locator (Installationen finden), deploy (mit Snapshot), "
            "rollback, installer (Setup im Benutzerkontext), status.",
            {
                "aktion": {
                    "type": "string",
                    "enum": [
                        "download", "pruefen", "sandbox", "locator",
                        "deploy", "rollback", "installer", "status",
                    ],
                },
                "url": {
                    "type": "string",
                    "maxLength": 1000,
                    "description": "HTTPS.",
                },
                "artifact_id": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Aus download.",
                },
                "target_id": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Aus locator.",
                },
                "sha256": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Hash des Herausgebers.",
                },
                "installer_args": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 200},
                    "description": "Installer-Argumente.",
                },
            },
            ["aktion"],
        ),
    ]

def question_payload(arguments: dict) -> dict:
    """Prueft eine Rueckfrage und bringt sie in die Form fuer die Oberflaeche.

    Bewusst streng: Der Text landet unveraendert als Knopfbeschriftung im Chat,
    und ein Klick darauf wird zur naechsten Benutzernachricht. Ein Modell, das
    hier eine Anweisung an sich selbst unterbringt, wuerde sie sich also vom
    Benutzer bestaetigen lassen — deshalb laufen Frage und Beschriftungen durch
    dieselbe Redigierung wie jeder andere Modelltext.
    """
    if set(arguments) - {"question", "options"}:
        raise AiActionValidationError("Rueckfrage hat ungueltige Argumente")
    question = arguments.get("question")
    if not isinstance(question, str) or not question.strip():
        raise AiActionValidationError("Rueckfrage ohne Text")
    raw_options = arguments.get("options")
    if not isinstance(raw_options, list) or not 2 <= len(raw_options) <= MAX_QUESTION_OPTIONS:
        raise AiActionValidationError(
            f"Eine Rueckfrage braucht zwei bis {MAX_QUESTION_OPTIONS} Vorschlaege"
        )

    options: list[dict] = []
    for item in raw_options:
        if not isinstance(item, dict):
            raise AiActionValidationError("Vorschlag ist ungueltig")
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            raise AiActionValidationError("Vorschlag ohne Beschriftung")
        hint = item.get("hint")
        options.append({
            "label": redact_sensitive_text(label.strip())[:MAX_OPTION_CHARS],
            "hint": (
                redact_sensitive_text(hint.strip())[:MAX_OPTION_HINT_CHARS]
                if isinstance(hint, str) and hint.strip() else None
            ),
        })
    # Zwei gleich beschriftete Knoepfe sind keine Wahl.
    if len({option["label"] for option in options}) != len(options):
        raise AiActionValidationError("Die Vorschlaege muessen sich unterscheiden")

    return {
        "question": redact_sensitive_text(question.strip())[:MAX_QUESTION_CHARS],
        "options": options,
    }

def _execute_search_memory(db: Session, *, user: User, arguments: dict) -> dict:
    """Sucht im Gedaechtnis nach Bedeutung statt nach Wortgleichheit.

    Gesucht wird ausschliesslich in dem, was der Benutzer ohnehin sehen darf —
    `search_entries` nutzt denselben Sichtbarkeitsfilter wie der Abruf in den
    Kontext. Eine Suche kann damit nichts aufdecken, was ohne sie verborgen
    waere.

    Seit Stufe 2 (06.10.2026) liest die KI ihr Gedaechtnis nur noch;
    geschrieben und vergessen wird nach dem Gespraech, im Hintergrund
    (`ai_gedaechtnis_schreiber`). Ein Treffer sagt deshalb, **was** wo steht
    und von wem es kommt — Kennungen fuer einen Loeschaufruf braucht er nicht
    mehr.
    """
    from models import Team
    from services import ai_memory_service, team_service

    if not permission_service.has_global_permission(db, user, "ai.memory.use"):
        raise AiActionValidationError("Memory ist fuer diesen Benutzer nicht freigegeben")
    if set(arguments) - {"query"}:
        raise AiActionValidationError("Memory-Suche hat ungueltige Argumente")
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise AiActionValidationError("Suchbegriff fehlt")

    try:
        hits = ai_memory_service.search_entries(db, user, query)
    except HTTPException as exc:
        raise AiActionValidationError(str(exc.detail)) from exc
    # Titel und Altnamen in einem Sidecar-Aufruf statt einem je Treffer.
    ai_memory_service._schluessel_laden([row for row, _value, _score in hits])

    # Zu jedem Team-Treffer der Name, unter dem der Benutzer den Bereich kennt:
    # "in Alpha steht noch das alte Wartungsfenster" ist ein Satz, "in Team 7"
    # keiner. Er kommt aus `ansprechbarer_name` und nicht aus `team.name`, denn
    # Teamnamen sind nur je Gruender eindeutig — zwei Teams namens "Alpha"
    # stuenden sonst ununterscheidbar nebeneinander. Je Team einmal fragen,
    # nicht je Treffer: fuenfzehn Treffer aus einem Team sind der Normalfall.
    namen: dict[int, str | None] = {}

    def _teamname(team_id: int | None) -> str | None:
        """Der Name eines Teams, oder ``None``, wenn er sich nicht holen laesst."""
        if team_id is None:
            return None
        if team_id not in namen:
            team = db.get(Team, team_id)
            namen[team_id] = (
                team_service.ansprechbarer_name(db, user, team)
                if team is not None and team.name else None
            )
        return namen[team_id]

    results = []
    for row, value, _score in hits:
        treffer: dict = {"scope": row.scope, "text": value, "origin": row.origin}
        if row.titel:
            treffer["titel"] = row.titel
        if row.key:
            # Altbestand aus der Zeit vor den Saetzen: dort traegt der Name
            # einen Teil der Aussage ("vorlieben.getraenke: Mio Mio").
            treffer["key"] = row.key
        if row.server_id is not None:
            # Ohne die Nummer weiss das Modell nicht, zu welchem Server eine
            # Notiz gehoert, und wendet sie womoeglich auf den falschen an.
            treffer["server_id"] = row.server_id
        name = _teamname(row.team_id)
        if name is not None:
            treffer["team"] = name
        results.append(treffer)

    return {"untrusted": True, "query": query, "results": results}


def _execute_forget_skill(db: Session, *, user: User, arguments: dict) -> dict:
    """Loescht einen erlernten Skill — aufgeloest ueber das, was loeschbar ist.

    Frueher lief die Aufloesung ueber `read_body`, also ueber die
    Sichtbarkeitsueberlagerung aus `visible_skills`. Die kennt je Schluessel
    genau einen Gewinner, und bei Gleichstand — derselbe Schluessel panelweit
    **und** in einem Team — entscheidet die Zeilenreihenfolge der Datenbank,
    welcher das ist. Beim Lesen ist das hoechstens unscharf. Beim Loeschen ist
    es eine Zeile weniger auf der Platte, im schlechten Fall die panelweite,
    die fuer jeden Kunden gilt, waehrend die gemeinte Team-Zeile stehen bleibt.
    Umgekehrt war eine globale Zeile ueber dieses Werkzeug gar nicht mehr
    erreichbar, sobald ein Team-Skill sie verdeckte.

    Deshalb wird hier ueber `manageable_skills` aufgeloest: die Menge dessen,
    was dieser Benutzer wirklich veraendern darf. Bleibt mehr als ein Bereich
    uebrig, wird nicht geraten, sondern zurueckgefragt. Die Antwort kommt als
    `scope`/`team` zurueck, sonst waere die Rueckfrage eine Sackgasse.
    """
    from models import Team
    from services import ai_skill_service

    if not permission_service.has_global_permission(db, user, "ai.skills.use"):
        raise AiActionValidationError("Skills sind fuer diesen Benutzer nicht freigegeben")
    if set(arguments) - {"skill_key", "scope", "team"}:
        raise AiActionValidationError("Skill-Werkzeug hat ungueltige Argumente")
    skill_key = arguments.get("skill_key")
    if not isinstance(skill_key, str) or not skill_key.strip():
        raise AiActionValidationError("Ungueltiger Skill-Schluessel")
    wunsch_scope = arguments.get("scope")
    if wunsch_scope is not None and wunsch_scope not in {"global", "team"}:
        raise AiActionValidationError("Unbekannter Skill-Bereich")
    wunsch_team = arguments.get("team")
    if wunsch_team is not None and not isinstance(wunsch_team, str):
        raise AiActionValidationError("Ungueltiger Teamname")

    key = skill_key.strip().lower()
    # Zu jeder loeschbaren Zeile der Name, unter dem der Mensch den Bereich
    # kennt. Eine Team-ID ist fuer eine Rueckfrage wertlos — der Benutzer
    # antwortet mit dem Namen, den er im Panel sieht.
    treffer = []
    for row in ai_skill_service.manageable_skills(db, user):
        if row.skill_key != key:
            continue
        if row.team_id is None:
            treffer.append((row, "panelweit"))
            continue
        team = db.get(Team, row.team_id)
        if team is None:
            treffer.append((row, f"Team {row.team_id}"))
        elif team.personal_for_user_id == user.id:
            treffer.append((row, "persoenlich"))
        else:
            treffer.append((row, team.name))

    if not treffer:
        # Nichts, was dieser Benutzer loeschen darf. Warum, sagt der Blick auf
        # das, was er sehen darf — und nicht mehr: ein erratener fremder
        # Schluessel bleibt ein 404 ohne Existenzauskunft.
        try:
            view, _body = ai_skill_service.read_body(db, user, key)
        except HTTPException as exc:
            raise AiActionValidationError(str(exc.detail)) from exc
        if view.id is None:
            # Eine mitgelieferte Datei gibt es auf der Platte, nicht in der
            # Datenbank. Sie zu "loeschen" waere ein Versprechen, das das
            # naechste Update zurueckdreht.
            return {
                "forgotten": False,
                "reason": (
                    "Dieser Skill wird mit MSM ausgeliefert und laesst sich nicht "
                    "loeschen. Lege mit `learn_skill` unter demselben Schluessel "
                    "einen eigenen an, um ihn zu ersetzen."
                ),
            }
        raise AiActionValidationError("Diesen Skill darf dieser Benutzer nicht loeschen")

    kandidaten = treffer
    if wunsch_scope == "global":
        kandidaten = [paar for paar in kandidaten if paar[0].team_id is None]
    elif wunsch_scope == "team":
        kandidaten = [paar for paar in kandidaten if paar[0].team_id is not None]
    if isinstance(wunsch_team, str) and wunsch_team.strip():
        # Der Wunsch ist ein **Auswahlmittel, keine Berechtigung**: er darf nur
        # einen Eintrag aus der ohnehin loeschbaren Liste treffen.
        gesucht = wunsch_team.strip().casefold()
        kandidaten = [paar for paar in kandidaten if paar[1].casefold() == gesucht]
    if not kandidaten:
        raise AiActionValidationError("In diesem Bereich gibt es den Skill nicht")
    if len(kandidaten) > 1:
        # Zwei Zeilen, ein Name. Welche gemeint ist, weiss der Mensch und nicht
        # das Modell — und ein Fehlgriff ist hier nicht rueckgaengig zu machen.
        bereiche = sorted(bereich for _row, bereich in kandidaten)
        return {
            "forgotten": False,
            "skill_key": key,
            "scopes": bereiche,
            "ask_user": (
                f"Den Skill `{key}` gibt es in mehreren Bereichen: "
                + ", ".join(bereiche)
                + ". Frage nach, welcher gemeint ist, und rufe das Werkzeug "
                "erneut mit scope und team auf."
            ),
        }

    row, bereich = kandidaten[0]
    # **Dieselbe Schranke wie beim Überschreiben, nur am anderen Ende.**
    #
    # `upsert_skill` weist einen KI-Text ab, der einen von einem Menschen
    # geschriebenen Skill ersetzen will — was ein Mensch geschrieben hat,
    # überschreibt die KI nicht stillschweigend. Ohne diese Prüfung war
    # genau das in zwei Zügen zu haben: erst `forget_skill`, dann `learn_skill`
    # unter demselben Schlüssel — und wo die Vorgabe des Betreibers stand,
    # stand danach Modelltext, ohne dass jemand etwas bestätigt hat.
    #
    # Das wiegt schwerer als ein verlorener Absatz. Ein Skill wirkt in jedem
    # künftigen Lauf des Panels oder des Teams; eine präparierte Logzeile, die
    # das Modell zu genau diesen zwei Aufrufen bringt, wäre damit eine
    # dauerhafte Anweisung an alle. Und `upsert_skill` führt bewusst keine
    # Versionen — nach dem Löschen gibt es nichts zurückzuholen.
    #
    # Was die KI selbst gelernt hat, räumt sie weiter ohne Rückfrage weg; das
    # ist die Hälfte, die ihr gehört. Für die andere bleibt der Weg offen, den
    # ein Mensch ohnehin geht: `routers/ai_skills.py` löscht dieselbe Zeile
    # ohne diese Schranke.
    #
    # Antwortform wie beim mitgelieferten Skill: eine Absage mit Weg statt
    # einer Ausnahme. Ein `raise` würde das Modell eine Runde drehen lassen,
    # statt es dem Benutzer sagen zu lassen.
    if row.origin != "ai":
        return {
            "forgotten": False,
            "skill_key": row.skill_key,
            "scope": "global" if row.team_id is None else "team",
            "bereich": bereich,
            "reason": (
                "Diesen Skill hat ein Mensch geschrieben — du löschst ihn nicht "
                "und legst auch keinen ähnlichen zweiten an. Sag dem Benutzer, "
                "welchen Skill du für überholt hältst und warum; entfernen kann "
                "er ihn selbst in der Skill-Verwaltung des Panels."
            ),
        }
    # **Dieselbe Schranke, an der zweiten Tür.**
    #
    # `upsert_skill` lässt den Schalter `enabled` nur von einem Menschen
    # anfassen: ein abgeschalteter Skill bleibt abgeschaltet, auch wenn die KI
    # ihn unter demselben Schlüssel neu schreibt. Genau dafür ist Abschalten da
    # — es ist das Gegenmittel gegen einen per Injection gelernten Skill.
    #
    # Ohne diese Prüfung war es in zwei Zügen wieder weg: die abgeschaltete
    # Zeile stammt von der KI, sie durfte sie also löschen — und das direkt
    # folgende `learn_skill` landete im Anlege-Zweig, wo `enabled` wieder auf
    # ``True`` steht. Der Betreiber hätte dasselbe am nächsten Tag noch einmal
    # abgeschaltet, und wieder, ohne je zu erfahren, warum es zurückkommt.
    #
    # Es ist eine Zustandsprüfung und kein entzogenes Werkzeug: was die KI
    # gelernt hat und was gilt, räumt sie weiter ohne Rückfrage weg. Nur die
    # eine Zeile, über die ein Mensch bereits entschieden hat, bleibt liegen —
    # und der Weg dorthin ist derselbe wie oben.
    if not row.enabled:
        return {
            "forgotten": False,
            "skill_key": row.skill_key,
            "scope": "global" if row.team_id is None else "team",
            "bereich": bereich,
            "reason": (
                "Diesen Skill hat ein Mensch abgeschaltet; er wirkt bereits "
                "nicht mehr. Lösche ihn nicht und lege auch keinen ähnlichen "
                "zweiten an — entfernen kann er ihn selbst in der "
                "Skill-Verwaltung des Panels."
            ),
        }
    # Das Ergebnis entsteht **vor** dem Loeschen: nach `db.delete` und `commit`
    # sind die Attribute der Zeile nicht mehr abrufbar.
    #
    # `scope` und `bereich` gehoeren hinein, weil es sonst niemand erfaehrt:
    # ohne sie kann das Modell nicht berichten, ob die Team-Zeile oder die
    # panelweite Vorgabe verschwunden ist, und ein Irrtum faellt erst auf, wenn
    # jemand den Skill vermisst.
    ergebnis = {
        "forgotten": True,
        "skill_key": row.skill_key,
        "name": row.name,
        "scope": "global" if row.team_id is None else "team",
        "bereich": bereich,
    }
    try:
        # `origin="ai"` ist der ganze Zweck des Parameters: Skills sind nicht
        # versioniert, das Audit-Log ist die einzige Spur einer Löschung — und
        # ohne diese Angabe stand jede von der KI ausgelöste als Klick eines
        # Menschen im Panel darin. Nach einer per Injection ausgelösten Löschung
        # hätte niemand mehr unterscheiden können, wessen Hand es war.
        ai_skill_service.delete_skill(db, user=user, skill_id=row.id, origin="ai")
    except HTTPException as exc:
        raise AiActionValidationError(str(exc.detail)) from exc
    return ergebnis

def _execute_search_docs(arguments: dict) -> dict:
    """Volltextsuche ueber die Dokumentation dieses Panels.

    **Ohne zusaetzliches Recht.** Alle fuenf Seiten sind im Panel fuer jeden
    angemeldeten Benutzer erreichbar (`/docs/*` und `/privacy`); ein Gate hier
    waere eine Schranke, die es nebenan nicht gibt, und wuerde ausgerechnet die
    Belegpflicht dort aushebeln, wo sie am noetigsten ist — bei jemandem, der
    das Panel noch nicht kennt.

    Kein Treffer ist ein Ergebnis und wird auch so gemeldet: `found: 0` mit den
    durchsuchten Seiten. Eine leere Liste ohne diese Angabe laesst offen, ob
    nichts drinsteht oder nichts gelesen wurde.
    """
    from services import ai_docs_corpus

    if set(arguments) - {"query", "page"} or "query" not in arguments:
        raise AiActionValidationError("Doku-Suche hat ungueltige Argumente")
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise AiActionValidationError("Suchbegriff fehlt")
    page = arguments.get("page")
    if page is not None:
        if not isinstance(page, str) or page not in ai_docs_corpus.SEITEN:
            raise AiActionValidationError(
                f"Unbekannte Doku-Seite. Verfuegbar: {', '.join(sorted(ai_docs_corpus.SEITEN))}"
            )

    treffer = ai_docs_corpus.suche(query[:200], page)
    return {
        "untrusted": True,
        "query": query[:200],
        "searched_pages": [page] if page else sorted(ai_docs_corpus.SEITEN),
        "matches": treffer,
        "found": len(treffer),
    }

def _execute_read_docs(arguments: dict) -> dict:
    """Gliederung oder Abschnitt einer Doku-Seite. Rechtefrage wie oben.

    Eine unlesbare Quelle wird als solche gemeldet (`available: false`) und
    **nicht** als leerer Abschnitt. Das ist dieselbe Unterscheidung wie bei
    `web_search`: "steht nichts drin" und "konnte nicht lesen" sind zwei
    Auskuenfte, und nur eine davon darf beim Benutzer ankommen.
    """
    from services import ai_docs_corpus

    if set(arguments) - {"page", "section"} or "page" not in arguments:
        raise AiActionValidationError("Doku-Werkzeug hat ungueltige Argumente")
    page = arguments.get("page")
    if not isinstance(page, str) or page not in ai_docs_corpus.SEITEN:
        raise AiActionValidationError(
            f"Unbekannte Doku-Seite. Verfuegbar: {', '.join(sorted(ai_docs_corpus.SEITEN))}"
        )
    section = arguments.get("section")
    if section is not None and not isinstance(section, str):
        raise AiActionValidationError("Ungueltige Abschnittskennung")

    try:
        if not section:
            return {"untrusted": True, "available": True, **ai_docs_corpus.verzeichnis(page)}
        return {"untrusted": True, "available": True, **ai_docs_corpus.abschnitt(page, section)}
    except ai_docs_corpus.DokuNichtVerfuegbar as exc:
        # Bewusst kein Fehler: das Modell soll den Ausfall benennen koennen,
        # statt den Zug zu verlieren und im naechsten Anlauf zu raten.
        return {
            "untrusted": True,
            "available": False,
            "page": page,
            "reason": str(exc),
        }
    except KeyError as exc:
        vorhanden = [a["section"] for a in ai_docs_corpus.verzeichnis(page)["sections"]]
        raise AiActionValidationError(
            f"Abschnitt {exc.args[0]!r} gibt es auf dieser Seite nicht. "
            f"Vorhanden: {', '.join(vorhanden)}"
        ) from exc

def _execute_read_skill(db: Session, *, user: User, arguments: dict) -> dict:
    """Laedt den Text eines Skills — Stufe zwei des schrittweisen Ladens.

    Die Sichtbarkeitspruefung liegt vollstaendig in
    `ai_skill_service.read_body`: ein erratener Schluessel eines fremden Teams
    endet dort mit 404, ohne zu verraten, ob es ihn gibt.

    Der Text wird als **untrusted** zurueckgegeben. Ein Team-Skill ist woertlich
    Text, den ein anderer Mensch geschrieben hat und der hier in den Kontext
    dieses Benutzers geladen wird — er ist eine Anleitung, keine Anweisung.
    """
    from services import ai_skill_service

    if not permission_service.has_global_permission(db, user, "ai.skills.use"):
        raise AiActionValidationError("Skills sind fuer diesen Benutzer nicht freigegeben")
    if set(arguments) - {"skill_key"}:
        raise AiActionValidationError("Skill-Werkzeug hat ungueltige Argumente")
    skill_key = arguments.get("skill_key")
    if not isinstance(skill_key, str) or not skill_key.strip():
        raise AiActionValidationError("Ungueltiger Skill-Schluessel")

    try:
        view, body = ai_skill_service.read_body(db, user, skill_key)
    except HTTPException as exc:
        raise AiActionValidationError(str(exc.detail)) from exc
    return {
        "untrusted": True,
        "skill_key": view.skill_key,
        "name": view.name,
        "scope": view.scope,
        "body": body,
    }

def _execute_learn_skill(db: Session, *, user: User, arguments: dict) -> dict:
    """Laesst die KI eine Vorgehensweise dauerhaft festhalten.

    Das Versprechen "die KI lernt selbst" steht und faellt hier: es gibt keine
    Bestaetigung, kein Formular, keinen Knopf. Vertretbar ist das, weil Prosa
    nichts ausfuehrt — der Skill aendert die Herangehensweise des Modells, nicht
    seine Rechte.

    Das Ziel bestimmt der Dienst, nicht das Modell. Welchem Team jemand
    angehoert, ist eine Tatsache der Datenbank; eine Team-Nummer aus einem
    Prompt waere eine Angabe aus einer Quelle, die ein Angreifer beeinflussen
    kann.
    """
    from services import ai_learning_policy, ai_skill_service, team_service

    if not permission_service.has_global_permission(db, user, "ai.skills.use"):
        raise AiActionValidationError("Skills sind fuer diesen Benutzer nicht freigegeben")
    if set(arguments) - {"skill_key", "name", "description", "body", "scope", "team"}:
        raise AiActionValidationError("Skill-Werkzeug hat ungueltige Argumente")

    scope = arguments.get("scope")
    if scope not in {"team", "global"}:
        raise AiActionValidationError("Unbekannter Skill-Bereich")
    for field in ("skill_key", "name", "description", "body"):
        if not isinstance(arguments.get(field), str) or not arguments[field].strip():
            raise AiActionValidationError(f"Skill-Feld \"{field}\" fehlt oder ist leer")

    team_id: int | None = None
    status = "active"
    if scope == "global":
        may_manage = permission_service.has_global_permission(db, user, "ai.skills.manage")
        resolved = ai_learning_policy.resolve_global_status(may_manage)
        if resolved is None:
            # Globales Lernen ist abgeschaltet. Kein Fehler, sondern ein
            # Hinweis: das Modell soll es ins Team schreiben statt aufzugeben.
            return {
                "learned": False,
                "reason": (
                    "Globales Lernen ist auf diesem Panel abgeschaltet. "
                    "Lege den Skill mit scope='team' an."
                ),
            }
        status = resolved
    else:
        target, question = team_service.learning_team(
            db, user, schalter="skills", wunsch=arguments.get("team"),
        )
        if target is None:
            return {"learned": False, "ask_user": question}
        team_id = target.id

    try:
        row = ai_skill_service.upsert_skill(
            db, user=user, skill_key=arguments["skill_key"], name=arguments["name"],
            description=arguments["description"], body=arguments["body"],
            team_id=team_id, origin="ai", status=status,
            # Auf dem globalen Weg **ist** die Lernpolitik die Berechtigung:
            # `resolve_global_status` hat die Entscheidung des Betreibers
            # bereits umgesetzt — "off" endet oben, "review" ohne
            # `ai.skills.manage` landet in der Warteschlange, "instant" ist die
            # ausdrueckliche Freigabe fuer jedes Gespraech. Eine zweite Pruefung
            # gegen `ai.skills.manage` wuerde zwei dieser drei Faelle
            # unerreichbar machen.
            #
            # Der Team-Weg behaelt seine Pruefung: dort entscheidet der
            # Schalter in der Mitgliedschaft, nicht der Betreiber.
            skip_permission_check=(scope == "global"),
        )
    except HTTPException as exc:
        raise AiActionValidationError(str(exc.detail)) from exc

    return {
        "learned": True,
        "skill_key": row.skill_key,
        "name": row.name,
        "scope": "global" if row.team_id is None else "team",
        "status": row.status,
        "note": (
            "Der Skill wartet auf die Freigabe des Betreibers und wirkt bis "
            "dahin nicht." if row.status == "pending" else None
        ),
    }

def _execute_web_search(
    db: Session, *, user: User, arguments: dict, prefetch_session_id: str | None = None
) -> dict:
    """Websuche im Namen des Benutzers.

    Die Rechtegrenze ist `ai.web_search.use` — und sie ist die **einzige**.
    Wer das Recht hat, darf suchen lassen; wer es nicht hat, nicht. Sonst
    entscheidet nichts mehr mit.

    **Hier stand einmal eine zweite Grenze, und sie ist ersatzlos gefallen.**
    `docs_searchable` liess die Herkunft des Blueprints darueber entscheiden:
    mitgeliefert hiess suchbar, selbst importiert hiess gesperrt, mit der
    Annahme "nativ = oeffentlich dokumentiert, community = privater
    Discord-Bot". Im Betrieb ist sie umgekippt. Ein selbst gepflegter
    ARK-Blueprint ist community und beschreibt trotzdem ein Spiel mit
    oeffentlichem Wiki — die Suche war dort gesperrt, das Modell fiel auf sein
    Trainingswissen zurueck und schrieb Werte in eine Datei, die es so nicht
    gab.

    Die Vorgabe des Betreibers ist deshalb ausnahmslos: die Websuche ist ein
    Merkmal, das immer funktioniert. Sie gilt nicht nur fuer Spielserver,
    sondern fuer alles, was MSM verwaltet — und je weiter das reicht (Anwendungs-
    server, spaeter Geraete im Haus), desto weniger laesst sich vorab
    aufzaehlen, wozu es oeffentliche Dokumentation gibt. Eine Erlaubnisliste
    waere genau die Sorte Pflegeposten, deren Vergessen still die
    Antwortqualitaet senkt.

    Was den Wegfall traegt: die Anfrage wird geschwaerzt, bevor sie das Panel
    verlaesst (siehe unten). Der Schutz haengt damit an dem, was tatsaechlich
    hinausgeht, statt an einer Vermutung darueber, was ein Servertyp wohl ist.
    """
    from services import ai_web_search_service

    if not permission_service.has_global_permission(db, user, "ai.web_search.use"):
        raise AiActionValidationError("Websuche ist fuer diesen Benutzer nicht freigegeben")
    if set(arguments) - {"query", "count", "server_id"}:
        raise AiActionValidationError("Websuche hat ungueltige Argumente")

    server_id = arguments.get("server_id")
    if server_id is not None and server_id != "" and server_id != 0:
        _resolve_server(db, user, {"server_id": server_id})

    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise AiActionValidationError("Suchanfrage ist leer")
    count = arguments.get("count", ai_web_search_service.MAX_RESULTS)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= ai_web_search_service.MAX_RESULTS:
        raise AiActionValidationError("Ungueltige Trefferanzahl")

    # Die Anfrage geht an einen fremden Dienst und wird dort protokolliert.
    # Solange die Herkunftssperre bestand, war sie der faktische Schutz davor,
    # dass dabei etwas Vertrauliches mitfaehrt; sie faellt weg, der Schutz
    # nicht. Dieselbe Schwaerzung wie bei den Treffern, nur eine Richtung
    # frueher.
    #
    # Sie ist bewusst wertbezogen: `ServerAdminPassword` als *Wort* bleibt
    # stehen, `ServerAdminPassword=Maik1234` verliert den Wert. Andersherum
    # waere die Suche fuer ihren haeufigsten Zweck unbrauchbar — nach dem Namen
    # einer Einstellung zu suchen ist der Normalfall, nicht die Ausnahme.
    sichere_anfrage = redact_sensitive_text(query.strip())

    try:
        if prefetch_session_id:
            results = ai_web_search_service.search(
                sichere_anfrage, count,
                cache_scope=f"voice:{user.id}:{prefetch_session_id}",
            )
        else:
            results = ai_web_search_service.search(sichere_anfrage, count)
    except ai_web_search_service.WebSearchUnavailable as exc:
        # Ehrlich melden statt eine leere Trefferliste liefern: "nichts
        # gefunden" waere eine falsche Aussage ueber das Web.
        return {"available": False, "reason": exc.code, "results": []}
    return {"available": True, "query": sichere_anfrage[:200], "results": results}
