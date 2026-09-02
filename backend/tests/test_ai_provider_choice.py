"""Die Modellwahl folgt dem Konto, nicht dem Browser.

Anlass (22.08.2026): Die Wahl des KI-Zugangs lag allein im localStorage des
Browsers. Die Desktop-App hat eine eigene Herkunft (tauri.localhost) und damit
einen leeren Speicher — ihr Chat lief still auf dem erstbesten Zugang, ihr
Sprach-Overlay auf der Backend-Reihenfolge: ein anderes (womöglich deutlich
langsameres) Modell, als der Benutzer im Panel gewählt hatte, und niemand sah
es. Seitdem: `users.ai_provider_id` über PATCH /auth/me/ai-provider, gelesen
vom Chat beim Öffnen und vom Sprachmodus, wenn keine explizite Wahl mitkommt
(test_ai_voice_router.test_die_gespeicherte_modellwahl_traegt_den_sprachmodus).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiProvider, Role, RolePermission, User
from services.role_service import set_user_roles


def _chatzugang(db: Session, name: str = "OpenRouter") -> AiProvider:
    zugang = AiProvider(
        name=name,
        provider_kind="openrouter",
        default_model="openai/gpt-5.6-luna",
        enabled=True,
        requires_api_key=True,
    )
    db.add(zugang)
    db.commit()
    return zugang


def _chat_erlauben(db: Session, user: User) -> None:
    """Dieselbe Schranke wie die Auswahlliste: `ai.chat.use` an der Rolle."""
    role = Role(name="chat-nutzer", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    db.commit()
    set_user_roles(db, user, [role.id])


@pytest.fixture
def chat_user(db: Session, regular_user: User) -> User:
    _chat_erlauben(db, regular_user)
    return regular_user


class TestModellwahlApi:
    def test_ohne_chatrecht_gibt_es_keine_modellwahl(
        self, client: TestClient, db: Session, regular_user: User,
        user_cookies: dict, user_csrf_token: str,
    ):
        # Wer den Chat nicht nutzen darf, speichert auch keine Wahl — dieselbe
        # Schranke wie `/api/ai/providers`.
        zugang = _chatzugang(db)
        resp = client.patch(
            "/api/auth/me/ai-provider",
            json={"provider_id": zugang.id},
            headers={"X-CSRF-Token": user_csrf_token},
            cookies=user_cookies,
        )
        assert resp.status_code == 403
        db.refresh(regular_user)
        assert regular_user.ai_provider_id is None

    def test_patch_speichert_die_wahl_und_me_liefert_sie(
        self, client: TestClient, db: Session, chat_user: User,
        user_cookies: dict, user_csrf_token: str,
    ):
        zugang = _chatzugang(db)
        resp = client.patch(
            "/api/auth/me/ai-provider",
            json={"provider_id": zugang.id},
            headers={"X-CSRF-Token": user_csrf_token},
            cookies=user_cookies,
        )
        assert resp.status_code == 200
        assert resp.json()["ai_provider_id"] == zugang.id
        me = client.get("/api/auth/me", cookies=user_cookies)
        assert me.json()["ai_provider_id"] == zugang.id

    def test_null_loescht_die_wahl(
        self, client: TestClient, db: Session, chat_user: User,
        user_cookies: dict, user_csrf_token: str,
    ):
        chat_user.ai_provider_id = _chatzugang(db).id
        db.commit()
        resp = client.patch(
            "/api/auth/me/ai-provider",
            json={"provider_id": None},
            headers={"X-CSRF-Token": user_csrf_token},
            cookies=user_cookies,
        )
        assert resp.status_code == 200
        assert resp.json()["ai_provider_id"] is None

    def test_unbekannter_zugang_wird_nicht_gespeichert(
        self, client: TestClient, db: Session, chat_user: User,
        user_cookies: dict, user_csrf_token: str,
    ):
        resp = client.patch(
            "/api/auth/me/ai-provider",
            json={"provider_id": 999_999},
            headers={"X-CSRF-Token": user_csrf_token},
            cookies=user_cookies,
        )
        assert resp.status_code == 404
        db.refresh(chat_user)
        assert chat_user.ai_provider_id is None

    def test_ein_reiner_stimmzugang_ist_keine_chatwahl(
        self, client: TestClient, db: Session, chat_user: User,
        user_cookies: dict, user_csrf_token: str,
    ):
        # Dieselbe Grenze wie beim Senden einer Nachricht: was der Chat-Router
        # mit 404 abwiese, wird hier gar nicht erst gespeichert.
        stimme = AiProvider(
            name="Stimme",
            provider_kind="elevenlabs",
            default_model="eleven_flash_v2_5",
            enabled=True,
            requires_api_key=True,
            default_voice="21m00Tcm4TlvDq8ikWAM",
        )
        db.add(stimme)
        db.commit()
        resp = client.patch(
            "/api/auth/me/ai-provider",
            json={"provider_id": stimme.id},
            headers={"X-CSRF-Token": user_csrf_token},
            cookies=user_cookies,
        )
        assert resp.status_code == 404
