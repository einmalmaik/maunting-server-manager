"""Ethics Advisory Service für das MSM-Agentic-Framework.

Führt eine fundierte, isolierte ethische Bewertung einer geplanten Handlung durch.
Arbeitet als interner Berater für das Gehirn (Brain) und führt selbst keine Tools aus.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal
import httpx
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from models import AiProvider, User
from services import ai_provider_service, audit_service
from services.ai_ethics_trigger import DecisionContext
from services.openai_compatible_adapter import (
    AiProviderRequestError,
    StreamUsage,
    stream_chat_completion,
)


logger = logging.getLogger(__name__)


class AiEthicsEvaluation(BaseModel):
    """Das strukturierte Ergebnis einer ethischen Beurteilung."""

    assessment: Literal["low", "review", "critical"]
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    concerns: list[str] = Field(default_factory=list)
    affected_interests: list[str] = Field(default_factory=list)
    possible_harm: list[str] = Field(default_factory=list)
    alternative: str | None = None
    recommendation: str
    reason: str


ETHICS_SYSTEM_PROMPT = """Du bist die interne Ethics Advisory Engine von Maunting Server Manager (MSM).
Deine Aufgabe ist eine fundierte, ethisch-konstruktive und sicherheitsbewusste Beurteilung einer geplanten Systemhandlung.

Prüfkriterien:
1. Perspektivübernahme & Betroffene: Wer oder was ist von dieser Handlung betroffen (Benutzer, Server, Kunden, Dritte, Integrität von Daten)?
2. Möglicher Schaden: Welche direkten oder indirekten Schäden könnten entstehen (Datenverlust, Ausfallzeiten, Missbrauch, Vertrauensverlust)?
3. Wertekonflikte: Gibt es Konflikte zwischen Schnelligkeit, Komfort, Datensparsamkeit und Systemsicherheit?
4. Verhältnismäßigkeit & Alternativen: Gibt es eine schonendere, sicherere oder risikoärmere Vorgehensweise?
5. Absichts-Klarheit: Ist das Handlungsziel eindeutig vom autorisierten Benutzer gewollt oder birgt es Missverständnisse?

Antworte AUSSCHLIESSLICH im folgenden JSON-Format ohne zusätzlichen Begleittext:
{
  "assessment": "low" | "review" | "critical",
  "confidence": 0.0 bis 1.0,
  "concerns": ["Konkreter Bedenkenpunkt 1", "..."],
  "affected_interests": ["Betroffenes Interesse / Systemgut 1", "..."],
  "possible_harm": ["Möglicher negativer Effekt 1", "..."],
  "alternative": "Empfohlene schonendere Alternative oder null",
  "recommendation": "Konkrete, handlungsorientierte Empfehlung für das Gehirn",
  "reason": "Kompakte Begründung der ethischen Abwägung"
}
"""


def _build_user_prompt(
    context: DecisionContext, relevant_memories: list[str] | None = None
) -> str:
    """Erstellt den kompakten Kontext-Prompt für die Ethics Engine."""
    parts = [
        f"Ziel: {context.goal}",
        f"Geplante Handlung: {context.planned_action}",
        f"Werkzeug: {context.tool_name}",
        f"Parameter: {json.dumps(context.tool_arguments, ensure_ascii=False)}",
        f"Destruktiv / Unumkehrbar: {'Ja' if context.is_destructive else 'Nein'}",
        f"Bedarf Bestätigung: {'Ja' if context.requires_confirmation else 'Nein'}",
        f"Autonomer Modus: {'Ja' if context.autonomous else 'Nein'}",
    ]
    if context.server_id is not None:
        parts.append(f"Betroffene Server-ID: {context.server_id}")
    if context.target_description:
        parts.append(f"Zielbeschreibung: {context.target_description}")
    if relevant_memories:
        mem_str = "\n".join(f"- {m}" for m in relevant_memories[:5])
        parts.append(f"Relevanter Gedächtniskontext:\n{mem_str}")

    return "\n".join(parts)


def fallback_evaluation(
    context: DecisionContext, *, reason: str = "Ethics Engine Failsafe-Modus"
) -> AiEthicsEvaluation:
    """Sicherer Fallback, falls der Provider nicht erreichbar ist oder kein Modell gesetzt ist."""
    assessment: Literal["low", "review", "critical"] = (
        "critical" if context.is_destructive else ("review" if context.requires_confirmation else "low")
    )
    return AiEthicsEvaluation(
        assessment=assessment,
        confidence=0.5,
        concerns=["Ethics-Engine-Antwort konnte nicht bezogen werden. Mechanische Sicherheitsgrenzen greifen."],
        affected_interests=["Systemstabilität", "Datensicherheit"],
        possible_harm=["Potenzieller unbemerkter Eingriff ohne ethische Detailprüfung"],
        alternative=None,
        recommendation="Führe die geplante Handlung unter strikter Beachtung aller Bestätigungs- und Sicherheitsregeln durch.",
        reason=reason,
    )


async def evaluate_decision(
    http_client: httpx.AsyncClient,
    db: Session,
    provider: AiProvider,
    user: User,
    context: DecisionContext,
    relevant_memories: list[str] | None = None,
) -> AiEthicsEvaluation:
    """Führt die ethische Bewertung mit dem konfigurierten Ethik-Modell durch."""
    if not provider.ethics_model or (provider.ethics_mode or "auto") == "off":
        return fallback_evaluation(
            context, reason="Ethics Engine ist nicht konfiguriert oder deaktiviert"
        )

    api_key = ai_provider_service.resolve_api_key(db, provider, user.id)
    if provider.requires_api_key and not api_key:
        logger.warning(
            "Ethics Engine: Kein API-Schlüssel für Provider %s hinterlegt", provider.id
        )
        return fallback_evaluation(
            context, reason="API-Schlüssel für Ethics Engine fehlt"
        )

    user_prompt = _build_user_prompt(context, relevant_memories)
    messages = [
        {"role": "system", "content": ETHICS_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    usage = StreamUsage()
    raw_response_text = ""

    try:
        async for chunk in stream_chat_completion(
            http_client,
            provider=provider,
            api_key=api_key,
            messages=messages,
            usage=usage,
            tools=None,
            model=provider.ethics_model,
            reasoning_effort=provider.ethics_reasoning_effort,
        ):
            if chunk.kind == "content" and chunk.text:
                raw_response_text += chunk.text

        # JSON aus der Antwort extrahieren
        clean_text = raw_response_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
        clean_text = clean_text.strip()

        data = json.loads(clean_text)
        evaluation = AiEthicsEvaluation(**data)

        # Audit-Protokollierung bei kritischen oder relevanten Befunden
        if evaluation.assessment in ("review", "critical"):
            audit_service.record_privileged_action(
                db,
                user_id=user.id,
                action="ai.ethics.evaluated",
                target_type="ai_action",
                target_id=context.server_id,
                details={
                    "tool_name": context.tool_name,
                    "assessment": evaluation.assessment,
                    "confidence": evaluation.confidence,
                    "reason": evaluation.reason[:200],
                },
            )
            db.commit()

        return evaluation

    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "Ethics Engine: Fehler beim Parsen der JSON-Antwort von %s: %s",
            provider.ethics_model,
            exc,
        )
        return fallback_evaluation(
            context, reason="Antwort der Ethics Engine entsprach nicht dem geforderten Schema"
        )
    except (AiProviderRequestError, httpx.HTTPError, Exception) as exc:
        logger.warning(
            "Ethics Engine: Provider-Aufruf für Modell %s fehlgeschlagen: %s",
            provider.ethics_model,
            exc,
        )
        return fallback_evaluation(
            context, reason=f"Provider-Fehler bei der Ethics Engine: {type(exc).__name__}"
        )
