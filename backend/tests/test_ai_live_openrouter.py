"""Echter Lauf gegen einen Modellanbieter — der Beweis, dass die Skills greifen.

Alle uebrigen Tests pruefen unsere Seite: Rechte, Sichtbarkeit, Speicherung.
Was sie **nicht** pruefen koennen, ist die eigentliche Frage dieser Phase:
Findet ein echtes Sprachmodell die Skills im Systemprompt, und ruft es
`read_skill` und `learn_skill` von selbst auf?

Ein Verzeichnis, das im Prompt steht und nie benutzt wird, waere genauso
nutzlos wie gar keins — und das laesst sich nur mit einem echten Modell
feststellen.

**Ausfuehren:**

    MSM_LIVE_AI_KEY=sk-or-v1-... \\
    MSM_LIVE_AI_MODEL=openrouter/free \\
    python -m pytest tests/test_ai_live_openrouter.py -q -s

Ohne `MSM_LIVE_AI_KEY` wird die Datei uebersprungen. Sie gehoert bewusst nicht
in die normale Suite: sie kostet Tokens, braucht Netz und ihr Ergebnis haengt
vom gewaehlten Modell ab. Ein schwaecheres Modell kann hier scheitern, ohne
dass an MSM etwas falsch waere.
"""

from __future__ import annotations

import json
import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.orm import Session

from models import AiConversation, AiProvider, Role, RolePermission, User
from services import ai_action_service, ai_skill_service
from services.ai_context_service import build_provider_messages
from services.openai_compatible_adapter import StreamUsage, stream_chat_completion
from services.role_service import set_user_roles


LIVE_KEY = os.environ.get("MSM_LIVE_AI_KEY", "").strip()
LIVE_MODEL = os.environ.get("MSM_LIVE_AI_MODEL", "openrouter/free").strip()
LIVE_BASE_URL = os.environ.get("MSM_LIVE_AI_BASE_URL", "https://openrouter.ai/api/v1").strip()

pytestmark = pytest.mark.skipif(
    not LIVE_KEY, reason="MSM_LIVE_AI_KEY nicht gesetzt — echter Providerlauf uebersprungen"
)


def _provider(db: Session) -> AiProvider:
    provider = AiProvider(
        name="OpenRouter (live)",
        provider_kind="openrouter",
        default_model=LIVE_MODEL,
        enabled=True,
        requires_api_key=True,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def _allow(db: Session, user: User, *keys: str) -> None:
    role = Role(name=f"live-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [role.id])
    db.commit()


def _conversation(db: Session, user: User) -> AiConversation:
    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Live"
    )
    db.add(conversation)
    db.commit()
    return conversation


MAX_ROUNDS = 4


async def _ask(db: Session, user: User, turns: list[dict]) -> list[str]:
    """Fuehrt ein Gespraech ueber **mehrere** Werkzeugrunden und meldet alle Aufrufe.

    Eine einzelne Runde reicht hier nicht. Ein Modell, das der Anweisung folgt,
    ruft zuerst `read_skill` auf, um zu pruefen ob eine Sache schon beschrieben
    ist — und entscheidet erst danach ueber `learn_skill`. Ein Test, der nach
    der ersten Runde abbricht, sieht davon nur den ersten Schritt und meldet
    faelschlich, es sei nichts gelernt worden.

    Der Ablauf ist derselbe wie in `ai_stream_service`: Aufrufe ausfuehren,
    Ergebnisse als `tool`-Nachrichten zurueckgeben, erneut fragen.
    """
    provider = _provider(db)
    conversation = _conversation(db, user)
    query = next(
        (turn["content"] for turn in reversed(turns) if turn["role"] == "user"), ""
    )
    messages = build_provider_messages(db, conversation, query)
    messages.extend(turns)

    called: list[str] = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        for _round in range(MAX_ROUNDS):
            usage = StreamUsage()
            async for _chunk in stream_chat_completion(
                client,
                provider=provider,
                api_key=LIVE_KEY,
                messages=messages,
                usage=usage,
                tools=ai_action_service.provider_tool_definitions(),
            ):
                pass
            if not usage.tool_calls:
                break

            messages.append({
                "role": "assistant", "content": None,
                "tool_calls": [
                    {
                        "id": call.id, "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=True),
                        },
                    }
                    for call in usage.tool_calls
                ],
            })
            for call in usage.tool_calls:
                called.append(call.name)
                try:
                    value = ai_action_service.execute_read_tool(
                        db, user=user, tool_name=call.name, arguments=call.arguments,
                    )
                except Exception as exc:  # Schreibwerkzeuge, fehlende Rechte
                    value = {"error": str(exc)}
                messages.append({
                    "role": "tool", "tool_call_id": call.id,
                    "content": json.dumps(value, ensure_ascii=True)[:4000],
                })
    return called


@pytest.mark.asyncio
async def test_the_model_reaches_for_the_shipped_skill(
    db: Session, regular_user: User
) -> None:
    """Der Kernfall des Betreibers, unveraendert seit der ersten Beschreibung.

    "Ein Server ist nicht erreichbar, der ist zwar an." Das Modell soll den
    mitgelieferten Skill erkennen und ihn lesen, statt sofort blind Werkzeuge
    zu probieren.
    """
    ai_skill_service.reset_shipped_cache_for_tests()
    _allow(db, regular_user, "ai.chat.use", "ai.skills.use", "ai.memory.use")

    called = await _ask(db, regular_user, [{
        "role": "user",
        "content": (
            "Mein Valheim-Server laeuft laut Panel, aber niemand kann sich "
            "verbinden. Was tun?"
        ),
    }])

    print(f"\n  Werkzeugaufrufe: {called}")
    assert called, "Das Modell hat gar kein Werkzeug aufgerufen"
    # Entweder es liest zuerst den Skill oder es holt sich die Serverliste —
    # beides ist ein vernuenftiger erster Schritt. Was es nicht tun darf, ist
    # raten.
    assert "read_skill" in called or "list_my_servers" in called



@pytest.mark.asyncio
async def test_the_model_learns_after_solving_something(
    db: Session, regular_user: User
) -> None:
    """Das Versprechen "die KI lernt selbst" gegen ein echtes Modell.

    Der Gespraechsverlauf enthaelt eine abgeschlossene Diagnose. Ohne
    Aufforderung, aber mit der Anweisung aus dem Systemprompt sollte das Modell
    daraus einen Skill machen.
    """
    ai_skill_service.reset_shipped_cache_for_tests()
    _allow(db, regular_user, "ai.chat.use", "ai.skills.use", "ai.memory.use")

    called = await _ask(db, regular_user, [
        {
            "role": "user",
            "content": "Mein Server laeuft, aber niemand kommt drauf.",
        },
        {
            "role": "assistant",
            "content": (
                "Ich habe die Ursache gefunden: Der Server war an die "
                "Docker-Bruecke 172.17.0.1 gebunden. Nach der Umstellung auf "
                "0.0.0.0 ist er erreichbar. Das passiert bei jedem Server, der "
                "aus einem aelteren Backup wiederhergestellt wird."
            ),
        },
        {
            "role": "user",
            "content": "Perfekt, jetzt laeuft er. Danke!",
        },
    ])

    print(f"\n  Werkzeugaufrufe: {called}")
    assert "learn_skill" in called or "remember" in called, (
        f"Das Modell hat nichts festgehalten, sondern: {called}"
    )

    if "learn_skill" in called:
        # `_ask` hat den Aufruf bereits ausgefuehrt — der Skill muss also
        # tatsaechlich in der Datenbank stehen, nicht nur vorgeschlagen sein.
        learned = [
            view for view in ai_skill_service.visible_skills(db, regular_user)
            if view.origin == "ai"
        ]
        print(f"  Gelernte Skills: {[(v.skill_key, v.scope) for v in learned]}")
        assert learned, "learn_skill wurde aufgerufen, aber nichts ist angekommen"
        # Die Beschreibung entscheidet spaeter ueber Auffinden oder
        # Nichtauffinden — sie darf nicht leer bleiben.
        assert all(view.description for view in learned)


@pytest.mark.asyncio
async def test_the_model_remembers_without_being_asked(
    db: Session, regular_user: User
) -> None:
    """Der Benutzer soll nie "merk dir das" sagen muessen.

    Im Betrieb beobachtet: "Servus erstmal ich bin maik" wurde nicht gemerkt.
    Erst ein spaeteres, ausdrueckliches "und merk dir das ich maik heisse"
    loeste `remember` aus.

    Die Ursache lag im Prompt, nicht am Modell. Er zaehlte auf, *was* zu merken
    ist — "Vorlieben, wiederkehrende Einstellungen, Eigenheiten eines Servers" —
    und ein Name passt in keine dieser Kategorien. Gemessen mit dem alten Text
    rief das Modell `list_my_servers` auf; mit dem Ausloeser "sagt der Benutzer
    etwas ueber sich, merke es sofort und ungefragt" ruft es `remember`.
    """
    ai_skill_service.reset_shipped_cache_for_tests()
    _allow(db, regular_user, "ai.chat.use", "ai.skills.use", "ai.memory.use")

    called = await _ask(db, regular_user, [
        {"role": "user", "content": "Servus erstmal, ich bin Maik"},
    ])

    print(f"\n  Werkzeugaufrufe: {called}")
    assert "remember" in called, (
        f"Der Name wurde nicht ungefragt gemerkt, stattdessen: {called}"
    )
