"""Der Werkzeugkatalog der KI und die Ausfuehrung aller Lesezugriffe.

Was das Modell tun *darf*, steht hier: welche Werkzeuge es angeboten bekommt,
welche Argumente sie annehmen, welches Recht jedes verlangt und wie aus einer
genannten `server_id` ein Server wird, den dieser Benutzer wirklich sehen darf
(`_resolve_server`).

Schreibende Aktionen laufen nicht hier, sondern in `ai_proposal_service` —
anlegen, bestaetigen, ausfuehren. Die Trennung folgt der Sicherheitsgrenze:
Lesen passiert sofort, Schreiben erst nach Bestaetigung.

Die Zuordnung "welches Werkzeug gehoert in welche Menge" steht in
`ai_tool_registry`, die Fehlerarten in `ai_action_errors`.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import PurePosixPath
import json
import logging
import re
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import AiActionProposal, Server, User
# Die Aufzaehlungen des Aufgabenmodells als `enum` im Katalog — aus derselben
# Quelle wie der CHECK in der Datenbank und die Pruefung im Dienst. Waeren es
# Abschriften, boete der Katalog irgendwann einen Wert an, den der
# Vorschlagspfad abweist: das Modell versucht es dann wieder und wieder.
from models.ai_task import ARTEN as _AUFGABENARTEN
from models.ai_task import KANAELE as _KANAELE
from models.ai_task import PLANARTEN as _PLANARTEN
# Und die des Meldemodells getrennt davon. Die beiden Listen sind heute gleich,
# gehoeren aber zwei verschiedenen Tabellen mit je eigenem CHECK: `create_task`
# schreibt nach `ai_tasks`, `worker_start` meldet ueber `ai_meldestelle` nach
# `ai_meldungen`. Eine gemeinsame Konstante waere die bequeme Abschrift, vor
# der der Kommentar oben warnt — nur in die andere Richtung.
from models.ai_meldung import KANAELE as _MELDEKANAELE
from services import permission_service
from services.ai_action_errors import AiActionValidationError
# Die Intervallgrenzen sind im Dienst eine Kostenentscheidung und stehen hier
# nur im Schema, damit das Modell sie sieht, statt sie durch Abweisungen zu
# erraten. Der Import zeigt in eine Richtung: `ai_task_service` kennt
# `ai_action_errors`, nicht diese Datei.
from services.ai_task_service import (
    MAX_INTERVALL_STUNDEN as _MAX_INTERVALL_STUNDEN,
    MIN_INTERVALL_STUNDEN as _MIN_INTERVALL_STUNDEN,
)
from services.ai_redaction import redact_sensitive_text
# Aus demselben Grund wie die Intervallgrenzen: der Werkzeugtext nennt dem
# Modell, welche Zahl bei `max_memory_entries` gilt, wenn nichts hinterlegt ist.
# Als Abschrift im Text wuerde sie still falsch, sobald jemand die Systemgrenze
# verschiebt — und still falsch ist sie genau dort am teuersten, wo sie
# woertlich an das Modell geht.
from services.ai_limit_service import MAX_SYSTEM_SCOPE_ENTRIES as _MAX_SCOPE_ENTRIES
from services.server_file_access_service import read_server_text


logger = logging.getLogger(__name__)

CONFIRMATION_TTL = timedelta(minutes=5)
MAX_CONFIG_CHARS = 64_000
MAX_DIFF_CHARS = 16_000
MAX_DIFF_LINES = 200
# Harte Obergrenzen fuer alles, was aus einem Server zum Provider fliesst.
# Bewusst als Konstanten, weil dieselben Werte im Phase-4-Vertrag stehen.
MAX_READ_CONFIG_CHARS = 24_000
MAX_LOG_CHARS = 24_000
# Das Fenster von `read_config`. Der Zeichendeckel allein reicht nicht mehr,
# seit eine Datei auch stueckweise gelesen werden kann: eine Megabyte grosse
# Spielkonfiguration hat gut dreizehntausend Zeilen, und ohne Startzeile kaeme
# immer nur derselbe Anfang zurueck. Die Zeilenzahl ist die Groesse, in der ein
# Mensch eine Fundstelle beschreibt ("ab Zeile 4200"), deshalb zaehlt das
# Fenster in Zeilen und nicht in Zeichen. Der Zeichendeckel bleibt als harte
# Obergrenze darueber liegen.
MAX_READ_CONFIG_LINES = 400
# Grenzen der Dateisuche. Jede gelesene Datei ist bei einem entfernten Server
# ein eigener Abruf ueber den Node-Agenten, jede Trefferzeile ein Stueck
# unvertrauenswuerdiger Text im Kontext des Modells. Beides will begrenzt sein,
# und zwar aus verschiedenen Gruenden: das eine kostet Zeit, das andere Geld.
MAX_SEARCH_QUERY_CHARS = 128
MAX_SEARCH_FILES = 40
MAX_SEARCH_DEPTH = 4
MAX_SEARCH_MATCHES = 40
MAX_SEARCH_LINE_CHARS = 200
MAX_SEARCH_CONTEXT_LINES = 5
# Grenzen der Teilaenderung. Zwanzig Ersetzungen sind mehr, als eine
# nachvollziehbare Aenderung braucht; wer mehr will, macht zwei Vorschlaege und
# der Mensch sieht zweimal, was passiert.
MAX_PATCH_EDITS = 20
MAX_PATCH_CHUNK_CHARS = 8_000
# Obergrenzen fuer die Listen-Tools. Jede Zeile landet als unvertrauenswuerdiger
# Text im Modellkontext und damit im Kostenbudget des Benutzers.
MAX_LISTED_MODS = 60
MAX_LISTED_BACKUPS = 20
MAX_LISTED_INCIDENTS = 15
MAX_LISTED_ACTIONS = 20
MAX_LISTED_BLUEPRINTS = 80
MAX_LISTED_NODES = 30
MAX_REASON_CHARS = 500
# Ein Backup-Name ist eine Wiedererkennungshilfe in einer Liste, keine
# Beschreibung. Was laenger ist, wird in der Oberflaeche ohnehin abgeschnitten.
MAX_BACKUP_NAME_CHARS = 64
# Grenzen der Rueckfrage. Vier Vorschlaege sind das Aeusserste, was man
# nebeneinander noch vergleicht; darueber wird aus einer Wahl eine Liste.
MAX_QUESTION_OPTIONS = 4
MAX_QUESTION_CHARS = 300
MAX_OPTION_CHARS = 60
MAX_OPTION_HINT_CHARS = 120
# Was die KI in einem Zug in eine Datei auf dem Rechner des Benutzers
# schreiben darf. Grosszuegig genug fuer eine Quelldatei, klein genug, dass ein
# Schreibauftrag nicht das halbe Kontextfenster verbraucht. Die eigentliche
# Grenze zieht der Rechner selbst (Sandbox); das hier haelt nur den Prompt
# beisammen.
MAX_DESKTOP_INHALT_CHARS = 60_000
# Wie viele Pfade ein Aufraeumauftrag tragen darf. Dieselbe Zahl steht in
# `aufraeumen.rs` und gilt dort noch einmal: das Panel haelt den Prompt klein,
# der Rechner haelt die Aktion klein. Wer eine Grenze nur oben zieht, hat sie
# nicht gezogen — die App nimmt auch Auftraege entgegen, die nie durch dieses
# Schema gegangen sind.
MAX_AUFRAEUM_PFADE = 500

# ── Tool-Mengen ───────────────────────────────────────────────────────────
# Abgeleitet aus `services/ai_tool_registry.py`. Dort steht **eine** Zeile je
# Werkzeug; alles Weitere — welche Menge, welche Gruppe, ob autonomiefaehig —
# faellt daraus ab. Vorher waren es zehn von Hand gepflegte Mengen, und eine
# vergessene fiel erst zur Laufzeit auf.
from services.ai_tool_registry import (  # noqa: E402
    GLOBAL_READ_TOOLS,
    READ_TOOLS,
    WERKZEUGE,
    angebotsrechte,
    bekannt as _werkzeug_bekannt,
)

# Jedes serverbezogene Werkzeug traegt seit dem Einzelchat seine eigene
# `server_id`. Vorher stand sie an der Unterhaltung — dadurch konnte der
# Panel-Chat gar kein Server-Werkzeug anbieten und man musste erst wissen,
# welcher Server gemeint ist, bevor man fragen durfte.
_SERVER_ID_SCHEMA = {
    "server_id": {
        "type": "integer",
        "minimum": 1,
        "description": "ID des Servers aus list_my_servers.",
    }
}
MAX_LISTED_SERVERS = 60

# Diese Aktionen fassen Serverdateien an und teilen sich deshalb den
# vorhandenen, nicht blockierenden Server-Lifecycle-Mutex. Lifecycle-Aktionen
# brauchen ihn nicht: `request_lifecycle_operation` hat eine eigene Job-Sperre.
# Mod-Installation ebenso wenig: `install_mod_bg` haelt den Install-Lock selbst.
_MUTEX_TOOLS = {"propose_backup", "propose_config_update", "propose_config_patch", "propose_config_set"}


# Ein "reason" beschreibt, warum die KI die Aenderung vorschlaegt, ein
# "expected_effect" was danach anders sein soll. Beides ist eine Begruendung des
# Modells, keine Zusicherung des Panels — und wird deshalb redigiert und gekuerzt.
_RATIONALE_SCHEMA = {
    "reason": {"type": "string", "maxLength": MAX_REASON_CHARS},
    "expected_effect": {"type": "string", "maxLength": MAX_REASON_CHARS},
}
_RATIONALE_REQUIRED = ["reason", "expected_effect"]


# Wie ein Gedächtniswerkzeug ein Team anspricht — einmal, für `remember` und
# `forget_memory` zusammen. Zwei Abschriften wären zwei Wahrheiten: die Nummer
# stünde im einen Werkzeug und im anderen nicht, und das Modell müsste raten,
# welches der beiden sie annimmt.
#
# Die Nummer steht vorn, weil sie der genaue Weg ist. Ein Teamname ist nur je
# Gründer eindeutig, `team_id` trifft dagegen genau ein Team — dieselbe Nummer,
# die `search_memory` neben jedem Team-Treffer meldet. Die Begründung steht bei
# `_memory_team`, wo beide Wege zusammenlaufen.
_MEMORY_TEAM_SCHEMA = {
    "team_id": {
        "type": ["integer", "null"],
        "description": "Nur bei scope=team: die team_id aus dem Suchergebnis.",
    },
    "team": {
        "type": "string",
        "maxLength": 64,
        "description": (
            "Ersatz ohne team_id: Teamname aus einer Rückfrage, genau so "
            "geschrieben."
        ),
    },
}


def _function(name: str, description: str, properties: dict, required: list[str]) -> dict:
    # Ohne Zeile in `ai_tool_registry` waere das Werkzeug zwar im Katalog, aber
    # in keiner Menge — das Modell duerfte es aufrufen und die Allowlist wuerde
    # es abweisen. Hier faellt der fehlende Eintrag sofort auf.
    assert _werkzeug_bekannt(name), f"Werkzeug {name!r} fehlt in ai_tool_registry"
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                **({"required": required} if required else {}),
                "additionalProperties": False,
            },
        },
    }


def _server_function(
    name: str, description: str, properties: dict | None = None, required: list[str] | None = None
) -> dict:
    """Wie ``_function``, aber mit verpflichtender ``server_id``."""
    return _function(
        name,
        description,
        {**_SERVER_ID_SCHEMA, **(properties or {})},
        ["server_id", *(required or [])],
    )


def _global_tool_definitions() -> list[dict]:
    """Werkzeuge ohne Serverbezug: Serverliste, Blueprints, Kapazitaet, Anlage."""
    optional: list[dict] = []
    # Ohne hinterlegten Schluessel gar nicht erst anbieten. Ein Werkzeug, das
    # immer scheitert, verwirrt ein Modell mehr als es hilft: es versucht es
    # erneut, formuliert um und verbraucht dabei Tokens.
    from services.ai_web_search_service import MAX_RESULTS, is_configured

    if is_configured():
        optional.append(_function(
            "web_search",
            "Sucht im Web. Fuer aktuelle Informationen, die nicht aus dem "
            "Panel kommen — Fehlermeldungen, Modkompatibilitaet, "
            "Spielversionen. Liefert Titel, Adresse und Kurztext.",
            {
                "query": {"type": "string", "maxLength": 200},
                "count": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS},
                "server_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Server, um den es geht — aus list_my_servers.",
                },
            },
            ["query"],
        ))

    from services.ai_satellite_service import is_configured as is_satellite_configured

    if is_satellite_configured():
        optional.append(_function(
            "analyze_region",
            "Führt eine regionale Analyse für einen geografischen Ort durch. "
            "Ermittelt Koordinaten, Wetterdaten und ruft aktuelle "
            "Satellitendaten (Copernicus/Sentinel-2) der Region ab. Der Ort "
            "kann auch eine Sehenswürdigkeit sein; die zurückgegebene WGS84-"
            "Position steuert die Karten- und Globusansicht. Waehle den "
            "Kameramodus passend zum Wunsch des Benutzers.",
            {
                "location": {
                    "type": "string",
                    "maxLength": 100,
                    "description": "Name der Stadt, Region oder des Ortes (z.B. 'Berlin', 'Washington').",
                },
                "camera": {
                    "type": "string",
                    "enum": ["overview", "focus", "detail"],
                    "description": "overview fuer Weltuebersicht, focus fuer eine Region, detail nur wenn der Benutzer gezielt hineinzoomen moechte.",
                },
            },
            ["location"],
        ))
        optional.append(_function(
            "control_region_camera",
            "Steuert ausschließlich die bereits geöffnete Regionskarte, ohne "
            "Wetter, Satellitenbilder oder Nachrichten erneut abzurufen. "
            "Nutze dies für kurze Folgeanweisungen wie näher heranzoomen, "
            "herauszoomen, zur Weltübersicht wechseln oder eine konkrete "
            "Sehenswürdigkeit fokussieren.",
            {
                "action": {
                    "type": "string",
                    "enum": ["zoom_in", "zoom_out", "overview", "focus_location"],
                    "description": "Kamerabefehl für die bereits sichtbare Karte.",
                },
                "location": {
                    "type": "string",
                    "maxLength": 100,
                    "description": "Nur bei focus_location: genauer Name der Sehenswürdigkeit samt Stadt.",
                },
            },
            ["action"],
        ))

    # Globales Lernen kann der Betreiber abschalten. Dann steht "global" gar
    # nicht erst in der Auswahl — ein Modell, das eine Moeglichkeit angeboten
    # bekommt, die immer abgewiesen wird, versucht sie mehrfach.
    from services.ai_learning_policy import policy as learning_policy

    learn_scopes = ["team"] if learning_policy() == "off" else ["team", "global"]

    # Die Seitenliste steht in **beiden** Beschreibungen ausgeschrieben. Das
    # Modell kann sonst nur raten, was es ueberhaupt nachschlagen koennte — und
    # eine geratene Seitenkennung ist der erste Schritt zu einer geratenen
    # Antwort.
    from services.ai_docs_corpus import SEITEN as DOKU_SEITEN

    doku_seiten = sorted(DOKU_SEITEN)
    doku_liste = ", ".join(f"{s.schluessel} ({s.titel})" for s in DOKU_SEITEN.values())

    return optional + [
        _function(
            "search_docs",
            "Durchsucht die Dokumentation dieses Panels. **Der erste Schritt, "
            "bevor du etwas ueber MSM behauptest.** Verfuegbar: " + doku_liste + ".\n"
            "Liefert Seite, Abschnitt und einen Ausschnitt — den Abschnitt "
            "selbst holst du danach mit `read_docs`. Such danach, wie der "
            "Benutzer fragt; Umlaute und ihre Umschreibung findet die Suche "
            "gleichermassen.\n"
            "Findest du nichts, ist das ein Ergebnis: sag, dass dazu nichts in "
            "der MSM-Dokumentation steht. Nicht mit Wissen ueber andere Panels "
            "auffuellen — Pterodactyl, Pelican und Plesk arbeiten anders, und "
            "eine plausible Antwort ist hier schlimmer als keine.\n"
            "Nicht aufrufen bei Fragen zu einem laufenden Server, zu "
            "Spielinhalten oder zu Werten in einer Konfigurationsdatei — dafuer "
            "gibt es die Serverwerkzeuge.",
            {
                "query": {"type": "string", "maxLength": 200},
                "page": {
                    "type": "string",
                    "enum": doku_seiten,
                    "description": "Nur diese Seite durchsuchen. Weglassen sucht in allen.",
                },
            },
            ["query"],
        ),
        _function(
            "read_docs",
            "Liest die Dokumentation dieses Panels. Ohne `section` bekommst du "
            "die Gliederung der Seite, mit `section` den Text des Abschnitts. "
            "Seiten: " + doku_liste + ".\n"
            "**Abschnittskennungen nie raten** — sie kommen aus der Gliederung "
            "oder aus `search_docs`. Ein erfundener Abschnitt wird abgewiesen, "
            "aber der Umweg kostet eine Runde.\n"
            "Was du hier liest, gilt. Was hier nicht steht, behauptest du nicht. "
            "Nenne dem Benutzer die Seite (`panel_page`), damit er dasselbe "
            "nachlesen kann.",
            {
                "page": {"type": "string", "enum": doku_seiten},
                "section": {
                    "type": ["string", "null"],
                    "maxLength": 64,
                    "description": (
                        "Abschnittskennung aus der Gliederung oder aus "
                        "search_docs. Weglassen liefert die Gliederung."
                    ),
                },
            },
            ["page"],
        ),
        _function(
            "read_hoster_setup",
            "Zeigt die panelseitige Shop-Anbindung vollstaendig: vorhandene "
            "Integrationen mit Slug, Dienstbenutzer, Webhook-Ziel und "
            "Kuendigungsfrist, ihre Produktzuordnungen, die vergebenen Slugs, "
            "die Benutzer, die als Dienstbenutzer taugen, und die Rollen, die "
            "**dieser** Benutzer vergeben darf — samt ihrem KI-Kontingent.\n"
            "Beim Kontingent gilt dieselbe Ausnahme wie beim Anlegen: fehlt "
            "`ai_limits` ganz oder steht `max_memory_entries` darin auf `null`, "
            "sagt diese Rolle zum Gedaechtnisvorrat **nichts** — weder "
            f"'unbegrenzt' noch '{_MAX_SCOPE_ENTRIES}'. Es gewinnt die hoechste "
            "gesetzte Zahl unter allen Rollen ihres Traegers; die Systemgrenze "
            f"von {_MAX_SCOPE_ENTRIES} Eintraegen greift erst, wenn keine seiner "
            "Rollen eine Zahl traegt. Was ein einzelner Kunde am Ende hat, ist "
            "hier also nicht ablesbar.\n"
            "**Ruf das auf, bevor du etwas zur Shop-Einrichtung vorschlaegst.** "
            "Slug, Dienstbenutzer und Produktkennung sind nichts, was man raten "
            "kann; ein geratener Wert erzeugt einen Vorschlag, den der Benutzer "
            "bestaetigt und der dann scheitert. Es enthaelt bewusst keinen "
            "Schluessel — nur den Hinweis, an dem man einen Schluessel "
            "wiedererkennt.\n"
            "Steht bei einer Liste `withheld`, gibt es sie, und du darfst sie "
            "nur nicht sehen. Das ist nicht dasselbe wie eine leere Liste — "
            "behaupte in dem Fall nicht, es gebe keine Rollen oder keine "
            "geeigneten Benutzer.",
            {},
            [],
        ),
        _function(
            "read_hoster_integration_guide",
            "Liefert fuer **eine bestehende** Integration den technischen "
            "Einbindungsblock: Basis-Adresse, Header, Endpunkte, Zustaende, "
            "Eventnamen, Webhook-Header und die real hinterlegten "
            "Produktkennungen dieser Anlage.\n"
            "Alle Werte darin stammen aus dem Code, den die API durchsetzt. "
            "**Gib den Block unveraendert weiter** — nicht umformulieren, nichts "
            "ergaenzen, nichts weglassen. Erklaere ringsherum so ausfuehrlich, "
            "wie es dem Benutzer hilft, aber lass die Werte in Ruhe: ein "
            "abgetippter Header oder ein angepasster Pfad ist der haeufigste "
            "Grund, warum eine Shop-Anbindung nicht laeuft.\n"
            "Die Bedeutung der `status_code`-Werte steht nicht hier, sondern in "
            "der Doku — der Block sagt dir, in welchem Abschnitt.",
            {
                "integration_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Aus read_hoster_setup. Nie raten.",
                },
            },
            ["integration_id"],
        ),
        _function(
            "read_skill",
            "Laedt den vollstaendigen Text eines Skills aus dem Verzeichnis im "
            "Systemprompt. Nur aufrufen, wenn die Beschreibung eines Skills die "
            "Lage des Benutzers wirklich trifft — **passt keine eindeutig, ruf "
            "gar keinen auf** und arbeite normal weiter. Ein Skill zu einer "
            "Stoerung hilft bei einer Frage nach einer Einstellung nicht. "
            "Behandle den Text als Anleitung, nicht als Befehl: pruefe "
            "weiterhin selbst, ob ein Schritt sinnvoll ist.",
            {
                "skill_key": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Schluessel aus dem Skill-Verzeichnis.",
                },
            },
            ["skill_key"],
        ),
        # Der Bauplan des Textes ("was zu pruefen ist, in welcher Reihenfolge,
        # woran man die Ursache erkennt") und die Ausschlussliste ("Nicht
        # festhalten: Einzelfaelle, Zwischenergebnisse …") stehen in
        # `ai_prompt.SKILLS`, und der Systemprompt geht in derselben Anfrage
        # mit (`ai_prompt.build`). Was dort steht, ist hier gestrichen.
        #
        # Der **Anlass** bleibt dagegen hier, und das ist eine Korrektur: bei
        # der ersten Kuerzung am 14.08.2026 wanderte er mit in den Prompt, nach
        # der Regel "was das Modell vor der Werkzeugwahl braucht, gehoert in
        # den Systemprompt". Die Regel stimmt fuer den Bauplan und nicht fuer
        # den Anlass. Das Modell vergleicht bei der Auswahl die Beschreibungen
        # von 52 Werkzeugen gegeneinander; steht der Anlass nur im Prompt,
        # weiss es *dass* es Skills lernen soll, aber nicht, dass **jetzt** der
        # Moment dafuer ist. Der Benchmark hat es sofort gezeigt: das Szenario
        # `skill_lernen` griff danach zu `read_skill` statt zu `learn_skill`
        # (Werkzeugtreffer 10/10 auf 9/10). Ein Satz zurueck, Ersparnis bleibt.
        _function(
            "learn_skill",
            "Haelt eine Vorgehensweise dauerhaft fest. Keine Zugangsdaten, "
            "keine Personennamen.\n"
            "Anlass: du hast gerade ein Problem gelöst oder eine Vorgehensweise "
            "erarbeitet, die beim nächsten Mal wieder gebraucht wird.\n"
            "Bereich: 'team' fuer alles, was zu diesem Betrieb gehoert. "
            "'global' nur fuer Erkenntnisse, die bei jedem Betreiber gelten — "
            "etwa eine Eigenschaft eines Spiels oder einer Mod. Pruefsatz: ein "
            "globaler Skill muss auf einem fremden Panel genauso stimmen. Im "
            "Zweifel 'team'.\n"
            "Gibt es den Schlüssel schon, wird der Skill ersetzt — "
            "vollständig, nicht ergänzt; lies ihn vorher mit read_skill.",
            {
                "skill_key": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Kleinbuchstaben, Ziffern, Bindestriche. z. B. valheim-ram",
                },
                "name": {"type": "string", "maxLength": 100},
                "description": {
                    "type": "string",
                    "maxLength": 500,
                    "description": (
                        "Was der Skill tut, wann er zu verwenden ist UND wann "
                        "nicht. Nur diese Zeile entscheidet spaeter, ob du ihn "
                        "findest — und ob du ihn in einer Lage greifst, in die "
                        "er nicht gehoert. Schreib die Grenze mit hinein."
                    ),
                },
                # "nichts behaupten, was du nicht geprueft hast" ist der eine
                # Halbsatz der alten Beschreibung, für den `ai_prompt.SKILLS`
                # keinen Ersatz hat. Er steht deshalb nicht weiter oben,
                # sondern an dem Feld, das er regiert.
                "body": {
                    "type": "string",
                    "maxLength": 12_000,
                    "description": (
                        "Die Vorgehensweise als Fliesstext, gern mit Markdown. "
                        "Behaupte darin nichts, was du nicht geprueft hast."
                    ),
                },
                "scope": {"type": "string", "enum": learn_scopes},
                "team": {
                    "type": "string",
                    "maxLength": 64,
                    "description": (
                        "Nur bei scope=team und nur, wenn zuvor eine Rueckfrage "
                        "nach dem Team kam: der Name aus dieser Rueckfrage, "
                        "genau so geschrieben. Sonst weglassen."
                    ),
                },
            },
            ["skill_key", "name", "description", "body", "scope"],
        ),
        _function(
            "list_my_servers",
            "Listet alle Server, die der Benutzer sehen darf, mit ID, Name, Spiel "
            "und Status. Immer zuerst aufrufen, wenn der Benutzer einen Server "
            "nur mit Namen nennt oder gar nicht benennt.",
            {},
            [],
        ),
        _function(
            "set_agent_name",
            "Setzt deinen Rufnamen fuer diesen Benutzer — nur auf seinen "
            "ausdruecklichen Wunsch (\"nenn dich ab jetzt …\"). Ein leerer "
            "Name stellt den Standardnamen Singra wieder her. In der "
            "Desktop-App ist der Name zugleich das Wake-Word; der Benutzer "
            "bekommt dort von selbst den Vorschlag, es neu zu kalibrieren.",
            {
                "name": {
                    "type": "string",
                    "maxLength": 32,
                    "description": (
                        "Der neue Rufname: 2-32 Zeichen, Buchstaben, Ziffern, "
                        "Leerzeichen, Punkt, Apostroph oder Bindestrich. "
                        "Leer = Standardname."
                    ),
                },
            },
            ["name"],
        ),
        # Wann gemerkt wird und was **nicht** gemerkt wird, steht in
        # `ai_prompt.GEDAECHTNIS` und geht in derselben Anfrage mit: "Nicht
        # merken: Zwischenergebnisse, Logauszuege, Tagesform …" und
        # "Aktualisierst du einen bekannten Fakt, verwende denselben
        # Schluessel erneut". Beides stand hier ein zweites Mal und ist
        # gestrichen. Das Verbot von Zugangsdaten bleibt: es steht nirgends
        # sonst — `ai_prompt.GEHEIMNISSE` verbietet das *Ausgeben*, nicht das
        # Merken.
        _function(
            "remember",
            "Merkt sich eine dauerhafte Vorliebe oder Eigenheit. Niemals "
            "Passwoerter, Schluessel oder Tokens merken.\n"
            # Der Bereich wird in dieser Reihenfolge bestimmt, und zwar an
            # **beobachtbaren** Merkmalen des Satzes statt an einer Definition.
            # Zweimal gemessen (siehe ai_prompt.py): eine Reihenfolge konkreter
            # Merkmale trifft das Modell zuverlaessiger als eine noch so genaue
            # Beschreibung dessen, was ein Bereich "bedeutet".
            #
            # Hier stand vorher woertlich die Beschreibung dieses neuen
            # Bereichs — "eine Eigenschaft der Anlage, die fuer alle Kollegen
            # gilt" — und zeigte auf `team`. Bliebe der Satz stehen, aenderte
            # sich am beobachteten Verhalten gar nichts.
            #
            # **Die Merkmale waren aber rein sprachlich, und das war zu eng.**
            # Sie setzten voraus, dass der Benutzer den Satz gesagt hat: Regel
            # 1 sucht "ich"/"mein", Regel 3 sucht "wir"/"bei uns". Was die KI
            # selbst herausfindet, enthaelt keines dieser Woerter — es landete
            # ueber Regel 4 pauschal bei `user` oder wurde gar nicht erst
            # gemerkt. Gemessen am 19.08.2026: 7 Eintraege insgesamt, davon
            # **null** im Team-Bereich, juengster vom 16.08. Deshalb steht vor
            # der sprachlichen Reihenfolge jetzt die inhaltliche Frage, wem
            # eine Erkenntnis gehoert.
            "Wahl des Bereichs:\n"
            "Zuerst inhaltlich: Betrifft es **eine Person** (ihre Vorliebe, "
            "ihre Arbeitsweise, ihre Ausstattung), ist es persoenlich. "
            "Betrifft es **die Anlage** — wie ein Server sich verhaelt, wie "
            "hier gearbeitet wird, was du selbst ueber eine Einrichtung "
            "herausgefunden hast —, gehoert es dem Server oder dem Team, auch "
            "wenn niemand \"wir\" gesagt hat.\n"
            "Dann genauer, in dieser Reihenfolge pruefen:\n"
            "1. Persoenlich und zu genau einem Server: scope=server. "
            "Persoenlich ohne Serverbezug: scope=user (\"ich nehme immer "
            "8 GB\").\n"
            "2. Es geht um genau einen Server, dessen Nummer aus einem "
            "Werkzeugergebnis stammt, und gilt fuer jeden, der ihn bedient: "
            "scope=server_shared mit dieser server_id (\"dieser Server "
            "braucht nach dem Start zwei Minuten\").\n"
            "3. Es gilt fuer die ganze Anlage oder die Arbeitsweise des Teams: "
            "scope=team (\"vor einem Update wird gesichert\").\n"
            "4. Sonst scope=user.\n"
            "Pruefsatz fuer 2 und 3: der Eintrag muss wahr bleiben, egal wer "
            "ihn liest. Im Zweifel persoenlich.",
            {
                "scope": {
                    "type": "string",
                    "enum": ["user", "server", "server_shared", "team"],
                    "description": (
                        "user = persoenlich, nur fuer diesen Benutzer. "
                        "server = persoenlich, aber nur zu diesem Server. "
                        "server_shared = gehoert dem Server selbst, sichtbar "
                        "fuer alle, die ihn sehen duerfen. "
                        "team = geteilt mit allen Kollegen im Team."
                    ),
                },
                "server_id": {
                    "type": ["integer", "null"],
                    "description": (
                        "Nur bei scope=server oder scope=server_shared, dort "
                        "aber Pflicht. Sonst null."
                    ),
                },
                **_MEMORY_TEAM_SCHEMA,
                "key": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Kurzer stabiler Bezeichner, z. B. ram.bevorzugt.",
                },
                "value": {"type": "string", "maxLength": 2_000},
                "replace_user_entry": {
                    "type": "boolean",
                    "description": (
                        "Nur setzen, wenn der Benutzer die Korrektur "
                        "ausdruecklich verlangt hat (\"nein, er heisst Rex\"). "
                        "Ueberschreibt dann einen Eintrag, den er selbst "
                        "hinterlegt hat. Ohne ausdrueckliche Bitte weglassen."
                    ),
                },
            },
            ["scope", "key", "value"],
        ),
        _function(
            "ask_user",
            "Stellt dem Benutzer eine Frage mit anklickbaren Vorschlaegen. "
            "Nutze das **nur**, wenn Raten teuer waere: eine Version, ein "
            "Zielserver, eine Entscheidung, die sich schlecht zuruecknehmen "
            "laesst. Nicht fuer \"soll ich anfangen?\" und nicht fuer etwas, "
            "das du aus den Werkzeugen selbst herausfinden kannst — frag erst, "
            "wenn du nachgesehen hast. "
            "Der Benutzer kann immer auch frei antworten; die Vorschlaege sind "
            "eine Abkuerzung, keine Einschraenkung. Nach dieser Frage endet "
            "dein Zug.",
            {
                "question": {
                    "type": "string",
                    "maxLength": MAX_QUESTION_CHARS,
                    "description": "Die Frage, vollstaendig und aus sich heraus verstaendlich.",
                },
                "options": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": MAX_QUESTION_OPTIONS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "maxLength": MAX_OPTION_CHARS},
                            "hint": {
                                "type": "string",
                                "maxLength": MAX_OPTION_HINT_CHARS,
                                "description": "Was diese Wahl bedeutet. Kurz.",
                            },
                        },
                        "required": ["label"],
                        "additionalProperties": False,
                    },
                },
            },
            ["question", "options"],
        ),
        _function(
            "search_memory",
            "Durchsucht das Gedaechtnis nach Bedeutung. Nutze es, bevor du "
            "etwas loeschst oder korrigierst — und wenn der Benutzer wissen "
            "will, was du ueber ein Thema gespeichert hast. Findet auch, was "
            "anders formuliert ist: \"mein Hund\" findet einen Eintrag, in dem "
            "nur der Name des Hundes steht. Liefert Bereich, Schluessel und "
            "Inhalt, dazu server_id oder team_id — die braucht "
            "`forget_memory` wieder.",
            {
                "query": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Wonach gesucht wird, in Worten des Benutzers.",
                },
            },
            ["query"],
        ),
        _function(
            "forget_memory",
            "Loescht benannte Eintraege aus dem Gedaechtnis. Rufe **immer "
            "zuerst** `search_memory` auf und nenne dem Benutzer, was du "
            "gefunden hast — geloescht wird ausschliesslich, was du hier "
            "namentlich auffuehrst, nie ein Suchbegriff. Eine unscharfe "
            "Aehnlichkeit darf entscheiden, was jemand zu sehen bekommt, aber "
            "nicht, was verschwindet.",
            {
                "scope": {
                    "type": "string",
                    "enum": ["user", "server", "server_shared", "team"],
                    "description": "Bereich aus dem Suchergebnis.",
                },
                "server_id": {
                    "type": ["integer", "null"],
                    "description": (
                        "Nur bei scope=server oder scope=server_shared, dort "
                        "aber Pflicht: die server_id aus dem Suchergebnis. "
                        "Sonst null."
                    ),
                },
                "keys": {
                    "type": "array",
                    "maxItems": 25,
                    "items": {"type": "string", "maxLength": 64},
                    "description": "Die Schluessel aus dem Suchergebnis.",
                },
                **_MEMORY_TEAM_SCHEMA,
            },
            ["scope", "keys"],
        ),
        _function(
            "forget_skill",
            "Loescht einen erlernten Skill. Nur eigene und Team-Skills — die "
            "mit MSM ausgelieferten lassen sich nicht loeschen, sondern nur "
            "ueberschreiben, indem du unter demselben Schluessel einen neuen "
            "anlegst. Zum *Aendern* eines Skills nimm `learn_skill` mit "
            "demselben Schluessel; loeschen und neu anlegen verliert die "
            "Herkunft.\n"
            "Denselben Schluessel kann es in mehreren Bereichen geben — "
            "panelweit und in einem Team. Dann kommt eine Rueckfrage statt "
            "einer Loeschung; nenne dem Benutzer die Bereiche und rufe das "
            "Werkzeug mit seiner Antwort erneut auf.",
            {
                "skill_key": {"type": "string", "maxLength": 64},
                "scope": {
                    "type": "string",
                    "enum": ["global", "team"],
                    "description": (
                        "Nur, wenn zuvor eine Rueckfrage nach dem Bereich kam. "
                        "Sonst weglassen."
                    ),
                },
                "team": {
                    "type": "string",
                    "maxLength": 64,
                    "description": (
                        "Nur bei scope=team und nur nach einer Rueckfrage: der "
                        "Bereichsname aus dieser Rueckfrage, genau so "
                        "geschrieben. Sonst weglassen."
                    ),
                },
            },
            ["skill_key"],
        ),
        _function(
            "list_blueprints",
            "Listet verfuegbare Servertypen (Blueprints) mit Modunterstuetzung und Portrollen.",
            {},
            [],
        ),
        _function(
            "read_blueprint",
            "Liest einen Blueprint vollstaendig — Image, Startbefehl, Ports und "
            "Umgebungsvariablen. **Die Spielversion steht hier, nicht am "
            "Server**: bei Minecraft in runtime.env.VERSION, bei Steam-Titeln in "
            "source.steam.branch, sonst im Image-Tag. `origin: native` bedeutet "
            "mitgeliefert und schreibgeschuetzt.",
            {"blueprint_id": {"type": "string", "maxLength": 64}},
            ["blueprint_id"],
        ),
        _function(
            "read_node_capacity",
            "Liest die Kapazitaet aller Hosts. **Buchung und Verbrauch sind "
            "zweierlei**: `ram_allocated_mb` ist die Summe aller zugewiesenen "
            "Grenzen einschliesslich **gestoppter** Server — die belegen "
            "nichts. Was tatsaechlich laeuft, steht in "
            "`ram_allocated_running_mb`, was die Node misst in `ram_used_mb`. "
            "Ist die Buchung voll, aber Server sind gestoppt, ist der Host "
            "nicht ausgelastet: dann ist die Frage, ob ueberbucht werden darf, "
            "und nicht ob Platz da ist.",
            {},
            [],
        ),
        _function(
            "read_node_health",
            "Liest den Gesundheitszustand aller Hosts: erreichbar, Docker "
            "verbunden, CPU, RAM, Festplatte, Containerzahl, letzter Kontakt. "
            "Fuer Fragen wie 'bei einer meiner Nodes stimmt etwas nicht'.",
            {},
            [],
        ),
        _function(
            "propose_blueprint_change",
            "Leitet aus einem vorhandenen Blueprint einen neuen ab — so aendert "
            "man eine Spielversion, ohne die Vorlage aller anderen Server "
            "anzufassen. Die Quelle bleibt unveraendert. Aenderbar sind "
            "meta.name, meta.description, runtime.image, runtime.env und "
            "runtime.startup — ueber runtime.startup korrigierst du fehlende "
            "oder falsche Startparameter. runtime.env wird gemischt, vorhandene "
            "Variablen bleiben also erhalten. Fuehrt der Quell-Blueprint "
            "runtime.startupProfiles, wird eine Aenderung an runtime.startup "
            "abgewiesen: dort entscheidet das Profil ueber die Startzeile, die "
            "Korrektur bliebe wirkungslos.",
            {
                **_RATIONALE_SCHEMA,
                "source_id": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Vorlage aus list_blueprints, auch eine native.",
                },
                "new_id": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Neue ID, nur a-z, 0-9 und _.",
                },
                "changes": {
                    "type": "object",
                    "description": (
                        'Punktpfade auf Werte, z. B. {"runtime.env": '
                        '{"VERSION": "1.20.1"}}.'
                    ),
                },
            },
            ["source_id", "new_id", "changes", *_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_blueprint_delete",
            "Loescht einen selbst erstellten oder abgeleiteten Community-Blueprint. "
            "Native Blueprints koennen nicht geloescht werden.",
            {
                "blueprint_id": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "ID des zu loeschenden Community-Blueprints.",
                },
                **_RATIONALE_SCHEMA,
            },
            ["blueprint_id", *_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_server_create",
            "Schlaegt die Erstellung eines neuen Servers zur manuellen Bestaetigung vor. "
            "Ports, Installationsverzeichnis und Host werden von MSM vergeben.",
            {
                "name": {"type": "string", "maxLength": 128},
                "game_type": {"type": "string", "maxLength": 64},
                "ram_limit_mb": {"type": "integer", "minimum": 512, "maximum": 4_194_304},
                "cpu_limit_percent": {"type": "integer", "minimum": 10, "maximum": 3_200},
                "disk_limit_gb": {"type": "integer", "minimum": 1, "maximum": 1_048_576},
                "node_id": {"type": ["integer", "null"]},
                **_RATIONALE_SCHEMA,
            },
            [
                "name",
                "game_type",
                "ram_limit_mb",
                "cpu_limit_percent",
                "disk_limit_gb",
                *_RATIONALE_REQUIRED,
            ],
        ),
        _function(
            "propose_ai_tarif_role",
            "Legt eine globale Rolle fuer einen Shop-Tarif an — **mit leerer "
            "Rechteliste** und nur einem KI-Kontingent. Genau darin liegt ihr "
            "Zweck: Kontingente haengen an globalen Rollen, und ohne eine solche "
            "Rolle bekommt jeder Shop-Kunde dasselbe Kontingent wie jeder "
            "andere.\n"
            "Rechte vergibt sie ausdruecklich keine. Braucht der Tarif welche, "
            "gehoert das in die Rollenverwaltung des Panels und nicht hierher.\n"
            "Bei den Kontingenten heisst ein Feld auf `null` **unbegrenzt**, "
            "nicht null; `max_memory_entries` ist die Ausnahme, siehe dort. "
            "Setz nur, was der Benutzer genannt hat, und frag im Zweifel nach "
            "— ein geratenes Tageslimit merkt der Kunde erst, wenn es greift.",
            {
                "name": {"type": "string", "maxLength": 64},
                "description": {"type": ["string", "null"], "maxLength": 255},
                "daily_token_limit": {"type": ["integer", "null"], "minimum": 0},
                "weekly_token_limit": {"type": ["integer", "null"], "minimum": 0},
                "monthly_token_limit": {"type": ["integer", "null"], "minimum": 0},
                "requests_per_minute": {"type": ["integer", "null"], "minimum": 0},
                "concurrent_operations": {"type": ["integer", "null"], "minimum": 0},
                "monthly_cost_limit_cents": {"type": ["integer", "null"], "minimum": 0},
                "max_reasoning_effort": {"type": ["integer", "null"], "minimum": 0},
                "max_memory_entries": {
                    "type": ["integer", "null"],
                    "minimum": 0,
                    "description": (
                        "Memory-Eintraege je Bereich. `null` heisst hier "
                        "**nicht** unbegrenzt, sondern 'nichts hinterlegt': "
                        "die Rolle traegt zum Vorrat dann nichts bei. Es "
                        "gewinnt die hoechste gesetzte Zahl unter allen Rollen "
                        f"des Kunden; die Systemgrenze von {_MAX_SCOPE_ENTRIES} "
                        "Eintraegen greift erst, wenn keine davon eine Zahl "
                        "traegt. `null` taugt damit zu keinem der beiden "
                        "Wuensche: 'unbegrenztes Gedaechtnis' braucht eine "
                        "Zahl, die du dem Benutzer nennst, und senken kann "
                        "eine zusaetzliche Rolle gar nicht — dafuer muss die "
                        "Zahl der bestehenden Rolle sinken."
                    ),
                },
                **_RATIONALE_SCHEMA,
            },
            ["name", *_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_hoster_integration",
            "Legt eine Hoster-Integration an oder aendert eine bestehende — die "
            "panelseitige Haelfte einer Shop-Anbindung. Ist ein `webhook_url` "
            "gesetzt und noch kein Secret vorhanden, wird zugleich eines "
            "erzeugt: ein Ziel ohne Secret stellt nichts zu, und das faellt "
            "sonst erst im Betrieb auf.\n"
            "**Ruf vorher `read_hoster_setup` auf.** Der Slug muss panelweit "
            "eindeutig sein und der Dienstbenutzer aktiv sein, kein Owner und "
            "`servers.create` haben — beides steht dort, beides ist nicht zu "
            "erraten.\n"
            "Der API-Key entsteht erst beim Ausfuehren und wird dem Benutzer "
            "**einmalig** in der Oberflaeche gezeigt. Du bekommst ihn nie zu "
            "sehen und kannst ihn nicht wiederholen. Sag das dem Benutzer, "
            "bevor er bestaetigt.",
            {
                "integration_id": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "description": "Zum Aendern. Weglassen legt neu an.",
                },
                "name": {"type": "string", "maxLength": 128},
                "slug": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Kleinbuchstaben, Ziffern, Bindestriche. Panelweit eindeutig.",
                },
                "service_user_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Aus service_user_candidates in read_hoster_setup.",
                },
                "webhook_url": {"type": ["string", "null"], "maxLength": 2048},
                "terminate_grace_days": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 365,
                    "description": (
                        "Tage, die Server und Daten nach einer Kuendigung "
                        "erhalten bleiben. 0 = sofort loeschbar."
                    ),
                },
                "enabled": {"type": "boolean"},
                **_RATIONALE_SCHEMA,
            },
            [
                "name",
                "slug",
                "service_user_id",
                "terminate_grace_days",
                *_RATIONALE_REQUIRED,
            ],
        ),
        _function(
            "propose_hoster_product",
            "Ordnet eine Produktkennung des Shops einem Blueprint und einem "
            "Ressourcenpaket zu. Die Kennung muss **exakt** so heissen wie im "
            "Shop — MSM-interne IDs muss der Shop nie kennen.\n"
            "**Ruf vorher `read_hoster_setup` auf** fuer die Integration, die "
            "vorhandenen Produktkennungen und die Rollen, die dieser Benutzer "
            "vergeben darf. Eine Rolle, die dort nicht steht, wird abgewiesen — "
            "auch dann, wenn sie existiert.\n"
            "`role_id` ist der Bogen zwischen Tarif und KI-Kontingent: der "
            "Kunde bekommt diese Rolle, solange sein Vertrag laeuft, und "
            "verliert sie bei Sperre oder Kuendigung. Leer lassen heisst: keine "
            "Zusatzrolle.\n"
            "Leere Grenzen bedeuten die Voreinstellung des Blueprints, nicht "
            "null. Aenderungen gelten fuer neu erstellte Server; laufende passt "
            "der Betreiber bewusst von Hand an.",
            {
                "integration_id": {"type": "integer", "minimum": 1},
                "external_product_key": {"type": "string", "maxLength": 128},
                "game_type": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Blueprint aus list_blueprints.",
                },
                "ram_limit_mb": {"type": ["integer", "null"], "minimum": 1},
                "cpu_limit_percent": {"type": ["integer", "null"], "minimum": 1},
                "disk_limit_gb": {"type": ["integer", "null"], "minimum": 1},
                "node_id": {"type": ["integer", "null"], "minimum": 1},
                "backup_interval_hours": {"type": ["integer", "null"], "minimum": 1},
                "role_id": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "description": "Aus grantable_roles in read_hoster_setup.",
                },
                "enabled": {"type": "boolean"},
                **_RATIONALE_SCHEMA,
            },
            [
                "integration_id",
                "external_product_key",
                "game_type",
                *_RATIONALE_REQUIRED,
            ],
        ),
        *_aufgaben_tool_definitions(),
        *_worker_tool_definitions(),
        *_mailbox_and_calendar_tool_definitions(),
    ]


#: Der Zeitplan als Schema. Steht als eigene Konstante, weil `create` und
#: `update` ihn beide brauchen und zwei Abschriften zwei Gelegenheiten waeren,
#: verschiedene Grenzen anzubieten.
#:
#: Kein Feldname enthaelt `run`, `command`, `host` oder `args`: der Vertragstest
#: `test_no_write_tool_accepts_something_that_could_be_a_command` prueft die
#: Argumentnamen des Katalogs auf Wortteile, die nach Befehlsausfuehrung
#: klingen, und `run` faellt dort als ganzer Namensteil auf. Ein `next_run_at`
#: im Schema haette den Test gerissen — die Spalte in der Datenbank heisst
#: trotzdem so, dort greift die Regel nicht.
_PLAN_SCHEMA = {
    "plan_kind": {
        "type": "string",
        "enum": list(_PLANARTEN),
        "description": (
            "daily = jeden Tag (oder an bestimmten Wochentagen) zu einer "
            "Uhrzeit, interval = alle N Stunden, once = einmalig."
        ),
    },
    "time_of_day": {
        "type": "string",
        "maxLength": 5,
        "description": "Nur bei daily. 'HH:MM' in der angegebenen Zeitzone.",
    },
    "weekdays": {
        "type": "array",
        "items": {"type": "integer", "minimum": 1, "maximum": 7},
        "description": "Nur bei daily. 1 = Montag bis 7 = Sonntag. Leer = taeglich.",
    },
    "interval_hours": {
        "type": "integer",
        "minimum": _MIN_INTERVALL_STUNDEN,
        "maximum": _MAX_INTERVALL_STUNDEN,
        "description": "Nur bei interval.",
    },
    "once_at": {
        "type": "string",
        "maxLength": 32,
        "description": (
            "Nur bei once. ISO-8601 wie '2026-08-20T08:00'. Ohne "
            "Zeitzonenangabe gilt die der Aufgabe."
        ),
    },
    # Woher die Zone kommt und dass nach dem Zustellweg nicht gefragt wird,
    # sagt `ai_prompt.AUFGABEN` in derselben Anfrage ("nimm sie aus der Lage.
    # Nur wenn die Lage sie als unbekannt ausweist, frag mit `ask_user`" und
    # "Nach dem Zustellweg fragst du nicht: es gilt der Chat"). Hier steht nur
    # noch, wie das Feld auszusehen hat.
    "timezone": {
        "type": "string",
        "maxLength": 64,
        "description": "Pflicht. IANA-Zone des Benutzers, z. B. Europe/Berlin.",
    },
    "channel": {
        "type": "string",
        "enum": list(_KANAELE),
        "description": (
            "chat = nur im Panel, email = zusaetzlich per Mail, both = beides. "
            "Im Chat steht das Ergebnis immer."
        ),
    },
}


def _aufgaben_tool_definitions() -> list[dict]:
    """Stehende Auftraege: auflisten, anlegen, aendern, loeschen — und die Testmail.

    Eigene Funktion, damit der ohnehin lange Katalog nicht noch eine Handbreit
    weiter nach rechts waechst. Der Katalog geht in **jeder** Runde der
    Werkzeugschleife mit ueber die Leitung und taucht in keiner Budgetrechnung
    auf; `test_ai_tool_handler_contract` haelt ihn deshalb unter 45.000 Zeichen.
    """
    return [
        _function(
            "list_tasks",
            "Zeigt die stehenden Auftraege dieses Benutzers — Name, Zeitplan, "
            "Zeitzone, Zustellweg, ob aktiv, und wann sie das naechste Mal "
            "laufen. Ruf das auf, bevor du eine Aufgabe aenderst oder loeschst: "
            "die Nummern sind nicht zu erraten.",
            {},
            [],
        ),
        _function(
            "send_test_email",
            "Schickt eine Testmail an die hinterlegte Adresse **des Benutzers, "
            "der gerade fragt** — einen Empfaenger kannst du nicht waehlen. "
            "Dafuer, wenn er wissen will, ob sein E-Mail-Versand funktioniert. "
            "Die Antwort nennt den benutzten Weg und die maskierte Adresse.",
            {},
            [],
        ),
        # Der ganze *Anlass* steht in `ai_prompt.AUFGABEN` und geht in
        # derselben Anfrage mit: wann ein stehender Auftrag entsteht ("jeden
        # Tag um acht", "alle acht Stunden"), was in `instruction` gehört
        # ("dieser Text ist dein spaeterer Auftrag"), was `kind: "act"`
        # voraussetzt und dass die Zeitzone aus der Lage kommt. Das stand hier
        # ein zweites Mal und ist gestrichen.
        #
        # Was bleibt, ist die Feldkunde — und die trägt hier mehr als sonst:
        # `required` nennt nur die Begründung, weil dasselbe Werkzeug anlegt
        # **und** ändert. Welche Felder beim Anlegen nötig sind, erfährt das
        # Modell nirgends sonst; ein fehlendes kostet eine ganze Runde.
        _function(
            "propose_task_set",
            "Legt einen stehenden Auftrag an oder aendert einen vorhandenen.\n"
            "**Ohne `task_id` wird angelegt**; dann sind `title`, "
            "`instruction`, `kind`, `plan_kind` und `timezone` "
            "noetig. **Mit `task_id` (aus `list_tasks`) wird geaendert**, und "
            "nur genannte Felder werden angefasst. Aenderst du den Plan, gib "
            "`plan_kind` und dessen Felder zusammen an.",
            {
                "task_id": {
                    "type": "string",
                    "maxLength": 36,
                    "description": "Zum Aendern. Weglassen legt neu an.",
                },
                "title": {"type": "string", "maxLength": 120},
                "instruction": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "Was bei jeder Faelligkeit zu tun ist.",
                },
                "kind": {"type": "string", "enum": list(_AUFGABENARTEN)},
                "enabled": {
                    "type": "boolean",
                    "description": "false pausiert, true nimmt wieder auf.",
                },
                **_PLAN_SCHEMA,
                **_RATIONALE_SCHEMA,
            },
            [*_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_task_delete",
            "Entfernt einen stehenden Auftrag endgueltig. Soll er nur ruhen, "
            "nimm `propose_task_set` mit `enabled: false` — das laesst sich "
            "zuruecknehmen. `task_id` aus `list_tasks`.",
            {
                "task_id": {"type": "string", "maxLength": 36},
                **_RATIONALE_SCHEMA,
            },
            ["task_id", *_RATIONALE_REQUIRED],
        ),
    ]


def _mailbox_and_calendar_tool_definitions() -> list[dict]:
    """E-Mail- und Kalender-Werkzeuge (Verknüpfte Postfächer und Kalender)."""
    return [
        _function(
            "email_search",
            "Sucht in den verknüpften Postfächern des Benutzers nach E-Mails. "
            "Liefert Betreff, Absender, Empfänger, Datum und Nachrichten-ID.",
            {
                "query": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Suchbegriff für Betreff oder Inhalt.",
                },
                "sender": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "Filter nach Absender-Adresse oder Name.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 25,
                    "description": "Maximale Anzahl Ergebnisse (Standard: 10).",
                },
                "mailbox_id": {
                    "type": "integer",
                    "description": "Optionale ID des Postfachs.",
                },
            },
            [],
        ),
        _function(
            "email_read",
            "Liest den bereinigten Volltext einer E-Mail anhand ihrer Nachrichten-ID (aus email_search).",
            {
                "message_id": {
                    "type": "string",
                    "maxLength": 128,
                    "description": "ID der Nachricht aus email_search.",
                },
                "mailbox_id": {
                    "type": "integer",
                    "description": "Optionale ID des Postfachs.",
                },
            },
            ["message_id"],
        ),
        _function(
            "calendar_read",
            "Liest Termine aus dem verknüpften Kalender des Benutzers.",
            {
                "start_date": {
                    "type": "string",
                    "maxLength": 32,
                    "description": "Startdatum (z. B. YYYY-MM-DD oder ISO-8601).",
                },
                "end_date": {
                    "type": "string",
                    "maxLength": 32,
                    "description": "Enddatum (z. B. YYYY-MM-DD oder ISO-8601).",
                },
                "calendar_id": {
                    "type": "integer",
                    "description": "Optionale Kalender-ID.",
                },
            },
            [],
        ),
        _function(
            "propose_email_send",
            "Schlägt das Verfassen und Versenden einer E-Mail über ein verknüpftes Postfach vor. "
            "Erfordert zwingend eine Bestätigung des Benutzers vor dem tatsächlichen Versand.",
            {
                "recipient": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "Empfänger-E-Mail-Adresse.",
                },
                "subject": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "Betreff der E-Mail.",
                },
                "body_text": {
                    "type": "string",
                    "maxLength": 8000,
                    "description": "Vollständiger Textinhalt der E-Mail.",
                },
                "body_html": {
                    "type": "string",
                    "maxLength": 16000,
                    "description": "Optionaler HTML-Inhalt.",
                },
                "mailbox_id": {
                    "type": "integer",
                    "description": "Optionales Absender-Postfach. Fehlt es, wird das Standard-Postfach genutzt.",
                },
                **_RATIONALE_SCHEMA,
            },
            ["recipient", "subject", "body_text", *_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_calendar_event_create",
            "Schlägt einen neuen Termin im verknüpften Kalender vor (kann mehrfach aufgerufen werden für mehrere Termine in einem Tagesplan; Standard-Dauer 1 Stunde wenn keine Endzeit genannt). "
            "Erfordert die Freigabe des Benutzers.",
            {
                "title": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "Titel des Termins.",
                },
                "start_time": {
                    "type": "string",
                    "maxLength": 32,
                    "description": "Startzeit (z. B. 2026-08-26 14:00 oder ISO-8601).",
                },
                "end_time": {
                    "type": "string",
                    "maxLength": 32,
                    "description": "Endzeit (z. B. 2026-08-26 15:00 oder ISO-8601).",
                },
                "description": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "Optionale Beschreibung / Agenda.",
                },
                "location": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "Optionaler Ort oder Meeting-Link.",
                },
                "calendar_id": {
                    "type": "integer",
                    "description": "Optionale Kalender-ID.",
                },
                "event_type": {
                    "type": "string",
                    "enum": ["personal", "team", "server", "node"],
                    "description": "Semantische Kategorie des Termins: personal (privat, Standard), team (Team-Termin), server (Server-Wartung), node (Node-Infrastruktur).",
                },
                "team_id": {
                    "type": "integer",
                    "description": "Optionale Team-ID für Team-Termine (event_type=team).",
                },
                "server_id": {
                    "type": "integer",
                    "description": "Optionale Server-ID für Server-Wartungstermine (event_type=server).",
                },
                "color": {
                    "type": "string",
                    "description": "Optionale Farbe (z. B. blue, green, purple, amber, red, cyan).",
                },
                **_RATIONALE_SCHEMA,
            },
            ["title", "start_time", "end_time", *_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_calendar_event_update",
            "Schlägt die Anpassung oder Verschiebung eines bestehenden Termins im Kalender vor (nur wenn ein Termin explizit geändert werden soll, für neue Termine propose_calendar_event_create nutzen). "
            "Erfordert die Freigabe des Benutzers.",
            {
                "event_id": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "ID oder UID des zu ändernden Termins aus calendar_read.",
                },
                "title": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "Neuer Titel des Termins (optional).",
                },
                "start_time": {
                    "type": "string",
                    "maxLength": 32,
                    "description": "Neue Startzeit (z. B. 2026-08-26 15:00 oder ISO-8601).",
                },
                "end_time": {
                    "type": "string",
                    "maxLength": 32,
                    "description": "Neue Endzeit (z. B. 2026-08-26 16:00 oder ISO-8601).",
                },
                "description": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "Neue Beschreibung / Agenda.",
                },
                "location": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "Neuer Ort oder Link.",
                },
                "calendar_id": {
                    "type": "integer",
                    "description": "Optionale Kalender-ID.",
                },
                "event_type": {
                    "type": "string",
                    "enum": ["personal", "team", "server", "node"],
                    "description": "Kategorie anpassen: personal, team, server, node.",
                },
                "team_id": {
                    "type": "integer",
                    "description": "Optionale Team-ID.",
                },
                "server_id": {
                    "type": "integer",
                    "description": "Optionale Server-ID.",
                },
                "color": {
                    "type": "string",
                    "description": "Optionale Farbe.",
                },
                **_RATIONALE_SCHEMA,
            },
            ["event_id", *_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_calendar_event_delete",
            "Schlägt das Löschen eines Termins aus dem Kalender vor.",
            {
                "event_id": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "ID des zu löschenden Termins.",
                },
                "calendar_id": {
                    "type": "integer",
                    "description": "Optionale Kalender-ID.",
                },
                **_RATIONALE_SCHEMA,
            },
            ["event_id", *_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_popup_create",
            "Schlägt das Erstellen eines Panel-weiten Pop-ups / einer Ankündigung vor. "
            "Der Inhalt soll im sauberen Markdown-Format formuliert sein — menschlich, "
            "verständlich und frei von künstlichen KI-Schablonen oder Gedankenstrich-Ketten. "
            "Erfordert zwingend die Freigabe des Benutzers über eine Vorschlagskarte.",
            {
                "title": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "Prägnanter Titel des Pop-ups.",
                },
                "content_markdown": {
                    "type": "string",
                    "maxLength": 32000,
                    "description": "Vollständiger Textinhalt als Markdown.",
                },
                "is_active": {
                    "type": "boolean",
                    "description": "Ob das Pop-up sofort aktiv geschaltet werden soll (Standard: true).",
                },
                "start_at": {
                    "type": ["string", "null"],
                    "maxLength": 32,
                    "description": "Optionales Startdatum (ISO-8601, z. B. 2026-08-26T12:00:00Z).",
                },
                "end_at": {
                    "type": ["string", "null"],
                    "maxLength": 32,
                    "description": "Optionales Enddatum (ISO-8601).",
                },
                "button_text": {
                    "type": ["string", "null"],
                    "maxLength": 100,
                    "description": "Optionaler Beschriftungstext für einen zusätzlichen Aktions-Button (z. B. 'Mehr erfahren').",
                },
                "button_url": {
                    "type": ["string", "null"],
                    "maxLength": 2048,
                    "description": "Optionale Web-Adresse für den Aktions-Button (http:// oder https://).",
                },
                **_RATIONALE_SCHEMA,
            },
            ["title", "content_markdown", *_RATIONALE_REQUIRED],
        ),
    ]


def _worker_tool_definitions() -> list[dict]:
    """Gehirn und Worker (docs/agentic-framework.md).

    Fuenf Werkzeuge, zwei Adressaten: `worker_start`/`worker_cancel`/
    `worker_antwort` gehoeren dem Gehirn, `wait_until`/`worker_frage` nur den
    Workern selbst. Welcher Lauf welche sieht, entscheidet der
    Laufart-Schnitt — hier stehen nur die Schemata, und die stehen wie alle
    im einen Katalog.
    """
    from services.ai_worker_service import (
        MAX_AUFTRAG_CHARS,
        MAX_TITEL_CHARS,
        WAIT_MAX_MINUTEN,
        WAIT_MIN_MINUTEN,
    )

    return [
        _function(
            "worker_start",
            "Übergibt einen Auftrag an einen Worker, der ihn im Hintergrund "
            "erledigt, während du weiter im Gespräch bleibst. Der "
            "`auftrag` ist dessen **einzige** Wissensquelle — schreib alles "
            "hinein, was er braucht: was zu tun ist, woran der Erfolg zu "
            "erkennen ist, und jede Angabe des Benutzers. Nach dem Start "
            "antworte sofort weiter; das Ergebnis kommt später als Meldung. "
            "Versprich nichts über die Dauer.",
            {
                "auftrag": {
                    "type": "string",
                    "maxLength": MAX_AUFTRAG_CHARS,
                    "description": "Vollständiger, aus sich heraus verständlicher Auftrag.",
                },
                "titel": {
                    "type": "string",
                    "maxLength": MAX_TITEL_CHARS,
                    "description": "Kurzer Name für die Auftragsliste des Benutzers.",
                },
                "kanal": {
                    "type": "string",
                    # Die Liste der Meldestelle, nicht die des Aufgabenmodells:
                    # dieser Wert landet in `ai_meldungen`, und was der Katalog
                    # anbietet, muss der Konsument annehmen. Beide Tupel sind
                    # heute gleich; weichen sie einmal ab, boete der Katalog
                    # sonst einen Kanal an, den `ai_worker_service` abweist.
                    "enum": list(_MELDEKANAELE),
                    "description": (
                        "Wohin das Ergebnis gemeldet wird. chat = nur im "
                        "Panel (Standard), email = zusätzlich per Mail, "
                        "both = beides. Im Chat steht das Ergebnis immer."
                    ),
                },
            },
            ["auftrag"],
        ),
        _function(
            "worker_cancel",
            "Bricht einen laufenden Auftrag ab. `worker_id` stammt aus der "
            "Antwort von worker_start oder aus der Lage. Nutze das, wenn der "
            "Benutzer einen Auftrag stoppen will oder er sich erledigt hat.",
            {
                "worker_id": {"type": "string", "maxLength": 36},
            },
            ["worker_id"],
        ),
        _function(
            "worker_antwort",
            "Gibt die Antwort des Benutzers an einen Auftrag zurück, der "
            "eine Frage gestellt hat. `worker_id` steht in der Meldung mit "
            "der Frage. Schreib in `antwort`, was der Benutzer entschieden "
            "hat — wörtlich genug, dass der Auftrag danach handeln kann. "
            "Nicht nutzen, wenn kein Auftrag gefragt hat.",
            {
                "worker_id": {"type": "string", "maxLength": 36},
                "antwort": {
                    "type": "string",
                    "maxLength": MAX_AUFTRAG_CHARS,
                    "description": "Die Entscheidung des Benutzers, vollständig.",
                },
            },
            ["worker_id", "antwort"],
        ),
        _function(
            "wait_until",
            "Parkt **diesen** Lauf und weckt ihn nach der angegebenen Zeit "
            "wieder — für Aufträge, die auf etwas warten (\"in 30 Minuten "
            "nachsehen\", \"heute Nacht prüfen\"). Während des Wartens "
            "kostet der Lauf nichts. Nach dem Wecken prüfst du den Stand im "
            "Verlauf, statt blind zu wiederholen. Nicht für Wartezeiten "
            "unter einer Minute — arbeite dann einfach weiter.",
            {
                "minuten": {
                    "type": "integer",
                    "minimum": WAIT_MIN_MINUTEN,
                    "maximum": WAIT_MAX_MINUTEN,
                },
                "grund": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Worauf gewartet wird. Erscheint in der Auftragsliste.",
                },
            },
            ["minuten"],
        ),
        _function(
            "worker_frage",
            "Stellt dem Benutzer eine Frage, obwohl er dieses Fenster nie "
            "sieht: dein Lauf parkt, die Frage wird ihm im Gespräch gestellt, "
            "und die Antwort weckt genau diesen Lauf. Nutze sie **nur**, wenn "
            "du ohne die Entscheidung nicht weiterkommst — Raten wäre teuer, "
            "Warten sinnlos. Rechne damit, dass die Antwort dauert.",
            {
                "question": {
                    "type": "string",
                    "maxLength": MAX_QUESTION_CHARS,
                    "description": "Die Frage, vollständig und aus sich heraus verständlich.",
                },
                "options": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": MAX_QUESTION_OPTIONS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "maxLength": MAX_OPTION_CHARS},
                            "hint": {
                                "type": "string",
                                "maxLength": MAX_OPTION_HINT_CHARS,
                                "description": "Was diese Wahl bedeutet. Kurz.",
                            },
                        },
                        "required": ["label"],
                        "additionalProperties": False,
                    },
                },
            },
            ["question", "options"],
        ),
    ]


def _voice_tool_definitions() -> list[dict]:
    """Sitzungsgebundene Werkzeuge; andere Rollen filtern sie aus."""
    return [
        _function(
            "voice_resolve_latest_proposal",
            "Bestätigt oder verwirft ausschließlich den zuletzt in dieser "
            "Sprachsitzung angezeigten Vorschlag. Nutze dies nur, wenn der "
            "Benutzer dem sichtbaren Vorschlag eindeutig zustimmt oder ihn "
            "eindeutig ablehnt.",
            {
                "decision": {
                    "type": "string",
                    "enum": ["confirm", "reject"],
                },
            },
            ["decision"],
        )
    ]


def voice_control_tool_definitions() -> list[dict]:
    """Nur der Realtime-Transport erhält diese sitzungsgebundenen Tools."""
    return _voice_tool_definitions()


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
            "Übernimmt Maus und Tastatur. Starte mit aktion='freigabe': "
            "im autonomen Modus sofort erteilt, sonst vom Benutzer bestätigt. "
            "Koordinaten sind Bildpunkte des Bildschirmfotos (Ursprung links oben, "
            "Hauptbildschirm). Vor Klicks mit desktop_system(aktion='bildschirm') prüfen.",
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
            "Verwaltet Desktop-Artefakte (Software, Mods, Installer). "
            "aktion='download': Lädt Datei via HTTPS in Quarantäne. "
            "aktion='pruefen': SHA-256- und Defender-Scan. "
            "aktion='sandbox': Startet isolierte Windows Sandbox zur Prüfung. "
            "aktion='locator': Sucht Spiel- und Softwareinstallationen. "
            "aktion='deploy': Installiert Artefakt mit Snapshot-Manifest. "
            "aktion='rollback': Stellt vorherigen Snapshot-Zustand wieder her. "
            "aktion='installer': Startet Setup-Installer im Benutzerkontext. "
            "aktion='status': Prüft Quarantäne- und Sandbox-Status.",
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
                    "description": "Optionale Argumente für Installer.",
                },
            },
            ["aktion"],
        ),
    ]


def provider_tool_definitions() -> list[dict]:
    """Feste OpenAI-Tool-Allowlist; keine freie Command-Ausfuehrung.

    Es gibt genau *einen* Werkzeugsatz. Die frueher noetige Unterscheidung
    zwischen Panel-Chat und Server-Chat ist mit dem Einzelchat entfallen: der
    Server steht jetzt in den Argumenten, nicht im Gespraech. Das Modell findet
    ihn ueber `list_my_servers` und fragt bei Mehrdeutigkeit nach.
    """
    return [
        *_global_tool_definitions(),
        _server_function(
            "read_server_status",
            "Liest den minimierten Status eines Servers.",
        ),
        _server_function(
            "read_server_capacity",
            "Liest minimierte, zuletzt bekannte Kapazitaetswerte des Servers und "
            "seines Nodes. Die Zahlen des Hosts bekommt nur, wer die Grenzen "
            "dieses Servers aendern darf; sonst kommt `node_details: withheld` "
            "und du darfst ueber die Auslastung des Hosts nichts behaupten.",
        ),
        _server_function(
            "read_server_logs",
            "Liest einen begrenzten, redigierten Log-Ausschnitt des Servers.",
            {"lines": {"type": "integer", "minimum": 1, "maximum": 200}},
        ),
        _server_function(
            "list_server_files",
            "Listet ein Verzeichnis im Serververzeichnis auf. Ohne `path` die "
            "Wurzel. Nutze das, bevor du eine Datei liest — Dateinamen raten "
            "fuehrt zu Fehlversuchen.",
            {"path": {"type": "string", "maxLength": 256}},
        ),
        _server_function(
            "search_server_files",
            "Sucht einen Text in den Dateien des Servers und liefert Pfad und "
            "Zeilennummer jedes Treffers. **Der erste Schritt bei jeder grossen "
            "Datei** — eine Spielkonfiguration hat tausende Zeilen, und "
            "read_config zeigt immer nur ein Fenster davon. Mit `path` auf eine "
            "Datei suchst du in genau ihr, mit `path` auf ein Verzeichnis "
            "darunter, ohne `path` im ganzen Serververzeichnis. Exakter "
            "Teilstring, Gross- und Kleinschreibung egal.",
            {
                "query": {"type": "string", "maxLength": MAX_SEARCH_QUERY_CHARS},
                "path": {"type": "string", "maxLength": 256},
                "context": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": MAX_SEARCH_CONTEXT_LINES,
                    "description": "Zeilen vor und nach jedem Treffer.",
                },
            },
            ["query"],
        ),
        _server_function(
            "read_config",
            "Liest eine Textdatei des Servers revisionssicher — Konfigurationen, "
            "Whitelists, Skripte, alles was der Dateimanager auch zeigt. Ohne "
            f"`offset` die ersten {MAX_READ_CONFIG_LINES} Zeilen; `total_lines` "
            "sagt dir, wie lang die Datei wirklich ist. Zu einer Fundstelle aus "
            "search_server_files springst du mit `offset`. "
            "`editable: false` heisst **nur**, dass du die Datei nicht als "
            "Ganzes ersetzen darfst, weil du sie nicht ganz gesehen hast — mit "
            "`patchable: true` kannst du sie trotzdem per propose_config_patch "
            "aendern. Erst `patchable: false` (Binaerdatei) heisst Finger weg.",
            {
                "path": {"type": "string", "maxLength": 256},
                "offset": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Erste Zeile des Fensters, 1-basiert.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_READ_CONFIG_LINES,
                    "description": "Zeilen im Fenster.",
                },
            },
            ["path"],
        ),
        # ── Erweiterter Serverkontext (Zielpunkt 3.3) ──────────────────────
        _server_function(
            "read_server_ports",
            "Liest die vergebenen Ports des Servers mit Rolle und Protokoll.",
        ),
        _server_function(
            "read_server_network",
            "Liest die Netzwerkeinrichtung: Bind-IP mit Einordnung, Ports, "
            "verfuegbare Host-Adressen und Firewall-Zustand. Erster Schritt, "
            "wenn ein Server laeuft, aber niemand sich verbinden kann. "
            "Rufe danach check_server_reachability auf — erst beide zusammen "
            "ergeben eine Diagnose. read_server_status ist dafuer nicht noetig, "
            "der Status steht bereits in dieser Antwort.",
        ),
        _server_function(
            "check_server_reachability",
            "Misst, ob auf den Ports des Servers tatsaechlich etwas lauscht. "
            "Der eigentliche Beweis bei 'laeuft, aber niemand kommt drauf': "
            "meldet ein Port sich als frei, obwohl der Server laeuft, horcht "
            "der Dienst nicht oder horcht auf einer anderen Adresse. "
            "Beantwortet nicht, ob der Server aus dem Internet erreichbar ist — "
            "das kann MSM nicht messen und behauptet es auch nicht.\n"
            "`game_probe` traegt zusaetzlich das Urteil der Anwendungsprobe, die "
            "der Blueprint deklariert und der Guardian auf der Node ausfuehrt: "
            "`answering` (der Dienst antwortet im Spielprotokoll), "
            "`not_answering` (Port offen, Dienst stumm — der eigentliche Befund "
            "bei 'laeuft, aber niemand kommt drauf'), `not_declared` und "
            "`no_measurement`. **`not_declared` ist kein Fehlerbefund**, sondern "
            "heisst nur, dass dieser Blueprint keine Probe vorsieht; melde es "
            "nicht als Problem.",
        ),
        _server_function(
            "read_server_mods",
            "Liest die installierten Mods mit Aktivierungs-, Installations- und Updatestatus.",
        ),
        _server_function(
            "read_server_backups",
            "Liest die vorhandenen Backups mit Groesse und Zeitpunkt.",
        ),
        _server_function(
            "read_guardian_incidents",
            "Liest die zuletzt erkannten Guardian-Vorfaelle dieses Servers.",
        ),
        _server_function(
            "read_ai_action_history",
            "Liest die vom aktuellen Benutzer frueher vorgeschlagenen und "
            "ausgefuehrten KI-Aktionen dieses Servers.",
        ),
        _server_function(
            "read_mod_updates",
            "Prueft, fuer welche Mods ein Update oder eine Nachinstallation aussteht.",
        ),
        _server_function(
            "search_workshop_mods",
            "Sucht Mods im Steam Workshop oder bei CurseForge fuer das Spiel dieses Servers. "
            "Liefert Kennung, Titel und Tags.",
            {
                "query": {"type": "string", "maxLength": 128},
                "page": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            ["query"],
        ),
        # ── Schreib-Tools: erzeugen ausschliesslich Vorschlaege ────────────
        _server_function(
            "propose_server_lifecycle",
            "Schlaegt Start, Stop oder Neustart zur manuellen Bestaetigung vor.",
            {
                "operation": {"type": "string", "enum": ["start", "stop", "restart"]},
                **_RATIONALE_SCHEMA,
            },
            ["operation", *_RATIONALE_REQUIRED],
        ),
        _server_function(
            "propose_backup",
            "Schlaegt ein Server-Backup zur manuellen Bestaetigung vor. Der "
            "Name hilft dem Benutzer, es spaeter wiederzuerkennen — nenne den "
            "Anlass, nicht das Datum.",
            {
                **_RATIONALE_SCHEMA,
                "name": {"type": "string", "maxLength": MAX_BACKUP_NAME_CHARS},
            },
            list(_RATIONALE_REQUIRED),
        ),
        _server_function(
            "propose_backup_restore",
            "Schlaegt vor, ein vorhandenes Backup einzuspielen. Ueberschreibt "
            "**alle** Serverdaten und stoppt den Server dabei; was seit dem "
            "Backup entstanden ist, geht verloren. Verlangt immer eine "
            "Bestaetigung, auch im autonomen Modus. Die backup_id stammt aus "
            "read_server_backups — rate sie nie.",
            {
                **_RATIONALE_SCHEMA,
                "backup_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "ID aus read_server_backups.",
                },
            },
            ["backup_id", *_RATIONALE_REQUIRED],
        ),
        _server_function(
            "propose_server_blueprint_switch",
            # Der Satz "die Portrollen beider Blueprints muessen
            # uebereinstimmen" stand hier und ist ersatzlos gestrichen: er war
            # erfunden. `switch_server_blueprint` vergibt die Ports ohnehin neu
            # und prueft nichts dergleichen — dokumentiert in
            # `ai_proposal_service` bei den erfundenen Einschraenkungen. Eine
            # Bedingung, die es nicht gibt, haelt das Modell von Wechseln ab,
            # die durchgegangen waeren.
            "Schlaegt vor, einen bestehenden Server auf einen anderen Blueprint "
            "umzustellen — so aendert man die Spielversion, denn sie steht im "
            "Blueprint und nicht am Server. Der Server muss gestoppt sein. Leite "
            "vorher mit propose_blueprint_change einen passenden ab. Der "
            "Vorgang legt zwingend ein Backup an und **loescht danach alle "
            "Serverdateien**, damit die neue Version auf einem leeren "
            "Verzeichnis aufsetzt: Welt, Configs und Mods sind anschliessend "
            "weg und stehen nur noch im Backup. Sage das im Grund ausdruecklich. "
            "Braucht immer eine Bestaetigung durch einen Menschen — auch im "
            "autonomen Modus. Wenn Guardian fuer diesen Server nur falsch "
            "eingestellt ist, nimm propose_guardian_tuning: das aendert nichts "
            "an den Dateien.",
            {
                **_RATIONALE_SCHEMA,
                "blueprint_id": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Ziel-Blueprint aus list_blueprints.",
                },
            },
            ["blueprint_id", *_RATIONALE_REQUIRED],
        ),
        _server_function(
            "propose_server_delete",
            "Schlaegt vor, einen Server vollstaendig zu loeschen: Container, "
            "Dateien, Backups und Ports. Das ist nicht rueckgaengig zu machen "
            "und verlangt immer eine Bestaetigung durch den Benutzer, auch im "
            "autonomen Modus. Nenne im Grund, was verlorengeht.",
            dict(_RATIONALE_SCHEMA),
            list(_RATIONALE_REQUIRED),
        ),
        _server_function(
            "propose_config_update",
            "Ersetzt eine Datei **vollstaendig** — fuer neue Dateien und fuer "
            "kleine, die du ganz gelesen hast (`editable: true`). Bei allem "
            "anderen nimm propose_config_patch: eine Datei, die du nur "
            "ausschnittsweise kennst, ganz zu ersetzen wuerde alles Ungesehene "
            "loeschen, und genau das wird abgewiesen. Niemals Secrets einfuegen.",
            {
                "path": {"type": "string", "maxLength": 256},
                "content": {"type": "string", "maxLength": MAX_CONFIG_CHARS},
                "expected_revision": {"type": ["string", "null"]},
                **_RATIONALE_SCHEMA,
            },
            ["path", "content", "expected_revision", *_RATIONALE_REQUIRED],
        ),
        _server_function(
            "propose_config_patch",
            "Aendert **einzelne Stellen** einer Datei und laesst den Rest "
            "unberuehrt — der Weg fuer jede grosse Datei, auch wenn sie "
            "`editable: false` meldet. Je Eintrag wird `find` durch `replace` "
            "ersetzt. `find` muss **genau einmal** in der Datei vorkommen: nimm "
            "so viel Umgebung mit, dass es eindeutig ist (nicht `value=\"1\"`, "
            "sondern die ganze Zeile oder das Element drumherum). Kommt es "
            "keinmal oder mehrfach vor, wird der Vorschlag abgewiesen und du "
            "musst `find` genauer fassen. `expected_revision` stammt aus "
            "read_config. Weder `find` noch `replace` duerfen Zugangsdaten "
            "enthalten.",
            {
                **_RATIONALE_SCHEMA,
                "path": {"type": "string", "maxLength": 256},
                "expected_revision": {"type": "string", "maxLength": 71},
                "edits": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_PATCH_EDITS,
                    "description": "Ersetzungen, der Reihe nach angewandt.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "find": {
                                "type": "string",
                                "maxLength": MAX_PATCH_CHUNK_CHARS,
                                "description": "Exakter Text, genau einmal vorhanden.",
                            },
                            "replace": {
                                "type": "string",
                                "maxLength": MAX_PATCH_CHUNK_CHARS,
                                "description": "Was stattdessen dastehen soll; leer loescht.",
                            },
                        },
                        "required": ["find", "replace"],
                        "additionalProperties": False,
                    },
                },
            },
            ["path", "expected_revision", "edits", *_RATIONALE_REQUIRED],
        ),
        _server_function(
            "propose_config_set",
            "Setzt einzelne Schluessel in einer INI-artigen Datei — **der "
            "Normalfall fuer Spieleinstellungen**. Du nennst Sektion, "
            "Schluessel und Wert statt Text zu suchen: die Sektion wird "
            "gefunden oder angelegt, ein vorhandener Schluessel ueberschrieben "
            "statt gedoppelt, die Zeilenenden bleiben. Einen fehlenden "
            "Schluessel legst du damit an — Regelfall, kein Hindernis. Der Wert "
            "gilt dauerhaft und wird vor jedem Start neu geschrieben, haelt "
            "also auch bei Spielen, die ihre Konfiguration selbst "
            "zurueckschreiben. Ein laufender Server hindert dich nicht; es "
            "wirkt mit dem naechsten Neustart. `expected_revision` aus "
            "read_config, `null` legt die Datei an. Keine Passwortfelder.",
            {
                **_RATIONALE_SCHEMA,
                "path": {"type": "string", "maxLength": 256},
                "expected_revision": {"type": ["string", "null"], "maxLength": 71},
                "entries": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_PATCH_EDITS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "section": {
                                "type": "string",
                                "maxLength": 128,
                                "description": "Abschnitt ohne Klammern.",
                            },
                            "key": {"type": "string", "maxLength": 128},
                            "value": {"type": "string", "maxLength": 512},
                        },
                        "required": ["section", "key", "value"],
                        "additionalProperties": False,
                    },
                },
            },
            ["path", "expected_revision", "entries", *_RATIONALE_REQUIRED],
        ),
        _server_function(
            "propose_bind_ip_update",
            "Schlaegt eine andere Bind-IP vor — etwa wenn der Server an eine "
            "Docker- oder Loopback-Adresse gebunden ist und deshalb von aussen "
            "nicht erreichbar sein kann. Nur Adressen, die dem Host tatsaechlich "
            "gehoeren; nimm sie aus read_server_network. Ein laufender Server "
            "wird dabei neu gestartet.",
            {
                "bind_ip": {"type": "string", "maxLength": 45},
                **_RATIONALE_SCHEMA,
            },
            ["bind_ip", *_RATIONALE_REQUIRED],
        ),
        _server_function(
            "propose_mod_install",
            "Schlaegt Installation, Aktualisierung oder Neuinstallation einer Workshop- oder CurseForge-Mod vor. "
            "Der Download laeuft ueber den vorhandenen MSM-Installationspfad.",
            {
                "workshop_id": {"type": "string", "maxLength": 20},
                "action": {"type": "string", "enum": ["install", "update", "reinstall"]},
                "name": {"type": "string", "maxLength": 256, "description": "Lesbarer Mod-Titel"},
                **_RATIONALE_SCHEMA,
            },
            ["workshop_id", "action", *_RATIONALE_REQUIRED],
        ),
        _server_function(
            "propose_mod_toggle",
            "Schaltet eine bereits installierte Mod an oder aus. Welche Mods "
            "aktiv sind, steht in der Mod-Liste des Panels (read_server_mods, "
            "Feld `enabled`) — nie in einer Spielkonfiguration. Wirkt erst "
            "beim naechsten Start des Servers.",
            {
                "workshop_id": {"type": "string", "maxLength": 20},
                "enabled": {"type": "boolean"},
                **_RATIONALE_SCHEMA,
            },
            ["workshop_id", "enabled", *_RATIONALE_REQUIRED],
        ),
        # Die Reparatur der **Anlage** — alles unterhalb der Spieldateien.
        #
        # `action` ist ein `enum` und kein Freitext, und das ist der ganze Sinn
        # des Werkzeugs: das Modell waehlt eine von vier Kennungen. Es formuliert
        # keinen Pfad, kein Kommando und keinen Containernamen — der kommt aus
        # `container_name_for(server_id)`. Ein Modell, das durch eine Logzeile
        # zu etwas ueberredet wurde, kann hier hoechstens die falsche der vier
        # Reparaturen anstossen.
        _server_function(
            "propose_server_repair",
            "Repariert die Anlage unter dem Server, nicht seine Dateien. Zwei "
            "Moeglichkeiten: `repair_permissions` berichtigt die Besitzrechte am "
            "Serververzeichnis — der Weg bei 'permission denied', 'read-only "
            "file system' oder wenn der Server seine eigenen Dateien nicht mehr "
            "schreiben kann. `reallocate_port` vergibt die Ports neu, die auf "
            "dem Host jemand anderes belegt — der Weg bei 'address already in "
            "use', aber nur bei einem **gestoppten** Server; bei einem laufenden "
            "haelt er seine Ports selbst und es gibt nichts zu vergeben. "
            "Nichts davon aendert Spielstaende. "
            "Fuer 'Container haengt' oder 'startet nicht' nimm "
            "propose_server_lifecycle mit `restart` — das baut den Container "
            "ohnehin aus dem Blueprint neu auf.",
            {
                "action": {
                    "type": "string",
                    "enum": ["repair_permissions", "reallocate_port"],
                },
                **_RATIONALE_SCHEMA,
            },
            ["action", *_RATIONALE_REQUIRED],
        ),
        # Guardian **fuer diesen einen Server** anders einstellen.
        #
        # Alle Felder sind Zahlen mit Ober- und Untergrenze, und es gibt keine
        # anderen. Damit kann ein Modell, das durch eine praeparierte Logzeile
        # ueberredet wurde, hoechstens einen ungeschickten Wert waehlen — es
        # kann keine Probe abschalten, keinen Probentyp tauschen und kein
        # Muster einschmuggeln.
        _server_function(
            "propose_guardian_tuning",
            "Stellt die Guardian-Engine **fuer diesen einen Server** anders ein, "
            "ohne die Blueprint anderer Server anzufassen. Der Weg fuer den Fall, "
            "dass Guardian sich nicht geirrt hat und der Server nicht kaputt ist, "
            "sondern Guardian fuer diesen Server falsch eingestellt wurde — etwa "
            "wenn eine volle Node laenger zum Hochfahren braucht, als die "
            "Blueprint erwartet, und deshalb dauernd Neustarts gemeldet werden. "
            "Gib nur die Werte an, die du aendern willst; die uebrigen bleiben "
            "stehen. `reset: true` nimmt alles zurueck und laesst wieder die "
            "Blueprint gelten. Aendert keine Datei und keinen Spielstand.",
            {
                "startup_grace_period_seconds": {
                    "type": "integer", "minimum": 1, "maximum": 600,
                    "description": "Ruhe nach dem Start, bevor Proben zaehlen.",
                },
                "startup_timeout_seconds": {
                    "type": "integer", "minimum": 10, "maximum": 3600,
                    "description": (
                        "Ab wann ein Start als gescheitert gilt. "
                        "Muss ueber der Ruhezeit liegen."
                    ),
                },
                "probe_interval_seconds": {
                    "type": "integer", "minimum": 1, "maximum": 600,
                    "description": "Abstand zwischen zwei Proben (alle Proben).",
                },
                "probe_timeout_seconds": {
                    "type": "integer", "minimum": 1, "maximum": 30,
                    "description": "Geduld einer Netzprobe.",
                },
                "probe_failure_threshold": {
                    "type": "integer", "minimum": 1, "maximum": 20,
                    "description": "Fehlschlaege in Folge bis zum Vorfall.",
                },
                "probe_success_threshold": {
                    "type": "integer", "minimum": 1, "maximum": 20,
                    "description": "Erfolge in Folge bis 'gesund'.",
                },
                "recovery_max_attempts": {
                    "type": "integer", "minimum": 0, "maximum": 10,
                    "description": (
                        "Wieviele Selbstheilungsversuche Guardian unternimmt. "
                        "0 heisst: gar keine mehr, nur noch melden."
                    ),
                },
                "recovery_attempt_window_seconds": {
                    "type": "integer", "minimum": 60, "maximum": 86400,
                    "description": "Zeitfenster, in dem die Versuche gezaehlt werden.",
                },
                "recovery_cooldown_seconds": {
                    "type": "integer", "minimum": 1, "maximum": 3600,
                    "description": "Pause zwischen zwei Versuchen.",
                },
                "verification_min_healthy_seconds": {
                    "type": "integer", "minimum": 0, "maximum": 600,
                    "description": "Wie lange gesund, damit es als geheilt gilt.",
                },
                "verification_required_successes": {
                    "type": "integer", "minimum": 1, "maximum": 20,
                    "description": "Erfolge in Folge fuer die Bestaetigung.",
                },
                "verification_timeout_seconds": {
                    "type": "integer", "minimum": 10, "maximum": 3600,
                    "description": "Ab wann die Bestaetigung als gescheitert gilt.",
                },
                "reset": {
                    "type": "boolean",
                    "description": (
                        "Alle Uebersteuerungen zuruecknehmen. Schliesst jede "
                        "andere Angabe aus."
                    ),
                },
                **_RATIONALE_SCHEMA,
            },
            [*_RATIONALE_REQUIRED],
        ),
        # ── Die eingebauten Zeitpläne: Auto-Neustart und Auto-Backup ──────
        #
        # Der Durchgriff statt einer stehenden Aufgabe: was hier gesetzt wird,
        # sieht der Benutzer im Panel unter dem Server und kann es dort selbst
        # ändern. Eine manuelle Änderung nimmt der KI die Verwaltung wieder ab.
        #
        # Der **Anlass** — wann diese zwei Werkzeuge statt `propose_task_set`
        # gelten, dass je Server ein Aufruf reicht und dass nicht nachgefragt
        # wird — steht in `ai_prompt.AUFGABEN` und geht mit derselben Anfrage
        # mit. Hier steht nur die Feldkunde; die Wiederholung des Anlasses
        # kostete den Katalog knapp 1.000 Zeichen je Runde (siehe
        # test_ai_tool_handler_contract zum Katalogbudget).
        _server_function(
            "propose_restart_schedule_set",
            "Setzt den eingebauten Auto-Neustart-Zeitplan dieses Servers. "
            "Entweder `interval_hours` oder `times`, nie beides; "
            "`enabled: false` schaltet aus und braucht keinen Plan. "
            "`times` sind UTC und gelten täglich — rechne die Ortszeit des "
            "Benutzers um und nenne ihm beide Werte.",
            {
                "enabled": {"type": "boolean"},
                "interval_hours": {
                    "type": "integer", "minimum": 1, "maximum": 168,
                    "description": "Neustart alle N Stunden (1-168).",
                },
                "times": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 12,
                    "items": {"type": "string", "maxLength": 5},
                    "description": "Bis zu 12 Neustartzeiten 'HH:MM' in UTC, gelten täglich.",
                },
                **_RATIONALE_SCHEMA,
            },
            ["enabled", *_RATIONALE_REQUIRED],
        ),
        _server_function(
            "propose_backup_schedule_set",
            "Setzt den eingebauten Auto-Backup-Zeitplan dieses Servers. "
            "Nur genannte Felder werden angefasst; die übrigen bleiben stehen.",
            {
                "backup_on_start": {
                    "type": "boolean",
                    "description": "Vor jedem Serverstart automatisch sichern.",
                },
                "interval_hours": {
                    "type": "integer", "minimum": 0, "maximum": 720,
                    "description": (
                        "Backup alle N Stunden; 0 = aus, 24 = täglich, "
                        "168 = wöchentlich, 720 = alle 30 Tage."
                    ),
                },
                "retention_count": {
                    "type": "integer", "minimum": 1, "maximum": 100,
                    "description": "Aufbewahrte Backups (1-100); ältere werden gelöscht.",
                },
                **_RATIONALE_SCHEMA,
            },
            [*_RATIONALE_REQUIRED],
        ),
        _server_function(
            "propose_file_delete",
            "Loescht **eine** Datei des Servers. Gedacht fuer haengende "
            "Sperrdateien (`session.lock`, `*.pid`) und nachweislich kaputte "
            "Einzeldateien, die der Server beim Start nicht mehr lesen kann. "
            "Vorher wird derselbe Versionsschnappschuss angelegt wie beim "
            "Schreiben, der Dateimanager holt die Datei also einzeln zurueck. "
            "Im autonomen Guardian-Betrieb laeuft es nur mit einem nachweislich "
            "erfolgreichen Backup, das juenger ist als der Vorfall — fehlt es, "
            "wird der Vorschlag abgewiesen, und du legst erst eines an. "
            "Kein Verzeichnis, keine Platzhalter: genau ein Pfad, den du vorher "
            "mit list_server_files oder read_config gesehen hast.",
            {
                "path": {"type": "string", "maxLength": 256},
                **_RATIONALE_SCHEMA,
            },
            ["path", *_RATIONALE_REQUIRED],
        ),
        *_desktop_tool_definitions(),
    ]


def angebotene_werkzeuge(db: Session, user: User) -> frozenset[str]:
    """Die Werkzeuge, die diesem Benutzer ueberhaupt angeboten werden.

    **Der Katalog ist eine Bitte, keine Zusage.** Was hier fehlt, wird nicht
    angeboten; was hier steht, ist damit noch lange nicht erlaubt. Die Schranke
    bleibt unveraendert dort, wo sie war — `_resolve_server`, die
    Rechtepruefung im jeweiligen Handler und `_require_tool_permission` im
    Vorschlagspfad. Ein Modell, das sich ein Werkzeug ausdenkt oder aus dem
    Gespraechsverlauf abschreibt, prallt dort weiterhin ab.

    Warum es das trotzdem gibt, und zwar zuerst als **Korrektur**: die KI erbt
    die Rechte des Benutzers. Wer kein Hoster-Recht hat, dessen KI kann die
    Hoster-Werkzeuge nicht ausfuehren — angeboten bekam er sie trotzdem, alle
    51. Das Modell versuchte sie, wurde abgewiesen und hatte eine Runde
    verbraucht. Wir haben ihm also Faehigkeiten angeboten, die es in seinem
    Namen nie hatte.

    Die Ersparnis kommt obendrauf: der Katalog geht in **jeder** Runde der
    Werkzeugschleife mit ueber die Leitung und machte 94 Prozent des Prompts
    aus. Und die Trefferqualitaet steigt — bei 51 aehnlichen Werkzeugen greift
    ein Modell haeufiger zum falschen.

    Gefragt wird `has_permission_anywhere` und nicht `has_server_permission`:
    hier gibt es noch keinen Server. Den waehlt das Modell erst im Argument des
    Aufrufs, und dort wird dann am konkreten Server geprueft.
    """
    # Alle 24 Schluessel in einer Runde. Der Merkzettel je Schluessel, der hier
    # zuerst stand, half nur halb: er sparte die Wiederholung je Werkzeug, nicht
    # die je Schluessel — und darunter fragte jede Pruefung die Rollen des
    # Benutzers erneut ab. Gemessen waren das 73 Abfragen bei einem
    # gewoehnlichen Kunden und 93 bei einem Rolleninhaber, jedes Mal am Beginn
    # eines Segments und damit auf dem Pfad zum ersten Token.
    verlangt = {key for name in WERKZEUGE for key in angebotsrechte(name)}
    gehalten = permission_service.rechte_irgendwo(db, user, verlangt)

    from services.ai_guardian_settings import is_guardian_ai_enabled
    from services.ai_tool_registry import GUARDIAN_TOOLS

    verfuegbar = set(
        name for name in WERKZEUGE
        if not angebotsrechte(name) or any(key in gehalten for key in angebotsrechte(name))
    )
    if not is_guardian_ai_enabled():
        verfuegbar -= GUARDIAN_TOOLS
    return frozenset(verfuegbar)


#: Wieviele Wiederherstellungsversuche eines Vorfalls die KI zu sehen bekommt.
#: Die juengsten — bei einem Vorfall, der seit Stunden laeuft, sagt der erste
#: Versuch weniger als der letzte.
MAX_INCIDENT_ATTEMPTS = 8


def _vorfall_versuche(attempts_json: str | None) -> list[dict]:
    """Die Wiederherstellungsversuche des Agenten, auf das Noetige gekuerzt.

    Sie stehen als JSON-Zeichenkette am Vorfall (`incidents.attempts`) und
    tragen je Eintrag `attempt`, `stage`, `action`, `at` und `result`. Genau
    diese fuenf gehen weiter — mehr steht nicht drin, und was der Agent kuenftig
    ergaenzt, soll nicht ungefragt an einen Modellanbieter gehen.

    Ohne diese Liste faengt die KI bei jedem Vorfall mit einem Neustart an. Das
    ist der Schritt, den die Guardian-Engine ausweislich ihrer eigenen
    Eskalationsleiter schon dreimal gemacht hat, bevor sie den Vorfall ueberhaupt
    meldete — die KI wuerde also als Erstes das wiederholen, was nachweislich
    nicht geholfen hat.

    Unlesbares gibt eine leere Liste. Der Inhalt kommt vom Agenten, und ein
    kaputtes JSON darf hier kein Werkzeug zum Absturz bringen.
    """
    try:
        geladen = json.loads(attempts_json) if attempts_json else []
    except (TypeError, ValueError):
        return []
    if not isinstance(geladen, list):
        return []
    erlaubt = ("attempt", "stage", "action", "at", "result")
    versuche = [
        {k: v for k, v in eintrag.items() if k in erlaubt}
        for eintrag in geladen
        if isinstance(eintrag, dict)
    ]
    return versuche[-MAX_INCIDENT_ATTEMPTS:]


def _require_no_arguments(tool_name: str, arguments: dict) -> None:
    if arguments:
        raise AiActionValidationError(f"{tool_name} akzeptiert keine Argumente")


def _visible_servers(db: Session, user: User) -> list[Server]:
    """Alle Server, die der Benutzer sehen darf — die Grundlage von `list_my_servers`.

    Die Auflösung von Rollenrechten *und* einzeln delegierten Serverrechten
    liegt an genau einer Stelle — sie ist nur die Mengenfunktion und nicht die
    Einzelprüfung. Hier stand einmal eine Schleife, die `has_server_permission`
    je Serverzeile rief. Sie lieferte dieselbe Menge, kostete aber drei Abfragen
    je Zeile, und der Deckel griff erst bei 60 *sichtbaren* Treffern: ein Kunde
    mit einem Server unter fünfhundert lief alle fünfhundert Zeilen durch, auf
    dem Weg zum ersten Token. `list_visible_server_ids` beantwortet dieselbe
    Frage gebündelt, einschließlich des Teamwegs.

    Die Obergrenze verhindert, dass ein Betreiber mit hunderten Servern die
    halbe Liste ins Kostenbudget des Benutzers schreibt. Sie steht jetzt in der
    Abfrage statt in der Schleife und zieht dieselbe Grenze.
    """
    # Dreiwertig: `None` heißt **alle** (Eigentümer oder pauschale Rolle), eine
    # leere Liste heißt **keiner**. Die beiden zu verwechseln wäre in der einen
    # Richtung eine Rechteausweitung und in der anderen eine leere Liste für den
    # Betreiber.
    ids = permission_service.list_visible_server_ids(db, user)
    if ids is not None and not ids:
        return []
    abfrage = db.query(Server)
    if ids is not None:
        abfrage = abfrage.filter(Server.id.in_(ids))
    return abfrage.order_by(Server.id).limit(MAX_LISTED_SERVERS).all()


def _resolve_server(db: Session, user: User, arguments: dict) -> tuple[Server, dict]:
    """Entnimmt ``server_id``, laedt den Server und prueft `server.view`.

    Das ist die Stelle, an der "die KI erbt die Rechte des Benutzers" fuer jedes
    serverbezogene Werkzeug tatsaechlich durchgesetzt wird — einmal, zentral,
    fuer Lese- und Schreibwerkzeuge gleichermassen. Ein Modell, das eine fremde
    ID errraet oder aus einem manipulierten Logtext uebernimmt, kommt hier nicht
    vorbei.

    Ein nicht sichtbarer Server ist bewusst nicht von einem nicht existierenden
    zu unterscheiden: sonst waere die Fehlermeldung ein Existenzorakel.
    """
    rest = {key: value for key, value in arguments.items() if key != "server_id"}
    raw = arguments.get("server_id")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise AiActionValidationError(
            "server_id fehlt oder ist ungueltig. Zuerst list_my_servers aufrufen."
        )
    server = db.get(Server, raw)
    if server is None or not permission_service.has_server_permission(
        db, user, raw, "server.view"
    ):
        raise AiActionValidationError("Server nicht gefunden")
    return server, rest


_MEMORY_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _execute_set_agent_name(db: Session, *, user: User, arguments: dict) -> dict:
    """Setzt den Rufnamen des Assistenten — dasselbe Feld wie der Router
    PATCH /auth/me/agent-name (users.agent_name), mit derselben Prüfung.

    Kein eigenes Recht: es ist eine persönliche, jederzeit umkehrbare
    Einstellung des Benutzers, die er im Panel ohnehin selbst ändern darf.
    Sofort ausgeführt statt vorgeschlagen — dieselbe Einordnung wie
    `remember` (ai_tool_registry erklärt sie).

    Der neue Name wirkt ab dem nächsten Zug (Lageblock, services/ai_lage.py);
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
        # auch das Werkzeug ab — ein Formfehler kostet eine Runde, nie mehr.
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
    """Welches Team ein Gedächtniswerkzeug meint — die Nummer schlägt den Namen.

    Zwei Wege auf dasselbe Team, und der genauere gewinnt.

    **Der Name trägt nicht allein.** Teamnamen sind nur je Gründer eindeutig
    (`team_service._assert_name_is_free` lässt Gleichnamigkeit ausdrücklich zu).
    Ist der Benutzer in zwei Teams namens "Alpha", benennt `team="Alpha"` beide;
    `learning_team` fragt dann zurück, und seine Rückfrage unterscheidet die
    Kandidaten über den Gründer ("Alpha (bob)"). Ein Suchtreffer, der nur den
    blanken Namen trug, ließ sich keinem davon zuordnen — das Modell wählte
    eines der beiden und löschte mit halber Wahrscheinlichkeit im falschen Team.
    Folgenlos ist das nicht: Schlüssel sind bewusst stabil und wiederholen sich
    über Teams hinweg, drüben steht also etwas zu treffen.

    **Die Nummer aus dem Suchtreffer hat dieses Problem nicht.** Sie trifft
    genau ein Team, so wie `server_id` seit jeher genau einen Server trifft. Sie
    ist dabei **kein Freibrief**: `ai_memory_service.scope_identity` weist eine
    Nummer ohne Mitgliedschaft mit 404 ab, `_assert_may_write` eine ohne
    Verwaltungsschalter mit 403. Beide Schranken stehen ohnehin im Weg jedes
    Schreibens und Löschens — durchgereicht wird hier deshalb nur eine Zahl,
    keine Berechtigung.

    Der Name bleibt als Rückfall stehen und wird nicht ersetzt. Ein Modell, das
    ein Team nur aus dem Gespräch kennt und nie danach gesucht hat, soll nicht
    daran scheitern, dass ihm die Nummer fehlt.
    """
    roh = arguments.get("team_id")
    if scope != "team":
        # Dieselbe Strenge wie bei `server_id` im falschen Bereich: ein Bezug,
        # der nicht ausgewertet wird, ist ein Missverständnis und keine
        # Nachlässigkeit, über die man hinwegsehen darf.
        if roh is not None:
            raise AiActionValidationError("Nur Team-Memory akzeptiert eine team_id")
        return scope, None, None
    if roh is not None:
        if isinstance(roh, bool) or not isinstance(roh, int) or roh < 1:
            raise AiActionValidationError(
                "Ungültige team_id — nimm die Nummer aus dem Suchergebnis"
            )
        return scope, roh, None

    from services import team_service

    # `memory` und nicht `skills`: welcher Schalter zählt, entscheidet die Art
    # des Wissens. Beide Erinnerungswerkzeuge fragten hier den Skill-Schalter ab
    # und schrieben deshalb bei `memory=True, skills=False` still ins
    # persönliche Gedächtnis.
    ziel, frage = team_service.learning_team(
        db, user, schalter="memory", wunsch=arguments.get("team"),
    )
    if ziel is None:
        return scope, None, frage
    if ziel.is_personal:
        # Kein echtes Team vorhanden oder keine Verwaltungsberechtigung: der
        # Eintrag wird persönlich statt gar nicht. Lieber zu eng gespeichert als
        # zu weit.
        return "user", None, None
    return scope, ziel.id, None


def _execute_remember(db: Session, *, user: User, arguments: dict) -> dict:
    """Laesst die KI einen dauerhaften Fakt im Memory des Benutzers ablegen.

    Die Rechtegrenze ist `ai.memory.use` — dasselbe Recht, das entscheidet, ob
    Memory ueberhaupt in den Kontext fliesst. Wer sein Memory nicht nutzen darf,
    bekommt auch keines geschrieben.

    Alle inhaltlichen Schutzmassnahmen liegen bereits in
    `ai_memory_service.upsert_entry`: Secret-Abweisung, Groessengrenze,
    DIS-Verschluesselung, Scope-Trennung je Benutzer und die Regel, dass eine
    Ableitung der KI keine ausdrueckliche Ansage des Benutzers ueberschreibt.
    Hier steht die Argumentpruefung — und die Uebersetzung einer Absage in eine
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
    # Nummer nennen, aber nichts über sie behaupten: ob der Benutzer dort
    # Mitglied ist und dessen Wissen pflegen darf, bleibt eine Tatsache der
    # Datenbank und wird gleich in `upsert_entry` geprüft. Ist die Lage nicht
    # eindeutig, bekommt das Modell die Rückfrage als Ergebnis und fragt den
    # Benutzer.
    scope, team_id, rueckfrage = _memory_team(db, user, scope=scope, arguments=arguments)
    if rueckfrage is not None:
        return {"remembered": False, "ask_user": rueckfrage}

    # Die Einwilligung gilt dem **eigenen** Gedaechtnis, also `user` und
    # `server` — `team` und `panel` haengen an Mitgliedschaft und
    # Betreiberentscheidung (siehe `_visible_scope_rows`).
    #
    # Geprueft wurde sie bisher nur beim **Lesen**. Beim abgeschalteten Schalter
    # legte die KI also weiter Zeilen an; sie wurden nur nicht mehr vorgelesen.
    # Zwei Folgen, beide schlecht: der Hinweis in der Oberflaeche sagt „Derzeit
    # ist das Gedaechtnis deaktiviert“, waehrend im Hintergrund mitgeschrieben
    # wird — und wer den Schalter spaeter umlegt, bekommt schlagartig alles zu
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
        # lautlos passieren — zu Recht, ein Gedaechtnis soll wirken und nicht
        # auftreten. Genau das machte diesen Fall unsichtbar: der Schalter ist
        # ohne Zeile **aus** (Datenminimierung, `ai_memory_service.preference`),
        # das Modell versuchte es korrekt, scheiterte korrekt und schwieg
        # korrekt. Der Betreiber am 22.08.2026: "die KI merkt sich auch gar
        # nichts" — er konnte es nicht wissen, ihm hat es nie jemand gesagt.
        #
        # Die Ausnahme steht hier und nicht im Prompt, weil nur hier bekannt
        # ist, dass sie zutrifft. Ein Satz im Prompt kostete jeden Lauf Tokens,
        # auch die, in denen der Schalter an ist.
        return {
            "remembered": False,
            "reason": "memory_disabled",
            "message": (
                "Der Benutzer hat sein persoenliches Gedaechtnis abgeschaltet. "
                "Es wurde nichts gespeichert — und du wirst dir bis auf "
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
    # Modell dazu an — aber eine Anweisung ist keine Garantie, und `ram.vorgabe`
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
        # **Ein vorhandener Schlüssel ist kein Doppel, sondern das Update.**
        #
        # Die Absage unten empfiehlt genau diesen Aufruf — sie darf ihn nicht
        # selbst abweisen. `aehnlicher_eintrag` schließt nur den identischen
        # Schlüssel aus; stehen im Bereich schon zwei ähnliche Altlasten
        # nebeneinander (genau die, gegen die die Prüfung gebaut ist:
        # `ram.vorgabe` neben `standard_ram`), fand der Aufruf mit dem einen
        # Schlüssel den anderen und umgekehrt. Das Modell pendelte zwischen
        # zwei Absagen, bis die Runden aufgebraucht waren, und ein
        # ausdrücklich gewünschtes "ich will jetzt 16 GB" scheiterte still.
        #
        # Eine Abfrage auf (Bereich, Schlüssel) reicht dagegen: sie beantwortet
        # die einzige Frage, die hier zählt — Neuanlage oder Überschreiben.
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
                        f"mit key='{vorhanden.key}' auf — das ueberschreibt ihn. "
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
        # dienen soll, dient keinem — der Dienst sagt deshalb die Tatsache, und
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
        # zuletzt gebraucht wurde — bei `team` und `server_shared` obendrein die
        # Betriebsanleitung der Kollegen. `forget_memory` fragt vorher
        # niemanden.
        if exc.grenze == 0:
            hinweis = "Versuch es nicht erneut."
        elif exc.bestand == exc.grenze:
            hinweis = (
                "Suche mit search_memory, was nicht mehr gilt, nenne es dem "
                "Benutzer und lösche es mit forget_memory — aber nur Einträge "
                "aus genau diesem Bereich, denn die Suche geht über alle "
                "Bereiche, die er sehen darf."
            )
        else:
            hinweis = (
                "Nenne dem Benutzer den Stand und frag, was weg soll. Lösche "
                "hier nichts von dir aus: bei dieser Menge triffst du nicht, "
                "was nicht mehr gilt, sondern was zuletzt gebraucht wurde."
            )
        raise AiActionValidationError(f"{exc.detail} {hinweis}") from exc
    except DisSidecarError:
        # **Der Verschlüsselungsdienst antwortet nicht — und das darf nicht den
        # Lauf kosten.**
        #
        # `upsert_entry` verschlüsselt über den DIS-Sidecar; bei Zeitablauf oder
        # einer Antwort ungleich 200 kommt von dort eine gewöhnliche Ausnahme,
        # keine `HTTPException`. Sie flog bis in den Segmentfang des Streams:
        # der ganze Lauf endete mit `AI_STREAM_FAILED` und der Benutzer verlor
        # die komplette Antwort — wegen einer Notiz, die das Modell nebenbei
        # und lautlos machen sollte. Nebenan gilt längst das Gegenteil: "Ein
        # Gedächtnis ist eine Beigabe. Es darf fehlen; es darf nicht im Weg
        # stehen" (`ai_memory_service._entschluesseln`).
        #
        # `rollback` wie im Router-Zwilling: sonst trägt die Sitzung die
        # angefangene Zeile weiter und der nächste Werkzeugaufruf desselben
        # Laufs scheitert an ihr.
        #
        # Der Text sagt ausdrücklich, dass ein zweiter Versuch nichts bringt —
        # ohne das wiederholt das Modell den Aufruf, bis die Runden alle sind.
        db.rollback()
        return {
            "remembered": False,
            "reason": "memory_unavailable",
            "message": (
                "Das Gedächtnis ist gerade nicht erreichbar, es wurde nichts "
                "gespeichert. Versuch es nicht noch einmal — arbeite ohne die "
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
    # Er ist die Hälfte des Rückwegs: die Nummer daneben spricht das Team an
    # (`forget_memory(team_id=…)`), der Name macht es aussprechbar — "in Alpha
    # steht noch das alte Wartungsfenster" ist ein Satz, "in Team 7" keiner.
    # Damit ist auch die Auflage aus der vollen Absage befolgbar: "nur Einträge
    # aus genau diesem Bereich", wobei der Bereich dort als Name genannt wird
    # (`ai_memory_service._bereichsname`).
    #
    # **Der Name kommt aus `ansprechbarer_name` und nicht aus `team.name`.**
    # Teamnamen sind nur je Gründer eindeutig; ist der Benutzer in zwei Teams
    # namens "Alpha", benannte der blanke Name beide. Zwei Treffer standen dann
    # ununterscheidbar nebeneinander, und weil Schlüssel bewusst stabil sind und
    # sich über Teams hinweg wiederholen, löschte ein
    # `forget_memory(team="Alpha")` im falschen Team, statt ins Leere zu laufen.
    # `ansprechbarer_name` hängt in diesem Fall den Gründer an — genau die Form,
    # die `learning_team` in seiner Rückfrage anbietet und wieder annimmt.
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
        # Fehlt die Zeile wider Erwarten, bleibt es bei `team_id` allein — und
        # damit bei dem Weg, der ohnehin der genauere ist. Ein ersatzweises
        # "Team 7" wäre schlimmer als nichts: das Modell setzte es als `team`
        # ein, `learning_team` träfe damit keinen Kandidaten und antwortete mit
        # derselben Rückfrage wie ohne jede Angabe.
        results.append(treffer)

    return {"untrusted": True, "query": query, "results": results}


def _execute_forget_memory(db: Session, *, user: User, arguments: dict) -> dict:
    """Loescht ausdruecklich benannte Eintraege — nie einen Suchbegriff.

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
    # Memory-Bereich" — eine Sackgasse, die dem Benutzer als Weigerung erschien.
    server_id = arguments.get("server_id")
    serverbezogen = scope in {"server", "server_shared"}
    if serverbezogen:
        if isinstance(server_id, bool) or not isinstance(server_id, int) or server_id < 1:
            raise AiActionValidationError(
                "Server-Memory braucht die server_id aus dem Suchergebnis"
            )
    elif server_id is not None:
        raise AiActionValidationError("Dieser Memory-Bereich akzeptiert keinen Server")

    # Hier zählt die Nummer am meisten: gelöscht wird nichts, was sich
    # zurückholen lässt, und ein Griff ins gleichnamige Nachbarteam trifft dort
    # denselben Schlüssel. Die Prüfung dahinter ist dieselbe wie beim Schreiben
    # — `delete_by_keys` führt beide Schranken.
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
        # **Wo** gelöscht wurde, gehört ins Ergebnis. Bei zwei gleichnamigen
        # Teams ist "im Team gelöscht" keine Auskunft, sondern eine Zusage, die
        # das Modell nicht belegen kann — mit der Nummer sagt es dem Benutzer
        # dasselbe, was es dem Werkzeug gesagt hat.
        **({"team_id": team_id} if team_id is not None else {}),
        **({"not_found": missing} if missing else {}),
    }


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
    uebrig, wird nicht geraten, sondern zurueckgefragt — dieselbe Vorsicht, die
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


def _execute_analyze_region(
    db: Session, *, user: User, arguments: dict, prefetch_session_id: str | None = None,
) -> dict:
    """Führt eine regionale Analyse samt optionaler, aktueller Weblage durch."""
    from services import ai_geo_service, ai_web_search_service, permission_service

    if not permission_service.has_global_permission(db, user, "ai.satellite.use"):
        raise AiActionValidationError("Satelliten- und Regionsanalyse ist für diesen Benutzer nicht freigegeben")

    location = arguments.get("location")
    if not isinstance(location, str) or not location.strip():
        raise AiActionValidationError("Ort (location) fehlt oder ist ungültig")
    camera = arguments.get("camera", "focus")
    if camera not in {"overview", "focus", "detail"}:
        raise AiActionValidationError("Kameramodus ist ungültig")

    safe_location = redact_sensitive_text(location.strip())[:100]
    can_search = permission_service.has_global_permission(db, user, "ai.web_search.use")
    search_configured = can_search and ai_web_search_service.is_configured()

    def regional_news() -> tuple[list[dict], str]:
        if not can_search:
            return [], "not_allowed"
        if not search_configured:
            return [], "not_configured"
        try:
            results = ai_web_search_service.search(
                f"{safe_location} aktuelle Nachrichten Lagebericht",
                5,
                cache_scope=(f"voice:{user.id}:{prefetch_session_id}" if prefetch_session_id else None),
            )
            return results, "available"
        except ai_web_search_service.WebSearchUnavailable as exc:
            return [], exc.code.lower()

    # Das Geocoding startet Wetter und Sentinel intern parallel. Die Weblage
    # hängt davon nicht ab und läuft deshalb zeitgleich statt danach.
    regional_cache_scope = (
        f"regional:{user.id}:{prefetch_session_id}" if prefetch_session_id else None
    )
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="msm-region") as executor:
        analysis_future = executor.submit(
            ai_geo_service.analyze_region, safe_location, cache_scope=regional_cache_scope,
        )
        news_future = executor.submit(regional_news)
        analysis = analysis_future.result()
        news, news_status = news_future.result()
    if analysis.get("status") != "success":
        return analysis
    analysis["camera"] = {"mode": camera, "command_id": str(uuid4())}
    analysis["news"] = news
    analysis["news_status"] = news_status
    return analysis


def _execute_control_region_camera(db: Session, *, user: User, arguments: dict) -> dict:
    """Erzeugt einen einmaligen Kamerabefehl ohne erneute Regionsanalyse."""
    from services import ai_geo_service

    if not permission_service.has_global_permission(db, user, "ai.satellite.use"):
        raise AiActionValidationError("Kartensteuerung ist für diesen Benutzer nicht freigegeben")
    if set(arguments) - {"action", "location"}:
        raise AiActionValidationError("Kartensteuerung hat ungültige Argumente")
    action = arguments.get("action")
    if action not in {"zoom_in", "zoom_out", "overview", "focus_location"}:
        raise AiActionValidationError("Kartenaktion ist ungültig")
    if action == "focus_location":
        location = arguments.get("location")
        if not isinstance(location, str) or not location.strip():
            raise AiActionValidationError("Sehenswürdigkeit (location) fehlt oder ist ungültig")
        safe_location = redact_sensitive_text(location.strip())[:100]
        geo = ai_geo_service.geocode_location(safe_location)
        if not geo:
            raise AiActionValidationError("Sehenswürdigkeit konnte nicht geocodiert werden")
        return {
            "action": action,
            "command_id": str(uuid4()),
            "location": geo["name"],
            "country": geo["country"],
            "coordinates": {
                "latitude": geo["latitude"],
                "longitude": geo["longitude"],
                "bbox": geo["bbox"],
            },
        }
    if set(arguments) != {"action"}:
        raise AiActionValidationError("location ist nur für focus_location zulässig")
    return {"action": action, "command_id": str(uuid4())}


def _node_health(db: Session) -> dict:
    """Zustand aller Hosts — ohne Hostnamen und ohne IP.

    Dieselbe Zurueckhaltung wie bei `read_node_capacity`: das Modell soll
    Auslastung und Erreichbarkeit vergleichen koennen, nicht die Netzstruktur
    des Betreibers kennen. Ein Node-Name waere zusaetzlich frei befuellter Text
    und damit ein weiterer Einfallsweg fuer Prompt Injection.
    """
    from models import Node
    from services.node_service import is_node_offline

    rows = db.query(Node).order_by(Node.id).limit(MAX_LISTED_NODES).all()
    nodes = []
    for node in rows:
        ram_percent = (
            round(node.ram_used / node.ram_total * 100, 1)
            if node.ram_total and node.ram_used is not None
            else None
        )
        disk_percent = (
            round(node.disk_used / node.disk_total * 100, 1)
            if node.disk_total and node.disk_used is not None
            else None
        )
        nodes.append({
            "node_id": node.id,
            "is_local": bool(node.is_local),
            "status": node.status,
            "offline": is_node_offline(node),
            "docker_connected": node.docker_connected,
            "container_count": node.container_count,
            "cpu_total": node.cpu_total,
            "cpu_percent": node.cpu_percent,
            "ram_total_bytes": node.ram_total,
            "ram_used_bytes": node.ram_used,
            "ram_used_percent": ram_percent,
            "disk_total_bytes": node.disk_total,
            "disk_used_bytes": node.disk_used,
            "disk_used_percent": disk_percent,
            "agent_version": node.agent_version,
            "last_heartbeat": node.last_heartbeat.isoformat() if node.last_heartbeat else None,
        })
    return {"nodes": nodes, "count": len(nodes)}


#: Wann dieser Benutzer zuletzt Testmails ausgeloest hat. Im Prozessspeicher und
#: nicht in der Datenbank — dasselbe Muster wie `_notified_server_update_keys`
#: im Scheduler, aus demselben Grund: es geht um Anti-Spam innerhalb einer
#: Laufzeit, nicht um eine Tatsache, die einen Neustart ueberleben muss.
_TESTMAILS: dict[int, list[float]] = {}

#: Drei je Stunde. Die Grenze ist nicht gegen den Menschen gerichtet, sondern
#: gegen ein Modell in einer Schleife: "kommt sie an?" – "ich schicke nochmal" –
#: "und nochmal". Wer wirklich dreimal testen will, hat danach eine Antwort.
MAX_TESTMAILS_JE_STUNDE = 3


def _execute_send_test_email(db: Session, *, user: User) -> dict:
    """Schickt eine Testmail an die eigene Adresse des Fragenden.

    **Kein Empfaengerparameter.** Das ist die eigentliche Sicherheitsaussage
    dieses Werkzeugs: es gibt keinen Weg von einer Modellausgabe zu einer
    fremden Adresse, also kann MSM ueber die KI kein Mailversender fuer Dritte
    werden. Ein `to`-Argument haette genau das eroeffnet — und waere aus dem
    Chat heraus mit einem Satz auszuloesen gewesen.

    Zurueck kommt, was der Benutzer zum Nachsehen braucht: ob es rausging, an
    welches Postfach (maskiert) und **welche Art** von Versandweg benutzt wurde.
    Bewusst nicht der SMTP-Host: das ist Betreiberkonfiguration, die ein Kunde
    im Panel nur mit `panel.settings.read` zu sehen bekaeme. Fuer die Diagnose
    genuegt "es lief ueber SMTP" — wo die Einstellungen stehen, weiss der
    Betreiber selbst.

    Der Versand laeuft ueber `ai_mail` und damit ueber denselben Weg wie jede
    andere Mail der KI. Genau das macht die Pruefung aussagekraeftig: getestet
    wird nicht irgendein Mailversand, sondern **der**, den auch ein
    Aufgabenbericht nehmen wuerde.
    """
    import time

    from services import ai_mail
    from services.ai_redaction import maskiere_email
    from services.email_service import EmailService

    jetzt = time.monotonic()
    verlauf = [wann for wann in _TESTMAILS.get(user.id, []) if jetzt - wann < 3600]
    if len(verlauf) >= MAX_TESTMAILS_JE_STUNDE:
        _TESTMAILS[user.id] = verlauf
        return {
            "sent": False,
            "reason": "rate_limited",
            "detail": (
                f"In dieser Stunde wurden bereits {MAX_TESTMAILS_JE_STUNDE} "
                "Testmails verschickt. Sag dem Benutzer, er soll im Postfach "
                "und im Spam-Ordner nachsehen, statt es erneut zu versuchen."
            ),
        }

    # `empfaenger` prueft die drei Bedingungen, die auch fuer jede andere KI-Mail
    # gelten. Ein Test, der sie umginge, testete etwas anderes als den Ernstfall.
    adresse = ai_mail.empfaenger(db, user)
    if adresse is None:
        return {
            "sent": False,
            "reason": "not_deliverable",
            "detail": (
                "Es gibt keinen Weg zu diesem Benutzer: entweder sind seine "
                "E-Mail-Benachrichtigungen aus, oder der Betreiber hat im Panel "
                "keinen Versand eingerichtet, oder am Konto haengt keine "
                "Adresse. Nenne ihm diese drei Moeglichkeiten."
            ),
        }

    verlauf.append(jetzt)
    _TESTMAILS[user.id] = verlauf

    # Auch die Testmail schreibt die KI selbst — der Betreiber hat
    # ausdruecklich verlangt, dass hier nichts Vorgefertigtes mehr steht. Der
    # Verfassungsschritt liegt aber nicht mehr hier, sondern im Arbeiter am
    # Ausgangskorb: dort steht er innerhalb einer Schranke und ueberlebt einen
    # Neustart. Was hier entsteht, ist der Rueckfall — und bei genau dieser Mail
    # ist er wichtiger als bei den anderen beiden. Sie ist das Messgeraet fuer
    # den Versandweg und darf nicht ausgerechnet dann ausbleiben, wenn das
    # Modell klemmt.
    rahmen = EmailService.ai_rahmen_test(str(user.username))
    betreff, text, html = EmailService.ai_mail_rendern(
        rahmen, rueckfall=EmailService.AI_TESTMAIL_RUECKFALL
    )
    ai_mail.zustellen(
        name="ai-test-email",
        db=db,
        user_id=int(user.id),
        betreff=betreff,
        text=text,
        html=html,
        fakten=(
            "Anlass: der Benutzer hat im Chat um eine Testmail gebeten, "
            "um den eingerichteten Versandweg des Panels zu pruefen.\n"
            "Es ist nichts passiert, worueber zu berichten waere — die Mail "
            "beweist sich selbst, indem sie ankommt.\n"
            "Sag ihm in zwei bis drei Saetzen, dass der Versandweg damit "
            "nachgewiesen ist und dass auch die Berichte zu seinen Aufgaben "
            "und zu behobenen Stoerungen diesen Weg nehmen."
        ),
        rahmen=rahmen,
    )
    return {
        "sent": True,
        "recipient": maskiere_email(adresse),
        "transport": EmailService._get_provider(),
        "detail": (
            "Die Mail wurde dem Versand uebergeben. Ob sie ankommt, entscheidet "
            "der Weg dahinter — sag dem Benutzer, er soll jetzt nachsehen, auch "
            "im Spam-Ordner. Kommt nichts an, liegt es an der Einrichtung des "
            "Versands im Panel und nicht an dir."
        ),
    }


def _execute_global_read_tool(
    db: Session, *, user: User, tool_name: str, arguments: dict,
    herkunft: str = "panel", familie: str | None = None,
    prefetch_session_id: str | None = None,
) -> dict:
    """Werkzeuge ohne Serverbezug.

    `list_my_servers` ist die Einstiegsfrage jedes Gespraechs und deshalb an
    kein zusaetzliches Recht gebunden — es zeigt ausschliesslich Server, die der
    Benutzer ohnehin sieht, und ohne die Liste kann er den Assistenten gar nicht
    sinnvoll benutzen.

    Blueprintliste und Hostkapazitaet sind dagegen die Vorbereitung einer
    Servererstellung. Wer keine Server anlegen darf, hat auch keinen Grund, die
    Kapazitaetsplanung des Betreibers zu sehen.
    """
    if tool_name == "search_docs":
        return _execute_search_docs(arguments)

    if tool_name == "read_docs":
        return _execute_read_docs(arguments)

    if tool_name == "worker_start":
        from services import ai_worker_service

        # Die Herkunft wird **vererbt**, nicht gewaehlt: ein Auftrag aus der
        # App darf denselben Rechner sehen wie der Lauf, der ihn gestellt hat.
        # Ohne sie fiel der Worker auf "panel" und meldete dem Benutzer, er
        # koenne auf dessen Rechner nicht zugreifen (22.08.2026).
        #
        # Die Familie geht denselben Weg und beantwortet die zweite Hälfte
        # derselben Frage: die Herkunft sagt „aus der App", die Familie sagt
        # „aus **dieser** App". Nur mit ihr landet ein Desktop-Auftrag des
        # Workers bei dem Rechner, an dem der Mensch sitzt, statt bei dem, der
        # zuerst nach Arbeit fragt (`desktop_job_service.naechster`).
        return ai_worker_service.worker_start(
            db, user=user, arguments=arguments, herkunft=herkunft,
            familie=familie,
        )

    if tool_name == "worker_cancel":
        from services import ai_worker_service

        return ai_worker_service.worker_cancel(db, user=user, arguments=arguments)

    if tool_name == "worker_antwort":
        from services import ai_worker_service

        return ai_worker_service.worker_antwort(db, user=user, arguments=arguments)

    if tool_name == "wait_until":
        # Wird wie `ask_user` im Rundenlauf abgefangen, bevor ein Werkzeug
        # laeuft: der Lauf parkt (`waiting_wake`), dieser Dispatch sieht das
        # Werkzeug nie. Der Zweig steht trotzdem hier — als benannte Antwort
        # statt des Durchfall-raise, damit ein Aufruf ausserhalb eines
        # parkfaehigen Laufs eine Erklaerung bekommt und kein Raetsel.
        raise AiActionValidationError(
            "wait_until parkt den Lauf und wird im Rundenlauf behandelt — "
            "in diesem Lauf steht es nicht zur Verfügung"
        )

    if tool_name in (
        "desktop_dateien",
        "desktop_launch_app",
        "desktop_steuern",
        "desktop_system",
        "desktop_aufraeumen",
        "desktop_artifact",
    ):
        # Dieselbe Lage wie bei `wait_until`: die fuenf werden im Rundenlauf
        # abgefangen (`_desktop_behandeln`), werden zu einem Auftrag an den
        # Rechner des Benutzers, und der Lauf parkt. Dieser Dispatch sieht sie
        # nur, wenn die Bitte gar nicht von einem Rechner kam — dann sortiert
        # sie schon der Herkunfts-Spiegel aus, und wenn selbst der umgangen
        # waere, ist ein benannter Fehlschlag die einzig ehrliche Antwort. Ein
        # stiller Durchfall lieferte dem Modell ein "erledigt" fuer etwas, das
        # nie passiert ist.
        raise AiActionValidationError(
            "Werkzeuge für den Rechner des Benutzers laufen nur aus der "
            "Smart-System-App — in diesem Lauf stehen sie nicht zur Verfügung"
        )

    if tool_name == "list_tasks":
        from services import ai_task_service

        _require_no_arguments(tool_name, arguments)
        # Kein zusaetzliches Recht: die Liste zeigt ausschliesslich, was diesem
        # Benutzer gehoert. Wer keine Aufgaben anlegen darf, hat auch keine —
        # dann ist die Liste leer, und das ist die richtige Auskunft.
        return {"tasks": ai_task_service.auflisten(db, user=user)}

    if tool_name == "send_test_email":
        _require_no_arguments(tool_name, arguments)
        return _execute_send_test_email(db, user=user)

    if tool_name == "read_hoster_setup":
        from services import ai_hoster_tools

        _require_no_arguments(tool_name, arguments)
        return ai_hoster_tools.setup_uebersicht(db, user=user)

    if tool_name == "read_hoster_integration_guide":
        from services import ai_hoster_tools

        if set(arguments) != {"integration_id"}:
            raise AiActionValidationError("Hoster-Werkzeug hat ungueltige Argumente")
        roh = arguments.get("integration_id")
        if roh is None:
            raise AiActionValidationError(
                "integration_id fehlt — hol sie aus read_hoster_setup"
            )
        kennung = _positive_int(roh, name="integration_id", default=0, minimum=1)
        return ai_hoster_tools.integration_guide(db, user=user, integration_id=kennung)

    if tool_name == "remember":
        return _execute_remember(db, user=user, arguments=arguments)

    if tool_name == "set_agent_name":
        return _execute_set_agent_name(db, user=user, arguments=arguments)

    if tool_name == "web_search":
        return _execute_web_search(
            db, user=user, arguments=arguments, prefetch_session_id=prefetch_session_id
        )

    if tool_name == "analyze_region":
        return _execute_analyze_region(
            db, user=user, arguments=arguments, prefetch_session_id=prefetch_session_id,
        )

    if tool_name == "control_region_camera":
        return _execute_control_region_camera(db, user=user, arguments=arguments)

    if tool_name == "read_skill":
        return _execute_read_skill(db, user=user, arguments=arguments)

    if tool_name == "learn_skill":
        return _execute_learn_skill(db, user=user, arguments=arguments)

    if tool_name == "search_memory":
        return _execute_search_memory(db, user=user, arguments=arguments)

    if tool_name == "forget_memory":
        return _execute_forget_memory(db, user=user, arguments=arguments)

    if tool_name == "forget_skill":
        return _execute_forget_skill(db, user=user, arguments=arguments)

    if tool_name == "email_search":
        from services.mailbox_service import MailboxService

        query = str(arguments.get("query", "")) if arguments.get("query") else None
        sender = str(arguments.get("sender", "")) if arguments.get("sender") else None
        limit = int(arguments.get("limit", 10))
        mailbox_id = int(arguments["mailbox_id"]) if arguments.get("mailbox_id") else None
        messages = MailboxService.search_messages(
            db, user=user, mailbox_id=mailbox_id, query=query, sender=sender, limit=limit
        )
        return {"messages": messages, "count": len(messages)}

    if tool_name == "email_read":
        from services.mailbox_service import MailboxService

        if "message_id" not in arguments:
            raise AiActionValidationError("message_id ist erforderlich")
        message_id = str(arguments["message_id"]).strip()
        mailbox_id = int(arguments["mailbox_id"]) if arguments.get("mailbox_id") else None
        msg = MailboxService.read_message(
            db, user=user, message_id=message_id, mailbox_id=mailbox_id
        )
        if not msg:
            return {"error": "Nachricht nicht gefunden oder Postfach nicht erreichbar"}
        return msg

    if tool_name == "calendar_read":
        from services.calendar_service import CalendarService

        start_date = str(arguments.get("start_date", "")) if arguments.get("start_date") else None
        end_date = str(arguments.get("end_date", "")) if arguments.get("end_date") else None
        calendar_id = int(arguments["calendar_id"]) if arguments.get("calendar_id") else None
        events = CalendarService.get_events(
            db, user=user, calendar_id=calendar_id, start_date=start_date, end_date=end_date
        )
        return {"events": events, "count": len(events)}

    if tool_name == "read_blueprint":
        # Ein Blueprint ist eine Vorlage, kein Betriebsgeheimnis: wer Server
        # anlegen **oder** Blueprints pflegen darf, darf ihn lesen. Ohne den
        # zweiten Fall koennte jemand mit `blueprints.manage` seine eigene
        # Vorlage nicht ansehen.
        if not (
            permission_service.has_global_permission(db, user, "servers.create")
            or permission_service.has_global_permission(db, user, "blueprints.manage")
        ):
            raise AiActionValidationError("Blueprint-Einsicht ist nicht erlaubt")
        if set(arguments) != {"blueprint_id"}:
            raise AiActionValidationError("Blueprint-Tool hat ungueltige Argumente")
        from services import blueprint_service

        try:
            return blueprint_service.blueprint_view(str(arguments["blueprint_id"]))
        except HTTPException as exc:
            raise AiActionValidationError(str(exc.detail)) from exc

    _require_no_arguments(tool_name, arguments)

    if tool_name == "list_my_servers":
        servers = _visible_servers(db, user)
        return {
            "servers": [
                {
                    "server_id": server.id,
                    # Der Name ist frei vom Benutzer gesetzt und wird redigiert.
                    "name": redact_sensitive_text(str(server.name or ""))[:128],
                    "game_type": server.game_type,
                    "status": server.status,
                }
                for server in servers
            ],
            "count": len(servers),
            "truncated": len(servers) >= MAX_LISTED_SERVERS,
        }

    if tool_name == "read_node_health":
        # Bewusst `nodes.read` statt `servers.create`: den Zustand der Hosts zu
        # sehen ist eine Aufgabe des Betriebs, nicht der Serverplanung. Ein
        # Support-Mitarbeiter soll nachsehen koennen, ohne Server anlegen zu
        # duerfen.
        if not permission_service.has_global_permission(db, user, "nodes.read"):
            raise AiActionValidationError("Node-Einsicht ist nicht erlaubt")
        return _node_health(db)

    if tool_name == "list_blueprints":
        if not permission_service.has_global_permission(db, user, "servers.create"):
            raise AiActionValidationError("Serverplanung ist nicht erlaubt")
        from games import list_game_info

        entries = []
        for info in list_game_info()[:MAX_LISTED_BLUEPRINTS]:
            entries.append({
                "game_type": info.get("id"),
                "name": info.get("name"),
                "platform": info.get("platform"),
                "mod_support": bool(info.get("mod_support")),
                "ports": [port.get("name") for port in (info.get("ports") or [])],
            })
        return {"blueprints": entries, "count": len(entries)}

    if tool_name == "read_node_capacity":
        if not permission_service.has_global_permission(db, user, "servers.create"):
            raise AiActionValidationError("Serverplanung ist nicht erlaubt")
        from models import Node
        from services.node_capacity import (
            allocatable_ram_mb, sum_allocated_ram_mb, sum_running_ram_mb,
        )

        nodes = db.query(Node).order_by(Node.id).limit(MAX_LISTED_NODES).all()
        entries = []
        for node in nodes:
            allocated = sum_allocated_ram_mb(db, node.id)
            entries.append({
                # Bewusst ohne Hostname und IP: das Modell soll Kapazitaet
                # vergleichen koennen, nicht die Netzstruktur des Betreibers
                # kennen. Die Auswahl trifft ohnehin MSM.
                "node_id": node.id,
                "status": node.status,
                "is_local": bool(node.is_local),
                "cpu_total": node.cpu_total,
                # Gebucht ueber **alle** Server, auch gestoppte. Das ist die
                # Ueberbuchungsgrenze, nicht der Verbrauch.
                "ram_allocated_mb": allocated,
                # Gebucht von den Servern, die gerade wirklich laufen. Die
                # Unterscheidung ist der Kern einer wiederkehrenden Fehlauskunft:
                # vier gestoppte Server zu je 8 GB buchen 32 GB und belegen null.
                "ram_allocated_running_mb": sum_running_ram_mb(db, node.id),
                "ram_allocatable_mb": allocatable_ram_mb(node, allocated),
                # Was die Node selbst meldet — die einzige echte Messung hier.
                "ram_total_mb": int(node.ram_total / 1024 / 1024) if node.ram_total else None,
                "ram_used_mb": int(node.ram_used / 1024 / 1024) if node.ram_used else None,
            })
        return {"nodes": entries}

    # **Der Durchfall war die gefaehrlichste Zeile der Datei.** Bis hierher war
    # die Kapazitaetsabfrage der namenlose Rumpf am Ende der Kette: wer keinen
    # eigenen Zweig hatte, bekam ihn. Ein Werkzeug, das in der Tabelle und im
    # Katalog steht, aber beim Verdrahten vergessen wurde, lieferte dem Modell
    # damit RAM-Zahlen unter seinem eigenen Namen zurueck — eine falsche
    # Auskunft, die wie eine richtige aussieht, und der einzige Ort im ganzen
    # Werkzeugpfad, an dem das ohne Fehler passieren konnte.
    raise AiActionValidationError(f"Kein Handler für Werkzeug: {tool_name}")


def _execute_server_context_tool(
    db: Session, *, user: User, server: Server, tool_name: str, arguments: dict
) -> dict | None:
    """Die Kontext-Tools aus Zielpunkt 3.3. Jedes prueft sein eigenes Recht."""
    if tool_name == "read_server_ports":
        _require_no_arguments(tool_name, arguments)
        from models import ServerPort

        rows = db.query(ServerPort).filter(ServerPort.server_id == server.id).order_by(
            ServerPort.role
        ).all()
        return {
            "server_id": server.id,
            "ports": [
                {"role": row.role, "port": row.port, "protocol": row.protocol} for row in rows
            ],
        }

    if tool_name in {"read_server_network", "check_server_reachability"}:
        _require_no_arguments(tool_name, arguments)
        from services import server_network_diagnostics

        if tool_name == "check_server_reachability":
            return server_network_diagnostics.check_reachability(db, server)
        # Host-Adressen und Firewall-Regeln sind die Netzstruktur des
        # Betreibers, nicht die des Servers. Wer sie nicht aendern darf, muss
        # sie auch nicht sehen — die Ports des eigenen Servers schon.
        return server_network_diagnostics.describe_network(
            db, server,
            include_host_details=permission_service.has_server_permission(
                db, user, server.id, "server.network.manage"
            ),
        )

    if tool_name in {"read_server_mods", "read_mod_updates", "search_workshop_mods"}:
        if not permission_service.has_server_permission(db, user, server.id, "server.mods.read"):
            raise AiActionValidationError("Mod-Lesezugriff ist nicht erlaubt")
        return _execute_mod_tool(db, server=server, tool_name=tool_name, arguments=arguments)

    if tool_name == "read_server_backups":
        _require_no_arguments(tool_name, arguments)
        if not permission_service.has_server_permission(db, user, server.id, "server.backups.read"):
            raise AiActionValidationError("Backup-Lesezugriff ist nicht erlaubt")
        from models import Backup

        rows = (
            db.query(Backup)
            .filter(Backup.server_id == server.id)
            .order_by(Backup.created_at.desc())
            .limit(MAX_LISTED_BACKUPS)
            .all()
        )
        return {
            "server_id": server.id,
            "backups": [
                {
                    "id": row.id,
                    # Der Name ist frei vom Benutzer gesetzt und wird redigiert.
                    "name": redact_sensitive_text(str(row.name or ""))[:128],
                    "size_mb": row.size_mb,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ],
        }

    if tool_name == "read_guardian_incidents":
        _require_no_arguments(tool_name, arguments)
        from services.ai_guardian_settings import is_guardian_ai_enabled

        if not is_guardian_ai_enabled():
            raise AiActionValidationError("Die Guardian-KI-Integration ist deaktiviert.")

        from models import Incident

        rows = (
            db.query(Incident)
            .filter(Incident.server_id == server.id)
            .order_by(Incident.created_at.desc())
            .limit(MAX_LISTED_INCIDENTS)
            .all()
        )
        return {
            "server_id": server.id,
            "incidents": [
                {
                    # Ohne Kennung konnte das Modell einen Vorfall nicht
                    # benennen, auf den es sich bezieht — weder in seiner
                    # Antwort noch in der Begruendung eines Vorschlags. Bei
                    # mehreren offenen Vorfaellen desselben Servers war damit
                    # nicht unterscheidbar, welchen es meint.
                    "id": row.id,
                    "type": row.type,
                    "status": row.status,
                    "title": redact_sensitive_text(str(row.title))[:128],
                    # Guardian-Beschreibungen enthalten Ausschnitte aus Logs.
                    "description": redact_sensitive_text(str(row.description))[:512],
                    "occurrences": row.occurrences,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
                    # Was die Guardian-Engine selbst schon versucht hat. Ohne
                    # das faengt die KI bei jedem Vorfall mit einem Neustart an —
                    # dem Schritt, den der Agent nachweislich schon dreimal
                    # gemacht hat, bevor er aufgab.
                    "attempts": _vorfall_versuche(row.attempts),
                }
                for row in rows
            ],
        }

    if tool_name == "read_ai_action_history":
        _require_no_arguments(tool_name, arguments)
        rows = (
            db.query(AiActionProposal)
            .filter(
                AiActionProposal.server_id == server.id,
                # Nur die eigenen: das Werkzeug haengt an `server.view`, und
                # ein Gast auf einem geteilten Server erfuehre sonst, dass ein
                # anderer Benutzer hier `propose_server_delete` versucht hat
                # und woran es scheiterte. Der REST-Weg daneben
                # (`GET /api/ai/conversation/actions`) filtert genauso.
                AiActionProposal.user_id == user.id,
            )
            .order_by(AiActionProposal.created_at.desc())
            .limit(MAX_LISTED_ACTIONS)
            .all()
        )
        return {
            "server_id": server.id,
            "actions": [
                {
                    # Kein Payload und kein Diff: die Historie soll zeigen, was
                    # passiert ist, nicht frueheren Configinhalt erneut ausgeben.
                    "tool": row.tool_name,
                    "status": row.status,
                    "autonomous": bool(row.autonomous),
                    "error_code": row.error_code,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ],
        }
    return None


def _execute_mod_tool(db: Session, *, server: Server, tool_name: str, arguments: dict) -> dict:
    from games import get_plugin
    from models import Mod
    from services import mod_update_service

    if tool_name == "read_server_mods":
        _require_no_arguments(tool_name, arguments)
        rows = (
            db.query(Mod)
            .filter(Mod.server_id == server.id)
            .order_by(Mod.load_order, Mod.id)
            .limit(MAX_LISTED_MODS)
            .all()
        )
        return {
            "server_id": server.id,
            "mods": [
                {
                    "workshop_id": row.workshop_id,
                    "name": redact_sensitive_text(str(row.name or ""))[:128],
                    "enabled": bool(row.enabled),
                    "install_status": row.install_status,
                    "install_error": redact_sensitive_text(str(row.install_error or ""))[:256] if row.install_error else None,
                    "update_status": row.update_status,
                    "update_reason": row.update_reason,
                    "load_order": row.load_order,
                }
                for row in rows
            ],
        }

    plugin = get_plugin(server.game_type)
    if plugin is None or not getattr(plugin, "supports_mods", False):
        return {
            "server_id": server.id,
            "available": False,
            "reason": "mods_not_supported",
        }

    if tool_name == "read_mod_updates":
        _require_no_arguments(tool_name, arguments)
        updates = mod_update_service.refresh_update_availability(db, server, plugin)
        return {
            "server_id": server.id,
            "available": True,
            "updates": [
                {
                    "workshop_id": str(item.get("workshop_id") or ""),
                    "action": str(item.get("action") or ""),
                    "reason": str(item.get("reason") or "")[:128],
                }
                for item in updates[:MAX_LISTED_MODS]
            ],
        }

    if tool_name != "search_workshop_mods":
        raise AiActionValidationError(f"Kein Handler für Werkzeug: {tool_name}")
    if set(arguments) - {"query", "page"} or not isinstance(arguments.get("query"), str):
        raise AiActionValidationError("Workshop-Suche hat ungültige Argumente")
    page = arguments.get("page", 1)
    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 50:
        raise AiActionValidationError("Ungültige Seitenzahl")
    mod_support = plugin.get_mod_support() or {}

    # Die Anfrage geht an einen fremden Dienst (Steam oder CurseForge) und wird
    # dort protokolliert — dieselbe Lage wie bei `web_search`, also dieselbe
    # Schwärzung, eine Richtung früher als der Choke Point auf dem Rückweg.
    # Der Suchbegriff ist reine Modellausgabe, und das Modell hat vorher
    # Konfigurationsdateien und Logs gelesen: eine Zuweisung wie
    # `ServerAdminPassword=…` kann es wörtlich übernehmen. Die Schwärzung ist
    # wertbezogen, ein Einstellungs- oder Modname als Wort überlebt sie.
    #
    # Gekürzt wird auf die Länge, die das Schema verspricht. Ein Schema ist eine
    # Bitte an das Modell und keine Schranke; was hier durchkommt, geht als
    # URL-Parameter hinaus.
    sichere_anfrage = redact_sensitive_text(
        arguments["query"].strip()
    )[:MAX_SEARCH_QUERY_CHARS]

    # 1. CurseForge Provider
    if mod_support.get("provider") == "curseforge" or mod_support.get("curseforge_game_id"):
        cf_game_id = str(mod_support.get("curseforge_game_id") or "")
        cf_class_id = str(mod_support.get("curseforge_class_id") or "") or None
        if not cf_game_id:
            return {"server_id": server.id, "available": False, "reason": "curseforge_game_id_missing"}
        try:
            import asyncio
            from services.curseforge_service import get_curseforge_service, CurseForgeApiUnavailable

            async def _do_cf_search():
                svc = await get_curseforge_service()
                return await svc.search_mods(
                    game_id=cf_game_id,
                    class_id=cf_class_id,
                    query=sichere_anfrage,
                    page=page,
                    per_page=20,
                )

            # Ohne Weiche auf eine laufende Ereignisschleife: die Lesewerkzeuge
            # laufen ausschließlich über `_werkzeug_ausfuehren` und damit "in
            # eigener Sitzung und eigenem Thread", wo es nie eine gibt. Hier
            # stand ein `ThreadPoolExecutor`, der `asyncio.run` in einem zweiten
            # Thread startete — toter Verteidigungscode, der obendrein den
            # Eindruck machte, der Handler sei auf der Schleife aufrufbar. Wäre
            # er das, blockierte er sie für die volle Dauer des HTTP-Aufrufs;
            # nebenläufig wird davon nichts, der Executor verdeckt nur den
            # Konstruktionsfehler. `asyncio.run` sagt in dem Fall selbst
            # deutlich, was los ist.
            cf_mods = asyncio.run(_do_cf_search())

            results = [
                {
                    "workshop_id": m.publishedfileid,
                    "title": m.title,
                    "description": redact_sensitive_text(str(m.description or ""))[:256] if m.description else None,
                    "preview_url": m.preview_url,
                    "creator": m.creator,
                    "subscriptions": m.subscriptions,
                    "updated": m.updated.isoformat() if m.updated else None,
                    "direct_url": m.direct_url,
                    "provider": "curseforge",
                }
                for m in cf_mods
            ]
            return {"server_id": server.id, "available": True, "provider": "curseforge", "results": results}
        except CurseForgeApiUnavailable as exc:
            return {"server_id": server.id, "available": False, "reason": exc.code}
        except Exception as exc:
            # Ein stabiler Code wie in jedem anderen Zweig dieses Handlers.
            # Vorher stand hier `str(exc)`: beliebiger Text aus einer beliebigen
            # Bibliothek, der als Grund an das Modell ging und dort mit
            # `curseforge_game_id_missing` in einer Reihe stand. Die Einzelheit
            # gehört ins Log — und dort nur der Ausnahmetyp, wie es
            # `curseforge_service` schon hält: eine Fehlermeldung kann den
            # API-Schlüssel tragen.
            logger.warning(
                "CurseForge-Suche im Werkzeug fehlgeschlagen: %s", type(exc).__name__
            )
            return {"server_id": server.id, "available": False, "reason": "curseforge_fehler"}

    # 2. Steam Workshop Provider
    appid = mod_support.get("workshop_id")
    if not appid:
        return {"server_id": server.id, "available": False, "reason": "workshop_id_missing"}
    try:
        results = mod_update_service.search_workshop(
            appid=str(appid),
            query=sichere_anfrage,
            page=page,
            required_tags=mod_support.get("required_tags") or None,
        )
    except mod_update_service.ModSearchUnavailable as exc:
        # Ehrlich melden statt eine leere Trefferliste liefern: "nichts
        # gefunden" waere hier eine falsche Aussage ueber den Workshop.
        return {"server_id": server.id, "available": False, "reason": exc.code}
    return {"server_id": server.id, "available": True, "results": results}


def is_binary_text(content: str) -> bool:
    """Erkennt an, was `read_text` aus einer Nicht-Textdatei gemacht hat.

    Der Dateizugriff dekodiert mit ``errors="replace"``: eine Binaerdatei kommt
    als Folge von Ersatzzeichen (U+FFFD) zurueck, ein Nullbyte als solches. Beides
    kann in einer echten Textdatei nicht in Menge auftreten.

    Die Schwelle ist bewusst grosszuegig — eine einzelne kaputte Umlautstelle in
    einer sonst brauchbaren Konfigurationsdatei soll nicht dazu fuehren, dass die
    KI sie fuer binaer haelt und nicht mehr anfasst.
    """
    if "\x00" in content:
        return True
    if not content:
        return False
    return content.count("�") / len(content) > 0.02


def _config_path(value: object) -> str:
    """Prueft einen Pfad relativ zum Serververzeichnis.

    **Keine Endungsliste mehr.** Frueher stand hier ein Filter auf neun
    Erweiterungen, und alles andere war fuer die KI unsichtbar — Dateien **ohne**
    Endung (`Dockerfile`, `.env`, `whitelist`, `banlist`), `.xml` (Ark, Unreal),
    `.lua` (Garry's Mod, DayZ), `.sh`, `.md`. Ein Mensch bearbeitet die im
    Dateimanager selbstverstaendlich; die Vorgabe des Betreibers ist, dass die
    KI denselben Umfang hat und nicht "an einer anderen Stelle etwas anderes
    einstellt".

    Die Endung war ohnehin nie die Sicherheitsgrenze. Die liegt in `safe_path`,
    das ueber `resolve()` und `relative_to()` auch Symlinks nach aussen abfaengt,
    und in der Rechtepruefung. Was hier bleibt, ist die Formpruefung: relativ,
    kein Ausbruch, keine Backslashes, begrenzte Laenge.
    """
    if not isinstance(value, str) or not value or len(value) > 256 or "\\" in value:
        raise AiActionValidationError("Ungueltiger Dateipfad")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise AiActionValidationError("Dateipfad ist nicht erlaubt")
    # Kein Namensteil darf mit einem Bindestrich beginnen.
    #
    # Nicht wegen des Dateisystems — dort ist das erlaubt —, sondern wegen der
    # Werkzeuge, die diese Namen spaeter als Argumente weiterreichen. `tar`
    # deutet einen Operanden, der mit `-` beginnt, als Option; `games/updater.py`
    # sichert seine Aufrufe deshalb zusaetzlich mit `--` ab. Diese Pruefung ist
    # die zweite Haelfte davon: eine Datei, die niemand von Hand so genannt
    # haette, entsteht hier gar nicht erst.
    #
    # Ein Mensch verliert dadurch nichts Sinnvolles. Die KI legt Dateien
    # ungefragt an; ein Name wie `--use-compress-program=...` ist keine
    # Konfiguration, sondern ein Versuch.
    if any(teil.startswith("-") for teil in path.parts):
        raise AiActionValidationError(
            "Dateiname darf nicht mit einem Bindestrich beginnen"
        )
    return path.as_posix()


def execute_read_tool(
    db: Session,
    *,
    user: User,
    tool_name: str,
    arguments: dict,
    herkunft: str = "panel",
    familie: str | None = None,
    prefetch_session_id: str | None = None,
) -> dict:
    """Fuehrt ein Lesewerkzeug im Namen des Benutzers aus.

    Die Unterhaltung wird bewusst nicht mehr uebergeben: sie traegt keinen
    Kontext mehr, der die Ausfuehrung beeinflusst. Alles, was ein Werkzeug
    braucht, steht in seinen Argumenten und wird gegen die Rechte von ``user``
    geprueft.

    ``herkunft`` und ``familie`` sind die einzigen Ausnahmen davon, und sie
    stehen ausdrücklich **nicht** in den Argumenten: aus welcher Welt der
    Aufruf kam und von welchem Gerät, sind Tatsachen des Laufs. Gebraucht
    werden beide von genau einem Werkzeug — `worker_start` gibt sie an den
    Auftrag weiter, den es anlegt. Die Herkunft öffnet ihm die
    Desktop-Werkzeuge, die Familie sagt, an welchen Rechner er sich damit
    wendet; ohne sie holt seinen Auftrag der, der zuerst fragt.
    """
    if tool_name not in READ_TOOLS:
        raise AiActionValidationError("Read-Tool ist in diesem Kontext nicht erlaubt")

    # Ein Cache-Hit kommt nur aus derselben Sprachsitzung. Die kleinen,
    # werkzeugspezifischen Vorprüfungen sind die zweite Schranke nach dem
    # Prefetch und verhindern, dass ein inzwischen entzogener Zugriff ein altes
    # Ergebnis erhält.
    from services.ai_intent_classifier import prefetch_cache
    if prefetch_session_id and tool_name == "analyze_region":
        if not permission_service.has_global_permission(db, user, "ai.satellite.use"):
            raise AiActionValidationError("Satelliten- und Regionsanalyse ist für diesen Benutzer nicht freigegeben")
    elif prefetch_session_id and tool_name == "control_region_camera":
        if not permission_service.has_global_permission(db, user, "ai.satellite.use"):
            raise AiActionValidationError("Kartensteuerung ist für diesen Benutzer nicht freigegeben")
    elif prefetch_session_id and tool_name == "web_search":
        if not permission_service.has_global_permission(db, user, "ai.web_search.use"):
            raise AiActionValidationError("Websuche ist fuer diesen Benutzer nicht freigegeben")
    elif prefetch_session_id and tool_name == "calendar_read":
        if not permission_service.has_global_permission(db, user, "ai.calendar.use"):
            raise AiActionValidationError("Kalender ist fuer diesen Benutzer nicht freigegeben")
    elif prefetch_session_id and tool_name == "search_memory":
        if not permission_service.has_global_permission(db, user, "ai.memory.use"):
            raise AiActionValidationError("Memory ist fuer diesen Benutzer nicht freigegeben")
    elif prefetch_session_id and tool_name == "read_server_status":
        _resolve_server(db, user, arguments)
    hit, cached_result = prefetch_cache.get(
        session_id=prefetch_session_id, user_id=user.id, tool_name=tool_name, arguments=arguments,
    )
    if hit and cached_result is not None:
        logger.info("Spekulativer Prefetch-Cache HIT für tool=%s user=%s", tool_name, user.id)
        return cached_result

    if tool_name in GLOBAL_READ_TOOLS:
        return _execute_global_read_tool(
            db, user=user, tool_name=tool_name, arguments=arguments,
            herkunft=herkunft, familie=familie, prefetch_session_id=prefetch_session_id,
        )
    server, arguments = _resolve_server(db, user, arguments)
    context = _execute_server_context_tool(
        db, user=user, server=server, tool_name=tool_name, arguments=arguments
    )
    if context is not None:
        return context
    if tool_name == "read_server_status":
        if arguments:
            raise AiActionValidationError("Status-Tool akzeptiert keine Argumente")
        return {
            "server_id": server.id,
            "game": server.game_type,
            "status": server.status,
            "cpu_limit_percent": server.cpu_limit_percent,
            "ram_limit_mb": server.ram_limit_mb,
            "disk_limit_gb": server.disk_limit_gb,
        }
    if tool_name == "read_server_capacity":
        if arguments:
            raise AiActionValidationError("Kapazitaets-Tool akzeptiert keine Argumente")
        node = server.node
        if node is None:
            return {"server_id": server.id, "node_status": "unassigned"}
        # Die Zahlen der Node sind nicht die Zahlen dieses Servers.
        # `sum_allocated_ram_mb` filtert in `services/node_capacity.py` allein
        # auf `node_id` — das ist die Summe der Buchungen **aller** Kunden auf
        # diesem Host —, und cpu_total/ram_total/disk_* beschreiben die
        # Maschine des Betreibers. `_resolve_server` prueft nur `server.view`;
        # damit gab dieses Werkzeug jedem Hosting-Kunden die Ueberbuchungslage
        # seines Anbieters heraus, waehrend `read_node_capacity` dafuer
        # `servers.create` und `read_node_health` `nodes.read` verlangt.
        #
        # Die Grenze ist dieselbe wie bei `describe_network`: wer die Grenzen
        # dieses Servers aendern darf, muss sehen, wieviel Platz dafuer da ist.
        # Alle anderen bekommen den Status der Node und sonst nichts —
        # ausdruecklich als `withheld`, damit das Modell die Luecke kennt und
        # nicht ueber die Auslastung raet.
        if not permission_service.has_server_permission(
            db, user, server.id, "server.resources.manage"
        ):
            return {
                "server_id": server.id,
                "node_status": node.status,
                "node_details": "withheld",
            }
        from services.node_capacity import (
            allocatable_ram_mb, sum_allocated_ram_mb, sum_running_ram_mb,
        )

        allocated_ram_mb = sum_allocated_ram_mb(db, node.id)
        return {
            "server_id": server.id,
            "node_status": node.status,
            "cpu_total": node.cpu_total,
            "cpu_percent": node.cpu_percent,
            "ram_total_bytes": node.ram_total,
            "ram_used_bytes": node.ram_used,
            "ram_allocated_mb": allocated_ram_mb,
            # Gestoppte Server buchen, belegen aber nichts. Ohne diese Zeile
            # meldet das Modell "kein RAM frei", waehrend die Node leer laeuft.
            "ram_allocated_running_mb": sum_running_ram_mb(db, node.id),
            "ram_allocatable_mb": allocatable_ram_mb(node, allocated_ram_mb),
            "disk_total_bytes": node.disk_total,
            "disk_used_bytes": node.disk_used,
        }
    if tool_name == "read_server_logs":
        if set(arguments) - {"lines"}:
            raise AiActionValidationError("Log-Tool hat ungueltige Argumente")
        # Dasselbe Recht, das der Panel-Endpunkt verlangt (routers/servers.py:1172
        # und die Konsolen-WebSocket). `_resolve_server` prueft nur `server.view`
        # — damit war die Konsole ueber den KI-Pfad fuer jeden lesbar, der den
        # Server ueberhaupt sehen darf. Containerlogs sind kein Nebenprodukt:
        # dort stehen Spielerchat, Join-Zeilen mit IP-Adressen, Admin-Kommandos
        # und Stacktraces, und `redact_sensitive_text` entfernt davon nichts.
        if not permission_service.has_server_permission(
            db, user, server.id, "server.console.read"
        ):
            raise AiActionValidationError("Konsolen-Lesezugriff ist nicht erlaubt")
        lines = arguments.get("lines", 100)
        if not isinstance(lines, int) or isinstance(lines, bool) or not 1 <= lines <= 200:
            raise AiActionValidationError("Ungueltige Log-Zeilenanzahl")
        from services import docker_service
        from services.node_service import is_node_offline

        # docker_service.logs() liefert bei einem nicht erreichbaren Node
        # denselben leeren String wie bei einem Container ohne Ausgabe. Ohne
        # diese Unterscheidung wuerde das Modell "keine Fehler gefunden"
        # antworten, obwohl es in Wahrheit gar nichts gelesen hat.
        if is_node_offline(server.node):
            return {
                "server_id": server.id,
                "lines_requested": lines,
                "content": "",
                "available": False,
                "reason": "node_unreachable",
            }
        if not server.container_name:
            return {
                "server_id": server.id,
                "lines_requested": lines,
                "content": "",
                "available": False,
                "reason": "container_missing",
            }
        content = docker_service.logs(server.container_name, lines=lines, node=server.node)
        redacted = redact_sensitive_text(content)
        return {
            "server_id": server.id,
            "lines_requested": lines,
            "content": redacted[-MAX_LOG_CHARS:],
            "available": True,
            "truncated": len(redacted) > MAX_LOG_CHARS,
            "redacted": redacted != content,
        }
    if tool_name == "list_server_files":
        if set(arguments) - {"path"}:
            raise AiActionValidationError("Datei-Auflistung hat ungueltige Argumente")
        if not permission_service.has_server_permission(
            db, user, server.id, "server.files.read"
        ):
            raise AiActionValidationError("Datei-Lesezugriff ist nicht erlaubt")
        pfad = arguments.get("path") or ""
        # Die Wurzel ist der leere Pfad; alles andere geht durch dieselbe
        # Formpruefung wie beim Lesen.
        geprueft = _config_path(pfad) if pfad else ""
        from services.server_file_access_service import (
            MAX_LISTED_ENTRIES,
            list_server_directory,
        )

        return {
            "server_id": server.id,
            **list_server_directory(
                db,
                server_id=server.id,
                relative_path=geprueft,
                limit=MAX_LISTED_ENTRIES,
            ),
        }

    if tool_name == "search_server_files":
        return _execute_file_search(db, user=user, server=server, arguments=arguments)

    # Ab hier folgt `read_config` — und zwar bisher **ohne** dass sein Name
    # geprueft wurde. Jedes serverbezogene Lesewerkzeug, das keinen eigenen
    # Zweig hat, landete hier und wurde als Dateizugriff ausgefuehrt.
    #
    # Solange die Argumentpruefung darunter zuschlug, fiel das als
    # "Datei-Lesewerkzeug hat ungueltige Argumente" auf — eine Fehlermeldung,
    # die den falschen Grund nennt. Ein kuenftiges Werkzeug mit einem
    # `path`-Argument haette sie aber passiert und dem Modell den Inhalt einer
    # Datei unter dem Namen des anderen Werkzeugs geliefert: richtiger Name,
    # falsche Daten. Genau dieses Muster hat in diesem Projekt schon dreimal
    # zugeschlagen, und es faellt nie zur Laufzeit auf.
    #
    # `_werkzeug_bekannt` faengt beim Definieren ein Werkzeug ohne
    # Registry-Zeile. Diese Zeile hier faengt eines ohne Handler.
    if tool_name != "read_config":
        raise AiActionValidationError(f"Kein Handler für Werkzeug: {tool_name}")

    if set(arguments) - {"path", "offset", "limit"} or "path" not in arguments:
        raise AiActionValidationError("Datei-Lesewerkzeug hat ungueltige Argumente")
    if not permission_service.has_server_permission(
        db, user, server.id, "server.files.read"
    ):
        raise AiActionValidationError("Datei-Lesezugriff ist nicht erlaubt")
    path = _config_path(arguments["path"])
    offset = _positive_int(arguments.get("offset"), name="offset", default=1, minimum=1)
    limit = _positive_int(
        arguments.get("limit"),
        name="limit",
        default=MAX_READ_CONFIG_LINES,
        minimum=1,
        maximum=MAX_READ_CONFIG_LINES,
    )
    result = read_server_text(db, server_id=server.id, relative_path=path)
    content = str(result["content"])
    # Seit die Endungsliste weg ist, kann hier auch eine Binaerdatei landen —
    # ein Mod-Jar, ein Weltdatei-Chunk. `read_text` dekodiert mit
    # `errors="replace"`, aus einer solchen Datei wird also Ersatzzeichen-Salat.
    # Wuerde das Modell ihn zurueckschreiben, waere die Datei zerstoert.
    binaer = is_binary_text(content)
    redacted = redact_sensitive_text(content)
    was_redacted = redacted != content

    zeilen = redacted.splitlines(keepends=True)
    fenster = zeilen[offset - 1 : offset - 1 + limit]
    sicht = "".join(fenster)
    zeichen_gekuerzt = len(sicht) > MAX_READ_CONFIG_CHARS
    sicht = sicht[:MAX_READ_CONFIG_CHARS]
    # "Vollstaendig" heisst: dieses Fenster **ist** die Datei. Nur dann hat das
    # Modell den ganzen Stand gesehen.
    vollstaendig = offset == 1 and len(fenster) == len(zeilen) and not zeichen_gekuerzt

    # Zwei Fragen, die frueher eine waren — und dass sie eine waren, war der
    # Grund, warum eine grosse Spielkonfiguration fuer die KI nur lesbar war:
    #
    # `editable`  — darf die Datei **ganz** ersetzt werden? Nur wenn das Modell
    #               sie ganz und unveraendert gesehen hat. Sonst wuerde der
    #               Vollersatz alles hinter dem Fenster loeschen bzw. echte
    #               Zugangsdaten durch den Platzhalter ersetzen.
    # `patchable` — darf **eine Stelle** darin ersetzt werden? Dafuer genuegt,
    #               dass es Text ist. Wer eine Stelle austauscht, laesst den
    #               Rest Byte fuer Byte stehen; was er nie gesehen hat, kann er
    #               auch nicht zerstoeren.
    #
    # Die Revision ist damit wieder das, was sie ist: die Kennung *dieses
    # Dateistands*. Sie zurueckzuhalten war frueher die Absicherung der
    # Vollersetzung; die steht jetzt serverseitig in `ai_proposal_service` und
    # haengt nicht mehr daran, was das Modell gesehen zu haben behauptet.
    editable = vollstaendig and not was_redacted and not binaer
    patchable = not binaer
    grund = (
        "Diese Datei ist keine Textdatei. Automatisch aendern wuerde sie "
        "zerstoeren. Bitte nicht anfassen."
        if binaer
        else "Diese Datei wurde gekuerzt oder redigiert gelesen und kann "
        "deshalb nicht als Ganzes ersetzt werden. Aendere sie mit "
        "propose_config_patch — dabei bleibt alles Ungesehene unberuehrt."
    )
    return {
        "path": path,
        "revision": result["revision"] if patchable else None,
        # Von einer Binaerdatei geht nichts in den Kontext: der Salat kostet
        # Tokens und sagt dem Modell nichts, was es nicht schon aus `binary`
        # weiss.
        "content": "" if binaer else sicht,
        # Wo das Fenster liegt und wie gross die Datei ist — ohne diese beiden
        # Zahlen kann das Modell nicht weiterblaettern und weiss auch nicht, ob
        # es noch etwas zu blaettern gibt.
        "offset": offset,
        "lines": 0 if binaer else len(fenster),
        "total_lines": 0 if binaer else len(zeilen),
        "truncated": not vollstaendig,
        "redacted": was_redacted,
        "binary": binaer,
        "editable": editable,
        "patchable": patchable,
        **({} if editable else {"edit_blocked_reason": grund}),
    }


def _positive_int(
    value: object,
    *,
    name: str,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    """Ein optionales Zahlenargument, oder der Vorgabewert.

    ``None`` ist ausdruecklich erlaubt: manche Modelle setzen ein weggelassenes
    Argument auf null, statt es wegzulassen. Das als Fehler zu behandeln waere
    eine Huerde ohne Zweck.
    """
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise AiActionValidationError(f"'{name}' muss eine ganze Zahl sein")
    if value < minimum or (maximum is not None and value > maximum):
        grenze = f"{minimum}..{maximum}" if maximum is not None else f"ab {minimum}"
        raise AiActionValidationError(f"'{name}' liegt ausserhalb von {grenze}")
    return value


def _execute_file_search(
    db: Session, *, user: User, server: Server, arguments: dict
) -> dict:
    """Sucht einen Text in einer Datei oder unterhalb eines Verzeichnisses.

    Der Anlass ist eine Datei von einem Megabyte: `read_config` zeigt ein
    Fenster von vierhundert Zeilen, die Datei hat dreizehntausend. Ohne Suche
    muesste das Modell dreissigmal blaettern, um eine Einstellung zu finden —
    also blaettert es nicht, sondern raet oder gibt auf. Genau das war der
    Betriebsfall: die KI fand die Datei, sah den Anfang und erklaerte dem
    Benutzer, er muesse es von Hand tun.

    Gesucht wird mit `search_file_contents`, derselben Funktion, die auch der
    Dateimanager benutzt. Was hier dazukommt, ist genau das, was die KI von
    einem Menschen unterscheidet: die Rechtepruefung davor und die Redaktion
    danach. Enger sind auch die Deckel — bei einem entfernten Server ist jede
    gelesene Datei ein eigener Abruf, und jede Trefferzeile ist Text aus einer
    Quelle, der man nicht traut, im Kontext des Modells. Das erste kostet Zeit,
    das zweite Geld.
    """
    if set(arguments) - {"path", "query", "context"} or "query" not in arguments:
        raise AiActionValidationError("Datei-Suche hat ungueltige Argumente")
    if not permission_service.has_server_permission(
        db, user, server.id, "server.files.read"
    ):
        raise AiActionValidationError("Datei-Lesezugriff ist nicht erlaubt")

    query = arguments["query"]
    if not isinstance(query, str) or not query.strip():
        raise AiActionValidationError("Suchbegriff fehlt")
    if len(query) > MAX_SEARCH_QUERY_CHARS:
        raise AiActionValidationError("Suchbegriff ist zu lang")
    query = query.strip()
    kontext = _positive_int(
        arguments.get("context"),
        name="context",
        default=0,
        minimum=0,
        maximum=MAX_SEARCH_CONTEXT_LINES,
    )
    wurzel = _config_path(arguments["path"]) if arguments.get("path") else ""

    from services.server_file_access_service import search_file_contents

    ergebnis = search_file_contents(
        db,
        server_id=server.id,
        query=query,
        relative_path=wurzel,
        context=kontext,
        max_files=MAX_SEARCH_FILES,
        max_depth=MAX_SEARCH_DEPTH,
        max_matches=MAX_SEARCH_MATCHES,
    )

    def sichtbar(zeile: str) -> str:
        # Redigieren **vor** dem Kuerzen: andersherum schnitte die Kuerzung ein
        # Geheimnis mitten durch, und die Redaktion erkennt es dann nicht mehr.
        return redact_sensitive_text(zeile)[:MAX_SEARCH_LINE_CHARS]

    treffer = []
    for roh in ergebnis["matches"]:
        eintrag = {
            "path": roh["path"],
            "line": roh["line"],
            "text": sichtbar(str(roh["text"])),
        }
        if "context" in roh:
            eintrag["context"] = [sichtbar(str(z)) for z in roh["context"]]
            eintrag["context_first_line"] = roh["context_first_line"]
        treffer.append(eintrag)

    return {
        "server_id": server.id,
        "path": wurzel,
        "query": query,
        "matches": treffer,
        "files_searched": ergebnis["files_searched"],
        "truncated": ergebnis["truncated"],
    }
