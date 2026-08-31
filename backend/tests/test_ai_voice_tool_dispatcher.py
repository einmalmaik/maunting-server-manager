from __future__ import annotations

from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session

from models.user import User
from services.ai_voice.voice_dispatcher import dispatch_voice_action
from services.ai_voice import realtime_session
from services.ai_tool_registry import WERKZEUGE


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
    with patch("services.ai_voice.voice_dispatcher.ai_action_service.angebotene_werkzeuge") as mock_angebot:
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


def test_dispatch_voice_action_routes_write_tool_as_proposal(db: Session, regular_user: User):
    with patch("services.ai_voice.voice_dispatcher.ai_action_service.angebotene_werkzeuge") as mock_angebot:
        mock_angebot.return_value = frozenset({"propose_backup", "list_my_servers"})
        with patch("services.ai_stream.write_tools._persist_write_proposals") as mock_persist:
            mock_persist.return_value = [{"id": "prop-1", "tool_name": "propose_backup", "status": "proposed"}]

            wert, fehler, anzeige, vorschlaege = dispatch_voice_action(
                user_id=regular_user.id,
                arguments={"action": "Mach ein Backup von Server 2", "server_id": 2},
                conversation_id="conv-456",
            )

            assert fehler is None
            assert wert.get("executed_tool") == "propose_backup"
            assert wert.get("status") == "proposal_created"
            assert len(vorschlaege) == 1
            assert vorschlaege[0]["id"] == "prop-1"


def test_dispatch_voice_action_explicit_tool_name(db: Session, regular_user: User):
    with patch("services.ai_voice.voice_dispatcher.ai_action_service.angebotene_werkzeuge") as mock_angebot:
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
