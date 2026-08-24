"""Tests für die modulare Guardian-Engine & KI-Integration."""

import pytest
from unittest.mock import patch, MagicMock

from database import SessionLocal
from models import Incident, Server, User, AuditLog
from services import (
    ai_guardian_settings,
    ai_guardian_service,
    ai_guardian_repair_service,
    ai_action_service,
    ai_proposal_service,
    ai_prompt,
    panel_settings_service,
)
from services.ai_action_service import AiActionValidationError
from services.ai_proposal_service import AiActionValidationError as ProposalValidationError


@pytest.fixture(autouse=True)
def _reset_guardian_setting():
    """Stellt sicher, dass der Cache vor und nach jedem Test sauber ist."""
    panel_settings_service.PanelSettingsService.invalidate_cache()
    ai_guardian_settings.set_guardian_ai_enabled(False)
    yield
    panel_settings_service.PanelSettingsService.invalidate_cache()


def test_default_guardian_ai_disabled():
    panel_settings_service.PanelSettingsService.invalidate_cache()
    # Lösche den Key aus dem Service Cache / DB um Default zu testen
    from database import SessionLocal
    from models import PanelSetting
    with SessionLocal() as db:
        db.query(PanelSetting).filter_by(key=ai_guardian_settings.SETTINGS_KEY).delete()
        db.commit()
    panel_settings_service.PanelSettingsService.invalidate_cache()

    assert ai_guardian_settings.is_guardian_ai_enabled() is False


def test_set_guardian_ai_enabled():
    ai_guardian_settings.set_guardian_ai_enabled(True)
    assert ai_guardian_settings.is_guardian_ai_enabled() is True

    ai_guardian_settings.set_guardian_ai_enabled(False)
    assert ai_guardian_settings.is_guardian_ai_enabled() is False


def test_tools_stripped_when_disabled():
    with SessionLocal() as db:
        user = db.query(User).filter(User.is_active.is_(True)).first()
        if user is None:
            user = User(
                id=9999,
                username="test_guardian_user",
                email="guardian@test.local",
                password_hash="dummy_hash",
                is_active=True,
            )
            db.add(user)
            db.commit()

        # Disabled:
        ai_guardian_settings.set_guardian_ai_enabled(False)
        tools = ai_action_service.angebotene_werkzeuge(db, user)
        assert "read_guardian_incidents" not in tools
        assert "propose_guardian_tuning" not in tools

        # Enabled: (mit entsprechenden Rechten irgendwo)
        ai_guardian_settings.set_guardian_ai_enabled(True)
        with patch("services.permission_service.rechte_irgendwo", return_value={"server.view", "server.config.write"}):
            tools_enabled = ai_action_service.angebotene_werkzeuge(db, user)
            assert "read_guardian_incidents" in tools_enabled
            assert "propose_guardian_tuning" in tools_enabled


def test_read_guardian_incidents_rejected_when_disabled():
    with SessionLocal() as db:
        server = db.query(Server).first()
        if server is None:
            server = Server(id=9999, name="TestServer", game_type="generic", install_dir="/tmp/test")
            db.add(server)
            db.commit()

        user = User(
            id=9999,
            username="test_u",
            email="u@test.local",
            password_hash="dummy_hash",
            is_active=True,
            is_owner=True,
        )

        ai_guardian_settings.set_guardian_ai_enabled(False)
        with pytest.raises(AiActionValidationError) as exc_info:
            ai_action_service.execute_read_tool(
                db,
                user=user,
                tool_name="read_guardian_incidents",
                arguments={"server_id": server.id},
            )
        assert "Guardian-KI-Integration ist deaktiviert" in str(exc_info.value)



def test_propose_guardian_tuning_rejected_when_disabled():
    with SessionLocal() as db:
        user = User(
            id=9999,
            username="test_u",
            email="u@test.local",
            password_hash="dummy_hash",
            is_active=True,
            is_owner=True,
        )
        ai_guardian_settings.set_guardian_ai_enabled(False)
        with pytest.raises(AiActionValidationError) as exc_info:
            ai_proposal_service._require_tool_permission(
                db, user, 1, "propose_guardian_tuning", {}
            )
        assert "Guardian-KI-Integration ist deaktiviert" in str(exc_info.value)


@pytest.mark.asyncio
async def test_guardian_services_isolated_when_disabled():
    with SessionLocal() as db:
        server = Server(id=9998, name="TestServer", game_type="generic", install_dir="/tmp/test")
        vorfall = Incident(id=9998, server_id=9998, status="open", type="crash")
        user = User(id=9998, username="test_u", email="u@test.local", password_hash="dummy_hash", is_active=True)

        ai_guardian_settings.set_guardian_ai_enabled(False)

        # 1. vorfaelle_bearbeiten bricht sofort mit 0 ab
        count = await ai_guardian_service.vorfaelle_bearbeiten(db)
        assert count == 0

        # 2. heilungslauf_starten liefert None
        lauf = await ai_guardian_service.heilungslauf_starten(
            db, server=server, vorfall=vorfall, user=user
        )
        assert lauf is None

        # 3. briefing_nachricht liefert None
        briefing = ai_guardian_service.briefing_nachricht(db, user)
        assert briefing is None

        # 4. auftrag_anlegen im repair service liefert None
        auftrag = ai_guardian_repair_service.auftrag_anlegen(
            db, vorfall=vorfall, server=server, user=user
        )
        assert auftrag is None

        # 5. faellige_bearbeiten im repair service liefert 0
        faellig_count = await ai_guardian_repair_service.faellige_bearbeiten(db)
        assert faellig_count == 0


def test_api_guardian_settings_read_and_write(client, db, owner_user, owner_cookies, regular_user, user_cookies):
    csrf_owner = owner_cookies.get("__Secure-csrf_token", "")
    csrf_user = user_cookies.get("__Secure-csrf_token", "")

    # 1. Unprivilegierter User kann nicht schreiben
    resp = client.put(
        "/api/ai/settings/guardian",
        json={"enabled": True},
        cookies=user_cookies,
        headers={"X-CSRF-Token": csrf_user},
    )
    assert resp.status_code == 403

    # 2. Owner / Admin kann Status lesen (Standard: false)
    resp = client.get("/api/ai/settings/guardian", cookies=owner_cookies)
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False}

    # 3. Owner / Admin kann Status ändern
    resp = client.put(
        "/api/ai/settings/guardian",
        json={"enabled": True},
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_owner},
    )
    assert resp.status_code == 200
    assert resp.json() == {"enabled": True}
    assert ai_guardian_settings.is_guardian_ai_enabled() is True

    # 4. Audit-Log wurde erfasst
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.action == "ai.guardian.integration.updated")
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    assert audit is not None
    assert audit.user_id == owner_user.id


