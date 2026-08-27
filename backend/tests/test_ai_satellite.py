"""Tests für das modulare Satelliten- und Regionsanalysesystem (Copernicus / Sentinel).

Prüft Rechte, Tool-Gating, sichere Speicherung, Geocoding und Fehlerbehandlung.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.orm import Session

from models import Role, RolePermission, User
from services import (
    ai_action_errors,
    ai_action_service,
    ai_geo_service,
    ai_satellite_service,
)
from services.role_service import set_user_roles


def _allow_satellite(db: Session, user: User) -> None:
    role = Role(name=f"satellit-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.satellite.use"))
    db.commit()
    set_user_roles(db, user, [role.id])


def test_satellite_without_permission_is_rejected(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ai_satellite_service, "is_configured", lambda: True)

    with pytest.raises(ai_action_errors.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db,
            user=regular_user,
            tool_name="analyze_region",
            arguments={"location": "Berlin"},
        )


def test_without_credentials_tool_is_not_offered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ai_satellite_service, "is_configured", lambda: False)

    names = {item["function"]["name"] for item in ai_action_service.provider_tool_definitions()}
    assert "analyze_region" not in names


def test_with_credentials_tool_appears_in_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ai_satellite_service, "is_configured", lambda: True)

    names = {item["function"]["name"] for item in ai_action_service.provider_tool_definitions()}
    assert "analyze_region" in names


def test_store_and_retrieve_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_store = {}

    from services.panel_settings_service import PanelSettingsService
    from services.auth_service import AuthService

    monkeypatch.setattr(PanelSettingsService, "get", lambda k, default="": fake_store.get(k, default))
    monkeypatch.setattr(PanelSettingsService, "set", lambda k, v: fake_store.__setitem__(k, v))
    monkeypatch.setattr(AuthService, "encrypt_secret", lambda plain, aad=None: f"enc:{plain}")
    monkeypatch.setattr(AuthService, "decrypt_secret", lambda enc, aad=None: enc.replace("enc:", "", 1) if enc.startswith("enc:") else "")

    assert ai_satellite_service.is_configured() is False

    ai_satellite_service.store_credentials("client_123", "secret_abc")
    assert ai_satellite_service.is_configured() is True
    creds = ai_satellite_service.get_credentials()
    assert creds == {"client_id": "client_123", "client_secret": "secret_abc"}

    ai_satellite_service.store_credentials("", "")
    assert ai_satellite_service.is_configured() is False


def test_geo_service_analyze_region(db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_satellite(db, regular_user)
    monkeypatch.setattr(ai_satellite_service, "is_configured", lambda: False)

    result = ai_action_service.execute_read_tool(
        db,
        user=regular_user,
        tool_name="analyze_region",
        arguments={"location": "Berlin"},
    )

    assert result["status"] == "success"
    assert "Berlin" in result["location"]
    assert result["coordinates"]["latitude"] == 52.52
    assert result["coordinates"]["longitude"] == 13.405
    assert "weather" in result
