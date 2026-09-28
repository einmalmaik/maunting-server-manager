import json
import uuid
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy.orm import Session

from models.ai_action_proposal import AiActionProposal
from models.ai_conversation import AiConversation
from models.user import User
from models.user_mailbox import UserMailbox
from models.user_calendar import UserCalendar
from services import ai_action_service, ai_proposal_service, role_service
from services.ai_action_errors import AiActionValidationError, AiActionStateError
from services.dis_client import DisClient


@pytest.fixture
def mailbox_user(db: Session, regular_user: User) -> User:
    role = role_service.create_role(
        db, name="mail_tester_role", description="Mail and Calendar", keys=["ai.mailbox.use", "ai.calendar.use"]
    )
    regular_user.role_id = role.id
    db.commit()
    db.refresh(regular_user)
    return regular_user


@pytest.fixture
def conversation(db: Session, mailbox_user: User) -> AiConversation:
    conv = AiConversation(id=str(uuid.uuid4()), user_id=mailbox_user.id, title="Test Conversation")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def test_email_search_tool_dispatches_cleanly(db: Session, mailbox_user: User):
    with patch("services.mailbox_service.MailboxService.search_messages") as mock_search:
        mock_search.return_value = [
            {"id": "msg-1", "subject": "Meeting Notes", "sender": "boss@example.com", "date": "2026-08-25"}
        ]
        res = ai_action_service._execute_global_read_tool(
            db,
            user=mailbox_user,
            tool_name="email_search",
            arguments={"query": "Meeting"},
        )
        assert res["count"] == 1
        assert res["messages"][0]["subject"] == "Meeting Notes"
        mock_search.assert_called_once()


def test_email_read_tool_dispatches_cleanly(db: Session, mailbox_user: User):
    with patch("services.mailbox_service.MailboxService.read_message") as mock_read:
        mock_read.return_value = {
            "id": "msg-1",
            "subject": "Meeting Notes",
            "sender": "boss@example.com",
            "body_text": "Here are the notes for the meeting.",
        }
        res = ai_action_service._execute_global_read_tool(
            db,
            user=mailbox_user,
            tool_name="email_read",
            arguments={"message_id": "msg-1"},
        )
        assert res["id"] == "msg-1"
        assert "notes for the meeting" in res["body_text"]
        mock_read.assert_called_once()


def test_calendar_read_tool_dispatches_cleanly(db: Session, mailbox_user: User):
    with patch("services.calendar_service.CalendarService.get_events") as mock_events:
        mock_events.return_value = [
            {"id": "evt-1", "title": "Team Sync", "start": "2026-08-26T10:00:00Z", "end": "2026-08-26T11:00:00Z"}
        ]
        res = ai_action_service._execute_global_read_tool(
            db,
            user=mailbox_user,
            tool_name="calendar_read",
            arguments={"start_date": "2026-08-26"},
        )
        assert res["count"] == 1
        assert res["events"][0]["title"] == "Team Sync"
        mock_events.assert_called_once()


@pytest.mark.parametrize(
    ("werkzeug", "argumente", "gepatchter_dienst"),
    [
        ("email_search", {"query": "Meeting"}, "services.mailbox_service.MailboxService.search_messages"),
        ("email_read", {"message_id": "msg-1"}, "services.mailbox_service.MailboxService.read_message"),
        ("calendar_read", {"start_date": "2026-08-26"}, "services.calendar_service.CalendarService.get_events"),
    ],
)
def test_lesewerkzeuge_ohne_recht_lesen_nichts(
    db: Session, regular_user: User, werkzeug: str, argumente: dict, gepatchter_dienst: str
):
    """Ohne `ai.mailbox.use` / `ai.calendar.use` laeuft der Handler gar nicht.

    Diese drei hatten als einzige der persoenlichen Lesewerkzeuge keine
    Rechtepruefung — ihr Schluessel stand allein als `angebot` in
    `ai_tool_registry`, und das ist ausdruecklich nur ein Katalogfilter
    (`angebotene_werkzeuge`: „eine Bitte, keine Zusage"). Der Nachbar
    `notes_read` prueft seit jeher.

    Der Katalog allein traegt nicht: ein Modell kann einen Namen aus dem
    Verlauf abschreiben, `execute_server_action` loest Namen semantisch auf,
    und die Sprachsitzungen reichten den Namen des Modells weiter. Geprueft
    wird deshalb hier — und der Dienst darf nicht einmal gefragt worden sein.
    """
    with patch(gepatchter_dienst) as mock_dienst:
        with pytest.raises(AiActionValidationError):
            ai_action_service._execute_global_read_tool(
                db, user=regular_user, tool_name=werkzeug, arguments=argumente
            )
    mock_dienst.assert_not_called()


def test_email_search_limit_bleibt_im_rahmen(db: Session, mailbox_user: User):
    """Ein `limit` aus der Modellausgabe ist gedeckelt und getippt.

    Vorher stand hier ein blankes ``int(arguments.get("limit", 10))``. Das war
    zweimal falsch: ``limit: 1000000`` war eine Postfachabfrage ohne
    Obergrenze, und ``limit: "alle"`` warf einen ``ValueError`` — der faellt
    aus dem Zweig, den `_werkzeug_ausfuehren` fuer Formfehler abfaengt, und
    riss damit den ganzen Lauf ab statt einer Runde.
    """
    with patch("services.mailbox_service.MailboxService.search_messages") as mock_search:
        mock_search.return_value = []
        with pytest.raises(AiActionValidationError):
            ai_action_service._execute_global_read_tool(
                db, user=mailbox_user, tool_name="email_search",
                arguments={"query": "x", "limit": 1_000_000},
            )
        with pytest.raises(AiActionValidationError):
            ai_action_service._execute_global_read_tool(
                db, user=mailbox_user, tool_name="email_search",
                arguments={"query": "x", "limit": "alle"},
            )
    mock_search.assert_not_called()


def test_propose_email_send_lifecycle(db: Session, mailbox_user: User, conversation: AiConversation):
    # 1. Create proposal
    proposal = ai_proposal_service.create_proposal(
        db,
        user=mailbox_user,
        conversation=conversation,
        tool_name="propose_email_send",
        arguments={
            "recipient": "client@example.com",
            "subject": "Angebot Server-Upgrade",
            "body_text": "Sehr geehrte Damen und Herren,\nanbei unser Angebot.",
            "reason": "Kunde hat per Mail um Angebot gebeten",
            "expected_effect": "E-Mail wird verschickt",
        },
        correlation_id=str(uuid.uuid4()),
    )
    assert proposal.status == "proposed"
    assert proposal.requires_confirmation is True
    assert proposal.tool_name == "propose_email_send"

    preview = json.loads(proposal.preview_json)
    assert preview["recipient"] == "client@example.com"
    assert preview["subject"] == "Angebot Server-Upgrade"

    # 2. Confirm proposal
    _, token = ai_proposal_service.confirm_proposal(db, proposal_id=proposal.id, user=mailbox_user)
    assert token is not None

    # 3. Execute proposal
    with patch("services.mailbox_service.MailboxService.send_email") as mock_send:
        mock_send.return_value = {"sent": True, "message_id": "out-123"}
        exec_prop, result = ai_proposal_service.execute_proposal(
            db,
            proposal_id=proposal.id,
            user=mailbox_user,
            confirmation_token=token,
        )
        assert exec_prop.status == "succeeded"
        assert result["sent"] is True
        mock_send.assert_called_once()


def test_propose_calendar_event_lifecycle(db: Session, mailbox_user: User, conversation: AiConversation):
    # 1. Create proposal for event create
    proposal = ai_proposal_service.create_proposal(
        db,
        user=mailbox_user,
        conversation=conversation,
        tool_name="propose_calendar_event_create",
        arguments={
            "title": "Server Wartung",
            "start_time": "2026-08-27T02:00:00",
            "end_time": "2026-08-27T04:00:00",
            "description": "Kernel-Patching auf Node 1",
            "location": "Rechenzentrum",
            "reason": "Geplante Wartungsarbeiten",
            "expected_effect": "Termin wird im Kalender blockiert",
        },
        correlation_id=str(uuid.uuid4()),
    )
    assert proposal.status == "proposed"

    # 2. Confirm and execute
    _, token = ai_proposal_service.confirm_proposal(db, proposal_id=proposal.id, user=mailbox_user)
    with patch("services.calendar_service.CalendarService.create_event") as mock_create:
        mock_create.return_value = {"created": True, "event_id": "evt-wartung-1"}
        exec_prop, result = ai_proposal_service.execute_proposal(
            db,
            proposal_id=proposal.id,
            user=mailbox_user,
            confirmation_token=token,
        )
        assert exec_prop.status == "succeeded"
        assert result["created"] is True
        mock_create.assert_called_once()


def test_propose_calendar_event_update_lifecycle(db: Session, mailbox_user: User, conversation: AiConversation):
    # 1. Create proposal
    proposal = ai_proposal_service.create_proposal(
        db,
        user=mailbox_user,
        conversation=conversation,
        tool_name="propose_calendar_event_update",
        arguments={
            "event_id": "evt-wartung-1",
            "title": "Verschobene Server-Wartung",
            "start_time": "2026-08-27 16:00",
            "end_time": "2026-08-27 17:00",
            "reason": "Wartungsfenster verschoben",
            "expected_effect": "Termin wird im Kalender aktualisiert",
        },
        correlation_id=str(uuid.uuid4()),
    )
    assert proposal.tool_name == "propose_calendar_event_update"
    assert proposal.status == "proposed"

    # 2. Confirm and execute
    _, token = ai_proposal_service.confirm_proposal(db, proposal_id=proposal.id, user=mailbox_user)
    with patch("services.calendar_service.CalendarService.update_event") as mock_update:
        mock_update.return_value = {"updated": True, "event_id": "evt-wartung-1"}
        exec_prop, result = ai_proposal_service.execute_proposal(
            db,
            proposal_id=proposal.id,
            user=mailbox_user,
            confirmation_token=token,
        )
        assert exec_prop.status == "succeeded"
        assert result["updated"] is True
        mock_update.assert_called_once()


def test_email_send_without_permission_fails(db: Session, regular_user: User):
    conv = AiConversation(id=str(uuid.uuid4()), user_id=regular_user.id, title="No Perm Conv")
    db.add(conv)
    db.commit()
    db.refresh(conv)

    # regular_user does NOT have ai.mailbox.use permission
    with pytest.raises(AiActionValidationError, match="AI-Aktion ist nicht erlaubt"):
        ai_proposal_service.create_proposal(
            db,
            user=regular_user,
            conversation=conv,
            tool_name="propose_email_send",
            arguments={
                "recipient": "client@example.com",
                "subject": "Test",
                "body_text": "Text",
                "reason": "Test",
                "expected_effect": "Test",
            },
            correlation_id=str(uuid.uuid4()),
        )


def test_mail_and_calendar_tools_available_in_gehirn_mode(db: Session, mailbox_user: User):
    from services.ai_action_service import angebotene_werkzeuge, provider_tool_definitions
    from services.ai_tool_registry import GEHIRN_TOOLS, MAIL_TOOLS, CALENDAR_TOOLS

    # 1. Check GEHIRN_TOOLS includes mail & calendar tools
    assert MAIL_TOOLS <= GEHIRN_TOOLS
    assert CALENDAR_TOOLS <= GEHIRN_TOOLS

    # 2. Check angebotene_werkzeuge for user with mailbox permission
    erlaubt = angebotene_werkzeuge(db, mailbox_user)
    assert "propose_email_send" in erlaubt
    assert "email_search" in erlaubt
    assert "email_read" in erlaubt
    assert "calendar_read" in erlaubt
    assert "propose_calendar_event_create" in erlaubt
    assert "propose_calendar_event_update" in erlaubt
    assert "propose_calendar_event_delete" in erlaubt

    # 3. Check provider_tool_definitions includes schemas
    names = {t["function"]["name"] for t in provider_tool_definitions()}
    assert "propose_email_send" in names
    assert "email_search" in names
    assert "calendar_read" in names
    assert "propose_calendar_event_update" in names


def test_tasks_include_email_and_calendar_read_tools():
    from services.ai_tool_registry import AUFGABEN_LESEN

    assert "email_search" in AUFGABEN_LESEN
    assert "email_read" in AUFGABEN_LESEN
    assert "calendar_read" in AUFGABEN_LESEN


# ── Serientermine ueber die KI ────────────────────────────────────────────


def test_wiederholung_steht_in_beiden_terminwerkzeugen():
    """Kein eigenes Werkzeug, sondern ein Feld an den beiden bestehenden.

    Ein neues Werkzeug haette in `realtime_static_extra` und bei Gemini Live
    einzeln nachgetragen werden muessen — und genau dort faellt so etwas erst
    auf, wenn jemand es per Sprache versucht.
    """
    from services.ai_action_service import provider_tool_definitions

    schemata = {
        t["function"]["name"]: t["function"]["parameters"]["properties"]
        for t in provider_tool_definitions()
    }
    for werkzeug in ("propose_calendar_event_create", "propose_calendar_event_update"):
        assert "recurrence" in schemata[werkzeug], werkzeug
        takt = schemata[werkzeug]["recurrence"]["properties"]["takt"]
        assert takt["enum"] == ["taeglich", "woechentlich", "monatlich", "jaehrlich"]

    # Und es ist kein zusaetzliches Werkzeug dazugekommen.
    namen = {t["function"]["name"] for t in provider_tool_definitions()}
    assert not any("recurrence" in n or "serie" in n for n in namen)


def test_der_sprachweg_kennt_das_terminwerkzeug_weiterhin():
    """Die Schranke, die nur im Chatweg steht, fehlt im Sprachweg.

    Weil die Wiederholung ein Feld am bestehenden Werkzeug ist, gilt sie im
    Realtime-Weg automatisch mit — dieser Test haelt fest, dass das Werkzeug
    dort ueberhaupt gefuehrt wird, damit die Annahme nicht still verfaellt.
    """
    import inspect

    from services.ai_voice import realtime_session

    quelle = inspect.getsource(realtime_session)
    assert '"propose_calendar_event_create"' in quelle
    assert '"propose_calendar_event_update"' in quelle


def _kalender_nutzlast(rest: dict):
    from services.ai_proposals.personal_proposals import _calendar_event_create_payload

    return _calendar_event_create_payload(None, None, rest)


def test_vorschlag_traegt_die_regel_verschluesselbar_und_die_kurzform_lesbar():
    """Zwei Orte, zwei Zwecke.

    `payload` wird verschluesselt abgelegt und traegt die Regel selbst;
    `preview_json` liegt im Klartext und traegt die Kurzform — wer nicht
    sieht, dass er eine **jaehrliche** Wiederholung freigibt, gibt etwas
    anderes frei, als er denkt.
    """
    payload, preview = _kalender_nutzlast(
        {
            "title": "Geburtstag Lisa",
            "start_time": "2026-03-14 00:00",
            "end_time": "2026-03-15 00:00",
            "recurrence": {"takt": "jaehrlich"},
        }
    )
    assert json.loads(payload["recurrence"])["rrule"] == "FREQ=YEARLY"
    assert preview["recurrence"] == "jährlich, ohne Ende"


def test_vorschlag_ohne_wiederholung_speichert_trotzdem_ein_dokument():
    payload, preview = _kalender_nutzlast(
        {"title": "Zahnarzt", "start_time": "2026-03-14 10:00", "end_time": "2026-03-14 11:00"}
    )
    assert json.loads(payload["recurrence"]) == {"rrule": None}
    assert preview["recurrence"] == "einmalig"


@pytest.mark.parametrize(
    "eingabe,erwartet",
    [
        ({"takt": "woechentlich", "wochentage": ["MO", "DO"]}, "FREQ=WEEKLY;BYDAY=MO,TH"),
        ({"takt": "woechentlich", "wochentage": ["MO", "TH"]}, "FREQ=WEEKLY;BYDAY=MO,TH"),
        ({"takt": "jährlich"}, "FREQ=YEARLY"),
        ({"takt": "MONTHLY", "anzahl": 12}, "FREQ=MONTHLY;COUNT=12"),
    ],
)
def test_die_ki_darf_deutsch_oder_englisch_schreiben(eingabe, erwartet):
    """Nachsichtig lesen, streng speichern.

    Das Schema nennt die deutschen Kuerzel; ein Modell antwortet aber auch mal
    englisch. Ein Formfehler soll eine Runde kosten, nicht die Antwort.
    """
    payload, _ = _kalender_nutzlast(
        {
            "title": "T",
            "start_time": "2026-03-14 10:00",
            "end_time": "2026-03-14 11:00",
            "recurrence": eingabe,
        }
    )
    assert json.loads(payload["recurrence"])["rrule"] == erwartet


@pytest.mark.parametrize(
    "unbrauchbar",
    [
        {"takt": "alle drei Tage"},
        {"takt": "monatlich", "wochentage": ["MO"]},
        {"takt": "taeglich", "bis": "2026-01-10", "anzahl": 3},
        {"intervall": 2},
        "jaehrlich",
    ],
)
def test_unbrauchbare_wiederholung_wird_abgewiesen_statt_verworfen(unbrauchbar):
    """Abgewiesen, nicht stillschweigend fallengelassen.

    Eine verworfene Wiederholung ergaebe einen Einzeltermin unter dem Namen
    einer Serie — und das faellt erst im naechsten Jahr auf, wenn der
    Geburtstag ausbleibt.
    """
    with pytest.raises(AiActionValidationError):
        _kalender_nutzlast(
            {
                "title": "T",
                "start_time": "2026-03-14 10:00",
                "end_time": "2026-03-14 11:00",
                "recurrence": unbrauchbar,
            }
        )


def test_aenderung_ohne_recurrence_laesst_die_serie_in_ruhe():
    """Fehlendes Feld heisst 'nicht anfassen', ausdrueckliches null heisst 'aufloesen'."""
    from services.ai_proposals.personal_proposals import _calendar_event_update_payload

    unberuehrt, vorschau = _calendar_event_update_payload(None, None, {"event_id": "x", "title": "Neu"})
    assert unberuehrt["recurrence"] is None
    assert vorschau["recurrence"] is None

    aufgeloest, vorschau2 = _calendar_event_update_payload(
        None, None, {"event_id": "x", "recurrence": {"takt": None}}
    )
    assert json.loads(aufgeloest["recurrence"]) == {"rrule": None}
    assert vorschau2["recurrence"] == "einmalig"

