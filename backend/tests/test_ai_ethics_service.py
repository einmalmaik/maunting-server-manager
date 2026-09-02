"""Tests für den AI Ethics Advisory Service."""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.orm import Session

from models import AiProvider, User
from services.ai_ethics_service import (
    AiEthicsEvaluation,
    _build_user_prompt,
    evaluate_decision,
    fallback_evaluation,
)
from services.ai_ethics_trigger import DecisionContext
from services.openai_compatible_adapter import StreamChunk


def _make_context(is_destructive: bool = False, requires_confirm: bool = True) -> DecisionContext:
    return DecisionContext(
        goal="Server stoppen und Daten archivieren",
        planned_action="propose_server_lifecycle mit operation=stop",
        tool_name="propose_server_lifecycle",
        tool_arguments={"server_id": 42, "operation": "stop"},
        server_id=42,
        is_destructive=is_destructive,
        requires_confirmation=requires_confirm,
        autonomous=False,
    )


class TestEthicsServiceHelpers:
    def test_user_prompt_building(self):
        context = _make_context(is_destructive=True)
        prompt = _build_user_prompt(context, relevant_memories=["Server 42 ist Produktiv-Datenbank"])
        assert "Server stoppen" in prompt
        assert "Destruktiv / Unumkehrbar: Ja" in prompt
        assert "Server 42 ist Produktiv-Datenbank" in prompt

    def test_fallback_evaluation_destructive(self):
        context = _make_context(is_destructive=True)
        eval_res = fallback_evaluation(context, reason="Provider unreachable")
        assert eval_res.assessment == "critical"
        assert eval_res.confidence == 0.5
        assert "Provider unreachable" in eval_res.reason

    def test_fallback_evaluation_non_destructive(self):
        context = _make_context(is_destructive=False, requires_confirm=False)
        eval_res = fallback_evaluation(context)
        assert eval_res.assessment == "low"


@pytest.mark.asyncio
class TestEthicsServiceEvaluation:
    async def test_ethics_service_deaktiviert_liefert_fallback(
        self, db: Session, regular_user: User
    ):
        provider = AiProvider(
            name="TestProvider",
            provider_kind="openrouter",
            default_model="test-chat",
            ethics_model=None,
            ethics_mode="off",
            enabled=True,
            requires_api_key=False,
        )
        db.add(provider)
        db.commit()

        context = _make_context()
        eval_res = await evaluate_decision(
            AsyncMock(), db, provider, regular_user, context
        )
        assert eval_res.assessment in ("review", "low")
        assert "deaktiviert" in eval_res.reason

    async def test_ethics_service_erfolgreiche_auswertung(
        self, db: Session, regular_user: User
    ):
        provider = AiProvider(
            name="EthicsProvider",
            provider_kind="openrouter",
            default_model="test-chat",
            ethics_model="openai/gpt-5.6-ethics",
            ethics_reasoning_effort="medium",
            ethics_mode="auto",
            enabled=True,
            requires_api_key=False,
        )
        db.add(provider)
        db.commit()

        mock_json_response = {
            "assessment": "review",
            "confidence": 0.95,
            "concerns": ["Server ist möglicherweise in Verwendung durch Kunden"],
            "affected_interests": ["Verfügbarkeit von Kundendiensten"],
            "possible_harm": ["Vorübergehende Dienstunterbrechung"],
            "alternative": "Wartungsfenster ankündigen",
            "recommendation": "Vor dem Stoppen prüfen, ob aktive Verbindungen bestehen.",
            "reason": "Hohe Auswirkung auf laufende Kundensitzungen.",
        }

        async def mock_stream(*args, **kwargs):
            yield StreamChunk(
                kind="content",
                text="```json\n" + json.dumps(mock_json_response) + "\n```",
            )

        with patch(
            "services.ai_ethics_service.stream_chat_completion",
            side_effect=mock_stream,
        ):
            context = _make_context()
            eval_res = await evaluate_decision(
                AsyncMock(), db, provider, regular_user, context
            )
            assert eval_res.assessment == "review"
            assert eval_res.confidence == 0.95
            assert "Wartungsfenster ankündigen" in (eval_res.alternative or "")
            assert "laufende Kundensitzungen" in eval_res.reason
