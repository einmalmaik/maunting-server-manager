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

def _mailbox_and_calendar_tool_definitions() -> list[dict]:
    """E-Mail- und Kalender-Werkzeuge (VerknÃ¼pfte PostfÃ¤cher und Kalender)."""
    return [
        _function(
            "email_search",
            "Sucht in den verknÃ¼pften PostfÃ¤chern des Benutzers nach E-Mails. "
            "Liefert Betreff, Absender, EmpfÃ¤nger, Datum und Nachrichten-ID.",
            {
                "query": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Suchbegriff fÃ¼r Betreff oder Inhalt.",
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
            "Liest Termine aus dem verknÃ¼pften Kalender des Benutzers.",
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
            "SchlÃ¤gt das Verfassen und Versenden einer E-Mail Ã¼ber ein verknÃ¼pftes Postfach vor. "
            "Erfordert zwingend eine BestÃ¤tigung des Benutzers vor dem tatsÃ¤chlichen Versand.",
            {
                "recipient": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "EmpfÃ¤nger-E-Mail-Adresse.",
                },
                "subject": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "Betreff der E-Mail.",
                },
                "body_text": {
                    "type": "string",
                    "maxLength": 8000,
                    "description": "VollstÃ¤ndiger Textinhalt der E-Mail.",
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
            "SchlÃ¤gt einen neuen Termin im verknÃ¼pften Kalender vor (kann mehrfach aufgerufen werden fÃ¼r mehrere Termine in einem Tagesplan; Standard-Dauer 1 Stunde wenn keine Endzeit genannt). "
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
                    "description": "Optionale Team-ID fÃ¼r Team-Termine (event_type=team).",
                },
                "server_id": {
                    "type": "integer",
                    "description": "Optionale Server-ID fÃ¼r Server-Wartungstermine (event_type=server).",
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
            "SchlÃ¤gt die Anpassung oder Verschiebung eines bestehenden Termins im Kalender vor (nur wenn ein Termin explizit geÃ¤ndert werden soll, fÃ¼r neue Termine propose_calendar_event_create nutzen). "
            "Erfordert die Freigabe des Benutzers.",
            {
                "event_id": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "ID oder UID des zu Ã¤ndernden Termins aus calendar_read.",
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
            "SchlÃ¤gt das LÃ¶schen eines Termins aus dem Kalender vor.",
            {
                "event_id": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "ID des zu lÃ¶schenden Termins.",
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
            "SchlÃ¤gt das Erstellen eines Panel-weiten Pop-ups / einer AnkÃ¼ndigung vor. "
            "Der Inhalt soll im sauberen Markdown-Format formuliert sein â€” menschlich, "
            "verstÃ¤ndlich und frei von kÃ¼nstlichen KI-Schablonen oder Gedankenstrich-Ketten. "
            "Erfordert zwingend die Freigabe des Benutzers Ã¼ber eine Vorschlagskarte.",
            {
                "title": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "PrÃ¤gnanter Titel des Pop-ups.",
                },
                "content_markdown": {
                    "type": "string",
                    "maxLength": 32000,
                    "description": "VollstÃ¤ndiger Textinhalt als Markdown.",
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
                    "description": "Optionaler Beschriftungstext fÃ¼r einen zusÃ¤tzlichen Aktions-Button (z. B. 'Mehr erfahren').",
                },
                "button_url": {
                    "type": ["string", "null"],
                    "maxLength": 2048,
                    "description": "Optionale Web-Adresse fÃ¼r den Aktions-Button (http:// oder https://).",
                },
                **_RATIONALE_SCHEMA,
            },
            ["title", "content_markdown", *_RATIONALE_REQUIRED],
        ),
    ]

def _notes_tool_definitions() -> list[dict]:
    """Notiz-Werkzeuge (PersÃ¶nliche und geteilte Notizen, Aufgaben und Checklisten)."""
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
                    "description": "Optionale Team-ID (0 = nur persÃ¶nliche Notizen).",
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
            "SchlÃ¤gt das Erstellen einer neuen Notiz, Checkliste oder Einkaufsliste vor. "
            "Inhalte sollen Ã¼bersichtlich und prÃ¤gnant formatiert werden (z. B. Markdown, Checklisten [ ] / [x], "
            "oder Einkaufslisten mit geschÃ¤tzten Richtpreisen und Gesamtsumme).",
            {
                "title": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "PrÃ¤gnanter Titel der Notiz (z. B. 'Einkaufsliste Edeka', 'Projekt-Todos').",
                },
                "content": {
                    "type": "string",
                    "maxLength": 32000,
                    "description": "VollstÃ¤ndiger Inhalt der Notiz (strukturiertes Markdown, Checklisten, Mengenangaben).",
                },
                "category": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "Kategorie: 'personal', 'shopping', 'todo', 'work', 'idea' oder 'meeting'.",
                },
                "color": {
                    "type": "string",
                    "maxLength": 32,
                    "description": "Farbakzent: 'primary' (blau), 'emerald' (grÃ¼n), 'amber' (gelb/orange), 'rose' (rot), 'purple' (lila), 'cyan'.",
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
            "SchlÃ¤gt die Bearbeitung oder ErgÃ¤nzung einer bestehenden Notiz vor.",
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
                    "description": "Pin-Status Ã¤ndern.",
                },
                "is_archived": {
                    "type": "boolean",
                    "description": "Archivierungsstatus Ã¤ndern.",
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
            "SchlÃ¤gt das LÃ¶schen einer Notiz vor.",
            {
                "note_id": {
                    "type": "string",
                    "maxLength": 64,
                    "description": "ID oder UID der zu lÃ¶schenden Notiz.",
                },
                **_RATIONALE_SCHEMA,
            },
            ["note_id", *_RATIONALE_REQUIRED],
        ),
        _function(
            "execute_server_action",
            "FÃ¼hrt eine Server-, Mod-, Backup-, Konfigurations- oder Verwaltungsaktion aus, "
            "fÃ¼r die kein direktes Schnellwerkzeug im aktuellen Aufrufsatz vorliegt (z. B. Ports abfragen, "
            "Mods suchen/installieren, Backup anlegen/wiederherstellen, Konfigurationen Ã¤ndern, Aufgaben planen). "
            "Gib die gewÃ¼nschte Anweisung als 'action' und optional 'server_id' an.",
            {
                "action": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "Die auszufÃ¼hrende Aktion oder Abfrage in natÃ¼rlicher Sprache.",
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
    werden. Ein `to`-Argument haette genau das eroeffnet â€” und waere aus dem
    Chat heraus mit einem Satz auszuloesen gewesen.

    Zurueck kommt, was der Benutzer zum Nachsehen braucht: ob es rausging, an
    welches Postfach (maskiert) und **welche Art** von Versandweg benutzt wurde.
    Bewusst nicht der SMTP-Host: das ist Betreiberkonfiguration, die ein Kunde
    im Panel nur mit `panel.settings.read` zu sehen bekaeme. Fuer die Diagnose
    genuegt "es lief ueber SMTP" â€” wo die Einstellungen stehen, weiss der
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

    # Auch die Testmail schreibt die KI selbst â€” der Betreiber hat
    # ausdruecklich verlangt, dass hier nichts Vorgefertigtes mehr steht. Der
    # Verfassungsschritt liegt aber nicht mehr hier, sondern im Arbeiter am
    # Ausgangskorb: dort steht er innerhalb einer Schranke und ueberlebt einen
    # Neustart. Was hier entsteht, ist der Rueckfall â€” und bei genau dieser Mail
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
            "Es ist nichts passiert, worueber zu berichten waere â€” die Mail "
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
            "der Weg dahinter â€” sag dem Benutzer, er soll jetzt nachsehen, auch "
            "im Spam-Ordner. Kommt nichts an, liegt es an der Einrichtung des "
            "Versands im Panel und nicht an dir."
        ),
    }
