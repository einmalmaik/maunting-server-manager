"""Security-Invarianten fuer Provider, Credentials und persistente AI-Chats."""

from __future__ import annotations

import ipaddress
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
    AiUserCredential,
    AiUsageEvent,
    AuditLog,
    Role,
    RolePermission,
    ServerPermission,
    User,
)
from services import ai_chat_service, ai_provider_service
from services.ai_context_service import build_provider_messages, redact_sensitive_text
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.openai_compatible_adapter import (
    AiProviderRequestError,
    ProviderToolCall,
    StreamUsage,
    stream_chat_completion,
)
from services.role_service import set_user_roles


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ai_provider_service,
        "_resolved_addresses",
        lambda _host: {ipaddress.ip_address("93.184.216.34")},
    )


def _enable_chat(db: Session, user: User) -> None:
    role = Role(name=f"ai-chat-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    set_role_limit(db, role.id, {field: None for field in LIMIT_FIELDS})
    db.commit()
    set_user_roles(db, user, [role.id])


def _provider(db: Session, monkeypatch: pytest.MonkeyPatch) -> AiProvider:
    _public_dns(monkeypatch)
    provider = ai_provider_service.create_provider(
        db,
        name="Test Provider",
        base_url="https://api.example.invalid/v1",
        default_model="test-model",
        enabled=True,
        requires_api_key=True,
        allow_private_network=False,
        operator_api_key="operator-secret-value",
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
    _public_dns(monkeypatch)
    secret = "provider-secret-1234"

    response = client.post(
        "/api/ai/settings/providers",
        json={
            "name": "OpenAI Compatible",
            "base_url": "https://api.example.invalid/v1",
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


def test_user_credential_is_aad_bound_and_preferred(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_chat(db, regular_user)
    provider = _provider(db, monkeypatch)

    response = client.put(
        f"/api/ai/providers/{provider.id}/credential",
        json={"api_key": "user-secret-9876"},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )

    assert response.status_code == 200
    assert response.json()["key_hint"].endswith("9876")
    assert ai_provider_service.resolve_api_key(db, provider, regular_user.id) == "user-secret-9876"
    from services.dis_client import DisClient, DisDecryptionError

    row = db.query(AiUserCredential).one()
    with pytest.raises(DisDecryptionError):
        DisClient.decrypt(
            row.api_key_encrypted,
            aad=f"msm:ai:provider:{provider.id}:user:999:api-key",
        )


def test_provider_url_policy_blocks_metadata_and_requires_private_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ai_provider_service,
        "_resolved_addresses",
        lambda _host: {ipaddress.ip_address("169.254.169.254")},
    )
    with pytest.raises(ai_provider_service.AiProviderConfigurationError):
        ai_provider_service.validate_provider_base_url(
            "http://169.254.169.254/v1", allow_private_network=True
        )

    monkeypatch.setattr(
        ai_provider_service,
        "_resolved_addresses",
        lambda _host: {ipaddress.ip_address("127.0.0.1")},
    )
    with pytest.raises(ai_provider_service.AiProviderConfigurationError):
        ai_provider_service.validate_provider_base_url(
            "http://localhost:11434/v1", allow_private_network=False
        )
    assert ai_provider_service.validate_provider_base_url(
        "http://localhost:11434/v1/", allow_private_network=True
    ) == "http://localhost:11434/v1"

    _public_dns(monkeypatch)
    with pytest.raises(ai_provider_service.AiProviderConfigurationError):
        ai_provider_service.validate_provider_base_url(
            "http://api.example.invalid/v1", allow_private_network=True
        )


@pytest.mark.asyncio
async def test_adapter_normalizes_sse_and_never_exposes_provider_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = AiProvider(
        id=7,
        name="Adapter",
        base_url="https://api.example.invalid/v1",
        default_model="model-a",
        enabled=True,
        requires_api_key=True,
        allow_private_network=False,
    )
    monkeypatch.setattr(
        "services.openai_compatible_adapter.assert_provider_destination",
        lambda _provider: None,
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

    assert chunks == ["Hallo ", "Welt"]
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
        base_url="https://api.example.invalid/v1",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
        allow_private_network=False,
    )
    monkeypatch.setattr(
        "services.openai_compatible_adapter.assert_provider_destination",
        lambda _provider: None,
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

    async def fake_stream(_client, *, provider, api_key, messages, usage, tools=None):
        nonlocal calls
        calls += 1
        assert api_key == "operator-secret-value"
        assert messages[-1]["content"] == "Wie geht es?"
        usage.total_tokens = 42
        yield "Alles "
        yield "gut."

    monkeypatch.setattr("services.ai_stream_service.stream_chat_completion", fake_stream)
    created = client.post(
        "/api/ai/conversations",
        json={"title": "Runtime Chat"},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    request_id = str(uuid4())
    payload = {
        "content": "Wie geht es?",
        "provider_id": provider.id,
        "request_id": request_id,
    }

    first = client.post(
        f"/api/ai/conversations/{conversation_id}/messages/stream",
        json=payload,
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )
    replay = client.post(
        f"/api/ai/conversations/{conversation_id}/messages/stream",
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

    async def fake_stream(_client, *, usage, tools, **_kwargs):
        assert tools and {item["function"]["name"] for item in tools} >= {
            "read_server_status", "propose_backup"
        }
        usage.total_tokens = 8
        usage.tool_calls = [ProviderToolCall(
            id="call-backup",
            name="propose_backup",
            arguments={},
        )]
        if False:
            yield ""

    monkeypatch.setattr("services.ai_stream_service.stream_chat_completion", fake_stream)
    conversation = client.post(
        "/api/ai/conversations",
        json={"title": "Server Action", "server_id": test_server.id},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    ).json()

    response = client.post(
        f"/api/ai/conversations/{conversation['id']}/messages/stream",
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


def test_server_chat_is_hidden_after_view_permission_is_revoked(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
    test_server,
) -> None:
    _enable_chat(db, regular_user)
    permission = ServerPermission(
        user_id=regular_user.id,
        server_id=test_server.id,
        permission_key="server.view",
    )
    db.add(permission)
    db.commit()
    created = client.post(
        "/api/ai/conversations",
        json={"title": "Server Chat", "server_id": test_server.id},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )
    assert created.status_code == 201

    db.delete(permission)
    db.commit()
    hidden = client.get(
        f"/api/ai/conversations/{created.json()['id']}",
        cookies=user_cookies,
    )
    assert hidden.status_code == 404


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


def test_global_and_server_conversation_lists_are_separate(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
    test_server,
) -> None:
    _enable_chat(db, regular_user)
    db.add(ServerPermission(
        user_id=regular_user.id,
        server_id=test_server.id,
        permission_key="server.view",
    ))
    db.commit()
    for payload in (
        {"title": "Global"},
        {"title": "Server", "server_id": test_server.id},
    ):
        assert client.post(
            "/api/ai/conversations",
            json=payload,
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        ).status_code == 201

    global_rows = client.get("/api/ai/conversations", cookies=user_cookies).json()
    server_rows = client.get(
        f"/api/ai/conversations?server_id={test_server.id}", cookies=user_cookies
    ).json()

    assert [row["title"] for row in global_rows] == ["Global"]
    assert [row["title"] for row in server_rows] == ["Server"]


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
        yield "unexpected"

    monkeypatch.setattr("services.ai_stream_service.stream_chat_completion", forbidden_provider)
    created = client.post(
        "/api/ai/conversations",
        json={"title": "Quota"},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    ).json()
    response = client.post(
        f"/api/ai/conversations/{created['id']}/messages/stream",
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
        yield "partial"
        raise AiProviderRequestError("AI_PROVIDER_UNAVAILABLE")

    monkeypatch.setattr("services.ai_stream_service.stream_chat_completion", partial_stream)
    conversation = client.post(
        "/api/ai/conversations",
        json={"title": "Partial"},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    ).json()
    response = client.post(
        f"/api/ai/conversations/{conversation['id']}/messages/stream",
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


def test_provider_destination_returns_pinnable_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Die Zielpruefung muss ihr Ergebnis herausgeben, nicht nur wegwerfen.

    Loest der HTTP-Client den Namen spaeter erneut auf, kann er eine andere —
    etwa interne — Adresse erhalten. Die geprueft freigegebene Adresse ist
    deshalb Teil des Vertrags.
    """
    _public_dns(monkeypatch)
    named = AiProvider(
        id=101,
        name="Named",
        base_url="https://api.example.invalid/v1",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
        allow_private_network=False,
    )
    assert ai_provider_service.assert_provider_destination(named) == "93.184.216.34"

    literal = AiProvider(
        id=102,
        name="Literal",
        base_url="https://93.184.216.34/v1",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
        allow_private_network=False,
    )
    # Bei einem IP-Literal gibt es nichts zu pinnen.
    assert ai_provider_service.assert_provider_destination(literal) is None


@pytest.mark.asyncio
async def test_adapter_connects_to_the_validated_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der Request geht an die freigegebene IP, Host-Header bleibt der Name."""
    monkeypatch.setattr(
        "services.openai_compatible_adapter.assert_provider_destination",
        lambda _provider: "93.184.216.34",
    )
    provider = AiProvider(
        id=103,
        name="Pinned",
        base_url="https://api.example.invalid/v1",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
        allow_private_network=False,
    )
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["host_in_url"] = request.url.host
        seen["host_header"] = request.headers.get("host")
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )

    usage = StreamUsage()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        chunks = [
            chunk
            async for chunk in stream_chat_completion(
                http_client,
                provider=provider,
                api_key=None,
                messages=[{"role": "user", "content": "Hi"}],
                usage=usage,
            )
        ]

    assert chunks == ["ok"]
    assert seen["host_in_url"] == "93.184.216.34"
    assert seen["host_header"] == "api.example.invalid"
    assert seen["sni"] == "api.example.invalid"


@pytest.mark.asyncio
async def test_adapter_stops_a_stream_that_never_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein endlos tropfender Provider darf die Reservierung nicht ewig halten."""
    monkeypatch.setattr(
        "services.openai_compatible_adapter.assert_provider_destination",
        lambda _provider: None,
    )
    monkeypatch.setattr(
        "services.openai_compatible_adapter.MAX_STREAM_FRAMES", 5
    )
    provider = AiProvider(
        id=104,
        name="Endless",
        base_url="https://api.example.invalid/v1",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
        allow_private_network=False,
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
        "services.openai_compatible_adapter.assert_provider_destination",
        lambda _provider: None,
    )
    monkeypatch.setattr(
        "services.openai_compatible_adapter.MAX_STREAM_LINE_CHARS", 500
    )
    provider = AiProvider(
        id=105,
        name="Flood",
        base_url="https://api.example.invalid/v1",
        default_model="model-a",
        enabled=True,
        requires_api_key=False,
        allow_private_network=False,
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
        provider_total_tokens=None,
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
    provider.token_price_cents_per_million = 100_000
    db.commit()
    calls = 0

    async def forbidden_provider(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        yield "unexpected"

    monkeypatch.setattr("services.ai_stream_service.stream_chat_completion", forbidden_provider)
    created = client.post(
        "/api/ai/conversations",
        json={"title": "Kosten"},
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    ).json()
    response = client.post(
        f"/api/ai/conversations/{created['id']}/messages/stream",
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

    assert provider.token_price_cents_per_million is None
    assert ai_provider_service.estimate_cost_microunits(provider, 1_000_000) == 0

    provider.token_price_cents_per_million = 300  # 3,00 EUR je 1M Tokens
    # 1.000.000 Tokens * 300 Cent / 100 = 3.000.000 Microunits = 300 Cent.
    assert ai_provider_service.estimate_cost_microunits(provider, 1_000_000) == 3_000_000
    assert ai_provider_service.estimate_cost_microunits(provider, 0) == 0
