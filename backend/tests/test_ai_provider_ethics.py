"""Tests für Ethics Engine Felder und Validierung an AiProvider."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiProvider, User
from services import ai_provider_service
from services.ai_provider_service import AiProviderConfigurationError


class TestAiProviderEthicsValidation:
    def test_gueltige_ethik_konfiguration(self, db: Session):
        provider = ai_provider_service.create_provider(
            db,
            name="OpenRouter-Ethics",
            provider_kind="openrouter",
            default_model="openai/gpt-5.6-luna",
            ethics_model="anthropic/claude-sonnet-4.5",
            ethics_reasoning_effort="high",
            ethics_mode="critical",
            enabled=True,
            requires_api_key=False,
            operator_api_key=None,
        )
        assert provider.ethics_model == "anthropic/claude-sonnet-4.5"
        assert provider.ethics_reasoning_effort == "high"
        assert provider.ethics_mode == "critical"
        assert ai_provider_service.fuer_ethics(provider) is True

    def test_ungueltiger_ethik_modus_wird_abgelehnt(self, db: Session):
        with pytest.raises(AiProviderConfigurationError, match="Unbekannter Ethik-Modus"):
            ai_provider_service.create_provider(
                db,
                name="BadMode",
                provider_kind="openrouter",
                default_model="gpt-4o",
                ethics_model="gpt-4o-mini",
                ethics_mode="invalid_mode",
                enabled=True,
                requires_api_key=False,
                operator_api_key=None,
            )

    def test_denkstufe_ohne_ethik_modell_wird_abgelehnt(self, db: Session):
        with pytest.raises(AiProviderConfigurationError, match="braucht ein Ethik-Modell"):
            ai_provider_service.create_provider(
                db,
                name="NoModelWithEffort",
                provider_kind="openrouter",
                default_model="gpt-4o",
                ethics_model=None,
                ethics_reasoning_effort="medium",
                enabled=True,
                requires_api_key=False,
                operator_api_key=None,
            )

    def test_ethik_modell_ohne_standardmodell_wird_abgelehnt(self, db: Session):
        with pytest.raises(AiProviderConfigurationError, match="braucht ein Standardmodell"):
            ai_provider_service.create_provider(
                db,
                name="NoDefaultModel",
                provider_kind="openrouter",
                default_model=None,
                default_voice=None,
                transcription_model="whisper-1",  # Funktion vorhanden, aber kein Chat
                ethics_model="gpt-4o-mini",
                enabled=True,
                requires_api_key=False,
                operator_api_key=None,
            )


class TestAiProviderEthicsApi:
    def test_create_and_update_provider_with_ethics(
        self, client: TestClient, owner_cookies: dict, csrf_token: str
    ):
        # Create Provider
        payload = {
            "name": "Ethics-API-Provider",
            "provider_kind": "openrouter",
            "default_model": "openai/gpt-5.6-luna",
            "ethics_model": "openai/gpt-5.6-luna",
            "ethics_reasoning_effort": "low",
            "ethics_mode": "auto",
            "enabled": True,
            "requires_api_key": False,
        }
        res_create = client.post(
            "/api/ai/settings/providers",
            json=payload,
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res_create.status_code == 201
        created_data = res_create.json()
        assert created_data["ethics_model"] == "openai/gpt-5.6-luna"
        assert created_data["ethics_reasoning_effort"] == "low"
        assert created_data["ethics_mode"] == "auto"

        provider_id = created_data["id"]

        # Update Provider
        update_payload = {
            "ethics_mode": "always",
            "ethics_reasoning_effort": "high",
        }
        res_update = client.patch(
            f"/api/ai/settings/providers/{provider_id}",
            json=update_payload,
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res_update.status_code == 200
        updated_data = res_update.json()
        assert updated_data["ethics_mode"] == "always"
        assert updated_data["ethics_reasoning_effort"] == "high"
        assert updated_data["ethics_model"] == "openai/gpt-5.6-luna"
