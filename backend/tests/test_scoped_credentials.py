"""Invarianten fuer Zugangsdaten auf Panel-, Benutzer- und Serverebene (Phase 7).

Geprueft werden die Zusagen aus Zielpunkt 17:
- Klartext wird nach dem Speichern nie wieder ausgegeben.
- Ein Server kann ein Credential verwenden, ohne es zu kopieren.
- Kunden sehen und binden keine fremden Zugangsdaten.
- Der Betreiber entscheidet, ob ein zentraler Fallback erlaubt ist.
- Ohne Bindung verhaelt sich alles wie bisher (Self-Hosted bleibt unberuehrt).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import (
    AuditLog,
    KIND_GITHUB_TOKEN,
    KIND_STEAM_ACCOUNT,
    Server,
    ServerCredentialBinding,
    ServerPermission,
    User,
    UserCredential,
)
from services import credential_service
from services.auth_service import AuthService
from services.panel_settings_service import PanelSettingsService


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _server(db: Session, game_type: str = "dayz") -> Server:
    server = Server(
        name="Credential Server",
        game_type=game_type,
        install_dir="/tmp/credential-server",
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


@pytest.fixture(autouse=True)
def _reset_policy():
    """Jeder Test startet mit dem Default (Fallback erlaubt)."""
    PanelSettingsService.set(credential_service.PANEL_FALLBACK_SETTING, "true")
    yield
    PanelSettingsService.set(credential_service.PANEL_FALLBACK_SETTING, "true")


# ── Tresor ─────────────────────────────────────────────────────────────────


def test_secret_is_never_returned_and_only_stored_encrypted(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict
) -> None:
    saved = client.put(
        "/api/credentials/me",
        json={
            "kind": "steam_account",
            "label": "Hauptkonto",
            "username": "kunde42",
            "secret": "streng-geheim-1234",
        },
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    )
    listed = client.get("/api/credentials/me", cookies=user_cookies)

    assert saved.status_code == 200, saved.text
    assert "streng-geheim-1234" not in saved.text
    assert "streng-geheim-1234" not in listed.text
    assert saved.json()["secret_hint"] == "...1234"

    row = db.query(UserCredential).one()
    assert "streng-geheim-1234" not in row.secret_encrypted
    assert (
        AuthService.decrypt_secret(
            row.secret_encrypted, aad=f"msm:credential:{row.id}:secret"
        )
        == "streng-geheim-1234"
    )
    # Auch das Audit enthaelt weder Geheimnis noch Bezeichnung.
    for entry in db.query(AuditLog).all():
        assert "streng-geheim-1234" not in (entry.details or "")


def test_rotation_keeps_the_id_so_server_bindings_survive(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict
) -> None:
    """Ein rotiertes Geheimnis wirkt sofort, ohne die Bindung zu zerstoeren."""
    first = client.put(
        "/api/credentials/me",
        json={"kind": "github_token", "label": "CI", "secret": "token-aaaa"},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    ).json()
    second = client.put(
        "/api/credentials/me",
        json={"kind": "github_token", "label": "CI", "secret": "token-bbbb"},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    ).json()

    assert first["id"] == second["id"]
    assert second["secret_hint"] == "...bbbb"
    assert db.query(UserCredential).count() == 1


def test_steam_account_requires_a_username(
    client: TestClient, regular_user: User, user_cookies: dict
) -> None:
    response = client.put(
        "/api/credentials/me",
        json={"kind": "steam_account", "label": "Ohne Name", "secret": "geheim"},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    )
    assert response.status_code == 422


@pytest.mark.parametrize("label", ["", "   ", "a" * 80, "böse;label", "mit\nzeile"])
def test_invalid_labels_are_rejected(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict, label: str
) -> None:
    response = client.put(
        "/api/credentials/me",
        json={"kind": "github_token", "label": label, "secret": "token-aaaa"},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    )
    assert response.status_code == 422
    assert db.query(UserCredential).count() == 0


def test_a_user_cannot_see_or_delete_foreign_credentials(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    owner_user: User,
) -> None:
    foreign = credential_service.upsert_user_credential(
        db,
        user_id=owner_user.id,
        kind=KIND_GITHUB_TOKEN,
        label="Owner-Token",
        secret="owner-secret-9999",
    )
    db.commit()

    listed = client.get("/api/credentials/me", cookies=user_cookies)
    deleted = client.delete(
        f"/api/credentials/me/{foreign.id}",
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    )

    assert listed.json() == []
    assert deleted.status_code == 404
    assert db.query(UserCredential).filter(UserCredential.id == foreign.id).count() == 1


# ── Serverbindung ──────────────────────────────────────────────────────────


def test_binding_a_foreign_credential_is_refused(
    db: Session, regular_user: User, owner_user: User
) -> None:
    """Serverrechte erlauben nicht, fremde Zugangsdaten in Betrieb zu nehmen."""
    server = _server(db)
    foreign = credential_service.upsert_user_credential(
        db,
        user_id=owner_user.id,
        kind=KIND_GITHUB_TOKEN,
        label="Owner-Token",
        secret="owner-secret-9999",
    )
    db.commit()

    with pytest.raises(HTTPException) as excinfo:
        credential_service.set_binding(
            db,
            server_id=server.id,
            kind=KIND_GITHUB_TOKEN,
            credential_id=foreign.id,
            actor=regular_user,
        )

    assert excinfo.value.status_code == 404
    assert db.query(ServerCredentialBinding).count() == 0


def test_server_binding_wins_over_the_panel_default(
    db: Session, regular_user: User
) -> None:
    server = _server(db)
    own = credential_service.upsert_user_credential(
        db,
        user_id=regular_user.id,
        kind=KIND_GITHUB_TOKEN,
        label="Kunde",
        secret="kunden-token-1111",
    )
    credential_service.set_binding(
        db,
        server_id=server.id,
        kind=KIND_GITHUB_TOKEN,
        credential_id=own.id,
        actor=regular_user,
    )
    db.commit()

    with patch(
        "services.github_token_service.resolve_token", return_value="panel-token-0000"
    ):
        resolved = credential_service.resolve_for_server(
            db, server.id, KIND_GITHUB_TOKEN
        )

    assert resolved is not None
    assert resolved.secret == "kunden-token-1111"
    assert resolved.source == "server"


def test_without_a_binding_the_panel_default_is_used_unchanged(
    db: Session,
) -> None:
    """Self-Hosted bleibt unberuehrt: ohne Bindung gilt weiter der Panel-Zugang."""
    server = _server(db)

    with patch(
        "services.github_token_service.resolve_token", return_value="panel-token-0000"
    ), patch("services.github_token_service.current_source", return_value="panel"):
        resolved = credential_service.resolve_for_server(
            db, server.id, KIND_GITHUB_TOKEN
        )

    assert resolved is not None
    assert resolved.secret == "panel-token-0000"
    assert resolved.source == "panel"


def test_operator_can_switch_off_the_central_fallback(db: Session) -> None:
    """Im Hoster-Betrieb darf ein Server ohne Bindung nicht den Betreiberzugang nutzen."""
    server = _server(db)
    credential_service.set_panel_fallback_allowed(False)

    with patch(
        "services.github_token_service.resolve_token", return_value="panel-token-0000"
    ):
        resolved = credential_service.resolve_for_server(
            db, server.id, KIND_GITHUB_TOKEN
        )

    assert resolved is None


def test_a_bound_credential_cannot_be_deleted_silently(
    db: Session, regular_user: User
) -> None:
    """Sonst fiele der Server beim naechsten Install unbemerkt auf das Panel zurueck."""
    server = _server(db)
    own = credential_service.upsert_user_credential(
        db,
        user_id=regular_user.id,
        kind=KIND_STEAM_ACCOUNT,
        label="Konto",
        username="kunde",
        secret="passwort-2222",
    )
    credential_service.set_binding(
        db,
        server_id=server.id,
        kind=KIND_STEAM_ACCOUNT,
        credential_id=own.id,
        actor=regular_user,
    )
    db.commit()

    with pytest.raises(HTTPException) as excinfo:
        credential_service.delete_user_credential(
            db, user_id=regular_user.id, credential_id=own.id
        )

    assert excinfo.value.status_code == 409
    assert db.query(UserCredential).count() == 1


def test_undecryptable_binding_never_falls_back_to_the_panel_account(
    db: Session, regular_user: User
) -> None:
    """Ein defektes Credential darf nicht stillschweigend fremde Zugangsdaten nutzen."""
    from services.dis_client import DisDecryptionError

    server = _server(db)
    own = credential_service.upsert_user_credential(
        db,
        user_id=regular_user.id,
        kind=KIND_GITHUB_TOKEN,
        label="Kunde",
        secret="kunden-token-1111",
    )
    credential_service.set_binding(
        db,
        server_id=server.id,
        kind=KIND_GITHUB_TOKEN,
        credential_id=own.id,
        actor=regular_user,
    )
    db.commit()

    with patch(
        "services.auth_service.AuthService.decrypt_secret",
        side_effect=DisDecryptionError("kaputt"),
    ), patch(
        "services.github_token_service.resolve_token", return_value="panel-token-0000"
    ):
        with pytest.raises(HTTPException) as excinfo:
            credential_service.resolve_for_server(db, server.id, KIND_GITHUB_TOKEN)

    assert excinfo.value.status_code == 503


# ── Serveroberflaeche ──────────────────────────────────────────────────────


def test_server_status_lists_required_kinds_without_any_secret(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
) -> None:
    server = _server(db, game_type="dayz")  # dayz = Steam mit Login-Pflicht
    db.add(
        ServerPermission(
            user_id=regular_user.id, server_id=server.id, permission_key="server.view"
        )
    )
    db.commit()

    response = client.get(f"/api/servers/{server.id}/credentials", cookies=user_cookies)

    assert response.status_code == 200
    rows = {row["kind"]: row for row in response.json()}
    assert rows[KIND_STEAM_ACCOUNT]["required"] is True
    assert rows[KIND_GITHUB_TOKEN]["required"] is False
    for row in rows.values():
        assert "secret" not in row


def test_binding_requires_the_server_credentials_permission(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
) -> None:
    server = _server(db)
    db.add(
        ServerPermission(
            user_id=regular_user.id, server_id=server.id, permission_key="server.view"
        )
    )
    own = credential_service.upsert_user_credential(
        db,
        user_id=regular_user.id,
        kind=KIND_GITHUB_TOKEN,
        label="Kunde",
        secret="kunden-token-1111",
    )
    db.commit()

    response = client.put(
        f"/api/servers/{server.id}/credentials",
        json={"kind": KIND_GITHUB_TOKEN, "credential_id": own.id},
        cookies=user_cookies,
        headers=_csrf(user_cookies),
    )

    assert response.status_code == 403
    assert db.query(ServerCredentialBinding).count() == 0
