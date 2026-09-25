from __future__ import annotations

from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session

from models.user import User
from services.ai_voice.voice_dispatcher import dispatch_voice_action
from services.ai_voice import realtime_session
from services.ai_tool_registry import WERKZEUGE


def _ohne_karte():
    """Die Frage nach der Zustimmung, beantwortet wie im autonomen Modus."""
    return patch("services.ai_voice.interactions.freigabe_einholen", return_value=None)


def test_execute_server_action_is_registered_in_registry():
    assert "execute_server_action" in WERKZEUGE
    assert WERKZEUGE["execute_server_action"].art == "global_read"


def test_dispatch_voice_action_empty_input():
    wert, fehler, anzeige, vorschlaege = dispatch_voice_action(
        user_id=1,
        arguments={},
    )
    assert fehler == "Keine Aktion angegeben"
    assert anzeige.get("failed") is True
    assert vorschlaege == []


def test_dispatch_voice_action_routes_read_tool(db: Session, regular_user: User):
    # Mit autonomem Modus: keine Karte davor (`_ohne_karte`).
    with patch("services.ai_voice.voice_dispatcher.ai_action_service.angebotene_werkzeuge") as mock_angebot, _ohne_karte():
        mock_angebot.return_value = frozenset({"read_server_ports", "list_my_servers"})
        with patch("services.ai_stream.read_tools._werkzeug_ausfuehren") as mock_exec:
            mock_exec.return_value = ({"ports": [2456, 2457]}, None)

            wert, fehler, anzeige, vorschlaege = dispatch_voice_action(
                user_id=regular_user.id,
                arguments={"action": "Welche Ports nutzt Server 3?", "server_id": 3},
                conversation_id="conv-123",
            )

            assert fehler is None
            assert wert.get("executed_tool") == "read_server_ports"
            assert wert.get("data") == {"ports": [2456, 2457]}
            assert vorschlaege == []


def test_ohne_autonomie_fragt_auch_der_umweg_erst(db: Session, regular_user: User):
    """Ohne autonomen Modus liest `execute_server_action` nichts ohne Ja.

    Vorgabe des Betreibers vom 23.09.2026: „autonome Modus aus heißt ALLES
    muss bestätigt werden". Der Umweg über den Dispatcher erreicht jedes
    Werkzeug des Benutzers. Liefe er an der Frage vorbei, wäre die Regel für
    den geraden Weg eine Attrappe. `regular_user` hat keine Freigabe, also
    entsteht eine Karte, und das Werkzeug selbst läuft nicht.
    """
    with patch("services.ai_voice.voice_dispatcher.ai_action_service.angebotene_werkzeuge") as mock_angebot:
        mock_angebot.return_value = frozenset({"read_server_ports", "list_my_servers"})
        with patch("services.ai_stream.write_tools._persist_write_proposals") as mock_persist, \
                patch("services.ai_stream.read_tools._werkzeug_ausfuehren") as mock_exec:
            mock_persist.return_value = [{
                "id": "prop-lesen", "tool_name": "read_server_ports",
                "status": "proposed", "autonomous": False,
            }]

            wert, fehler, anzeige, vorschlaege = dispatch_voice_action(
                user_id=regular_user.id,
                arguments={"action": "Welche Ports nutzt Server 3?", "server_id": 3},
                conversation_id="conv-123",
            )

    assert fehler is None
    assert wert["executed_tool"] == "read_server_ports"
    assert wert["status"] == "needs_confirmation"
    assert "voice_resolve_latest_proposal" in wert["hinweis"]
    assert [v["id"] for v in vorschlaege] == ["prop-lesen"]
    mock_exec.assert_not_called()


def test_im_chat_fragt_der_umweg_kein_zweites_mal(db: Session, regular_user: User):
    """Ein Chatlauf hat für `execute_server_action` schon gefragt.

    Ohne Freigabe stand der Aufruf dort auf einer Karte (`ai_stream.engine`),
    und nach dem Klick kommt er hier an. Bis zum Review vom 23.09.2026 legte
    der Dispatcher dann eine zweite Karte an, und der bestätigte Aufruf lief
    nie. `regular_user` hat keine Freigabe.
    """
    from services.ai_tools.server_tools import execute_read_tool

    with patch("services.ai_voice.voice_dispatcher.ai_action_service.angebotene_werkzeuge") as mock_angebot:
        mock_angebot.return_value = frozenset({"read_server_ports", "list_my_servers"})
        with patch(
            "services.ai_stream.write_tools._persist_write_proposals",
            side_effect=AssertionError("eine zweite Karte"),
        ), patch("services.ai_stream.read_tools._werkzeug_ausfuehren") as mock_exec:
            mock_exec.return_value = ({"ports": [2456]}, None)

            wert = execute_read_tool(
                db, user=regular_user, tool_name="execute_server_action",
                arguments={"action": "Welche Ports nutzt Server 3?", "server_id": 3},
            )

    assert wert["executed_tool"] == "read_server_ports"
    assert wert["data"] == {"ports": [2456]}


def test_im_chat_fragt_auch_vergessen_kein_zweites_mal(db: Session, regular_user: User):
    """Auch Vergessen läuft nach der ersten Karte ohne zweite.

    Vom 23. bis 25.09.2026 fragte der Umweg hier trotzdem, weil Vergessen
    auch im autonomen Modus eine Karte verlangte. Seitdem gehört es zu den
    eigenen Daten (`EIGENE_DATEN_LOESCHEN`), und kein Lesewerkzeug verlangt
    den Klick mehr über die erste Karte hinaus (`verlangt_klick`).
    """
    from services.ai_tools.server_tools import execute_read_tool

    with patch("services.ai_voice.voice_dispatcher.ai_action_service.angebotene_werkzeuge") as mock_angebot:
        mock_angebot.return_value = frozenset({"forget_memory", "list_my_servers"})
        with patch(
            "services.ai_stream.write_tools._persist_write_proposals",
            side_effect=AssertionError("eine zweite Karte"),
        ), patch("services.ai_stream.read_tools._werkzeug_ausfuehren") as mock_exec:
            mock_exec.return_value = ({"forgotten": ["urlaub"]}, None)

            wert = execute_read_tool(
                db, user=regular_user, tool_name="execute_server_action",
                arguments={"tool_name": "forget_memory", "parameters": {"key": "urlaub"}},
            )

    assert wert["executed_tool"] == "forget_memory"
    assert wert["data"] == {"forgotten": ["urlaub"]}
    mock_exec.assert_called_once()


def test_die_suche_allein_schreibt_nie(db: Session, regular_user: User):
    """Eine gefundene Schreibaktion kommt als Auswahl zurück, nicht als Karte.

    Die Suche nach Textähnlichkeit trifft oft daneben. Beim Betreibertest vom
    25.09.2026 wurde aus "Rolle" das Löschen eines Servers; im autonomen Modus
    hätte ein anderer Fehlgriff sofort gehandelt. Geschrieben wird erst, wenn
    das Modell den Namen aus der Liste nennt.
    """
    with patch("services.ai_voice.voice_dispatcher.ai_action_service.angebotene_werkzeuge") as mock_angebot:
        mock_angebot.return_value = frozenset({"propose_backup", "list_my_servers"})
        with patch(
            "services.ai_stream.write_tools._persist_write_proposals",
            side_effect=AssertionError("ein Vorschlag aus der Suche allein"),
        ):
            wert, fehler, anzeige, vorschlaege = dispatch_voice_action(
                user_id=regular_user.id,
                arguments={"action": "Mach ein Backup von Server 2", "server_id": 2},
                conversation_id="conv-456",
            )

    assert fehler is None
    assert wert["status"] == "choose_tool"
    assert [k["tool_name"] for k in wert["candidates"]] == ["propose_backup"]
    assert wert["candidates"][0]["description"]
    assert vorschlaege == []


def test_dispatch_voice_action_routes_write_tool_as_proposal(db: Session, regular_user: User):
    """Mit genanntem Namen wird aus der Schreibaktion eine Karte."""
    with patch("services.ai_voice.voice_dispatcher.ai_action_service.angebotene_werkzeuge") as mock_angebot:
        mock_angebot.return_value = frozenset({"propose_backup", "list_my_servers"})
        with patch("services.ai_stream.write_tools._persist_write_proposals") as mock_persist:
            mock_persist.return_value = [{"id": "prop-1", "tool_name": "propose_backup", "status": "proposed"}]

            wert, fehler, anzeige, vorschlaege = dispatch_voice_action(
                user_id=regular_user.id,
                arguments={
                    "action": "Mach ein Backup von Server 2", "server_id": 2,
                    "tool_name": "propose_backup",
                },
                conversation_id="conv-456",
            )

            assert fehler is None
            assert wert.get("executed_tool") == "propose_backup"
            assert wert.get("status") == "proposal_created"
            assert len(vorschlaege) == 1
            assert vorschlaege[0]["id"] == "prop-1"


def test_ein_genannter_name_wird_nie_ersetzt(db: Session, regular_user: User):
    """Ein Name, den es hier nicht gibt, fällt nicht auf die Suche zurück.

    Bis zum 25.09.2026 suchte der Dispatcher dann über ``action`` weiter —
    und fand irgendein anderes Werkzeug.
    """
    with patch("services.ai_voice.voice_dispatcher.ai_action_service.angebotene_werkzeuge") as mock_angebot:
        mock_angebot.return_value = frozenset({"read_server_ports", "list_my_servers"})
        with patch("services.ai_stream.read_tools._werkzeug_ausfuehren") as mock_exec:
            wert, fehler, _, vorschlaege = dispatch_voice_action(
                user_id=regular_user.id,
                arguments={"tool_name": "propose_gibt_es_nicht", "action": "Welche Ports nutzt Server 3?"},
            )

    assert fehler == "Aktion nicht verfügbar"
    assert "propose_gibt_es_nicht" in wert["error"]
    mock_exec.assert_not_called()
    assert vorschlaege == []


def test_die_stimme_erreicht_ueber_den_umweg_nichts_schweres(db: Session, regular_user: User):
    """Was die Sprachsitzung nicht anbietet, erreicht sie auch hier nicht.

    `propose_server_delete` steht in `REALTIME_SCHWER` und fehlt dem
    Sprachkatalog. Über den Dispatcher kam die Stimme bis zum 25.09.2026
    trotzdem hin. Der Chat (``stimme=False``) behält das Werkzeug.
    """
    with patch("services.ai_voice.voice_dispatcher.ai_action_service.angebotene_werkzeuge") as mock_angebot:
        mock_angebot.return_value = frozenset({"propose_server_delete", "list_my_servers"})
        with patch("services.ai_stream.write_tools._persist_write_proposals") as mock_persist:
            mock_persist.return_value = [{"id": "prop-del", "tool_name": "propose_server_delete", "status": "proposed"}]
            aufruf = {"tool_name": "propose_server_delete", "server_id": 2}
            wert, fehler, _, _ = dispatch_voice_action(regular_user.id, aufruf, conversation_id="conv-1")
            assert fehler == "Aktion nicht verfügbar"
            mock_persist.assert_not_called()

            wert, fehler, _, vorschlaege = dispatch_voice_action(
                regular_user.id, aufruf, conversation_id="conv-1", stimme=False,
            )
    assert fehler is None
    assert [v["id"] for v in vorschlaege] == ["prop-del"]


def test_dispatch_voice_action_explicit_tool_name(db: Session, regular_user: User):
    with patch("services.ai_voice.voice_dispatcher.ai_action_service.angebotene_werkzeuge") as mock_angebot, _ohne_karte():
        mock_angebot.return_value = frozenset({"search_workshop_mods", "list_my_servers"})
        with patch("services.ai_stream.read_tools._werkzeug_ausfuehren") as mock_exec:
            mock_exec.return_value = ({"mods": [{"id": "123", "name": "ValheimPlus"}]}, None)

            wert, fehler, anzeige, vorschlaege = dispatch_voice_action(
                user_id=regular_user.id,
                arguments={"tool_name": "search_workshop_mods", "parameters": {"query": "ValheimPlus", "server_id": 1}},
                conversation_id="conv-789",
            )

            assert fehler is None
            assert wert.get("executed_tool") == "search_workshop_mods"


def test_realtime_tools_contain_execute_server_action(db: Session, regular_user: User, monkeypatch):
    from services import ai_provider_service
    provider = ai_provider_service.create_provider(
        db,
        name="Realtime-Test-Provider",
        provider_kind="openai",
        default_model=None,
        enabled=True,
        requires_api_key=True,
        operator_api_key="sk-test-realtime",
        realtime_default=True,
        realtime_model="gpt-4o-realtime",
        realtime_voice="marin",
        realtime_language="de",
        realtime_vad_eagerness="high",
    )
    db.commit()

    monkeypatch.setattr(realtime_session.ai_provider_service, "resolve_api_key", lambda *args: "test-key")
    monkeypatch.setattr(
        realtime_session.ai_action_service,
        "angebotene_werkzeuge",
        lambda *args: frozenset({"list_my_servers", "read_server_status", "execute_server_action"}),
    )

    vorbereitung = realtime_session.vorbereiten(
        db,
        provider=provider,
        user=regular_user,
        herkunft="panel",
    )

    tool_names = {t["name"] for t in vorbereitung.tools}
    assert "execute_server_action" in tool_names
    assert "list_my_servers" in tool_names
    assert "voice_resolve_latest_proposal" in tool_names


def test_realtime_prompt_includes_notes_and_calendar():
    from services.ai_prompt import build, NOTIZEN, POSTFACH_UND_KALENDER
    prompt = build(gesprochen=True, rolle="realtime")
    assert "Notizen und Einkaufslisten" in prompt
    assert "propose_note_create" in prompt
    assert "propose_calendar_event_create" in prompt


def test_notes_tools_in_chat_interaction_tools():
    from services.ai_tool_registry import CHAT_INTERACTION_TOOLS, WRITE_TOOLS
    assert "propose_note_create" in CHAT_INTERACTION_TOOLS
    assert "propose_note_create" in WRITE_TOOLS
    assert "propose_calendar_event_create" in CHAT_INTERACTION_TOOLS


def test_voice_werkzeug_ausfuehren_propose_note(db: Session, regular_user: User):
    from services.ai_stream.read_tools import voice_werkzeug_ausfuehren
    from services.openai_compatible_adapter import ProviderToolCall
    with patch("services.ai_stream.read_tools.angebotene_werkzeuge") as mock_angebot:
        mock_angebot.return_value = frozenset({"propose_note_create"})
        with patch("services.ai_stream.write_tools._persist_write_proposals") as mock_persist:
            mock_persist.return_value = [{"id": "prop-note-1", "tool_name": "propose_note_create", "status": "proposed"}]
            call = ProviderToolCall(
                id="call-note",
                name="propose_note_create",
                arguments={"title": "Einkaufsliste", "content": "- [ ] Butter\n- [ ] Milch", "category": "shopping"},
            )
            wert, fehler, anzeige, vorschlaege = voice_werkzeug_ausfuehren(
                user_id=regular_user.id,
                call=call,
                conversation_id="conv-note-test",
            )
            assert fehler is None
            assert len(vorschlaege) == 1
            assert vorschlaege[0]["id"] == "prop-note-1"


def test_voice_werkzeug_ausfuehren_weist_nicht_angebotenes_ab(
    db: Session, regular_user: User
):
    """Ein Name, den die Sitzung nie angeboten hat, laeuft gar nicht erst.

    Bis hierher trug diesen Fall allein die Rechtepruefung im jeweiligen
    Handler — und drei Handler hatten keine. Der Sprachweg reichte den Namen
    des Modells ungeprueft weiter, waehrend der Chatweg ihn laengst gegen die
    Angebotsmenge haelt.

    Geprueft wird ueber die echte Menge und nicht ueber eine Kopie: ein Test
    mit eigener Liste bliebe gruen, wenn jemand das Gate wieder herausnimmt.
    """
    from services.ai_stream.read_tools import voice_werkzeug_ausfuehren
    from services.openai_compatible_adapter import ProviderToolCall

    with patch("services.ai_stream.read_tools._werkzeug_ausfuehren") as mock_exec:
        with patch("services.ai_stream.read_tools.angebotene_werkzeuge") as mock_angebot:
            mock_angebot.return_value = frozenset({"list_my_servers"})
            call = ProviderToolCall(id="call-mail", name="email_read", arguments={"message_id": "1"})
            wert, fehler, anzeige, vorschlaege = voice_werkzeug_ausfuehren(
                user_id=regular_user.id,
                call=call,
                conversation_id="conv-mail-test",
            )

    assert fehler == "Dieses Werkzeug steht in dieser Sitzung nicht zur Verfügung"
    assert wert == {"error": fehler}
    assert vorschlaege == []
    # Der eigentliche Punkt: es ist nichts gelaufen, nicht nur nichts
    # zurueckgekommen.
    mock_exec.assert_not_called()

