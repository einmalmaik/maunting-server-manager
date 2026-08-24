"""Security-Invarianten fuer Provider und persistente AI-Chats."""

from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import (
    AiConversation,
    AiMessage,
    AiProvider,
    AiRun,
    AiUsageEvent,
    AuditLog,
    Role,
    RolePermission,
    ServerPermission,
    User,
)
from models.ai_run import BEENDET
from services import ai_chat_service, ai_provider_service, ai_run_service
from services.ai_stream_service import MAX_TOOL_ROUNDS
from services.ai_context_service import build_provider_messages, redact_sensitive_text
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.ai_usage_service import MICROUNITS_PER_CENT
from services.openai_compatible_adapter import (
    AiProviderRequestError,
    ProviderToolCall,
    StreamChunk,
    StreamUsage,
    stream_chat_completion,
)
from services.role_service import set_user_roles


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _enable_chat(db: Session, user: User, autonomy: bool = False) -> None:
    role = Role(name=f"ai-chat-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    if autonomy:
        db.add(RolePermission(role_id=role.id, permission_key="ai.autonomous.use"))
        from models import AiAutonomyGrant
        db.add(AiAutonomyGrant(user_id=user.id, server_id=None, enabled=True, max_actions_per_hour=50))
    set_role_limit(db, role.id, {field: None for field in LIMIT_FIELDS})
    db.commit()
    set_user_roles(db, user, [role.id])


def _provider(db: Session, monkeypatch: pytest.MonkeyPatch) -> AiProvider:
    provider = ai_provider_service.create_provider(
        db,
        name="Test Provider",
        provider_kind="openrouter",
        default_model="test-model",
        enabled=True,
        requires_api_key=True,
        operator_api_key="sk-or-v1-operator-secret",
    )
    db.commit()
    db.refresh(provider)
    return provider


def test_provider_settings_store_only_ciphertext_and_masked_metadata(
    client: TestClient,
    db: Session,
    owner_cookies: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-or-v1-provider-1234"

    response = client.post(
        "/api/ai/settings/providers",
        json={
            "name": "OpenAI Compatible",
            "provider_kind": "openrouter",
            "default_model": "model-a",
            "operator_api_key": secret,
        },
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["operator_key_configured"] is True
    assert body["operator_key_hint"].endswith("1234")
    assert secret not in response.text
    provider = db.query(AiProvider).one()
    assert provider.operator_api_key_encrypted != secret
    assert secret not in provider.operator_api_key_encrypted
    audit = db.query(AuditLog).filter(AuditLog.action == "ai.provider.created").one()
    assert secret not in (audit.details or "")
    assert "1234" not in (audit.details or "")


def test_provider_read_path_never_decrypts_secret(
    client: TestClient,
    db: Session,
    owner_cookies: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _provider(db, monkeypatch)
    monkeypatch.setattr(
        "services.dis_client.DisClient.decrypt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("decrypt called")),
    )

    response = client.get("/api/ai/settings/providers", cookies=owner_cookies)

    assert response.status_code == 200
    assert response.json()[0]["operator_key_configured"] is True


def test_provider_list_survives_a_row_with_an_unknown_kind(
    client: TestClient,
    db: Session,
    owner_cookies: dict,
) -> None:
    """Eine geparkte Zeile darf die Liste nicht mit 500 abräumen.

    Die Migration setzt `provider_kind` auf "" und `enabled` auf False, wenn
    sie die früher frei eintragbare Basis-URL keinem unterstützten Anbieter
    zuordnen kann. Genau diese Zeile muss der Betreiber sehen — sonst kann er
    sie weder umstellen noch löschen.
    """
    db.add(
        AiProvider(
            name="Geparkter Zugang",
            provider_kind="",
            default_model="llama3",
            enabled=False,
            requires_api_key=False,
        )
    )
    db.commit()

    response = client.get("/api/ai/settings/providers", cookies=owner_cookies)

    assert response.status_code == 200
    zeile = next(item for item in response.json() if item["name"] == "Geparkter Zugang")
    assert zeile["base_url"] is None


def test_a_user_cannot_bring_their_own_key_any_more(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BYOK ist entfallen — Schluessel, Modell und Provider stellt der Betreiber.

    Hier stand frueher das Gegenteil: ein Test, der bewies, dass ein
    Benutzerschluessel **vor** dem des Betreibers genommen wird. In einem Panel,
    das ein Hoster betreibt, ist das ein zweiter Abrechnungspfad neben dem
    kalkulierten, und die Funktion laesst sich damit nicht mehr als seine
    anbieten.

    Der Endpunkt ist weg, nicht nur die Schaltflaeche. Eine Oberflaeche, die
    etwas nicht mehr anzeigt, ist keine Sperre.
    """
    _enable_chat(db, regular_user)
    provider = _provider(db, monkeypatch)

    response = client.put(
        f"/api/ai/providers/{provider.id}/credential",
        json={"api_key": "user-secret-9876"},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )

    assert response.status_code == 404
    assert client.get(
        f"/api/ai/providers/{provider.id}/credential", cookies=user_cookies
    ).status_code == 404


def test_the_operator_key_is_the_only_source(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ohne Betreiberschluessel gibt es keinen — und der Provider ist gesperrt."""
    provider = _provider(db, monkeypatch)
    assert ai_provider_service.resolve_api_key(db, provider, regular_user.id) is not None

    provider.operator_api_key_encrypted = None
    db.commit()
    assert ai_provider_service.resolve_api_key(db, provider, regular_user.id) is None


# ── Der Schluessel fuer den Modellkatalog ─────────────────────────────
#
# `ai_model_catalog` haengt diese Funktion ein und ruft sie **nur** dort, wo
# ohnehin abgerufen wird. Der Umweg ist noetig, weil OpenAI seine Modelliste nur
# gegen einen Schluessel herausgibt, die Leser des Katalogs ihn aber nicht zur
# Hand haben — `ai_reasoning.vorgabe`, `ai_context_window.ermitteln` und die
# Providerliste im Chat fragen alle ohne.


def test_the_catalog_key_comes_from_an_enabled_access_of_that_kind(
    db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nach Anbieter gefragt, nicht nach Zugang — welche Modelle es gibt, haengt nicht daran, wer fragt."""
    _provider(db, monkeypatch)
    assert ai_provider_service.katalogschluessel("openrouter") == "sk-or-v1-operator-secret"


def test_a_catalog_key_never_travels_to_another_provider(
    db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der Schluessel eines Zugangs geht nur an die Adresse, fuer die er ausgestellt ist.

    Sonst holte der Abruf fuer OpenAI den OpenRouter-Schluessel und schickte ihn
    an ``api.openai.com`` — ein Geheimnis an einen fremden Dienst, fuer eine
    Liste, die dort ohnehin abgelehnt wird.
    """
    _provider(db, monkeypatch)
    assert ai_provider_service.katalogschluessel("openai") is None


def test_a_disabled_access_stops_the_background_fetch_too(
    db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Abschalten ist auch eine Aussage ueber ausgehende Verbindungen.

    Ein deaktivierter Zugang darf nicht im Hintergrund weiter beim Anbieter
    nachfragen — der Betreiber hat ihn abgeschaltet, und die Auffrischung des
    Katalogs sieht niemand.
    """
    provider = _provider(db, monkeypatch)
    provider.enabled = False
    db.commit()
    assert ai_provider_service.katalogschluessel("openrouter") is None


def test_an_access_without_a_key_is_no_answer_at_all(
    db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein Zugang ohne Schluessel darf keinen mit verdecken.

    Gaebe die Abfrage ihn heraus, endete sie bei ``None`` — obwohl daneben ein
    zweiter Zugang desselben Anbieters mit Schluessel steht. Der Katalog bliebe
    leer, und zwar dauerhaft.
    """
    ohne = ai_provider_service.create_provider(
        db,
        name="Ohne Schluessel",
        provider_kind="openrouter",
        default_model="test-model",
        enabled=True,
        requires_api_key=False,
        operator_api_key=None,
    )
    db.commit()
    assert ohne.id is not None
    assert ai_provider_service.katalogschluessel("openrouter") is None

    _provider(db, monkeypatch)
    assert ai_provider_service.katalogschluessel("openrouter") == "sk-or-v1-operator-secret"


@pytest.mark.asyncio
async def test_adapter_normalizes_sse_and_never_exposes_provider_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = AiProvider(
        id=7,
        name="Adapter",
        provider_kind="openrouter",
        default_model="model-a",
        enabled=True,
        requires_api_key=True,
    )
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        stream = (
            'data: {"choices":[{"delta":{"content":"Hallo "}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"Welt"}}],"usage":{"total_tokens":12}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    usage = StreamUsage()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        chunks = [
            chunk
            async for chunk in stream_chat_completion(
                http_client,
                provider=provider,
                api_key="test-key",
                messages=[{"role": "user", "content": "Hi"}],
                usage=usage,
            )
        ]

    assert [chunk.text for chunk in chunks] == ["Hallo ", "Welt"]
    assert usage.total_tokens == 12
    assert captured["authorization"] == "Bearer test-key"
    assert captured["body"]["stream"] is True


@pytest.mark.asyncio
async def test_adapter_reassembles_fragmented_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = AiProvider(
        id=8,
        name="Adapter Tools",
        provider_kind="openrouter",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
    )
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        stream = (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"propose_","arguments":"{\\"operation\\":"}}]}}]}\n\n'
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"backup","arguments":"\\"restart\\"}"}}]}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    usage = StreamUsage()
    tools = [{"type": "function", "function": {"name": "propose_backup"}}]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        chunks = [chunk async for chunk in stream_chat_completion(
            http_client,
            provider=provider,
            api_key=None,
            messages=[{"role": "user", "content": "restart"}],
            usage=usage,
            tools=tools,
        )]

    assert chunks == []
    assert usage.tool_calls == [ProviderToolCall(
        id="call-1",
        name="propose_backup",
        arguments={"operation": "restart"},
    )]
    assert captured["body"]["tools"] == tools
    assert captured["body"]["tool_choice"] == "auto"


def test_context_redacts_credentials_and_excludes_sensitive_server_fields(
    db: Session,
    regular_user: User,
    test_server,
) -> None:
    test_server.install_dir = "/secret/internal/path"
    test_server.public_bind_ip = "10.0.0.9"
    conversation = AiConversation(
        id=str(uuid4()),
        user_id=regular_user.id,
        server_id=test_server.id,
        title="Context",
    )
    db.add(conversation)
    db.flush()
    db.add(
        AiMessage(
            id=str(uuid4()),
            conversation_id=conversation.id,
            role="user",
            content="api_key=sk-abcdefghijklmnopqrstuvwxyz123456",
            status="complete",
        )
    )
    db.commit()

    messages = build_provider_messages(db, conversation)
    serialized = json.dumps(messages)

    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in serialized
    assert "[REDACTED]" in serialized
    assert test_server.install_dir not in serialized
    assert test_server.public_bind_ip not in serialized
    assert redact_sensitive_text("Authorization: Bearer abc.def.ghi") == "Authorization=[REDACTED]"


def test_chat_stream_persists_usage_and_replays_without_second_provider_call(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_chat(db, regular_user)
    provider = _provider(db, monkeypatch)
    calls = 0

    async def fake_stream(
        _client, *, provider, api_key, messages, usage, tools=None, tool_choice=None,
        reasoning=False, reasoning_effort=None, cache_marke=False, model=None,
    ):
        nonlocal calls
        calls += 1
        assert api_key == "sk-or-v1-operator-secret"
        # Ganz am Ende steht seit der Cache-Umstellung der Lageblock, davor die
        # Frage. Gesucht wird sie deshalb im Gespräch und nicht an einem Index.
        assert any("Wie geht es?" in (item.get("content") or "") for item in messages)
        usage.total_tokens = 42
        yield StreamChunk("content", "Alles ")
        yield StreamChunk("content", "gut.")

    monkeypatch.setattr("services.ai_stream_service.stream_chat_completion", fake_stream)
    created = client.get("/api/ai/conversation", cookies=user_cookies)
    assert created.status_code == 200
    request_id = str(uuid4())
    payload = {
        "content": "Wie geht es?",
        "provider_id": provider.id,
        "request_id": request_id,
    }

    first = client.post(
        "/api/ai/conversation/messages/stream",
        json=payload,
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )
    replay = client.post(
        "/api/ai/conversation/messages/stream",
        json=payload,
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )

    assert first.status_code == 200
    assert "Alles " in first.text and "gut." in first.text
    assert replay.status_code == 200
    assert '"replayed": true' in replay.text
    assert calls == 1
    db.expire_all()
    assistant = db.query(AiMessage).filter(AiMessage.request_id == request_id).one()
    usage = db.query(AiUsageEvent).filter(AiUsageEvent.request_id == request_id).one()
    assert assistant.status == "complete"
    assert assistant.content == "Alles gut."
    assert usage.status == "completed"
    assert usage.accounted_tokens == 42
    assert usage.provider_id == provider.id
    assert usage.model == provider.default_model


def test_server_stream_persists_write_tool_as_proposal_without_execution(
    client: TestClient,
    db: Session,
    owner_user: User,
    owner_cookies: dict,
    test_server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from models import AiActionProposal

    _enable_chat(db, owner_user)
    provider = _provider(db, monkeypatch)

    async def fake_stream(_client, *, usage, tools=None, **_kwargs):
        assert tools and {item["function"]["name"] for item in tools} >= {
            "read_server_status", "propose_backup"
        }
        usage.total_tokens = 8
        usage.tool_calls = [ProviderToolCall(
            id="call-backup",
            name="propose_backup",
            # Zielpunkt 3.6: jedes Schreib-Tool muss begruenden, warum es
            # vorschlaegt und was danach anders sein soll.
            arguments={
                "server_id": test_server.id,
                "reason": "Vor der Konfigurationsaenderung absichern",
                "expected_effect": "Ein wiederherstellbarer Stand liegt vor",
            },
        )]
        if False:
            yield StreamChunk("content", "")

    monkeypatch.setattr("services.ai_stream_service.stream_chat_completion", fake_stream)
    conversation = client.get("/api/ai/conversation", cookies=owner_cookies).json()

    response = client.post(
        "/api/ai/conversation/messages/stream",
        json={
            "content": "Erstelle ein Backup",
            "provider_id": provider.id,
            "request_id": str(uuid4()),
        },
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert response.status_code == 200
    assert "event: proposal" in response.text
    assert "confirmation_token" not in response.text
    proposal = db.query(AiActionProposal).one()
    assert proposal.tool_name == "propose_backup"
    assert proposal.status == "proposed"
    assert db.query(AuditLog).filter(AuditLog.action == "ai.action.executed").count() == 0


def test_a_tool_call_cannot_reach_a_server_the_user_may_not_see(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
    test_server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Die Rechtegrenze haengt am Werkzeugaufruf, nicht mehr am Gespraech.

    Frueher schuetzte die serverbezogene Unterhaltung: wurde `server.view`
    entzogen, verschwand sie. Mit dem Einzelchat gibt es diese Huelle nicht
    mehr — das Modell nennt die `server_id` selbst. Genau deshalb muss
    `_resolve_server` sie jeden einzelnen Aufruf lang pruefen. Ein Modell, das
    eine fremde ID errraet oder aus einem manipulierten Logtext uebernimmt,
    darf damit nichts erreichen.

    Wie die Absage aussieht, hat sich seit dem Schreiben dieses Tests geaendert:
    ein abgewiesener Lesezugriff riss frueher den ganzen Stream mit
    `AI_TOOL_REJECTED` ab. Heute erfaehrt das Modell den Grund und macht weiter,
    und der Verlauf zeigt den Aufruf als `failed`. Die Aussage des Tests ist
    dieselbe geblieben — **es kommt nichts durch** —, nur die Form der Absage
    ist eine bessere.
    """
    _enable_chat(db, regular_user, autonomy=True)
    provider = _provider(db, monkeypatch)
    permission = ServerPermission(
        user_id=regular_user.id,
        server_id=test_server.id,
        permission_key="server.view",
    )
    db.add(permission)
    db.commit()

    async def fake_stream(_client, *, usage, **_kwargs):
        usage.total_tokens = 5
        usage.tool_calls = [ProviderToolCall(
            id="call-status",
            name="read_server_status",
            arguments={"server_id": test_server.id},
        )]
        if False:
            yield StreamChunk("content", "")

    monkeypatch.setattr("services.ai_stream_service.stream_chat_completion", fake_stream)

    db.delete(permission)
    db.commit()
    response = client.post(
        "/api/ai/conversation/messages/stream",
        json={
            "content": "Wie ist der Status?",
            "provider_id": provider.id,
            "request_id": str(uuid4()),
        },
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )

    assert response.status_code == 200
    # Der Aufruf ist als gescheitert im Verlauf sichtbar — eine Antwort, der
    # eine Auskunft fehlt, soll nicht vollstaendig wirken.
    assert '"failed":true' in response.text.replace(" ", "")
    # Kein Serverdatum darf durchgesickert sein.
    assert test_server.name not in response.text
    assert "AI_STREAM_FAILED" not in response.text


def test_the_stream_ends_when_the_provider_calls_tools_that_were_never_offered(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
    test_server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Die Abschlussrunde muss auch dann enden, wenn der Anbieter nicht mitspielt.

    Vier Zweige der Schleife setzen `tools = None`, um zu sagen: das war die
    letzte Runde, antworte jetzt. Das war eine Bitte an den Anbieter, keine
    Grenze — ein Anbieter, der weiter Werkzeugaufrufe meldet, hielt den Stream
    endlos offen und verbrannte bei jedem Durchgang Tokens.

    Genau das lag hier: dieser Testaufbau — ein Anbieter, der unverdrossen
    denselben Aufruf schickt — brachte die Testsuite zum Stillstand, statt
    fehlzuschlagen. Ein Timeout in der Werkstatt, im Betrieb eine offene
    Verbindung mit laufender Abrechnung.

    Der Test haelt die Grenze auf unserer Seite fest: wer nichts anbietet,
    nimmt auch nichts an.
    """
    _enable_chat(db, regular_user)
    provider = _provider(db, monkeypatch)
    runden = {"n": 0}

    async def fake_stream(_client, *, usage, **_kwargs):
        # Meldet in **jeder** Runde einen Werkzeugaufruf, auch wenn ihm keine
        # Werkzeuge angeboten wurden. Ein wohlerzogener Anbieter tut das nicht;
        # darauf darf sich der Server aber nicht verlassen.
        runden["n"] += 1
        usage.total_tokens = 5
        usage.tool_calls = [ProviderToolCall(
            id=f"call-{runden['n']}",
            name="read_server_status",
            arguments={"server_id": test_server.id},
        )]
        if False:
            yield StreamChunk("content", "")

    monkeypatch.setattr("services.ai_stream_service.stream_chat_completion", fake_stream)

    response = client.post(
        "/api/ai/conversation/messages/stream",
        json={
            "content": "Wie ist der Status?",
            "provider_id": provider.id,
            "request_id": str(uuid4()),
        },
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )

    assert response.status_code == 200
    # Die eigentliche Aussage: der Aufruf kehrt zurueck. Ohne die Grenze haenge
    # dieser Test hier fuer immer.
    assert "event: done" in response.text
    # Und er endet begrenzt: die Werkzeugrunden plus die eine Abschlussrunde,
    # in der die Aufrufe verworfen werden. Ein Anbieter kann die Zahl der
    # Durchgaenge nicht selbst bestimmen.
    assert runden["n"] <= MAX_TOOL_ROUNDS + 2, f"{runden['n']} Anbieterrunden"


def test_provider_catalog_hides_admin_metadata_from_chat_user(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_chat(db, regular_user)
    _provider(db, monkeypatch)

    response = client.get("/api/ai/providers", cookies=user_cookies)

    assert response.status_code == 200
    serialized = response.text
    assert "base_url" not in serialized
    assert "operator_key_hint" not in serialized
    assert "operator-secret-value" not in serialized


def test_the_choice_list_passes_the_key_to_a_catalog_that_needs_one(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sonst fehlen bei OpenAI die Denkstufen — ohne dass irgendwo ein Fehler steht.

    OpenAIs ``/v1/models`` gibt seine Liste nur gegen einen Schlüssel heraus
    (`Anbieter.katalog_braucht_schluessel`). Der Aufruf hier lief ohne, `finde`
    endete im 401 und gab ``None`` zurück — und weil die drei Denkfelder nur
    innerhalb von ``if modell is not None`` gesetzt werden, blieben sie auf
    ihren Vorgaben stehen. Die Oberfläche zeigte deshalb keine Denkstufen, und
    zwar **still**: kein 500, keine Meldung, nur eine Auswahl, die es nicht gab.

    Geprüft wird die Weitergabe und nicht die Anzeige — was der Katalog daraus
    macht, ist in `test_ai_model_catalog` festgenagelt.
    """
    _enable_chat(db, regular_user)
    provider = ai_provider_service.create_provider(
        db,
        name="OpenAI direkt",
        provider_kind="openai",
        default_model="gpt-5.5",
        enabled=True,
        requires_api_key=True,
        operator_api_key="sk-geheim-nicht-loggen",
    )
    db.commit()

    gesehen: dict = {}

    async def fake_finde(_client, kind, model_id, *, schluessel=None):
        gesehen["kind"] = kind
        gesehen["hat_schluessel"] = bool(schluessel)
        return None

    monkeypatch.setattr("services.ai_model_catalog.finde", fake_finde)

    response = client.get("/api/ai/providers", cookies=user_cookies)

    assert response.status_code == 200
    assert gesehen["kind"] == "openai"
    assert gesehen["hat_schluessel"] is True, (
        "Ohne Schluessel antwortet OpenAIs Katalog mit 401 — und die Denkstufen "
        "fehlen in der Oberflaeche, ohne dass ein Fehler sichtbar wird"
    )
    # Und der Schluessel bleibt, wo er hingehoert: nicht in der Antwort.
    assert "sk-geheim" not in response.text
    assert provider.id in [eintrag["id"] for eintrag in response.json()]


def test_every_user_has_exactly_one_conversation_and_cannot_create_a_second(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
) -> None:
    """Der Assistent ist ein Gespraech, keine Ablage.

    Frueher gab es getrennte Listen fuer globale und serverbezogene Chats. Das
    ist ersatzlos entfallen: wiederholte Aufrufe liefern dieselbe Unterhaltung,
    und die Routen zum Anlegen und Auflisten existieren nicht mehr.
    """
    _enable_chat(db, regular_user)

    first = client.get("/api/ai/conversation", cookies=user_cookies)
    second = client.get("/api/ai/conversation", cookies=user_cookies)

    assert first.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert db.query(AiConversation).filter(
        AiConversation.user_id == regular_user.id
    ).count() == 1
    # Die alten Routen sind weg — nicht nur im Frontend ausgeblendet.
    assert client.post(
        "/api/ai/conversations",
        json={"title": "Zweiter Chat"},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    ).status_code == 404


def test_clearing_the_history_keeps_the_conversation_and_the_audit(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
) -> None:
    """Geloescht wird der Verlauf, nicht die Nachvollziehbarkeit."""
    _enable_chat(db, regular_user)
    conversation_id = client.get("/api/ai/conversation", cookies=user_cookies).json()["id"]
    db.add(AiMessage(
        id=str(uuid4()), conversation_id=conversation_id, role="user",
        content="Alte Nachricht", status="complete",
    ))
    db.commit()

    cleared = client.delete(
        "/api/ai/conversation/messages",
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )

    assert cleared.status_code == 204
    detail = client.get("/api/ai/conversation", cookies=user_cookies).json()
    assert detail["id"] == conversation_id
    assert detail["messages"] == []


def test_clearing_the_history_ends_the_open_run_and_wipes_its_plaintext(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
) -> None:
    """Löschen heißt löschen — auch für das Arbeitsgedächtnis eines Laufs.

    Ein geparkter Lauf trägt in `ai_runs.state_json` seine vollständigen
    `provider_messages`: den ganzen Verlauf und den entschlüsselten
    Gedächtnisblock, im Klartext, in einer gewöhnlichen Textspalte.
    `arbeitsspeicher_leeren` räumt ihn nur bei einem Endzustand, und
    `waiting_confirmation` ist keiner — der eben gelöschte Text lag also
    weiter da, und ein nachträglicher Klick auf die noch offene Karte weckte
    den Lauf Wochen später in einen Chat, den es nicht mehr gibt.
    """
    _enable_chat(db, regular_user)
    conversation_id = client.get("/api/ai/conversation", cookies=user_cookies).json()["id"]
    # Frei erfunden und harmlos: es geht darum, dass der Satz **verschwindet**,
    # nicht darum, was er sagt.
    merksatz = "Der Betreiber wohnt in der Beispielstrasse 3"
    run = AiRun(
        id=str(uuid4()),
        conversation_id=conversation_id,
        user_id=regular_user.id,
        status="waiting_confirmation",
    )
    ai_run_service.zustand_schreiben(
        run,
        ai_run_service.leerer_zustand(
            [{"role": "user", "content": merksatz}], request_id=str(uuid4())
        ),
    )
    db.add(run)
    db.commit()

    cleared = client.delete(
        "/api/ai/conversation/messages",
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )

    assert cleared.status_code == 204
    db.refresh(run)
    assert run.status in BEENDET, (
        "ein geparkter Lauf darf den geleerten Chat nicht ueberleben — sonst "
        "wacht er spaeter auf und antwortet aus dem geloeschten Zusammenhang"
    )
    assert merksatz not in (run.state_json or "")


def test_quota_rejection_never_calls_provider_or_persists_message(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_chat(db, regular_user)
    role = db.query(Role).filter(Role.name == f"ai-chat-{regular_user.id}").one()
    set_role_limit(db, role.id, {
        **{field: None for field in LIMIT_FIELDS},
        "daily_token_limit": 0,
    })
    db.commit()
    provider = _provider(db, monkeypatch)
    calls = 0

    async def forbidden_provider(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        yield StreamChunk("content", "unexpected")

    monkeypatch.setattr("services.ai_stream_service.stream_chat_completion", forbidden_provider)
    created = client.get("/api/ai/conversation", cookies=user_cookies).json()
    response = client.post(
        "/api/ai/conversation/messages/stream",
        json={"content": "Hello", "provider_id": provider.id, "request_id": str(uuid4())},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )

    assert response.status_code == 200
    assert "AI_QUOTA_DAILY_TOKEN_LIMIT" in response.text
    assert calls == 0
    assert db.query(AiMessage).count() == 0
    assert db.query(AiUsageEvent).count() == 0


def test_partial_provider_failure_is_persisted_and_accounted_conservatively(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_chat(db, regular_user)
    provider = _provider(db, monkeypatch)

    async def partial_stream(*_args, **_kwargs):
        yield StreamChunk("content", "partial")
        raise AiProviderRequestError("AI_PROVIDER_UNAVAILABLE")

    monkeypatch.setattr("services.ai_stream_service.stream_chat_completion", partial_stream)
    conversation = client.get("/api/ai/conversation", cookies=user_cookies).json()
    response = client.post(
        "/api/ai/conversation/messages/stream",
        json={"content": "Hello", "provider_id": provider.id, "request_id": str(uuid4())},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )

    assert "partial" in response.text
    assert "AI_PROVIDER_UNAVAILABLE" in response.text
    db.expire_all()
    assistant = db.query(AiMessage).filter(AiMessage.role == "assistant").one()
    usage = db.query(AiUsageEvent).one()
    assert assistant.status == "failed"
    assert assistant.content == "partial"
    assert usage.status == "completed"
    assert usage.accounted_tokens == usage.reserved_tokens


def test_startup_recovery_closes_stream_and_keeps_full_reservation(
    db: Session,
    regular_user: User,
) -> None:
    conversation = AiConversation(
        id=str(uuid4()), user_id=regular_user.id, title="Interrupted", server_id=None
    )
    request_id = str(uuid4())
    db.add(conversation)
    db.flush()
    db.add(AiMessage(
        id=str(uuid4()),
        conversation_id=conversation.id,
        role="assistant",
        content="partial",
        status="streaming",
        request_id=request_id,
    ))
    db.add(AiUsageEvent(
        request_id=request_id,
        user_id=regular_user.id,
        status="reserved",
        reserved_tokens=321,
        reserved_cost_microunits=0,
        accounted_tokens=321,
        accounted_cost_microunits=0,
    ))
    db.commit()

    assert ai_chat_service.reconcile_interrupted_ai_streams(db) == 1
    assert db.query(AiMessage).one().status == "failed"
    usage = db.query(AiUsageEvent).one()
    assert usage.status == "completed"
    assert usage.accounted_tokens == 321


@pytest.mark.asyncio
async def test_adapter_stops_a_stream_that_never_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein endlos tropfender Provider darf die Reservierung nicht ewig halten."""
    monkeypatch.setattr(
        "services.openai_compatible_adapter.MAX_STREAM_FRAMES", 5
    )
    provider = AiProvider(
        id=104,
        name="Endless",
        provider_kind="openrouter",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        # Kein [DONE] und mehr Frames als erlaubt.
        stream = 'data: {"choices":[{"delta":{"content":"x"}}]}\n\n' * 50
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    usage = StreamUsage()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(AiProviderRequestError) as excinfo:
            async for _ in stream_chat_completion(
                http_client,
                provider=provider,
                api_key=None,
                messages=[{"role": "user", "content": "Hi"}],
                usage=usage,
            ):
                pass

    assert excinfo.value.code == "AI_PROVIDER_RESPONSE_TOO_LARGE"


@pytest.mark.asyncio
async def test_adapter_bounds_a_single_unterminated_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein Provider ohne Zeilenumbruch darf den Panel-Prozess nicht fluten."""
    monkeypatch.setattr(
        "services.openai_compatible_adapter.MAX_STREAM_LINE_CHARS", 500
    )
    provider = AiProvider(
        id=105,
        name="Flood",
        provider_kind="openrouter",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="data: " + "y" * 5_000,  # bewusst ohne \n
            headers={"content-type": "text/event-stream"},
        )

    usage = StreamUsage()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(AiProviderRequestError) as excinfo:
            async for _ in stream_chat_completion(
                http_client,
                provider=provider,
                api_key=None,
                messages=[{"role": "user", "content": "Hi"}],
                usage=usage,
            ):
                pass

    assert excinfo.value.code == "AI_PROVIDER_RESPONSE_TOO_LARGE"


def test_finalization_settles_usage_even_if_the_message_is_gone(
    db: Session,
    regular_user: User,
) -> None:
    """Eine verwaiste Reservierung muss abgerechnet werden, nicht stillschweigend bleiben.

    Wird der Chat waehrend eines laufenden Streams geloescht, existiert die
    Assistant-Nachricht nicht mehr. Vorher brach die Finalisierung dann
    kommentarlos ab — die Reservierung blieb "reserved" und blockierte bis zum
    Prozessneustart Kontingent und einen Nebenlaeufigkeitsplatz.
    """
    from services.ai_stream_service import _finalize_stream

    request_id = str(uuid4())
    usage_event = AiUsageEvent(
        request_id=request_id,
        user_id=regular_user.id,
        status="reserved",
        reserved_tokens=150,
        reserved_cost_microunits=0,
        accounted_tokens=150,
        accounted_cost_microunits=0,
    )
    db.add(usage_event)
    db.commit()
    db.refresh(usage_event)

    _finalize_stream(
        message_id=str(uuid4()),  # existiert bewusst nicht
        usage_event_id=usage_event.id,
        content="ignoriert",
        usage=StreamUsage(),
        estimated_actual_tokens=0,
        failed=True,
        had_output=True,
    )

    db.expire_all()
    settled = db.query(AiUsageEvent).filter(AiUsageEvent.request_id == request_id).one()
    assert settled.status == "completed"
    assert settled.accounted_tokens == 150


def test_cost_limit_actually_blocks_once_a_token_price_is_configured(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Das monatliche Kostenlimit muss wirken, nicht nur konfigurierbar sein.

    Ohne Preisquelle wurde jeder Verbrauch mit null Kosten verbucht — ein
    Betreiber konnte ein Limit setzen und war trotzdem ungeschuetzt. Mit
    gepflegtem Providerpreis greift die Grenze und der Provider wird gar nicht
    erst aufgerufen.
    """
    _enable_chat(db, regular_user)
    role = db.query(Role).filter(Role.name == f"ai-chat-{regular_user.id}").one()
    set_role_limit(db, role.id, {
        **{field: None for field in LIMIT_FIELDS},
        "monthly_cost_limit_cents": 1,
    })
    db.commit()
    provider = _provider(db, monkeypatch)
    # 100.000 Cent je Million Tokens: schon eine kleine Anfrage sprengt 1 Cent.
    provider.token_price_micro_usd_per_million = 100_000 * MICROUNITS_PER_CENT
    db.commit()
    calls = 0

    async def forbidden_provider(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        yield StreamChunk("content", "unexpected")

    monkeypatch.setattr("services.ai_stream_service.stream_chat_completion", forbidden_provider)
    created = client.get("/api/ai/conversation", cookies=user_cookies).json()
    response = client.post(
        "/api/ai/conversation/messages/stream",
        json={"content": "Hallo", "provider_id": provider.id, "request_id": str(uuid4())},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )

    assert response.status_code == 200
    assert "AI_QUOTA_MONTHLY_COST_LIMIT_CENTS" in response.text
    assert calls == 0
    assert db.query(AiUsageEvent).count() == 0


def test_without_a_token_price_cost_stays_zero_and_the_limit_does_not_fire(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MSM erfindet keinen Preis: ohne Konfiguration bleiben die Kosten null."""
    provider = _provider(db, monkeypatch)

    assert provider.token_price_micro_usd_per_million is None
    assert ai_provider_service.estimate_cost_microunits(provider, 1_000_000) == 0

    # 3,00 USD je 1 Mio. Tokens. In Microunits, damit auch „1,20" eintragbar
    # ist — in ganzen Cent lag zwischen 1 und 2 nichts.
    provider.token_price_micro_usd_per_million = 300 * MICROUNITS_PER_CENT
    # 1.000.000 Tokens zum Preis von 1.000.000 Tokens: genau der Preis selbst.
    assert ai_provider_service.estimate_cost_microunits(provider, 1_000_000) == 3_000_000
    # Und die Nachkommastelle, die vorher nicht darstellbar war: 1,20 USD.
    provider.token_price_micro_usd_per_million = 120 * MICROUNITS_PER_CENT
    assert ai_provider_service.estimate_cost_microunits(provider, 1_000_000) == 1_200_000
    assert ai_provider_service.estimate_cost_microunits(provider, 0) == 0
