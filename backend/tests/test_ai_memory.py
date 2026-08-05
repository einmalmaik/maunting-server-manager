"""Ownership-, Secret- und Opt-out-Invarianten fuer AI-Memory."""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiConversation, AiMemoryEntry, Role, RolePermission, User
from services.ai_context_service import build_provider_messages
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.role_service import set_user_roles


def _enable_memory(db: Session, user: User) -> None:
    role = Role(name=f"memory-{user.id}", is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.memory.use"))
    set_role_limit(db, role.id, {field: None for field in LIMIT_FIELDS})
    db.commit()
    set_user_roles(db, user, [role.id])


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def test_memory_api_stores_ciphertext_and_returns_owned_plaintext(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
) -> None:
    _enable_memory(db, regular_user)
    value = "Antwortsprache ist Deutsch"

    saved = client.put(
        "/api/ai/memory",
        json={"scope": "user", "key": "language", "value": value},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    )
    listed = client.get(
        "/api/ai/memory?scope=user", cookies=user_cookies
    )

    assert saved.status_code == 200
    assert listed.status_code == 200
    assert listed.json()[0]["value"] == value
    row = db.query(AiMemoryEntry).one()
    assert row.value_encrypted != value
    assert value not in row.value_encrypted


def test_memory_rejects_secret_like_content_without_persistence(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
) -> None:
    _enable_memory(db, regular_user)

    response = client.put(
        "/api/ai/memory",
        json={"scope": "user", "key": "bad", "value": "api_key=do-not-store-this"},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    )

    assert response.status_code == 422
    assert db.query(AiMemoryEntry).count() == 0


def test_panel_memory_write_requires_settings_permission_but_is_visible(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    owner_cookies: dict,
) -> None:
    _enable_memory(db, regular_user)
    denied = client.put(
        "/api/ai/memory",
        json={"scope": "panel", "key": "maintenance", "value": "Sonntag 03:00 UTC"},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    )
    created = client.put(
        "/api/ai/memory",
        json={"scope": "panel", "key": "maintenance", "value": "Sonntag 03:00 UTC"},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )
    visible = client.get("/api/ai/memory?scope=panel", cookies=user_cookies)

    assert denied.status_code == 403
    assert created.status_code == 200
    assert visible.status_code == 200
    assert visible.json()[0]["key"] == "maintenance"


def test_disabled_memory_is_not_added_to_provider_context(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
) -> None:
    _enable_memory(db, regular_user)
    assert client.put(
        "/api/ai/memory",
        json={"scope": "user", "key": "language", "value": "Deutsch bevorzugt"},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    ).status_code == 200
    conversation = AiConversation(
        id=str(uuid4()), user_id=regular_user.id, server_id=None, title="Memory"
    )
    db.add(conversation)
    db.commit()
    enabled_context = str(build_provider_messages(db, conversation))
    assert "Deutsch bevorzugt" in enabled_context

    disabled = client.put(
        "/api/ai/memory/preference",
        json={"enabled": False},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    )
    assert disabled.status_code == 200
    assert "Deutsch bevorzugt" not in str(build_provider_messages(db, conversation))
