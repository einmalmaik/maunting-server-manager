"""Websuche — endlich mit Wirkung, und mit klaren Grenzen.

`ai.web_search.use` stand seit Monaten im Rechtekatalog und wurde an keiner
Stelle geprueft. Ein Schalter, der nichts bewirkt, ist schlimmer als ein
fehlender: der Betreiber haelt etwas fuer freigeschaltet oder begrenzt, was es
nicht ist.

Die Tests halten drei Dinge fest: das Recht greift, Fremdtext aus dem Web wird
wie Fremdtext behandelt, und ein fehlender Schluessel legt nichts lahm.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.orm import Session

from models import Role, RolePermission, User
from services import ai_action_service, ai_web_search_service
from services.role_service import set_user_roles


def _allow_search(db: Session, user: User) -> None:
    role = Role(name=f"suche-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.web_search.use"))
    db.commit()
    set_user_roles(db, user, [role.id])


def _mock_response(monkeypatch: pytest.MonkeyPatch, payload: dict, status: int = 200) -> dict:
    """Ersetzt den Suchaufruf und gibt zurueck, was tatsaechlich gesendet wurde."""
    seen: dict = {}

    def fake_get(url, *, params=None, headers=None, timeout=None):
        seen["url"] = url
        seen["params"] = params or {}
        seen["headers"] = headers or {}
        return httpx.Response(status, json=payload)

    monkeypatch.setattr(ai_web_search_service.httpx, "get", fake_get)
    monkeypatch.setattr(ai_web_search_service, "api_key", lambda: "test-schluessel")
    return seen


# ── Das Recht greift ──────────────────────────────────────────────────────

def test_search_without_the_permission_is_rejected(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_response(monkeypatch, {"web": {"results": []}})

    with pytest.raises(ai_action_service.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="web_search",
            arguments={"query": "irgendwas"},
        )


def test_a_missing_key_is_reported_honestly_not_as_no_results(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine leere Trefferliste waere eine falsche Aussage ueber das Web."""
    _allow_search(db, regular_user)
    monkeypatch.setattr(ai_web_search_service, "api_key", lambda: None)

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="web_search",
        arguments={"query": "minecraft port"},
    )

    assert result["available"] is False
    assert result["reason"] == "AI_WEB_SEARCH_NOT_CONFIGURED"
    assert result["results"] == []


# ── Der Katalog ───────────────────────────────────────────────────────────

def test_without_a_key_the_tool_is_not_even_offered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein Werkzeug, das immer scheitert, verwirrt ein Modell mehr als es hilft."""
    monkeypatch.setattr(ai_web_search_service, "is_configured", lambda: False)

    names = {item["function"]["name"] for item in ai_action_service.provider_tool_definitions()}

    assert "web_search" not in names


def test_with_a_key_the_tool_appears(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_web_search_service, "is_configured", lambda: True)

    names = {item["function"]["name"] for item in ai_action_service.provider_tool_definitions()}

    assert "web_search" in names


def test_an_unreadable_configuration_never_breaks_the_tool_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Die wichtigste Zusage dieser Datei.

    `is_configured` entscheidet mit, welche Werkzeuge der Katalog enthaelt.
    Laeuft sie in einen Fehler — unerreichbare Datenbank, kaputter Sidecar —
    waere ohne diesen Schutz nicht nur die Suche weg, sondern der ganze Chat.
    """
    def boom():
        raise RuntimeError("Datenbank nicht erreichbar")

    monkeypatch.setattr(ai_web_search_service, "api_key", boom)

    assert ai_web_search_service.is_configured() is False
    # Und der Katalog steht trotzdem.
    assert len(ai_action_service.provider_tool_definitions()) > 0


# ── Fremdtext bleibt Fremdtext ────────────────────────────────────────────

def test_results_are_shortened_and_redacted(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Treffer stammen aus dem offenen Web und werden wie Logzeilen behandelt."""
    _allow_search(db, regular_user)
    _mock_response(monkeypatch, {"web": {"results": [{
        "title": "T" * 500,
        "url": "https://example.invalid/hilfe",
        "description": "Nimm api_key=sk-abcdefghijklmnopqrstuvwxyz012345 " + "x" * 900,
    }]}})

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="web_search",
        arguments={"query": "fehler"},
    )

    hit = result["results"][0]
    assert len(hit["title"]) <= ai_web_search_service.MAX_TITLE_CHARS
    assert len(hit["snippet"]) <= ai_web_search_service.MAX_SNIPPET_CHARS
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in hit["snippet"]


def test_non_http_targets_are_dropped(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein javascript:-Ziel hat in einer spaeter verlinkten Liste nichts verloren."""
    _allow_search(db, regular_user)
    _mock_response(monkeypatch, {"web": {"results": [
        {"title": "Boese", "url": "javascript:alert(1)", "description": "x"},
        {"title": "Gut", "url": "https://example.invalid/ok", "description": "y"},
    ]}})

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="web_search",
        arguments={"query": "test"},
    )

    assert [hit["url"] for hit in result["results"]] == ["https://example.invalid/ok"]


def test_the_target_is_fixed_and_the_key_travels_in_the_header(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Benutzer steuert die Anfrage, niemals das Ziel — keine SSRF-Flaeche."""
    _allow_search(db, regular_user)
    seen = _mock_response(monkeypatch, {"web": {"results": []}})

    ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="web_search",
        arguments={"query": "https://interner-host.invalid/admin"},
    )

    assert seen["url"] == ai_web_search_service._ENDPOINT
    assert seen["headers"]["X-Subscription-Token"] == "test-schluessel"
    # Die Anfrage landet als Parameter, nicht als Ziel.
    assert seen["params"]["q"] == "https://interner-host.invalid/admin"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "AI_WEB_SEARCH_AUTH_FAILED"),
        (429, "AI_WEB_SEARCH_RATE_LIMITED"),
        (500, "AI_WEB_SEARCH_REJECTED"),
    ],
)
def test_each_failure_gets_its_own_code(
    status: int, code: str, db: Session, regular_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_search(db, regular_user)
    _mock_response(monkeypatch, {"error": "nope"}, status=status)

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="web_search", arguments={"query": "test"},
    )

    assert result["available"] is False
    assert result["reason"] == code


def test_too_many_results_are_capped(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die KI soll nachschlagen koennen, nicht das Web einlesen."""
    _allow_search(db, regular_user)

    with pytest.raises(ai_action_service.AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="web_search",
            arguments={"query": "test", "count": 50},
        )
