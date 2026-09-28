from __future__ import annotations

import logging
from sqlalchemy.orm import Session
from models import User
from services import permission_service
from services.ai_action_errors import AiActionValidationError
from services.ai_tool_registry import (
    GLOBAL_READ_TOOLS,
    READ_TOOLS,
    WERKZEUGE,
    angebotsrechte,
)
from services.ai_tools.base import (
    _function,
    _RATIONALE_SCHEMA,
    _RATIONALE_REQUIRED,
    MAX_TESTMAILS_JE_STUNDE,
)

logger = logging.getLogger(__name__)

# Lokaler Zaehler fuer Test-E-Mails je Benutzer und Stunde
_TESTMAILS: dict[int, list[float]] = {}

# Wiederholung als **Feld** an den beiden bestehenden Terminwerkzeugen statt
# als eigenes Werkzeug. Drei Gruende, in der Reihenfolge ihres Gewichts:
#
# 1. Ein Termin mit Wiederholung ist derselbe Vorgang mit einem Feld mehr,
#    nicht eine zweite Handlung. Zwei Werkzeuge haetten das Modell vor eine
#    Wahl gestellt, die es nicht zu treffen hat — und `propose_task_set` wie
#    `propose_popup_set` machen es aus demselben Grund schon so.
# 2. `propose_calendar_event_create` steht fest im Sprachweg
#    (`realtime_session.realtime_static_extra` und `HOTSET`). Ein neues
#    Werkzeug haette dort und bei Gemini Live einzeln nachgetragen werden
#    muessen; eine Faehigkeit, die nur im Chatweg steht, fehlt im Sprachweg.
# 3. Der Katalog geht in **jeder** Runde mit. Dieses Feld kostet rund 350
#    Zeichen, ein eigenes Werkzeug rund 900 (test_ai_tool_handler_contract).
#
# Die Wochentage stehen deutsch im Schema, werden aber in beiden Sprachen
# gelesen (`serie_aus_werkzeug`): MO, FR und SA meinen hier wie dort denselben
# Tag, es gibt also nichts zu verwechseln.
# Bewusst karg beschrieben. Beim Bau lag der Desktop-Katalog bei 91.908 von
# 92.000 Zeichen — 92 Zeichen Luft, weil eine ausfuehrliche Fassung dieses
# Schemas 1.664 Zeichen kostete (832 je Werkzeug, es steht an zweien).
#
# Was das Modell **wissen** muss, steht deshalb in `ai_prompt`: dass
# Wiederkehrendes eine Wiederholung ist und nicht zwanzig Einzeltermine, dass
# `wochentage` nur zum woechentlichen Takt gehoert, dass `bis` und `anzahl`
# einander ausschliessen. Der Systemprompt wird zwischengespeichert, der
# Katalog geht in **jeder** Runde ungecacht mit — dieselbe Auskunft ist dort
# um ein Vielfaches billiger.
#
# Die Feldnamen tragen den Rest: `takt`, `intervall`, `wochentage`, `bis`,
# `anzahl` sagen auf Deutsch, was sie meinen. Gelesen werden die Wochentage in
# beiden Sprachen (`serie_aus_werkzeug`); MO, FR und SA meinen hier wie dort
# denselben Tag.
_WIEDERHOLUNG_SCHEMA = {
    "type": "object",
    "description": "Optionale Wiederholung. Ohne Angabe einmalig.",
    "properties": {
        "takt": {
            "type": "string",
            "enum": ["taeglich", "woechentlich", "monatlich", "jaehrlich"],
        },
        "intervall": {"type": "integer", "minimum": 1},
        "wochentage": {
            "type": "array",
            "items": {"type": "string", "enum": ["MO", "DI", "MI", "DO", "FR", "SA", "SO"]},
        },
        "bis": {"type": "string", "description": "YYYY-MM-DD"},
        "anzahl": {"type": "integer", "minimum": 1},
    },
}

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
                "recurrence": _WIEDERHOLUNG_SCHEMA,
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
                "recurrence": _WIEDERHOLUNG_SCHEMA,
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
            "popups_read",
            "Listet die Panel-Pop-ups und Ankündigungen auf: Kennung, Titel, Text, "
            "Zeitfenster und ob sie aktiv sind. Der Schritt vor jeder Änderung: "
            "die `popup_id` für propose_popup_set kommt von hier.",
            {
                "only_active": {
                    "type": "boolean",
                    "description": "Nur aktiv geschaltete Pop-ups (Standard: alle).",
                },
            },
            [],
        ),
        _function(
            "propose_popup_set",
            "Schlägt ein Panel-weites Pop-up / eine Ankündigung vor: ohne `popup_id` "
            "als neues, mit `popup_id` aus popups_read als Änderung des bestehenden. "
            "Der Inhalt soll im sauberen Markdown-Format formuliert sein, menschlich "
            "und frei von künstlichen KI-Schablonen oder Gedankenstrich-Ketten. "
            "Erfordert zwingend die Freigabe des Benutzers über eine Vorschlagskarte.",
            {
                "popup_id": {
                    "type": "integer",
                    "description": "Kennung aus popups_read. Weglassen legt ein neues Pop-up an.",
                },
                "title": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "Prägnanter Titel des Pop-ups.",
                },
                "content_markdown": {
                    "type": "string",
                    "maxLength": 32000,
                    "description": "Vollständiger Textinhalt als Markdown. Beim Ändern der ganze neue Text, kein Ausschnitt.",
                },
                "is_active": {
                    "type": "boolean",
                    "description": "Ob das Pop-up aktiv geschaltet sein soll (Standard beim Anlegen: true).",
                },
                "start_at": {
                    "type": ["string", "null"],
                    "maxLength": 32,
                    "description": "Optionales Startdatum (ISO-8601, z. B. 2026-08-26T12:00:00Z). null entfernt es.",
                },
                "end_at": {
                    "type": ["string", "null"],
                    "maxLength": 32,
                    "description": "Optionales Enddatum (ISO-8601). null entfernt es.",
                },
                "button_text": {
                    "type": ["string", "null"],
                    "maxLength": 100,
                    "description": "Optionale Beschriftung eines zusätzlichen Aktions-Buttons (z. B. 'Mehr erfahren'). null entfernt ihn.",
                },
                "button_url": {
                    "type": ["string", "null"],
                    "maxLength": 2048,
                    "description": "Optionale Web-Adresse für den Aktions-Button (http:// oder https://). null entfernt sie.",
                },
                **_RATIONALE_SCHEMA,
            },
            [*_RATIONALE_REQUIRED],
        ),
    ]

def _notes_tool_definitions() -> list[dict]:
    """Notiz-Werkzeuge (Persönliche und geteilte Notizen, Aufgaben und Checklisten)."""
    return [
        _function(
            "notes_read",
            "Liest oder durchsucht die Notizen des Benutzers. Kann nach Suchbegriff, Kategorie oder Team filtern.",
            {
                "query": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Optionaler Suchbegriff im Titel oder Inhalt der Notiz.",
                },
                "category": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Optionaler Kategorie-Filter (z. B. shopping, todo, work, idea, meeting, personal).",
                },
                "team_id": {
                    "type": "integer",
                    "description": "Optionale Team-ID (0 = nur persönliche Notizen).",
                },
                "is_pinned": {
                    "type": "boolean",
                    "description": "Optional: Nur angepinnte Notizen filtern.",
                },
            },
            [],
        ),
        _function(
            "propose_note_create",
            "Schlägt das Erstellen einer neuen Notiz, Checkliste oder Einkaufsliste vor. "
            "Inhalte sollen übersichtlich und prägnant formatiert werden (z. B. Markdown, Checklisten [ ] / [x], "
            "oder Einkaufslisten mit geschätzten Richtpreisen und Gesamtsumme).",
            {
                "title": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "Prägnanter Titel der Notiz (z. B. 'Einkaufsliste Edeka', 'Projekt-Todos').",
                },
                "content": {
                    "type": "string",
                    "maxLength": 32000,
                    "description": "Vollständiger Inhalt der Notiz (strukturiertes Markdown, Checklisten, Mengenangaben).",
                },
                "category": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Kategorie: 'personal', 'shopping', 'todo', 'work', 'idea' oder 'meeting'.",
                },
                "color": {
                    "type": "string",
                    "maxLength": 32,
                    "description": "Farbakzent: 'primary' (blau), 'emerald' (grün), 'amber' (gelb/orange), 'rose' (rot), 'purple' (lila), 'cyan'.",
                },
                "is_pinned": {
                    "type": "boolean",
                    "description": "Ob die Notiz oben angepinnt werden soll (Standard: false).",
                },
                "note_type": {
                    "type": "string",
                    "maxLength": 32,
                    "description": "'personal' (privat) oder 'team' (im Team geteilt).",
                },
                "team_id": {
                    "type": "integer",
                    "description": "Optionale Team-ID, falls note_type 'team' ist.",
                },
                **_RATIONALE_SCHEMA,
            },
            ["title", *_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_note_update",
            "Schlägt die Bearbeitung oder Ergänzung einer bestehenden Notiz vor.",
            {
                "note_id": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "ID oder UID der zu bearbeitenden Notiz.",
                },
                "title": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "Optionaler neuer Titel.",
                },
                "content": {
                    "type": "string",
                    "maxLength": 32000,
                    "description": "Optionaler aktualisierter Inhalt.",
                },
                "category": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Optionale Kategorie.",
                },
                "color": {
                    "type": "string",
                    "maxLength": 32,
                    "description": "Optionaler Farbakzent.",
                },
                "is_pinned": {
                    "type": "boolean",
                    "description": "Pin-Status ändern.",
                },
                "is_archived": {
                    "type": "boolean",
                    "description": "Archivierungsstatus ändern.",
                },
                "note_type": {
                    "type": "string",
                    "maxLength": 32,
                    "description": "'personal' oder 'team'.",
                },
                "team_id": {
                    "type": "integer",
                    "description": "Optionale Team-ID.",
                },
                **_RATIONALE_SCHEMA,
            },
            ["note_id", *_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_note_delete",
            "Schlägt das Löschen einer Notiz vor.",
            {
                "note_id": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "ID oder UID der zu löschenden Notiz.",
                },
                **_RATIONALE_SCHEMA,
            },
            ["note_id", *_RATIONALE_REQUIRED],
        ),
        _function(
            "execute_server_action",
            "Führt eine Server-, Mod-, Backup-, Konfigurations- oder Verwaltungsaktion aus, "
            "für die kein direktes Schnellwerkzeug im aktuellen Aufrufsatz vorliegt (z. B. Ports abfragen, "
            "Mods suchen/installieren, Backup anlegen/wiederherstellen, Konfigurationen ändern, Aufgaben planen). "
            "Gib die gewünschte Anweisung als 'action' und optional 'server_id' an.",
            {
                "action": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "Die auszuführende Aktion oder Abfrage in natürlicher Sprache.",
                },
                "server_id": {
                    "type": "integer",
                    "description": "Optionale ID des betroffenen Servers.",
                },
                "tool_name": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Optionaler expliziter Werkzeugname.",
                },
                "parameters": {
                    "type": "object",
                    "description": "Optionale strukturierte Zusatzparameter.",
                },
            },
            ["action"],
        ),
    ]

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
