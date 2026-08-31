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
    _MEMORY_TEAM_SCHEMA,
    _MEMORY_KEY_RE,
    _MAX_SCOPE_ENTRIES,
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
    Desktop-Katalogs â€” wie der Systemprompt, an den der DESKTOP-Block auch nur
    angehaengt wird. Anbieter-Caches arbeiten auf Praefixen; standen die vier
    mitten im Katalog, teilten sich Panel- und App-Laeufe fast nichts
    (test_desktop_werkzeuge_stehen_am_katalogende haelt das fest).
    """
    return [
        _function(
            "desktop_dateien",
            "Arbeitet mit Dateien im Sandbox-Ordner auf dem Benutzer-Rechner "
            "(Pfade immer relativ zur Sandbox). GelÃ¶schtes landet im Papierkorb.",
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
            "Ãœbernimmt Maus und Tastatur. Starte mit aktion='freigabe': "
            "im autonomen Modus sofort erteilt, sonst vom Benutzer bestÃ¤tigt. "
            "Koordinaten sind Bildpunkte des Bildschirmfotos (Ursprung links oben, "
            "Hauptbildschirm). Vor Klicks mit desktop_system(aktion='bildschirm') prÃ¼fen.",
            {
                "aktion": {
                    "type": "string",
                    "enum": [
                        "freigabe", "klick", "doppelklick", "rechtsklick",
                        "maus_halten", "maus_bewegen", "maus_relativ", "kamera_drehen",
                        "tippen", "taste", "taste_halten", "scrollen", "warten",
                    ],
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
            "aktion='bildschirm': Screenshot des Hauptbildschirms. aktion='virenscan': VirenprÃ¼fung. "
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
            "LÃ¶scht Pfade auf dem Rechner in den Papierkorb (auch auÃŸerhalb Sandbox). "
            "'papierkorb' ist Standard. 'endgueltig' nur auf ausdrÃ¼cklichen Wunsch.",
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
                    "description": "BegrÃ¼ndung fÃ¼r den Benutzer.",
                },
            },
            ["aktion", "grund"],
        ),
        _function(
            "desktop_artifact",
            "Verwaltet Desktop-Artefakte (Software, Mods, Installer). "
            "aktion='download': LÃ¤dt Datei via HTTPS in QuarantÃ¤ne. "
            "aktion='pruefen': SHA-256- und Defender-Scan. "
            "aktion='sandbox': Startet isolierte Windows Sandbox zur PrÃ¼fung. "
            "aktion='locator': Sucht Spiel- und Softwareinstallationen. "
            "aktion='deploy': Installiert Artefakt mit Snapshot-Manifest. "
            "aktion='rollback': Stellt vorherigen Snapshot-Zustand wieder her. "
            "aktion='installer': Startet Setup-Installer im Benutzerkontext. "
            "aktion='status': PrÃ¼ft QuarantÃ¤ne- und Sandbox-Status.",
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
                    "description": "HTTPS-Download-URL des Artefakts.",
                },
                "artifact_id": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Opake Kennung des heruntergeladenen Artefakts.",
                },
                "target_id": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Opake Kennung des Installationsziels aus locator.",
                },
                "sha256": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Erwarteter SHA-256-Hash des Herausgebers.",
                },
                "installer_args": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 200},
                    "description": "Optionale Argumente fÃ¼r Installer.",
                },
            },
            ["aktion"],
        ),
    ]

def _execute_set_agent_name(db: Session, *, user: User, arguments: dict) -> dict:
    """Setzt den Rufnamen des Assistenten â€” dasselbe Feld wie der Router
    PATCH /auth/me/agent-name (users.agent_name), mit derselben PrÃ¼fung.

    Kein eigenes Recht: es ist eine persÃ¶nliche, jederzeit umkehrbare
    Einstellung des Benutzers, die er im Panel ohnehin selbst Ã¤ndern darf.
    Sofort ausgefÃ¼hrt statt vorgeschlagen â€” dieselbe Einordnung wie
    `remember` (ai_tool_registry erklÃ¤rt sie).

    Der neue Name wirkt ab dem nÃ¤chsten Zug (Lageblock, services/ai_lage.py);
    das Ergebnis sagt das dem Modell, damit es nichts Falsches verspricht.
    """
    from schemas.user import AgentNameUpdateRequest

    if set(arguments) != {"name"}:
        raise AiActionValidationError("set_agent_name erwartet genau das Feld name")
    roh = arguments.get("name")
    if roh is not None and not isinstance(roh, str):
        raise AiActionValidationError("name muss eine Zeichenkette sein")
    try:
        # Dieselbe Wahrheit wie der Router: was das Schema ablehnt, lehnt
        # auch das Werkzeug ab â€” ein Formfehler kostet eine Runde, nie mehr.
        geprueft = AgentNameUpdateRequest(agent_name=roh).agent_name
    except ValueError as fehler:
        raise AiActionValidationError(str(fehler)) from fehler

    user.agent_name = geprueft
    db.commit()
    return {
        "agent_name": geprueft,
        "hinweis": (
            "Gespeichert. Der Name gilt ab dem naechsten Zug; in der "
            "Desktop-App schlaegt die App dem Benutzer selbst vor, das "
            "Wake-Word neu zu kalibrieren."
        ),
    }

def _memory_team(
    db: Session, user: User, *, scope: str, arguments: dict
) -> tuple[str, int | None, str | None]:
    """Welches Team ein GedÃ¤chtniswerkzeug meint â€” die Nummer schlÃ¤gt den Namen.

    Zwei Wege auf dasselbe Team, und der genauere gewinnt.

    **Der Name trÃ¤gt nicht allein.** Teamnamen sind nur je GrÃ¼nder eindeutig
    (`team_service._assert_name_is_free` lÃ¤sst Gleichnamigkeit ausdrÃ¼cklich zu).
    Ist der Benutzer in zwei Teams namens "Alpha", benennt `team="Alpha"` beide;
    `learning_team` fragt dann zurÃ¼ck, und seine RÃ¼ckfrage unterscheidet die
    Kandidaten Ã¼ber den GrÃ¼nder ("Alpha (bob)"). Ein Suchtreffer, der nur den
    blanken Namen trug, lieÃŸ sich keinem davon zuordnen â€” das Modell wÃ¤hlte
    eines der beiden und lÃ¶schte mit halber Wahrscheinlichkeit im falschen Team.
    Folgenlos ist das nicht: SchlÃ¼ssel sind bewusst stabil und wiederholen sich
    Ã¼ber Teams hinweg, drÃ¼ben steht also etwas zu treffen.

    **Die Nummer aus dem Suchtreffer hat dieses Problem nicht.** Sie trifft
    genau ein Team, so wie `server_id` seit jeher genau einen Server trifft. Sie
    ist dabei **kein Freibrief**: `ai_memory_service.scope_identity` weist eine
    Nummer ohne Mitgliedschaft mit 404 ab, `_assert_may_write` eine ohne
    Verwaltungsschalter mit 403. Beide Schranken stehen ohnehin im Weg jedes
    Schreibens und LÃ¶schens â€” durchgereicht wird hier deshalb nur eine Zahl,
    keine Berechtigung.

    Der Name bleibt als RÃ¼ckfall stehen und wird nicht ersetzt. Ein Modell, das
    ein Team nur aus dem GesprÃ¤ch kennt und nie danach gesucht hat, soll nicht
    daran scheitern, dass ihm die Nummer fehlt.
    """
    roh = arguments.get("team_id")
    if scope != "team":
        # Dieselbe Strenge wie bei `server_id` im falschen Bereich: ein Bezug,
        # der nicht ausgewertet wird, ist ein MissverstÃ¤ndnis und keine
        # NachlÃ¤ssigkeit, Ã¼ber die man hinwegsehen darf.
        if roh is not None:
            raise AiActionValidationError("Nur Team-Memory akzeptiert eine team_id")
        return scope, None, None
    if roh is not None:
        if isinstance(roh, bool) or not isinstance(roh, int) or roh < 1:
            raise AiActionValidationError(
                "UngÃ¼ltige team_id â€” nimm die Nummer aus dem Suchergebnis"
            )
        return scope, roh, None

    from services import team_service

    # `memory` und nicht `skills`: welcher Schalter zÃ¤hlt, entscheidet die Art
    # des Wissens. Beide Erinnerungswerkzeuge fragten hier den Skill-Schalter ab
    # und schrieben deshalb bei `memory=True, skills=False` still ins
    # persÃ¶nliche GedÃ¤chtnis.
    ziel, frage = team_service.learning_team(
        db, user, schalter="memory", wunsch=arguments.get("team"),
    )
    if ziel is None:
        return scope, None, frage
    if ziel.is_personal:
        # Kein echtes Team vorhanden oder keine Verwaltungsberechtigung: der
        # Eintrag wird persÃ¶nlich statt gar nicht. Lieber zu eng gespeichert als
        # zu weit.
        return "user", None, None
    return scope, ziel.id, None

def _execute_remember(db: Session, *, user: User, arguments: dict) -> dict:
    """Laesst die KI einen dauerhaften Fakt im Memory des Benutzers ablegen.

    Die Rechtegrenze ist `ai.memory.use` â€” dasselbe Recht, das entscheidet, ob
    Memory ueberhaupt in den Kontext fliesst. Wer sein Memory nicht nutzen darf,
    bekommt auch keines geschrieben.

    Alle inhaltlichen Schutzmassnahmen liegen bereits in
    `ai_memory_service.upsert_entry`: Secret-Abweisung, Groessengrenze,
    DIS-Verschluesselung, Scope-Trennung je Benutzer und die Regel, dass eine
    Ableitung der KI keine ausdrueckliche Ansage des Benutzers ueberschreibt.
    Hier steht die Argumentpruefung â€” und die Uebersetzung einer Absage in eine
    Anweisung. Die kann nur hier stehen: der Dienst bedient auch den Router und
    schreibt deshalb fuer einen Menschen, nicht fuer ein Modell.
    """
    from models import AiMemoryEntry
    from services import ai_memory_service
    from services.dis_client import DisSidecarError

    if not permission_service.has_global_permission(db, user, "ai.memory.use"):
        raise AiActionValidationError("Memory ist fuer diesen Benutzer nicht freigegeben")
    if set(arguments) - {
        "scope", "server_id", "key", "value", "replace_user_entry", "team", "team_id",
    }:
        raise AiActionValidationError("Memory-Werkzeug hat ungueltige Argumente")

    scope = arguments.get("scope")
    if scope not in {"user", "server", "server_shared", "team"}:
        # "panel" ist bewusst nicht erreichbar: panelweites Memory gilt fuer
        # alle Benutzer und ist eine Betreiberentscheidung, keine der KI.
        raise AiActionValidationError("Unbekannter Memory-Bereich")

    key = arguments.get("key")
    if not isinstance(key, str) or not _MEMORY_KEY_RE.match(key):
        raise AiActionValidationError("Ungueltiger Memory-Schluessel")
    value = arguments.get("value")
    if not isinstance(value, str) or not value.strip():
        raise AiActionValidationError("Memory-Inhalt ist leer")

    server_id = arguments.get("server_id")
    serverbezogen = scope in {"server", "server_shared"}
    if serverbezogen:
        if isinstance(server_id, bool) or not isinstance(server_id, int) or server_id < 1:
            raise AiActionValidationError("Server-Memory braucht eine gueltige server_id")
    elif server_id is not None:
        raise AiActionValidationError("Benutzer-Memory akzeptiert keinen Server")

    # Welches Team gemeint ist, entscheidet `_memory_team`. Das Modell darf die
    # Nummer nennen, aber nichts Ã¼ber sie behaupten: ob der Benutzer dort
    # Mitglied ist und dessen Wissen pflegen darf, bleibt eine Tatsache der
    # Datenbank und wird gleich in `upsert_entry` geprÃ¼ft. Ist die Lage nicht
    # eindeutig, bekommt das Modell die RÃ¼ckfrage als Ergebnis und fragt den
    # Benutzer.
    scope, team_id, rueckfrage = _memory_team(db, user, scope=scope, arguments=arguments)
    if rueckfrage is not None:
        return {"remembered": False, "ask_user": rueckfrage}

    # Die Einwilligung gilt dem **eigenen** Gedaechtnis, also `user` und
    # `server` â€” `team` und `panel` haengen an Mitgliedschaft und
    # Betreiberentscheidung (siehe `_visible_scope_rows`).
    #
    # Geprueft wurde sie bisher nur beim **Lesen**. Beim abgeschalteten Schalter
    # legte die KI also weiter Zeilen an; sie wurden nur nicht mehr vorgelesen.
    # Zwei Folgen, beide schlecht: der Hinweis in der Oberflaeche sagt â€žDerzeit
    # ist das Gedaechtnis deaktiviertâ€œ, waehrend im Hintergrund mitgeschrieben
    # wird â€” und wer den Schalter spaeter umlegt, bekommt schlagartig alles zu
    # sehen, was in der Zwischenzeit ueber ihn gesammelt wurde. Der Systemprompt
    # weist das Modell ausdruecklich an, Vorlieben **ungefragt** abzulegen; ohne
    # diese Pruefung ist die Einstellung eine Anzeige und keine Entscheidung.
    #
    # Bewusst nur hier und nicht in `upsert_entry`: ueber den Router legt der
    # Benutzer selbst eine Notiz an, und das ist eine ausdrueckliche Handlung.
    # Sie darf an dem Schalter nicht scheitern, der die *KI* betrifft.
    # `server_shared` gehoert bewusst **nicht** dazu: das Wissen der Anlage
    # gehoert der Anlage, wie Teamwissen dem Team gehoert. Wer seinen eigenen
    # Schalter umlegt, trifft eine Entscheidung ueber sich, nicht ueber die
    # Betriebsanleitung, nach der seine Kollegen arbeiten.
    if scope in ai_memory_service.PERSOENLICHE_SCOPES and not ai_memory_service.preference(
        db, user.id
    ):
        # **Der einzige Fehlschlag, ueber den geredet werden soll.**
        #
        # `ai_prompt.GEDAECHTNIS` verlangt, dass Merken und Nachschlagen
        # lautlos passieren â€” zu Recht, ein Gedaechtnis soll wirken und nicht
        # auftreten. Genau das machte diesen Fall unsichtbar: der Schalter ist
        # ohne Zeile **aus** (Datenminimierung, `ai_memory_service.preference`),
        # das Modell versuchte es korrekt, scheiterte korrekt und schwieg
        # korrekt. Der Betreiber am 22.08.2026: "die KI merkt sich auch gar
        # nichts" â€” er konnte es nicht wissen, ihm hat es nie jemand gesagt.
        #
        # Die Ausnahme steht hier und nicht im Prompt, weil nur hier bekannt
        # ist, dass sie zutrifft. Ein Satz im Prompt kostete jeden Lauf Tokens,
        # auch die, in denen der Schalter an ist.
        return {
            "remembered": False,
            "reason": "memory_disabled",
            "message": (
                "Der Benutzer hat sein persoenliches Gedaechtnis abgeschaltet. "
                "Es wurde nichts gespeichert â€” und du wirst dir bis auf "
                "Weiteres nichts ueber ihn merken koennen. Hier gilt die Regel "
                "der Lautlosigkeit ausnahmsweise nicht: sag ihm einmal "
                "beilaeufig, dass du dir deshalb nichts merken kannst und dass "
                "der Schalter unter Profil > KI sitzt (in der App im Reiter "
                "Gedaechtnis). Einmal, nicht in jeder Antwort."
            ),
        }

    # **Legt die KI hier zum vierten Mal denselben Fakt unter neuem Namen ab?**
    #
    # Das Ueberschreiben ueber den Schluessel loest Konflikte nur, wenn der
    # vorhandene Schluessel wiedergefunden wird. Der Werkzeugtext weist das
    # Modell dazu an â€” aber eine Anweisung ist keine Garantie, und `ram.vorgabe`
    # neben `standard_ram` neben `speicher.default` faellt niemandem auf, bis
    # sich drei Antworten widersprechen.
    #
    # Die Meldung **nennt den vorhandenen Schluessel**, statt bloss abzulehnen.
    # Ein "das gibt es schon" ohne Namen ist eine Sackgasse: das Modell weiss
    # dann, dass es nicht schreiben darf, aber nicht, wohin stattdessen. Mit
    # dem Namen kann es denselben Aufruf mit `key=<gefunden>` wiederholen und
    # der Fakt wird aktualisiert statt verdoppelt.
    #
    # Nur fuer `origin="ai"`, also genau hier: was ein Mensch ausdruecklich
    # ablegt, wird nicht wegen Aehnlichkeit abgewiesen. Er darf zwei Notizen
    # zum selben Thema fuehren, wenn er das will.
    if not arguments.get("replace_user_entry"):
        try:
            kennung, _o, _s, _t = ai_memory_service.scope_identity(
                db, user, scope, server_id if serverbezogen else None, team_id
            )
        except HTTPException:
            # Die Bereichsaufloesung scheitert gleich noch einmal in
            # `upsert_entry`, und dort gehoert die Fehlermeldung hin.
            kennung = None
        # **Ein vorhandener SchlÃ¼ssel ist kein Doppel, sondern das Update.**
        #
        # Die Absage unten empfiehlt genau diesen Aufruf â€” sie darf ihn nicht
        # selbst abweisen. `aehnlicher_eintrag` schlieÃŸt nur den identischen
        # SchlÃ¼ssel aus; stehen im Bereich schon zwei Ã¤hnliche Altlasten
        # nebeneinander (genau die, gegen die die PrÃ¼fung gebaut ist:
        # `ram.vorgabe` neben `standard_ram`), fand der Aufruf mit dem einen
        # SchlÃ¼ssel den anderen und umgekehrt. Das Modell pendelte zwischen
        # zwei Absagen, bis die Runden aufgebraucht waren, und ein
        # ausdrÃ¼cklich gewÃ¼nschtes "ich will jetzt 16 GB" scheiterte still.
        #
        # Eine Abfrage auf (Bereich, SchlÃ¼ssel) reicht dagegen: sie beantwortet
        # die einzige Frage, die hier zÃ¤hlt â€” Neuanlage oder Ãœberschreiben.
        vorhanden_schon = kennung is not None and db.query(AiMemoryEntry.id).filter(
            AiMemoryEntry.scope_identity == kennung, AiMemoryEntry.key == key,
        ).first() is not None
        if kennung and not vorhanden_schon:
            treffer = ai_memory_service.aehnlicher_eintrag(
                db, scope_kennung=kennung, key=key, value=value,
            )
            if treffer is not None:
                vorhanden, wert = treffer
                return {
                    "remembered": False,
                    "reason": "duplicate",
                    "existing_key": vorhanden.key,
                    "similarity": round(wert, 2),
                    "message": (
                        f"Dazu gibt es bereits den Eintrag '{vorhanden.key}'. "
                        "Gilt das Neue statt des Alten, rufe `remember` erneut "
                        f"mit key='{vorhanden.key}' auf â€” das ueberschreibt ihn. "
                        "Steht wirklich etwas anderes darin, waehle einen "
                        "deutlich anderen Schluessel."
                    ),
                }

    try:
        row, stored = ai_memory_service.upsert_entry(
            db, user=user, scope=scope, server_id=server_id if serverbezogen else None,
            team_id=team_id, key=key, value=value, origin="ai",
            replace_user_entry=bool(arguments.get("replace_user_entry")),
        )
    except ai_memory_service.MemoryScopeVoll as exc:
        # Die Werkzeugnamen stehen **hier** und nicht im Dienst, weil derselbe
        # Vorgang zwei Adressaten hat: `upsert_entry` bedient auch den Router,
        # und dessen `detail` liest ein Mensch als Toast. Ein Text, der beiden
        # dienen soll, dient keinem â€” der Dienst sagt deshalb die Tatsache, und
        # erst an dieser Naht kommt dazu, was das Modell damit tun soll.
        #
        # Unterschieden wird ueber die Zahlen der Ausnahme und nicht ueber den
        # Meldungstext: sonst entschiede eine Umformulierung drueben
        # stillschweigend, ob hier zum Loeschen geraten wird.
        #
        # Und geraten wird dazu nur in einem der drei Faelle. Bei Grenze 0
        # schafft Loeschen keinen Platz, bei einer nachtraeglichen Senkung
        # trifft es die falschen: `search_memory` liefert hoechstens fuenfzehn
        # Treffer, und zwar die zur Frage **relevantesten**. Wer daraus dutzende
        # Eintraege wegraeumt, loescht nicht, was nicht mehr gilt, sondern was
        # zuletzt gebraucht wurde â€” bei `team` und `server_shared` obendrein die
        # Betriebsanleitung der Kollegen. `forget_memory` fragt vorher
        # niemanden.
        if exc.grenze == 0:
            hinweis = "Versuch es nicht erneut."
        elif exc.bestand == exc.grenze:
            hinweis = (
                "Suche mit search_memory, was nicht mehr gilt, nenne es dem "
                "Benutzer und lÃ¶sche es mit forget_memory â€” aber nur EintrÃ¤ge "
                "aus genau diesem Bereich, denn die Suche geht Ã¼ber alle "
                "Bereiche, die er sehen darf."
            )
        else:
            hinweis = (
                "Nenne dem Benutzer den Stand und frag, was weg soll. LÃ¶sche "
                "hier nichts von dir aus: bei dieser Menge triffst du nicht, "
                "was nicht mehr gilt, sondern was zuletzt gebraucht wurde."
            )
        raise AiActionValidationError(f"{exc.detail} {hinweis}") from exc
    except DisSidecarError:
        # **Der VerschlÃ¼sselungsdienst antwortet nicht â€” und das darf nicht den
        # Lauf kosten.**
        #
        # `upsert_entry` verschlÃ¼sselt Ã¼ber den DIS-Sidecar; bei Zeitablauf oder
        # einer Antwort ungleich 200 kommt von dort eine gewÃ¶hnliche Ausnahme,
        # keine `HTTPException`. Sie flog bis in den Segmentfang des Streams:
        # der ganze Lauf endete mit `AI_STREAM_FAILED` und der Benutzer verlor
        # die komplette Antwort â€” wegen einer Notiz, die das Modell nebenbei
        # und lautlos machen sollte. Nebenan gilt lÃ¤ngst das Gegenteil: "Ein
        # GedÃ¤chtnis ist eine Beigabe. Es darf fehlen; es darf nicht im Weg
        # stehen" (`ai_memory_service._entschluesseln`).
        #
        # `rollback` wie im Router-Zwilling: sonst trÃ¤gt die Sitzung die
        # angefangene Zeile weiter und der nÃ¤chste Werkzeugaufruf desselben
        # Laufs scheitert an ihr.
        #
        # Der Text sagt ausdrÃ¼cklich, dass ein zweiter Versuch nichts bringt â€”
        # ohne das wiederholt das Modell den Aufruf, bis die Runden alle sind.
        db.rollback()
        return {
            "remembered": False,
            "reason": "memory_unavailable",
            "message": (
                "Das GedÃ¤chtnis ist gerade nicht erreichbar, es wurde nichts "
                "gespeichert. Versuch es nicht noch einmal â€” arbeite ohne die "
                "Notiz weiter und beantworte die Frage des Benutzers."
            ),
        }
    except HTTPException as exc:
        # Secret im Wert, fremder Server, geschuetzter Eintrag, fehlendes
        # `server.config.write`: alles regulaere Faelle, die das Modell erfahren
        # soll, statt dass der Stream mit einem Serverfehler abbricht.
        #
        # Ausdruecklich **keine** stille Herabstufung wie beim Team weiter oben.
        # Dort ist "kein echtes Team vorhanden" ein Zustand des Panels, und
        # persoenlich zu speichern ist enger als gewuenscht, also unbedenklich.
        # Hier waere es umgekehrt gefaehrlich: der Benutzer glaubte, ein Kollege
        # lese den Satz, und niemand tut es. Die Meldung aus `_assert_may_write`
        # nennt den Weg, der offensteht.
        raise AiActionValidationError(str(exc.detail)) from exc
    return {
        "remembered": True, "scope": row.scope, "key": row.key, "value": stored,
        "team_id": row.team_id, "server_id": row.server_id,
    }

def question_payload(arguments: dict) -> dict:
    """Prueft eine Rueckfrage und bringt sie in die Form fuer die Oberflaeche.

    Bewusst streng: Der Text landet unveraendert als Knopfbeschriftung im Chat,
    und ein Klick darauf wird zur naechsten Benutzernachricht. Ein Modell, das
    hier eine Anweisung an sich selbst unterbringt, wuerde sie sich also vom
    Benutzer bestaetigen lassen â€” deshalb laufen Frage und Beschriftungen durch
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

    Gesucht wird ausschliesslich in dem, was der Benutzer ohnehin sehen darf â€”
    `search_entries` nutzt denselben Sichtbarkeitsfilter wie der Abruf in den
    Kontext. Eine Suche kann damit nichts aufdecken, was ohne sie verborgen
    waere.

    Ein Treffer muss ausserdem **wieder ansprechbar** sein: die Suche ist die
    erste Haelfte des zweistufigen Loeschwegs, und `forget_memory` braucht den
    Bereich in genau der Form, in der es ihn annimmt.
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

    # Zu jedem Team-Treffer der Name, unter dem der Benutzer den Bereich kennt.
    # Er ist die HÃ¤lfte des RÃ¼ckwegs: die Nummer daneben spricht das Team an
    # (`forget_memory(team_id=â€¦)`), der Name macht es aussprechbar â€” "in Alpha
    # steht noch das alte Wartungsfenster" ist ein Satz, "in Team 7" keiner.
    # Damit ist auch die Auflage aus der vollen Absage befolgbar: "nur EintrÃ¤ge
    # aus genau diesem Bereich", wobei der Bereich dort als Name genannt wird
    # (`ai_memory_service._bereichsname`).
    #
    # **Der Name kommt aus `ansprechbarer_name` und nicht aus `team.name`.**
    # Teamnamen sind nur je GrÃ¼nder eindeutig; ist der Benutzer in zwei Teams
    # namens "Alpha", benannte der blanke Name beide. Zwei Treffer standen dann
    # ununterscheidbar nebeneinander, und weil SchlÃ¼ssel bewusst stabil sind und
    # sich Ã¼ber Teams hinweg wiederholen, lÃ¶schte ein
    # `forget_memory(team="Alpha")` im falschen Team, statt ins Leere zu laufen.
    # `ansprechbarer_name` hÃ¤ngt in diesem Fall den GrÃ¼nder an â€” genau die Form,
    # die `learning_team` in seiner RÃ¼ckfrage anbietet und wieder annimmt.
    #
    # Je Team einmal fragen, nicht je Treffer: fuenfzehn Treffer aus einem Team
    # sind der Normalfall.
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
        treffer = {
            "scope": row.scope,
            "team_id": row.team_id,
            # Ohne die Nummer findet das Modell einen serverbezogenen
            # Eintrag, kann ihn aber nicht mehr loeschen: `forget_memory`
            # braucht sie, um denselben Bereich noch einmal aufzuloesen.
            # Genau die Sackgasse, in der "vergiss das" ins Leere lief.
            "server_id": row.server_id,
            "key": row.key,
            "value": value,
            "origin": row.origin,
        }
        name = _teamname(row.team_id)
        if name is not None:
            # Der Feldname ist der Argumentname von `forget_memory`, damit der
            # Weg vom Treffer zum Aufruf ohne Uebersetzung auskommt.
            treffer["team"] = name
        # Fehlt die Zeile wider Erwarten, bleibt es bei `team_id` allein â€” und
        # damit bei dem Weg, der ohnehin der genauere ist. Ein ersatzweises
        # "Team 7" wÃ¤re schlimmer als nichts: das Modell setzte es als `team`
        # ein, `learning_team` trÃ¤fe damit keinen Kandidaten und antwortete mit
        # derselben RÃ¼ckfrage wie ohne jede Angabe.
        results.append(treffer)

    return {"untrusted": True, "query": query, "results": results}

def _execute_forget_memory(db: Session, *, user: User, arguments: dict) -> dict:
    """Loescht ausdruecklich benannte Eintraege â€” nie einen Suchbegriff.

    Der zweistufige Weg ist Absicht. Eine Vektoraehnlichkeit von 0,4 ist eine
    brauchbare Grundlage dafuer, jemandem etwas *anzuzeigen*, und eine
    schlechte dafuer, etwas *zu vernichten*. Deshalb sucht das Modell zuerst,
    nennt was es gefunden hat, und loescht danach die Schluessel.
    """
    from services import ai_memory_service

    if not permission_service.has_global_permission(db, user, "ai.memory.use"):
        raise AiActionValidationError("Memory ist fuer diesen Benutzer nicht freigegeben")
    if set(arguments) - {"scope", "server_id", "keys", "team", "team_id"}:
        raise AiActionValidationError("Memory-Loeschung hat ungueltige Argumente")
    scope = arguments.get("scope")
    if scope not in {"user", "server", "server_shared", "team"}:
        # "panel" bleibt dem Betreiber vorbehalten: was fuer alle gilt, loescht
        # die KI nicht auf Zuruf eines einzelnen Benutzers.
        raise AiActionValidationError("Unbekannter Memory-Bereich")
    keys = arguments.get("keys")
    if not isinstance(keys, list) or not keys:
        raise AiActionValidationError("Es wurde kein Schluessel genannt")

    # Beide serverbezogenen Bereiche, nicht nur der neue. `search_memory` hat
    # serverbezogene Eintraege schon immer gefunden, `forget_memory` kannte sie
    # nie: "vergiss die Notiz zu Server 62" lief in "Unbekannter
    # Memory-Bereich" â€” eine Sackgasse, die dem Benutzer als Weigerung erschien.
    server_id = arguments.get("server_id")
    serverbezogen = scope in {"server", "server_shared"}
    if serverbezogen:
        if isinstance(server_id, bool) or not isinstance(server_id, int) or server_id < 1:
            raise AiActionValidationError(
                "Server-Memory braucht die server_id aus dem Suchergebnis"
            )
    elif server_id is not None:
        raise AiActionValidationError("Dieser Memory-Bereich akzeptiert keinen Server")

    # Hier zÃ¤hlt die Nummer am meisten: gelÃ¶scht wird nichts, was sich
    # zurÃ¼ckholen lÃ¤sst, und ein Griff ins gleichnamige Nachbarteam trifft dort
    # denselben SchlÃ¼ssel. Die PrÃ¼fung dahinter ist dieselbe wie beim Schreiben
    # â€” `delete_by_keys` fÃ¼hrt beide Schranken.
    scope, team_id, rueckfrage = _memory_team(db, user, scope=scope, arguments=arguments)
    if rueckfrage is not None:
        return {"forgotten": [], "ask_user": rueckfrage}

    try:
        removed = ai_memory_service.delete_by_keys(
            db, user, scope=scope, keys=keys, team_id=team_id,
            server_id=server_id if serverbezogen else None,
        )
    except HTTPException as exc:
        raise AiActionValidationError(str(exc.detail)) from exc
    # Was nicht da war, wird ausdruecklich gemeldet: sonst berichtet das Modell
    # ein Loeschen, das nie stattgefunden hat.
    missing = sorted({key for key in keys if isinstance(key, str)} - set(removed))
    return {
        "forgotten": removed,
        "scope": scope,
        **({"server_id": server_id} if serverbezogen else {}),
        # **Wo** gelÃ¶scht wurde, gehÃ¶rt ins Ergebnis. Bei zwei gleichnamigen
        # Teams ist "im Team gelÃ¶scht" keine Auskunft, sondern eine Zusage, die
        # das Modell nicht belegen kann â€” mit der Nummer sagt es dem Benutzer
        # dasselbe, was es dem Werkzeug gesagt hat.
        **({"team_id": team_id} if team_id is not None else {}),
        **({"not_found": missing} if missing else {}),
    }

def _execute_forget_skill(db: Session, *, user: User, arguments: dict) -> dict:
    """Loescht einen erlernten Skill â€” aufgeloest ueber das, was loeschbar ist.

    Frueher lief die Aufloesung ueber `read_body`, also ueber die
    Sichtbarkeitsueberlagerung aus `visible_skills`. Die kennt je Schluessel
    genau einen Gewinner, und bei Gleichstand â€” derselbe Schluessel panelweit
    **und** in einem Team â€” entscheidet die Zeilenreihenfolge der Datenbank,
    welcher das ist. Beim Lesen ist das hoechstens unscharf. Beim Loeschen ist
    es eine Zeile weniger auf der Platte, im schlechten Fall die panelweite,
    die fuer jeden Kunden gilt, waehrend die gemeinte Team-Zeile stehen bleibt.
    Umgekehrt war eine globale Zeile ueber dieses Werkzeug gar nicht mehr
    erreichbar, sobald ein Team-Skill sie verdeckte.

    Deshalb wird hier ueber `manageable_skills` aufgeloest: die Menge dessen,
    was dieser Benutzer wirklich veraendern darf. Bleibt mehr als ein Bereich
    uebrig, wird nicht geraten, sondern zurueckgefragt â€” dieselbe Vorsicht, die
    `forget_memory` ueber die Schluesselliste erzwingt. Die Antwort kommt als
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
    # kennt. Eine Team-ID ist fuer eine Rueckfrage wertlos â€” der Benutzer
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
        # das, was er sehen darf â€” und nicht mehr: ein erratener fremder
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
        # das Modell â€” und ein Fehlgriff ist hier nicht rueckgaengig zu machen.
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
    # **Dieselbe Schranke wie beim Ãœberschreiben, nur am anderen Ende.**
    #
    # `upsert_skill` weist einen KI-Text ab, der einen von einem Menschen
    # geschriebenen Skill ersetzen will â€” was ein Mensch geschrieben hat,
    # Ã¼berschreibt die KI nicht stillschweigend. Ohne diese PrÃ¼fung war
    # genau das in zwei ZÃ¼gen zu haben: erst `forget_skill`, dann `learn_skill`
    # unter demselben SchlÃ¼ssel â€” und wo die Vorgabe des Betreibers stand,
    # stand danach Modelltext, ohne dass jemand etwas bestÃ¤tigt hat.
    #
    # Das wiegt schwerer als ein verlorener Absatz. Ein Skill wirkt in jedem
    # kÃ¼nftigen Lauf des Panels oder des Teams; eine prÃ¤parierte Logzeile, die
    # das Modell zu genau diesen zwei Aufrufen bringt, wÃ¤re damit eine
    # dauerhafte Anweisung an alle. Und `upsert_skill` fÃ¼hrt bewusst keine
    # Versionen â€” nach dem LÃ¶schen gibt es nichts zurÃ¼ckzuholen.
    #
    # Was die KI selbst gelernt hat, rÃ¤umt sie weiter ohne RÃ¼ckfrage weg; das
    # ist die HÃ¤lfte, die ihr gehÃ¶rt. FÃ¼r die andere bleibt der Weg offen, den
    # ein Mensch ohnehin geht: `routers/ai_skills.py` lÃ¶scht dieselbe Zeile
    # ohne diese Schranke.
    #
    # Antwortform wie beim mitgelieferten Skill: eine Absage mit Weg statt
    # einer Ausnahme. Ein `raise` wÃ¼rde das Modell eine Runde drehen lassen,
    # statt es dem Benutzer sagen zu lassen.
    if row.origin != "ai":
        return {
            "forgotten": False,
            "skill_key": row.skill_key,
            "scope": "global" if row.team_id is None else "team",
            "bereich": bereich,
            "reason": (
                "Diesen Skill hat ein Mensch geschrieben â€” du lÃ¶schst ihn nicht "
                "und legst auch keinen Ã¤hnlichen zweiten an. Sag dem Benutzer, "
                "welchen Skill du fÃ¼r Ã¼berholt hÃ¤ltst und warum; entfernen kann "
                "er ihn selbst in der Skill-Verwaltung des Panels."
            ),
        }
    # **Dieselbe Schranke, an der zweiten TÃ¼r.**
    #
    # `upsert_skill` lÃ¤sst den Schalter `enabled` nur von einem Menschen
    # anfassen: ein abgeschalteter Skill bleibt abgeschaltet, auch wenn die KI
    # ihn unter demselben SchlÃ¼ssel neu schreibt. Genau dafÃ¼r ist Abschalten da
    # â€” es ist das Gegenmittel gegen einen per Injection gelernten Skill.
    #
    # Ohne diese PrÃ¼fung war es in zwei ZÃ¼gen wieder weg: die abgeschaltete
    # Zeile stammt von der KI, sie durfte sie also lÃ¶schen â€” und das direkt
    # folgende `learn_skill` landete im Anlege-Zweig, wo `enabled` wieder auf
    # ``True`` steht. Der Betreiber hÃ¤tte dasselbe am nÃ¤chsten Tag noch einmal
    # abgeschaltet, und wieder, ohne je zu erfahren, warum es zurÃ¼ckkommt.
    #
    # Es ist eine ZustandsprÃ¼fung und kein entzogenes Werkzeug: was die KI
    # gelernt hat und was gilt, rÃ¤umt sie weiter ohne RÃ¼ckfrage weg. Nur die
    # eine Zeile, Ã¼ber die ein Mensch bereits entschieden hat, bleibt liegen â€”
    # und der Weg dorthin ist derselbe wie oben.
    if not row.enabled:
        return {
            "forgotten": False,
            "skill_key": row.skill_key,
            "scope": "global" if row.team_id is None else "team",
            "bereich": bereich,
            "reason": (
                "Diesen Skill hat ein Mensch abgeschaltet; er wirkt bereits "
                "nicht mehr. LÃ¶sche ihn nicht und lege auch keinen Ã¤hnlichen "
                "zweiten an â€” entfernen kann er ihn selbst in der "
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
        # versioniert, das Audit-Log ist die einzige Spur einer LÃ¶schung â€” und
        # ohne diese Angabe stand jede von der KI ausgelÃ¶ste als Klick eines
        # Menschen im Panel darin. Nach einer per Injection ausgelÃ¶sten LÃ¶schung
        # hÃ¤tte niemand mehr unterscheiden kÃ¶nnen, wessen Hand es war.
        ai_skill_service.delete_skill(db, user=user, skill_id=row.id, origin="ai")
    except HTTPException as exc:
        raise AiActionValidationError(str(exc.detail)) from exc
    return ergebnis

def _execute_search_docs(arguments: dict) -> dict:
    """Volltextsuche ueber die Dokumentation dieses Panels.

    **Ohne zusaetzliches Recht.** Alle fuenf Seiten sind im Panel fuer jeden
    angemeldeten Benutzer erreichbar (`/docs/*` und `/privacy`); ein Gate hier
    waere eine Schranke, die es nebenan nicht gibt, und wuerde ausgerechnet die
    Belegpflicht dort aushebeln, wo sie am noetigsten ist â€” bei jemandem, der
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
    """Laedt den Text eines Skills â€” Stufe zwei des schrittweisen Ladens.

    Die Sichtbarkeitspruefung liegt vollstaendig in
    `ai_skill_service.read_body`: ein erratener Schluessel eines fremden Teams
    endet dort mit 404, ohne zu verraten, ob es ihn gibt.

    Der Text wird als **untrusted** zurueckgegeben. Ein Team-Skill ist woertlich
    Text, den ein anderer Mensch geschrieben hat und der hier in den Kontext
    dieses Benutzers geladen wird â€” er ist eine Anleitung, keine Anweisung.
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
    nichts ausfuehrt â€” der Skill aendert die Herangehensweise des Modells, nicht
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
            # bereits umgesetzt â€” "off" endet oben, "review" ohne
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

    Die Rechtegrenze ist `ai.web_search.use` â€” und sie ist die **einzige**.
    Wer das Recht hat, darf suchen lassen; wer es nicht hat, nicht. Sonst
    entscheidet nichts mehr mit.

    **Hier stand einmal eine zweite Grenze, und sie ist ersatzlos gefallen.**
    `docs_searchable` liess die Herkunft des Blueprints darueber entscheiden:
    mitgeliefert hiess suchbar, selbst importiert hiess gesperrt, mit der
    Annahme "nativ = oeffentlich dokumentiert, community = privater
    Discord-Bot". Im Betrieb ist sie umgekippt. Ein selbst gepflegter
    ARK-Blueprint ist community und beschreibt trotzdem ein Spiel mit
    oeffentlichem Wiki â€” die Suche war dort gesperrt, das Modell fiel auf sein
    Trainingswissen zurueck und schrieb Werte in eine Datei, die es so nicht
    gab.

    Die Vorgabe des Betreibers ist deshalb ausnahmslos: die Websuche ist ein
    Merkmal, das immer funktioniert. Sie gilt nicht nur fuer Spielserver,
    sondern fuer alles, was MSM verwaltet â€” und je weiter das reicht (Anwendungs-
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

    # `server_id` bleibt zulaessig und laeuft weiter ueber `_resolve_server`.
    # Sie entscheidet nichts mehr, aber sie darf auch kein Orakel werden: wer
    # keinen Zugriff auf den Server hat, soll an der Antwort nicht ablesen
    # koennen, ob es ihn gibt.
    server_id = arguments.get("server_id")
    if server_id is not None:
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
    # waere die Suche fuer ihren haeufigsten Zweck unbrauchbar â€” nach dem Namen
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
