from __future__ import annotations

import os
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import AuditLog, Node, Server, User, AiActionProposal
from schemas.ai_action import AiActionProposalResponse
from services import audit_service, permission_service
from services.ai_action_errors import (
    AiActionStateError,
    AiActionValidationError,
)
from services.ai_redaction import redact_sensitive_text
from services.server_file_access_service import read_server_text
from services.ai_tool_registry import (
    GLOBAL_READ_TOOLS,
    SERVER_READ_TOOLS,
    READ_TOOLS,
    WERKZEUGE,
    angebotsrechte,
)
from services.ai_tool_registry import (
    GLOBAL_READ_TOOLS,
    READ_TOOLS,
    WERKZEUGE,
    angebotsrechte,
)
from services.ai_tools.base import (
    MAX_CONFIG_CHARS,
    MAX_DIFF_CHARS,
    MAX_DIFF_LINES,
    MAX_READ_CONFIG_CHARS,
    MAX_LOG_CHARS,
    MAX_READ_CONFIG_LINES,
    MAX_SEARCH_QUERY_CHARS,
    MAX_SEARCH_FILES,
    MAX_SEARCH_DEPTH,
    MAX_SEARCH_MATCHES,
    MAX_SEARCH_LINE_CHARS,
    MAX_SEARCH_CONTEXT_LINES,
    MAX_PATCH_EDITS,
    MAX_PATCH_CHUNK_CHARS,
    MAX_LISTED_MODS,
    MAX_LISTED_BACKUPS,
    MAX_LISTED_INCIDENTS,
    MAX_LISTED_ACTIONS,
    MAX_LISTED_BLUEPRINTS,
    MAX_LISTED_NODES,
    MAX_LISTED_SERVERS,
    MAX_REASON_CHARS,
    MAX_BACKUP_NAME_CHARS,
    MAX_QUESTION_OPTIONS,
    MAX_QUESTION_CHARS,
    MAX_OPTION_CHARS,
    MAX_OPTION_HINT_CHARS,
    MAX_DESKTOP_INHALT_CHARS,
    MAX_AUFRAEUM_PFADE,
    MAX_INCIDENT_ATTEMPTS,
    MAX_TESTMAILS_JE_STUNDE,
    CONFIRMATION_TTL,
    _SERVER_ID_SCHEMA,
    _MUTEX_TOOLS,
    _RATIONALE_SCHEMA,
    _RATIONALE_REQUIRED,
    _MEMORY_TEAM_SCHEMA,
    _PLAN_SCHEMA,
    _MEMORY_KEY_RE,
    _MAX_SCOPE_ENTRIES,
    _function,
    _server_function,
    _vorfall_versuche,
    _require_no_arguments,
    _visible_servers,
    _resolve_server,
    _node_health,
    is_binary_text,
    _config_path,
    _positive_int,
)
from services.ai_tools.task_tools import (
    _aufgaben_tool_definitions,
    _worker_tool_definitions,
)
from services.ai_tools.personal_tools import (
    _mailbox_and_calendar_tool_definitions,
    _notes_tool_definitions,
    _execute_send_test_email,
)
from services.ai_tools.geo_tools import (
    _voice_tool_definitions,
    voice_control_tool_definitions,
    _region_request,
    execute_realtime_region_initial,
    execute_realtime_region_enrichment,
    _execute_analyze_region,
    _execute_control_region_camera,
)
from services.ai_tools.system_tools import (
    _desktop_tool_definitions,
    _execute_set_agent_name,
    _memory_team,
    _execute_remember,
    question_payload,
    _execute_search_memory,
    _execute_forget_memory,
    _execute_forget_skill,
    _execute_search_docs,
    _execute_read_docs,
    _execute_read_skill,
    _execute_learn_skill,
    _execute_web_search,
)

logger = logging.getLogger(__name__)

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
            "Panel kommen â€” Fehlermeldungen, Modkompatibilitaet, "
            "Spielversionen. Liefert Titel, Adresse und Kurztext.",
            {
                "query": {"type": "string", "maxLength": 200},
                "count": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS},
                "server_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Server, um den es geht â€” aus list_my_servers.",
                },
            },
            ["query"],
        ))

    from services.ai_satellite_service import is_configured as is_satellite_configured

    if is_satellite_configured():
        optional.append(_function(
            "analyze_region",
            "FÃ¼hrt eine regionale Analyse fÃ¼r einen geografischen Ort durch. "
            "Ermittelt Koordinaten, Wetterdaten und ruft aktuelle "
            "Satellitendaten (Copernicus/Sentinel-2) der Region ab. Der Ort "
            "kann auch eine SehenswÃ¼rdigkeit sein; die zurÃ¼ckgegebene WGS84-"
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
            "Steuert ausschlieÃŸlich die bereits geÃ¶ffnete Regionskarte, ohne "
            "Wetter, Satellitenbilder oder Nachrichten erneut abzurufen. "
            "Nutze dies fÃ¼r kurze Folgeanweisungen wie nÃ¤her heranzoomen, "
            "herauszoomen, zur WeltÃ¼bersicht wechseln oder eine konkrete "
            "SehenswÃ¼rdigkeit fokussieren.",
            {
                "action": {
                    "type": "string",
                    "enum": ["zoom_in", "zoom_out", "overview", "focus_location"],
                    "description": "Kamerabefehl fÃ¼r die bereits sichtbare Karte.",
                },
                "location": {
                    "type": "string",
                    "maxLength": 100,
                    "description": "Nur bei focus_location: genauer Name der SehenswÃ¼rdigkeit samt Stadt.",
                },
            },
            ["action"],
        ))

    # Globales Lernen kann der Betreiber abschalten. Dann steht "global" gar
    # nicht erst in der Auswahl â€” ein Modell, das eine Moeglichkeit angeboten
    # bekommt, die immer abgewiesen wird, versucht sie mehrfach.
    from services.ai_learning_policy import policy as learning_policy

    learn_scopes = ["team"] if learning_policy() == "off" else ["team", "global"]

    # Die Seitenliste steht in **beiden** Beschreibungen ausgeschrieben. Das
    # Modell kann sonst nur raten, was es ueberhaupt nachschlagen koennte â€” und
    # eine geratene Seitenkennung ist der erste Schritt zu einer geratenen
    # Antwort.
    from services.ai_docs_corpus import SEITEN as DOKU_SEITEN

    doku_seiten = sorted(DOKU_SEITEN)
    doku_liste = ", ".join(f"{s.schluessel} ({s.titel})" for s in DOKU_SEITEN.values())

    from services.cloudflare_service import is_configured as is_cloudflare_configured
    if is_cloudflare_configured():
        optional.append(_function("cloudflare_list_zones", "Listet Cloudflare Zonen, Domains und Hauptdomains auf. Vor jedem DNS-Create immer aufrufen um zone_id zu ermitteln.", {}, []))
        optional.append(_function("cloudflare_list_dns_records", "Listet alle DNS Records, Subdomains, Hostnames und EintrÃ¤ge einer Zone/Domain auf (z.B. zone_id oder Domain wie 'mauntingstudios.de' oder leer fuer Standardzone). Vor create auf Kollision pruefen.", {"zone_id": {"type": "string", "maxLength": 128}}, []))

    optional.append(_function("advise_node_placement", "Empfiehlt einen Host fuer einen neuen Server. Nutze vor propose_server_create um RAM/Disk bewusst zu waehlen. Unterscheidet gebucht vs wirklich belegt.", {"ram_need_mb": {"type": "integer", "minimum": 512}, "disk_need_gb": {"type": "integer", "minimum": 1}}, ["ram_need_mb"]))
    optional.append(_function("search_curseforge_modpacks", "Sucht Modpacks auf CurseForge (classId 432). Fuer Minecraft und Spiele mit Modpack-Support. Liefert id, name, downloads.", {"query": {"type": "string", "maxLength": 128}, "game_id": {"type": "string", "maxLength": 12}}, ["query"]))

    return optional + [
        _function(
            "search_docs",
            "Durchsucht die Dokumentation dieses Panels. **Der erste Schritt, "
            "bevor du etwas ueber MSM behauptest.** Verfuegbar: " + doku_liste + ".\n"
            "Liefert Seite, Abschnitt und einen Ausschnitt â€” den Abschnitt "
            "selbst holst du danach mit `read_docs`. Such danach, wie der "
            "Benutzer fragt; Umlaute und ihre Umschreibung findet die Suche "
            "gleichermassen.\n"
            "Findest du nichts, ist das ein Ergebnis: sag, dass dazu nichts in "
            "der MSM-Dokumentation steht. Nicht mit Wissen ueber andere Panels "
            "auffuellen â€” Pterodactyl, Pelican und Plesk arbeiten anders, und "
            "eine plausible Antwort ist hier schlimmer als keine.\n"
            "Nicht aufrufen bei Fragen zu einem laufenden Server, zu "
            "Spielinhalten oder zu Werten in einer Konfigurationsdatei â€” dafuer "
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
            "**Abschnittskennungen nie raten** â€” sie kommen aus der Gliederung "
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
            "Zeigt die panelseitige Hoster-API- und WHMCS-Shop-Anbindung (Server-Hosting-Verkauf) vollstaendig: vorhandene "
            "Integrationen mit Slug, Dienstbenutzer, Webhook-Ziel und "
            "Kuendigungsfrist, ihre Produktzuordnungen, die vergebenen Slugs, "
            "die Benutzer, die als Dienstbenutzer taugen, und die Rollen, die "
            "**dieser** Benutzer vergeben darf â€” samt ihrem KI-Kontingent.\n"
            "Beim Kontingent gilt dieselbe Ausnahme wie beim Anlegen: fehlt "
            "`ai_limits` ganz oder steht `max_memory_entries` darin auf `null`, "
            "sagt diese Rolle zum Gedaechtnisvorrat **nichts** â€” weder "
            f"'unbegrenzt' noch '{_MAX_SCOPE_ENTRIES}'. Es gewinnt die hoechste "
            "gesetzte Zahl unter allen Rollen ihres Traegers; die Systemgrenze "
            f"von {_MAX_SCOPE_ENTRIES} Eintraegen greift erst, wenn keine seiner "
            "Rollen eine Zahl traegt. Was ein einzelner Kunde am Ende hat, ist "
            "hier also nicht ablesbar.\n"
            "**Ruf das auf, bevor du etwas zur Shop-Einrichtung vorschlaegst.** "
            "Slug, Dienstbenutzer und Produktkennung sind nichts, was man raten "
            "kann; ein geratener Wert erzeugt einen Vorschlag, den der Benutzer "
            "bestaetigt und der dann scheitert. Es enthaelt bewusst keinen "
            "Schluessel â€” nur den Hinweis, an dem man einen Schluessel "
            "wiedererkennt.\n"
            "Steht bei einer Liste `withheld`, gibt es sie, und du darfst sie "
            "nur nicht sehen. Das ist nicht dasselbe wie eine leere Liste â€” "
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
            "**Gib den Block unveraendert weiter** â€” nicht umformulieren, nichts "
            "ergaenzen, nichts weglassen. Erklaere ringsherum so ausfuehrlich, "
            "wie es dem Benutzer hilft, aber lass die Werte in Ruhe: ein "
            "abgetippter Header oder ein angepasster Pfad ist der haeufigste "
            "Grund, warum eine Shop-Anbindung nicht laeuft.\n"
            "Die Bedeutung der `status_code`-Werte steht nicht hier, sondern in "
            "der Doku â€” der Block sagt dir, in welchem Abschnitt.",
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
            "Lage des Benutzers wirklich trifft â€” **passt keine eindeutig, ruf "
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
        # festhalten: Einzelfaelle, Zwischenergebnisse â€¦") stehen in
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
            "Anlass: du hast gerade ein Problem gelÃ¶st oder eine Vorgehensweise "
            "erarbeitet, die beim nÃ¤chsten Mal wieder gebraucht wird.\n"
            "Bereich: 'team' fuer alles, was zu diesem Betrieb gehoert. "
            "'global' nur fuer Erkenntnisse, die bei jedem Betreiber gelten â€” "
            "etwa eine Eigenschaft eines Spiels oder einer Mod. Pruefsatz: ein "
            "globaler Skill muss auf einem fremden Panel genauso stimmen. Im "
            "Zweifel 'team'.\n"
            "Gibt es den SchlÃ¼ssel schon, wird der Skill ersetzt â€” "
            "vollstÃ¤ndig, nicht ergÃ¤nzt; lies ihn vorher mit read_skill.",
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
                        "findest â€” und ob du ihn in einer Lage greifst, in die "
                        "er nicht gehoert. Schreib die Grenze mit hinein."
                    ),
                },
                # "nichts behaupten, was du nicht geprueft hast" ist der eine
                # Halbsatz der alten Beschreibung, fÃ¼r den `ai_prompt.SKILLS`
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
            "Setzt deinen Rufnamen fuer diesen Benutzer â€” nur auf seinen "
            "ausdruecklichen Wunsch (\"nenn dich ab jetzt â€¦\"). Ein leerer "
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
        # merken: Zwischenergebnisse, Logauszuege, Tagesform â€¦" und
        # "Aktualisierst du einen bekannten Fakt, verwende denselben
        # Schluessel erneut". Beides stand hier ein zweites Mal und ist
        # gestrichen. Das Verbot von Zugangsdaten bleibt: es steht nirgends
        # sonst â€” `ai_prompt.GEHEIMNISSE` verbietet das *Ausgeben*, nicht das
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
            # Bereichs â€” "eine Eigenschaft der Anlage, die fuer alle Kollegen
            # gilt" â€” und zeigte auf `team`. Bliebe der Satz stehen, aenderte
            # sich am beobachteten Verhalten gar nichts.
            #
            # **Die Merkmale waren aber rein sprachlich, und das war zu eng.**
            # Sie setzten voraus, dass der Benutzer den Satz gesagt hat: Regel
            # 1 sucht "ich"/"mein", Regel 3 sucht "wir"/"bei uns". Was die KI
            # selbst herausfindet, enthaelt keines dieser Woerter â€” es landete
            # ueber Regel 4 pauschal bei `user` oder wurde gar nicht erst
            # gemerkt. Gemessen am 19.08.2026: 7 Eintraege insgesamt, davon
            # **null** im Team-Bereich, juengster vom 16.08. Deshalb steht vor
            # der sprachlichen Reihenfolge jetzt die inhaltliche Frage, wem
            # eine Erkenntnis gehoert.
            "Wahl des Bereichs:\n"
            "Zuerst inhaltlich: Betrifft es **eine Person** (ihre Vorliebe, "
            "ihre Arbeitsweise, ihre Ausstattung), ist es persoenlich. "
            "Betrifft es **die Anlage** â€” wie ein Server sich verhaelt, wie "
            "hier gearbeitet wird, was du selbst ueber eine Einrichtung "
            "herausgefunden hast â€”, gehoert es dem Server oder dem Team, auch "
            "wenn niemand \"wir\" gesagt hat.\n"
            "Dann genauer, in dieser Reihenfolge pruefen:\n"
            "1. Persoenlich und zu genau einem Server: scope=server. "
            "Persoenlich ohne Serverbezug: scope=user (\"ich trinke am liebsten Mio Mio\", \"ich heisse Maik\", \"ich nehme immer 8 GB\").\n"
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
                    "description": "Kurzer stabiler Bezeichner, z. B. vorlieben.getraenke, favoriten.snack, ram.bevorzugt.",
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
            "das du aus den Werkzeugen selbst herausfinden kannst â€” frag erst, "
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
            "etwas loeschst oder korrigierst â€” und wenn der Benutzer wissen "
            "will, was du ueber ein Thema gespeichert hast. Findet auch, was "
            "anders formuliert ist: \"mein Hund\" findet einen Eintrag, in dem "
            "nur der Name des Hundes steht. Liefert Bereich, Schluessel und "
            "Inhalt, dazu server_id oder team_id â€” die braucht "
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
            "gefunden hast â€” geloescht wird ausschliesslich, was du hier "
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
            "Loescht einen erlernten Skill. Nur eigene und Team-Skills â€” die "
            "mit MSM ausgelieferten lassen sich nicht loeschen, sondern nur "
            "ueberschreiben, indem du unter demselben Schluessel einen neuen "
            "anlegst. Zum *Aendern* eines Skills nimm `learn_skill` mit "
            "demselben Schluessel; loeschen und neu anlegen verliert die "
            "Herkunft.\n"
            "Denselben Schluessel kann es in mehreren Bereichen geben â€” "
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
            "Liest einen Blueprint vollstaendig â€” Image, Startbefehl, Ports und "
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
            "Grenzen einschliesslich **gestoppter** Server â€” die belegen "
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
            "Leitet aus einem vorhandenen Blueprint einen neuen ab â€” so aendert "
            "man eine Spielversion, ohne die Vorlage aller anderen Server "
            "anzufassen. Die Quelle bleibt unveraendert. Aenderbar sind "
            "meta.name, meta.description, runtime.image, runtime.env und "
            "runtime.startup â€” ueber runtime.startup korrigierst du fehlende "
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
            "Legt eine globale Rolle fuer einen Shop-Tarif an â€” **mit leerer "
            "Rechteliste** und nur einem KI-Kontingent. Genau darin liegt ihr "
            "Zweck: Kontingente haengen an globalen Rollen, und ohne eine solche "
            "Rolle bekommt jeder Shop-Kunde dasselbe Kontingent wie jeder "
            "andere.\n"
            "Rechte vergibt sie ausdruecklich keine. Braucht der Tarif welche, "
            "gehoert das in die Rollenverwaltung des Panels und nicht hierher.\n"
            "Bei den Kontingenten heisst ein Feld auf `null` **unbegrenzt**, "
            "nicht null; `max_memory_entries` ist die Ausnahme, siehe dort. "
            "Setz nur, was der Benutzer genannt hat, und frag im Zweifel nach "
            "â€” ein geratenes Tageslimit merkt der Kunde erst, wenn es greift.",
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
                        "eine zusaetzliche Rolle gar nicht â€” dafuer muss die "
                        "Zahl der bestehenden Rolle sinken."
                    ),
                },
                **_RATIONALE_SCHEMA,
            },
            ["name", *_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_hoster_integration",
            "Legt eine Hoster-Integration an oder aendert eine bestehende â€” die "
            "panelseitige Haelfte einer Shop-Anbindung. Ist ein `webhook_url` "
            "gesetzt und noch kein Secret vorhanden, wird zugleich eines "
            "erzeugt: ein Ziel ohne Secret stellt nichts zu, und das faellt "
            "sonst erst im Betrieb auf.\n"
            "**Ruf vorher `read_hoster_setup` auf.** Der Slug muss panelweit "
            "eindeutig sein und der Dienstbenutzer aktiv sein, kein Owner und "
            "`servers.create` haben â€” beides steht dort, beides ist nicht zu "
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
            "Shop â€” MSM-interne IDs muss der Shop nie kennen.\n"
            "**Ruf vorher `read_hoster_setup` auf** fuer die Integration, die "
            "vorhandenen Produktkennungen und die Rollen, die dieser Benutzer "
            "vergeben darf. Eine Rolle, die dort nicht steht, wird abgewiesen â€” "
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
        _function(
            "propose_cloudflare_dns_record",
            "Legt einen Cloudflare DNS Record, DNS-Eintrag oder eine Subdomain an (A/CNAME, Test-Eintrag). Vorher cloudflare_list_zones aufrufen um zone_id zu ermitteln, Kollision pruefen. Name als Subdomain {game}-{slug}.{zone}, Inhalt ist Server-IP/Target.",
            {
                "zone_id": {"type": "string", "maxLength": 64},
                "name": {"type": "string", "maxLength": 253},
                "rtype": {"type": "string", "enum": ["A", "CNAME"]},
                "content": {"type": "string", "maxLength": 253},
                "proxied": {"type": "boolean"},
                **_RATIONALE_SCHEMA,
            },
            ["zone_id", "name", "rtype", "content", *_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_cloudflare_dns_delete",
            "Loescht einen Cloudflare DNS Record / Eintrag anhand seiner record_id oder seines Namens/Subdomain (z.B. 'test.mauntingstudios.de').",
            {
                "record_id": {"type": "string", "maxLength": 253, "description": "Hex-ID oder Hostname/Subdomain des DNS-Records"},
                "zone_id": {"type": "string", "maxLength": 64, "description": "Optional: Zonen-ID oder Domain"},
                **_RATIONALE_SCHEMA,
            },
            ["record_id", *_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_modpack_install",
            "Installiert ein Modpack (CurseForge) auf einem bestehenden Server. Braucht server_id aus list_my_servers und Modpack-Info aus search_curseforge_modpacks.",
            {
                **_SERVER_ID_SCHEMA,
                "modpack_mod_id": {"type": "string", "maxLength": 32},
                "file_id": {"type": "string", "maxLength": 32},
                **_RATIONALE_SCHEMA,
            },
            ["server_id", "modpack_mod_id", "file_id", *_RATIONALE_REQUIRED],
        ),
        *_aufgaben_tool_definitions(),
        *_worker_tool_definitions(),
        *_mailbox_and_calendar_tool_definitions(),
        *_notes_tool_definitions(),
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
            "Wurzel. Nutze das, bevor du eine Datei liest â€” Dateinamen raten "
            "fuehrt zu Fehlversuchen.",
            {"path": {"type": "string", "maxLength": 256}},
        ),
        _server_function(
            "search_server_files",
            "Sucht einen Text in den Dateien des Servers und liefert Pfad und "
            "Zeilennummer jedes Treffers. **Der erste Schritt bei jeder grossen "
            "Datei** â€” eine Spielkonfiguration hat tausende Zeilen, und "
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
            "Liest eine Textdatei des Servers revisionssicher â€” Konfigurationen, "
            "Whitelists, Skripte, alles was der Dateimanager auch zeigt. Ohne "
            f"`offset` die ersten {MAX_READ_CONFIG_LINES} Zeilen; `total_lines` "
            "sagt dir, wie lang die Datei wirklich ist. Zu einer Fundstelle aus "
            "search_server_files springst du mit `offset`. "
            "`editable: false` heisst **nur**, dass du die Datei nicht als "
            "Ganzes ersetzen darfst, weil du sie nicht ganz gesehen hast â€” mit "
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
        # â”€â”€ Erweiterter Serverkontext (Zielpunkt 3.3) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        _server_function(
            "read_server_ports",
            "Liest die vergebenen Ports des Servers mit Rolle und Protokoll.",
        ),
        _server_function(
            "read_server_network",
            "Liest die Netzwerkeinrichtung: Bind-IP mit Einordnung, Ports, "
            "verfuegbare Host-Adressen und Firewall-Zustand. Erster Schritt, "
            "wenn ein Server laeuft, aber niemand sich verbinden kann. "
            "Rufe danach check_server_reachability auf â€” erst beide zusammen "
            "ergeben eine Diagnose. read_server_status ist dafuer nicht noetig, "
            "der Status steht bereits in dieser Antwort.",
        ),
        _server_function(
            "check_server_reachability",
            "Misst, ob auf den Ports des Servers tatsaechlich etwas lauscht. "
            "Der eigentliche Beweis bei 'laeuft, aber niemand kommt drauf': "
            "meldet ein Port sich als frei, obwohl der Server laeuft, horcht "
            "der Dienst nicht oder horcht auf einer anderen Adresse. "
            "Beantwortet nicht, ob der Server aus dem Internet erreichbar ist â€” "
            "das kann MSM nicht messen und behauptet es auch nicht.\n"
            "`game_probe` traegt zusaetzlich das Urteil der Anwendungsprobe, die "
            "der Blueprint deklariert und der Guardian auf der Node ausfuehrt: "
            "`answering` (der Dienst antwortet im Spielprotokoll), "
            "`not_answering` (Port offen, Dienst stumm â€” der eigentliche Befund "
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
        # â”€â”€ Schreib-Tools: erzeugen ausschliesslich Vorschlaege â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
            "Name hilft dem Benutzer, es spaeter wiederzuerkennen â€” nenne den "
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
            "read_server_backups â€” rate sie nie.",
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
            # und prueft nichts dergleichen â€” dokumentiert in
            # `ai_proposal_service` bei den erfundenen Einschraenkungen. Eine
            # Bedingung, die es nicht gibt, haelt das Modell von Wechseln ab,
            # die durchgegangen waeren.
            "Schlaegt vor, einen bestehenden Server auf einen anderen Blueprint "
            "umzustellen â€” so aendert man die Spielversion, denn sie steht im "
            "Blueprint und nicht am Server. Der Server muss gestoppt sein. Leite "
            "vorher mit propose_blueprint_change einen passenden ab. Der "
            "Vorgang legt zwingend ein Backup an und **loescht danach alle "
            "Serverdateien**, damit die neue Version auf einem leeren "
            "Verzeichnis aufsetzt: Welt, Configs und Mods sind anschliessend "
            "weg und stehen nur noch im Backup. Sage das im Grund ausdruecklich. "
            "Braucht immer eine Bestaetigung durch einen Menschen â€” auch im "
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
            "Ersetzt eine Datei **vollstaendig** â€” fuer neue Dateien und fuer "
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
            "unberuehrt â€” der Weg fuer jede grosse Datei, auch wenn sie "
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
            "Setzt einzelne Schluessel in einer INI-artigen Datei â€” **der "
            "Normalfall fuer Spieleinstellungen**. Du nennst Sektion, "
            "Schluessel und Wert statt Text zu suchen: die Sektion wird "
            "gefunden oder angelegt, ein vorhandener Schluessel ueberschrieben "
            "statt gedoppelt, die Zeilenenden bleiben. Einen fehlenden "
            "Schluessel legst du damit an â€” Regelfall, kein Hindernis. Der Wert "
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
            "Schlaegt eine andere Bind-IP vor â€” etwa wenn der Server an eine "
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
            "Feld `enabled`) â€” nie in einer Spielkonfiguration. Wirkt erst "
            "beim naechsten Start des Servers.",
            {
                "workshop_id": {"type": "string", "maxLength": 20},
                "enabled": {"type": "boolean"},
                **_RATIONALE_SCHEMA,
            },
            ["workshop_id", "enabled", *_RATIONALE_REQUIRED],
        ),
        # Die Reparatur der **Anlage** â€” alles unterhalb der Spieldateien.
        #
        # `action` ist ein `enum` und kein Freitext, und das ist der ganze Sinn
        # des Werkzeugs: das Modell waehlt eine von vier Kennungen. Es formuliert
        # keinen Pfad, kein Kommando und keinen Containernamen â€” der kommt aus
        # `container_name_for(server_id)`. Ein Modell, das durch eine Logzeile
        # zu etwas ueberredet wurde, kann hier hoechstens die falsche der vier
        # Reparaturen anstossen.
        _server_function(
            "propose_server_repair",
            "Repariert die Anlage unter dem Server, nicht seine Dateien. Zwei "
            "Moeglichkeiten: `repair_permissions` berichtigt die Besitzrechte am "
            "Serververzeichnis â€” der Weg bei 'permission denied', 'read-only "
            "file system' oder wenn der Server seine eigenen Dateien nicht mehr "
            "schreiben kann. `reallocate_port` vergibt die Ports neu, die auf "
            "dem Host jemand anderes belegt â€” der Weg bei 'address already in "
            "use', aber nur bei einem **gestoppten** Server; bei einem laufenden "
            "haelt er seine Ports selbst und es gibt nichts zu vergeben. "
            "Nichts davon aendert Spielstaende. "
            "Fuer 'Container haengt' oder 'startet nicht' nimm "
            "propose_server_lifecycle mit `restart` â€” das baut den Container "
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
        # ueberredet wurde, hoechstens einen ungeschickten Wert waehlen â€” es
        # kann keine Probe abschalten, keinen Probentyp tauschen und kein
        # Muster einschmuggeln.
        _server_function(
            "propose_guardian_tuning",
            "Stellt die Guardian-Engine **fuer diesen einen Server** anders ein, "
            "ohne die Blueprint anderer Server anzufassen. Der Weg fuer den Fall, "
            "dass Guardian sich nicht geirrt hat und der Server nicht kaputt ist, "
            "sondern Guardian fuer diesen Server falsch eingestellt wurde â€” etwa "
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
        # â”€â”€ Die eingebauten ZeitplÃ¤ne: Auto-Neustart und Auto-Backup â”€â”€â”€â”€â”€â”€
        #
        # Der Durchgriff statt einer stehenden Aufgabe: was hier gesetzt wird,
        # sieht der Benutzer im Panel unter dem Server und kann es dort selbst
        # Ã¤ndern. Eine manuelle Ã„nderung nimmt der KI die Verwaltung wieder ab.
        #
        # Der **Anlass** â€” wann diese zwei Werkzeuge statt `propose_task_set`
        # gelten, dass je Server ein Aufruf reicht und dass nicht nachgefragt
        # wird â€” steht in `ai_prompt.AUFGABEN` und geht mit derselben Anfrage
        # mit. Hier steht nur die Feldkunde; die Wiederholung des Anlasses
        # kostete den Katalog knapp 1.000 Zeichen je Runde (siehe
        # test_ai_tool_handler_contract zum Katalogbudget).
        _server_function(
            "propose_restart_schedule_set",
            "Setzt den eingebauten Auto-Neustart-Zeitplan dieses Servers. "
            "Entweder `interval_hours` oder `times`, nie beides; "
            "`enabled: false` schaltet aus und braucht keinen Plan. "
            "`times` sind UTC und gelten tÃ¤glich â€” rechne die Ortszeit des "
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
                    "description": "Bis zu 12 Neustartzeiten 'HH:MM' in UTC, gelten tÃ¤glich.",
                },
                **_RATIONALE_SCHEMA,
            },
            ["enabled", *_RATIONALE_REQUIRED],
        ),
        _server_function(
            "propose_backup_schedule_set",
            "Setzt den eingebauten Auto-Backup-Zeitplan dieses Servers. "
            "Nur genannte Felder werden angefasst; die Ã¼brigen bleiben stehen.",
            {
                "backup_on_start": {
                    "type": "boolean",
                    "description": "Vor jedem Serverstart automatisch sichern.",
                },
                "interval_hours": {
                    "type": "integer", "minimum": 0, "maximum": 720,
                    "description": (
                        "Backup alle N Stunden; 0 = aus, 24 = tÃ¤glich, "
                        "168 = wÃ¶chentlich, 720 = alle 30 Tage."
                    ),
                },
                "retention_count": {
                    "type": "integer", "minimum": 1, "maximum": 100,
                    "description": "Aufbewahrte Backups (1-100); Ã¤ltere werden gelÃ¶scht.",
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
            "erfolgreichen Backup, das juenger ist als der Vorfall â€” fehlt es, "
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
    bleibt unveraendert dort, wo sie war â€” `_resolve_server`, die
    Rechtepruefung im jeweiligen Handler und `_require_tool_permission` im
    Vorschlagspfad. Ein Modell, das sich ein Werkzeug ausdenkt oder aus dem
    Gespraechsverlauf abschreibt, prallt dort weiterhin ab.

    Warum es das trotzdem gibt, und zwar zuerst als **Korrektur**: die KI erbt
    die Rechte des Benutzers. Wer kein Hoster-Recht hat, dessen KI kann die
    Hoster-Werkzeuge nicht ausfuehren â€” angeboten bekam er sie trotzdem, alle
    51. Das Modell versuchte sie, wurde abgewiesen und hatte eine Runde
    verbraucht. Wir haben ihm also Faehigkeiten angeboten, die es in seinem
    Namen nie hatte.

    Die Ersparnis kommt obendrauf: der Katalog geht in **jeder** Runde der
    Werkzeugschleife mit ueber die Leitung und machte 94 Prozent des Prompts
    aus. Und die Trefferqualitaet steigt â€” bei 51 aehnlichen Werkzeugen greift
    ein Modell haeufiger zum falschen.

    Gefragt wird `has_permission_anywhere` und nicht `has_server_permission`:
    hier gibt es noch keinen Server. Den waehlt das Modell erst im Argument des
    Aufrufs, und dort wird dann am konkreten Server geprueft.
    """
    # Alle 24 Schluessel in einer Runde. Der Merkzettel je Schluessel, der hier
    # zuerst stand, half nur halb: er sparte die Wiederholung je Werkzeug, nicht
    # die je Schluessel â€” und darunter fragte jede Pruefung die Rollen des
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

def _execute_global_read_tool(
    db: Session, *, user: User, tool_name: str, arguments: dict,
    herkunft: str = "panel", familie: str | None = None,
    prefetch_session_id: str | None = None,
    fast_region: bool = False,
) -> dict:
    """Werkzeuge ohne Serverbezug.

    `list_my_servers` ist die Einstiegsfrage jedes Gespraechs und deshalb an
    kein zusaetzliches Recht gebunden â€” es zeigt ausschliesslich Server, die der
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

    if tool_name == "execute_server_action":
        from services.ai_voice.voice_dispatcher import dispatch_voice_action
        wert, fehler, _anzeige, _vorschlaege = dispatch_voice_action(
            user.id,
            arguments,
            conversation_id=None,
            herkunft=herkunft,
            familie=familie,
        )
        if fehler and isinstance(wert, dict) and "error" not in wert:
            wert["error"] = fehler
        return wert if isinstance(wert, dict) else {"result": wert}

    if tool_name == "worker_start":
        from services import ai_worker_service

        # Die Herkunft wird **vererbt**, nicht gewaehlt: ein Auftrag aus der
        # App darf denselben Rechner sehen wie der Lauf, der ihn gestellt hat.
        # Ohne sie fiel der Worker auf "panel" und meldete dem Benutzer, er
        # koenne auf dessen Rechner nicht zugreifen (22.08.2026).
        #
        # Die Familie geht denselben Weg und beantwortet die zweite HÃ¤lfte
        # derselben Frage: die Herkunft sagt â€žaus der App", die Familie sagt
        # â€žaus **dieser** App". Nur mit ihr landet ein Desktop-Auftrag des
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
        # Werkzeug nie. Der Zweig steht trotzdem hier â€” als benannte Antwort
        # statt des Durchfall-raise, damit ein Aufruf ausserhalb eines
        # parkfaehigen Laufs eine Erklaerung bekommt und kein Raetsel.
        raise AiActionValidationError(
            "wait_until parkt den Lauf und wird im Rundenlauf behandelt â€” "
            "in diesem Lauf steht es nicht zur VerfÃ¼gung"
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
        # nur, wenn die Bitte gar nicht von einem Rechner kam â€” dann sortiert
        # sie schon der Herkunfts-Spiegel aus, und wenn selbst der umgangen
        # waere, ist ein benannter Fehlschlag die einzig ehrliche Antwort. Ein
        # stiller Durchfall lieferte dem Modell ein "erledigt" fuer etwas, das
        # nie passiert ist.
        raise AiActionValidationError(
            "Werkzeuge fÃ¼r den Rechner des Benutzers laufen nur aus der "
            "Smart-System-App â€” in diesem Lauf stehen sie nicht zur VerfÃ¼gung"
        )

    if tool_name == "list_tasks":
        from services import ai_task_service

        _require_no_arguments(tool_name, arguments)
        # Kein zusaetzliches Recht: die Liste zeigt ausschliesslich, was diesem
        # Benutzer gehoert. Wer keine Aufgaben anlegen darf, hat auch keine â€”
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
                "integration_id fehlt â€” hol sie aus read_hoster_setup"
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
        if fast_region:
            return execute_realtime_region_initial(db, user=user, arguments=arguments)
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

    if tool_name == "notes_read":
        if not permission_service.has_global_permission(db, user, "ai.notes.use"):
            raise AiActionValidationError("Notiz-Einsicht ist nicht erlaubt")
        from services.notes_service import NotesService

        query = str(arguments.get("query", "")) if arguments.get("query") else None
        category = str(arguments.get("category", "")) if arguments.get("category") else None
        team_id = int(arguments["team_id"]) if arguments.get("team_id") is not None else None
        is_pinned = bool(arguments["is_pinned"]) if arguments.get("is_pinned") is not None else None
        notes = NotesService.get_notes(
            db, user=user, search=query, category=category, team_id=team_id, is_pinned=is_pinned
        )
        return {"notes": notes, "count": len(notes)}

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

    if tool_name == "list_my_servers":
        _require_no_arguments(tool_name, arguments)
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
        _require_no_arguments(tool_name, arguments)
        # Bewusst `nodes.read` statt `servers.create`: den Zustand der Hosts zu
        # sehen ist eine Aufgabe des Betriebs, nicht der Serverplanung. Ein
        # Support-Mitarbeiter soll nachsehen koennen, ohne Server anlegen zu
        # duerfen.
        if not permission_service.has_global_permission(db, user, "nodes.read"):
            raise AiActionValidationError("Node-Einsicht ist nicht erlaubt")
        return _node_health(db)

    if tool_name == "list_blueprints":
        _require_no_arguments(tool_name, arguments)
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
        _require_no_arguments(tool_name, arguments)
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
                # Was die Node selbst meldet â€” die einzige echte Messung hier.
                "ram_total_mb": int(node.ram_total / 1024 / 1024) if node.ram_total else None,
                "ram_used_mb": int(node.ram_used / 1024 / 1024) if node.ram_used else None,
            })
        return {"nodes": entries}

    if tool_name == "advise_node_placement":
        if not permission_service.has_global_permission(db, user, "servers.create"):
            raise AiActionValidationError("Serverplanung ist nicht erlaubt")
        from services.node_advisor_service import advise_node

        ram = int(arguments.get("ram_need_mb", 2048))
        disk = int(arguments.get("disk_need_gb", 5))
        return {"advice": advise_node(db, ram, disk), "requested": {"ram_need_mb": ram, "disk_need_gb": disk}}

    if tool_name == "search_curseforge_modpacks":
        from services.curseforge_api_key_service import resolve_key as _cf_resolve

        if not _cf_resolve():
            return {"error": "curseforge_api_key_missing", "hint": "CurseForge API-Key in Einstellungen hinterlegen"}
        query = str(arguments.get("query", "") or "")[:128]
        game_id = str(arguments.get("game_id", "432") or "432")
        try:
            import concurrent.futures

            def _sync_search():
                import asyncio as _aio

                async def _do():
                    from services.curseforge_service import get_curseforge_service

                    s = await get_curseforge_service()
                    return await s.search_modpacks(game_id=game_id, query=query, per_page=12)

                try:
                    return _aio.run(_do())
                except RuntimeError:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                        fut = ex.submit(lambda: _aio.run(_do()))
                        return fut.result(timeout=20)

            mods = _sync_search()
            return {"mods": [{"id": m.publishedfileid, "name": m.title, "downloads": m.subscriptions, "author": m.creator} for m in mods], "query": query}
        except Exception:
            return {"error": "curseforge_search_failed", "query": query}

    if tool_name == "cloudflare_list_zones":
        if not permission_service.has_global_permission(db, user, "cloudflare.manage"):
            raise AiActionValidationError("Cloudflare-Einsicht ist nicht erlaubt")
        from services.cloudflare_service import is_configured as _cf_cfg

        if not _cf_cfg():
            return {"error": "cloudflare_not_configured", "hint": "Cloudflare API-Token in Einstellungen hinterlegen und aktivieren"}
        try:
            import concurrent.futures, asyncio as _aio

            def _do_zones():
                from services.cloudflare_service import list_zones

                async def _inner():
                    return await list_zones()

                try:
                    return _aio.run(_inner())
                except RuntimeError:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                        return ex.submit(lambda: _aio.run(_inner())).result(timeout=15)

            zones = _do_zones()
            return {"zones": [{"id": z.get("id"), "name": z.get("name"), "status": z.get("status")} for z in zones]}
        except Exception:
            return {"error": "cloudflare_list_failed"}

    if tool_name == "cloudflare_list_dns_records":
        if not permission_service.has_global_permission(db, user, "cloudflare.manage"):
            raise AiActionValidationError("Cloudflare-Einsicht ist nicht erlaubt")
        zone_id = str(arguments.get("zone_id", "") or arguments.get("domain", "") or arguments.get("zone", "") or arguments.get("name", "")).strip()
        try:
            import concurrent.futures, asyncio as _aio

            def _do_records():
                from services.cloudflare_service import list_dns_records

                async def _inner():
                    return await list_dns_records(zone_id if zone_id else None)

                try:
                    return _aio.run(_inner())
                except RuntimeError:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                        return ex.submit(lambda: _aio.run(_inner())).result(timeout=15)

            records = _do_records()
            return {"records": [{"id": r.get("id"), "name": r.get("name"), "type": r.get("type"), "content": r.get("content")} for r in records], "zone_id": zone_id}
        except Exception as exc:
            return {"error": "cloudflare_list_failed", "detail": str(exc), "zone_id": zone_id}

    # **Der Durchfall war die gefaehrlichste Zeile der Datei.** Bis hierher war
    # die Kapazitaetsabfrage der namenlose Rumpf am Ende der Kette: wer keinen
    # eigenen Zweig hatte, bekam ihn. Ein Werkzeug, das in der Tabelle und im
    # Katalog steht, aber beim Verdrahten vergessen wurde, lieferte dem Modell
    # damit RAM-Zahlen unter seinem eigenen Namen zurueck â€” eine falsche
    # Auskunft, die wie eine richtige aussieht, und der einzige Ort im ganzen
    # Werkzeugpfad, an dem das ohne Fehler passieren konnte.
    raise AiActionValidationError(f"Kein Handler fÃ¼r Werkzeug: {tool_name}")

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
        # sie auch nicht sehen â€” die Ports des eigenen Servers schon.
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
                    # benennen, auf den es sich bezieht â€” weder in seiner
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
                    # das faengt die KI bei jedem Vorfall mit einem Neustart an â€”
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
        raise AiActionValidationError(f"Kein Handler fÃ¼r Werkzeug: {tool_name}")
    if set(arguments) - {"query", "page"} or not isinstance(arguments.get("query"), str):
        raise AiActionValidationError("Workshop-Suche hat ungÃ¼ltige Argumente")
    page = arguments.get("page", 1)
    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 50:
        raise AiActionValidationError("UngÃ¼ltige Seitenzahl")
    mod_support = plugin.get_mod_support() or {}

    # Die Anfrage geht an einen fremden Dienst (Steam oder CurseForge) und wird
    # dort protokolliert â€” dieselbe Lage wie bei `web_search`, also dieselbe
    # SchwÃ¤rzung, eine Richtung frÃ¼her als der Choke Point auf dem RÃ¼ckweg.
    # Der Suchbegriff ist reine Modellausgabe, und das Modell hat vorher
    # Konfigurationsdateien und Logs gelesen: eine Zuweisung wie
    # `ServerAdminPassword=â€¦` kann es wÃ¶rtlich Ã¼bernehmen. Die SchwÃ¤rzung ist
    # wertbezogen, ein Einstellungs- oder Modname als Wort Ã¼berlebt sie.
    #
    # GekÃ¼rzt wird auf die LÃ¤nge, die das Schema verspricht. Ein Schema ist eine
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
            # laufen ausschlieÃŸlich Ã¼ber `_werkzeug_ausfuehren` und damit "in
            # eigener Sitzung und eigenem Thread", wo es nie eine gibt. Hier
            # stand ein `ThreadPoolExecutor`, der `asyncio.run` in einem zweiten
            # Thread startete â€” toter Verteidigungscode, der obendrein den
            # Eindruck machte, der Handler sei auf der Schleife aufrufbar. WÃ¤re
            # er das, blockierte er sie fÃ¼r die volle Dauer des HTTP-Aufrufs;
            # nebenlÃ¤ufig wird davon nichts, der Executor verdeckt nur den
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
            # gehÃ¶rt ins Log â€” und dort nur der Ausnahmetyp, wie es
            # `curseforge_service` schon hÃ¤lt: eine Fehlermeldung kann den
            # API-SchlÃ¼ssel tragen.
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

def _execute_file_search(
    db: Session, *, user: User, server: Server, arguments: dict
) -> dict:
    """Sucht einen Text in einer Datei oder unterhalb eines Verzeichnisses.

    Der Anlass ist eine Datei von einem Megabyte: `read_config` zeigt ein
    Fenster von vierhundert Zeilen, die Datei hat dreizehntausend. Ohne Suche
    muesste das Modell dreissigmal blaettern, um eine Einstellung zu finden â€”
    also blaettert es nicht, sondern raet oder gibt auf. Genau das war der
    Betriebsfall: die KI fand die Datei, sah den Anfang und erklaerte dem
    Benutzer, er muesse es von Hand tun.

    Gesucht wird mit `search_file_contents`, derselben Funktion, die auch der
    Dateimanager benutzt. Was hier dazukommt, ist genau das, was die KI von
    einem Menschen unterscheidet: die Rechtepruefung davor und die Redaktion
    danach. Enger sind auch die Deckel â€” bei einem entfernten Server ist jede
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

def execute_read_tool(
    db: Session,
    *,
    user: User,
    tool_name: str,
    arguments: dict,
    herkunft: str = "panel",
    familie: str | None = None,
    prefetch_session_id: str | None = None,
    fast_region: bool = False,
) -> dict:
    """Fuehrt ein Lesewerkzeug im Namen des Benutzers aus.

    Die Unterhaltung wird bewusst nicht mehr uebergeben: sie traegt keinen
    Kontext mehr, der die Ausfuehrung beeinflusst. Alles, was ein Werkzeug
    braucht, steht in seinen Argumenten und wird gegen die Rechte von ``user``
    geprueft.

    ``herkunft`` und ``familie`` sind die einzigen Ausnahmen davon, und sie
    stehen ausdrÃ¼cklich **nicht** in den Argumenten: aus welcher Welt der
    Aufruf kam und von welchem GerÃ¤t, sind Tatsachen des Laufs. Gebraucht
    werden beide von genau einem Werkzeug â€” `worker_start` gibt sie an den
    Auftrag weiter, den es anlegt. Die Herkunft Ã¶ffnet ihm die
    Desktop-Werkzeuge, die Familie sagt, an welchen Rechner er sich damit
    wendet; ohne sie holt seinen Auftrag der, der zuerst fragt.
    """
    if tool_name not in READ_TOOLS:
        raise AiActionValidationError("Read-Tool ist in diesem Kontext nicht erlaubt")

    # Ein Cache-Hit kommt nur aus derselben Sprachsitzung. Die kleinen,
    # werkzeugspezifischen VorprÃ¼fungen sind die zweite Schranke nach dem
    # Prefetch und verhindern, dass ein inzwischen entzogener Zugriff ein altes
    # Ergebnis erhÃ¤lt.
    from services.ai_intent_classifier import prefetch_cache
    if prefetch_session_id and tool_name == "analyze_region":
        if not permission_service.has_global_permission(db, user, "ai.satellite.use"):
            raise AiActionValidationError("Satelliten- und Regionsanalyse ist fÃ¼r diesen Benutzer nicht freigegeben")
    elif prefetch_session_id and tool_name == "control_region_camera":
        if not permission_service.has_global_permission(db, user, "ai.satellite.use"):
            raise AiActionValidationError("Kartensteuerung ist fÃ¼r diesen Benutzer nicht freigegeben")
    elif prefetch_session_id and tool_name == "web_search":
        if not permission_service.has_global_permission(db, user, "ai.web_search.use"):
            raise AiActionValidationError("Websuche ist fuer diesen Benutzer nicht freigegeben")
    elif prefetch_session_id and tool_name == "calendar_read":
        if not permission_service.has_global_permission(db, user, "ai.calendar.use"):
            raise AiActionValidationError("Kalender ist fuer diesen Benutzer nicht freigegeben")
    elif prefetch_session_id and tool_name == "notes_read":
        if not permission_service.has_global_permission(db, user, "ai.notes.use"):
            raise AiActionValidationError("Notizen sind fuer diesen Benutzer nicht freigegeben")
    elif prefetch_session_id and tool_name == "search_memory":
        if not permission_service.has_global_permission(db, user, "ai.memory.use"):
            raise AiActionValidationError("Memory ist fuer diesen Benutzer nicht freigegeben")
    elif prefetch_session_id and tool_name == "read_server_status":
        _resolve_server(db, user, arguments)
    hit, cached_result = prefetch_cache.get(
        session_id=prefetch_session_id, user_id=user.id, tool_name=tool_name, arguments=arguments,
    )
    if hit and cached_result is not None:
        logger.info("Spekulativer Prefetch-Cache HIT fÃ¼r tool=%s user=%s", tool_name, user.id)
        return cached_result

    if tool_name in GLOBAL_READ_TOOLS:
        return _execute_global_read_tool(
            db, user=user, tool_name=tool_name, arguments=arguments,
            herkunft=herkunft, familie=familie, prefetch_session_id=prefetch_session_id,
            fast_region=fast_region,
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
        # auf `node_id` â€” das ist die Summe der Buchungen **aller** Kunden auf
        # diesem Host â€”, und cpu_total/ram_total/disk_* beschreiben die
        # Maschine des Betreibers. `_resolve_server` prueft nur `server.view`;
        # damit gab dieses Werkzeug jedem Hosting-Kunden die Ueberbuchungslage
        # seines Anbieters heraus, waehrend `read_node_capacity` dafuer
        # `servers.create` und `read_node_health` `nodes.read` verlangt.
        #
        # Die Grenze ist dieselbe wie bei `describe_network`: wer die Grenzen
        # dieses Servers aendern darf, muss sehen, wieviel Platz dafuer da ist.
        # Alle anderen bekommen den Status der Node und sonst nichts â€”
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
        # â€” damit war die Konsole ueber den KI-Pfad fuer jeden lesbar, der den
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

    # Ab hier folgt `read_config` â€” und zwar bisher **ohne** dass sein Name
    # geprueft wurde. Jedes serverbezogene Lesewerkzeug, das keinen eigenen
    # Zweig hat, landete hier und wurde als Dateizugriff ausgefuehrt.
    #
    # Solange die Argumentpruefung darunter zuschlug, fiel das als
    # "Datei-Lesewerkzeug hat ungueltige Argumente" auf â€” eine Fehlermeldung,
    # die den falschen Grund nennt. Ein kuenftiges Werkzeug mit einem
    # `path`-Argument haette sie aber passiert und dem Modell den Inhalt einer
    # Datei unter dem Namen des anderen Werkzeugs geliefert: richtiger Name,
    # falsche Daten. Genau dieses Muster hat in diesem Projekt schon dreimal
    # zugeschlagen, und es faellt nie zur Laufzeit auf.
    #
    # `_werkzeug_bekannt` faengt beim Definieren ein Werkzeug ohne
    # Registry-Zeile. Diese Zeile hier faengt eines ohne Handler.
    if tool_name != "read_config":
        raise AiActionValidationError(f"Kein Handler fÃ¼r Werkzeug: {tool_name}")

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
    import sys
    _mod = sys.modules.get("services.ai_action_service")
    _fn = getattr(_mod, "read_server_text", read_server_text) if _mod else read_server_text
    result = _fn(db, server_id=server.id, relative_path=path)
    content = str(result["content"])
    # Seit die Endungsliste weg ist, kann hier auch eine Binaerdatei landen â€”
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

    # Zwei Fragen, die frueher eine waren â€” und dass sie eine waren, war der
    # Grund, warum eine grosse Spielkonfiguration fuer die KI nur lesbar war:
    #
    # `editable`  â€” darf die Datei **ganz** ersetzt werden? Nur wenn das Modell
    #               sie ganz und unveraendert gesehen hat. Sonst wuerde der
    #               Vollersatz alles hinter dem Fenster loeschen bzw. echte
    #               Zugangsdaten durch den Platzhalter ersetzen.
    # `patchable` â€” darf **eine Stelle** darin ersetzt werden? Dafuer genuegt,
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
        "propose_config_patch â€” dabei bleibt alles Ungesehene unberuehrt."
    )
    return {
        "path": path,
        "revision": result["revision"] if patchable else None,
        # Von einer Binaerdatei geht nichts in den Kontext: der Salat kostet
        # Tokens und sagt dem Modell nichts, was es nicht schon aus `binary`
        # weiss.
        "content": "" if binaer else sicht,
        # Wo das Fenster liegt und wie gross die Datei ist â€” ohne diese beiden
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
