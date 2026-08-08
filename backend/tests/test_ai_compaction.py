"""Ein Chat, der nie endet, braucht ein Gedaechtnis fuer sich selbst.

Der Assistent hat genau eine Unterhaltung. Ohne Kompression hiess "lang"
schlicht "abgeschnitten": `build_provider_messages` nahm die letzten 20
Nachrichten bzw. 24.000 Zeichen und liess den Rest weg — ohne Hinweis, ohne
Ersatz. Die KI wusste nach ein paar Dutzend Nachrichten nicht mehr, worum es am
Anfang ging, tat aber so, als kenne sie den Verlauf.

Die Tests halten drei Zusagen fest: es wird nur gefaltet wenn noetig, der
Verbrauch wird verbucht, und ein Fehlschlag darf niemals Nachrichten
verschwinden lassen.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import (
    AiConversation,
    AiMessage,
    AiProvider,
    AiUsageEvent,
    Role,
    RolePermission,
    User,
)
from services import ai_compaction_service
from services.ai_context_service import build_provider_messages
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.openai_compatible_adapter import AiProviderRequestError, StreamChunk
from services.role_service import set_user_roles


def _provider(db: Session) -> AiProvider:
    provider = AiProvider(
        name="Kompression", base_url="https://provider.invalid/v1",
        default_model="model-a", enabled=True, requires_api_key=False,
        allow_private_network=False,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def _enable(db: Session, user: User) -> None:
    role = Role(name=f"compact-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    set_role_limit(db, role.id, {field: None for field in LIMIT_FIELDS})
    db.commit()
    set_user_roles(db, user, [role.id])


def _conversation(db: Session, user: User, *, messages: int, chars: int) -> AiConversation:
    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Lang"
    )
    db.add(conversation)
    db.flush()
    start = datetime.now(timezone.utc) - timedelta(hours=messages)
    for index in range(messages):
        db.add(AiMessage(
            id=str(uuid4()), conversation_id=conversation.id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"Nachricht {index} " + "x" * chars,
            status="complete",
            created_at=start + timedelta(minutes=index),
        ))
    db.commit()
    return conversation


def _fake_summary(monkeypatch: pytest.MonkeyPatch, text: str) -> dict:
    seen: dict = {}

    async def fake(_client, *, provider, api_key, messages, usage, **_kwargs):
        del provider, api_key
        seen["messages"] = messages
        usage.total_tokens = 30
        yield StreamChunk("content", text)

    monkeypatch.setattr(ai_compaction_service, "stream_chat_completion", fake)
    return seen


def test_a_short_conversation_is_never_compacted(db: Session, regular_user: User) -> None:
    """Unter der Schwelle passt alles in den Kontext — es gaebe nichts zu sparen."""
    conversation = _conversation(db, regular_user, messages=6, chars=100)

    assert ai_compaction_service.needs_compaction(db, conversation) is False


def test_a_long_conversation_is_recognized_as_foldable(
    db: Session, regular_user: User
) -> None:
    conversation = _conversation(db, regular_user, messages=40, chars=2_000)

    assert ai_compaction_service.needs_compaction(db, conversation) is True


@pytest.mark.asyncio
async def test_compaction_keeps_the_recent_messages_verbatim(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der juengste Teil traegt den aktuellen Faden und bleibt woertlich stehen."""
    _enable(db, regular_user)
    provider = _provider(db)
    conversation = _conversation(db, regular_user, messages=40, chars=2_000)
    _fake_summary(monkeypatch, "Es ging um einen Minecraft-Server und fehlende Ports.")

    done = await ai_compaction_service.compact_conversation(
        client=None, user_id=regular_user.id,
        conversation_id=conversation.id, provider_id=provider.id,
    )

    assert done is True
    db.expire_all()
    conversation = db.get(AiConversation, conversation.id)
    assert conversation.summary.startswith("Es ging um einen Minecraft-Server")
    assert conversation.summarized_until is not None

    # Die Historie enthaelt jetzt die Zusammenfassung plus den juengsten Rest —
    # nicht mehr die gefalteten Nachrichten einzeln.
    messages = build_provider_messages(db, conversation)
    serialized = " ".join(str(item.get("content")) for item in messages)
    assert "Es ging um einen Minecraft-Server" in serialized
    assert "Nachricht 0 " not in serialized
    assert "Nachricht 39 " in serialized


@pytest.mark.asyncio
async def test_compaction_books_its_own_token_usage(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Zusammenfassung ist ein echter Providerruf und kostet Tokens.

    Ein unsichtbarer Verbrauch waere genau das, was Zielpunkt 6 verhindern
    soll: der Betreiber wuerde Kosten sehen, die in keiner Zeile stehen.
    """
    _enable(db, regular_user)
    provider = _provider(db)
    conversation = _conversation(db, regular_user, messages=40, chars=2_000)
    _fake_summary(monkeypatch, "Zusammenfassung.")

    await ai_compaction_service.compact_conversation(
        client=None, user_id=regular_user.id,
        conversation_id=conversation.id, provider_id=provider.id,
    )

    db.expire_all()
    events = db.query(AiUsageEvent).all()
    assert len(events) == 1
    assert events[0].status == "completed"
    assert events[0].accounted_tokens == 30


@pytest.mark.asyncio
async def test_a_failed_compaction_loses_no_messages(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scheitert die Zusammenfassung, bleibt der Chat unveraendert.

    Der gefaehrliche Fehler waere, `summarized_until` zu setzen und danach
    keine Zusammenfassung zu haben: dann waeren die Nachrichten aus dem Kontext
    verschwunden und nichts an ihrer Stelle.
    """
    _enable(db, regular_user)
    provider = _provider(db)
    conversation = _conversation(db, regular_user, messages=40, chars=2_000)

    async def failing(_client, **_kwargs):
        raise AiProviderRequestError("AI_PROVIDER_UNAVAILABLE")
        yield  # pragma: no cover - macht die Funktion zum Generator

    monkeypatch.setattr(ai_compaction_service, "stream_chat_completion", failing)

    done = await ai_compaction_service.compact_conversation(
        client=None, user_id=regular_user.id,
        conversation_id=conversation.id, provider_id=provider.id,
    )

    assert done is False
    db.expire_all()
    conversation = db.get(AiConversation, conversation.id)
    assert conversation.summary is None
    assert conversation.summarized_until is None
    # Die Reservierung darf keinen Nebenlaeufigkeitsplatz blockieren.
    assert db.query(AiUsageEvent).filter(AiUsageEvent.status == "reserved").count() == 0


@pytest.mark.asyncio
async def test_an_empty_summary_is_treated_as_a_failure(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine leere Antwort ist keine Zusammenfassung."""
    _enable(db, regular_user)
    provider = _provider(db)
    conversation = _conversation(db, regular_user, messages=40, chars=2_000)
    _fake_summary(monkeypatch, "   ")

    done = await ai_compaction_service.compact_conversation(
        client=None, user_id=regular_user.id,
        conversation_id=conversation.id, provider_id=provider.id,
    )

    assert done is False
    db.expire_all()
    assert db.get(AiConversation, conversation.id).summarized_until is None


@pytest.mark.asyncio
async def test_the_summary_prompt_never_carries_credentials(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Verlauf geht redigiert an den Anbieter — wie jeder andere Kontext."""
    _enable(db, regular_user)
    provider = _provider(db)
    conversation = _conversation(db, regular_user, messages=40, chars=2_000)
    db.add(AiMessage(
        id=str(uuid4()), conversation_id=conversation.id, role="user",
        content="mein key ist api_key=sk-abcdefghijklmnopqrstuvwxyz012345",
        status="complete",
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
    ))
    db.commit()
    seen = _fake_summary(monkeypatch, "Zusammenfassung.")

    await ai_compaction_service.compact_conversation(
        client=None, user_id=regular_user.id,
        conversation_id=conversation.id, provider_id=provider.id,
    )

    serialized = " ".join(str(item["content"]) for item in seen["messages"])
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in serialized
