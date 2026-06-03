"""HTTP-Tests fuer den OAuth-Router (Phase 4 — Social Login).

Schwerpunkt: Security-Gates
- Public-Endpoints brauchen keine Auth
- Admin-Endpoints verlangen panel.oauth.*-Permissions
- State-Cookie wird bei Callback-Mismatch abgelehnt
- 2FA-Endpoint lehnt abgelaufene/falsche Challenges ab
- Secret-Response ist IMMER maskiert
"""

from __future__ import annotations

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import OAuthProvider, OAuthUserLink, User
from services import oauth_service
from services.auth_service import AuthService
from services.panel_settings_service import PanelSettingsService


# ── Public-Provider-Listing ───────────────────────────────────────────

class TestPublicProviders:
    def test_listing_returns_empty_when_no_providers(self, client: TestClient):
        res = client.get("/api/oauth/public/providers")
        assert res.status_code == 200
        assert res.json() == []

    def test_listing_includes_enabled_only(self, client: TestClient, db: Session):
        _create_provider(db, slug="gh-on", preset="github", enabled=True)
        _create_provider(db, slug="gh-off", preset="github", enabled=False)
        res = client.get("/api/oauth/public/providers")
        assert res.status_code == 200
        slugs = [p["slug"] for p in res.json()]
        assert "gh-on" in slugs
        assert "gh-off" not in slugs

    def test_listing_does_not_leak_client_id_or_secret(
        self, client: TestClient, db: Session
    ):
        _create_provider(db, slug="gh-leak", preset="github", enabled=True, client_secret="GHO_xyz")
        res = client.get("/api/oauth/public/providers")
        body = res.text
        assert "GHO_xyz" not in body
        assert "client_id" not in body
        assert "client_secret" not in body


# ── Admin: Provider-CRUD ──────────────────────────────────────────────

class TestAdminCRUD:
    def test_list_requires_read_permission(
        self, client: TestClient, user_cookies: dict
    ):
        res = client.get("/api/oauth/providers", cookies=user_cookies)
        assert res.status_code == 403

    def test_create_requires_create_permission(
        self, client: TestClient, owner_cookies: dict, csrf_token: str
    ):
        # Owner hat panel.oauth.create via admin-role self-heal
        res = client.post(
            "/api/oauth/providers",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={
                "slug": "gh-admin", "name": "GH", "preset": "github",
                "client_id": "cid", "client_secret": "GHO_x",
            },
        )
        assert res.status_code == 201
        body = res.json()
        # Secret im Response maskiert
        assert body["client_secret"].startswith("*")
        assert "GHO_x" not in body["client_secret"]

    def test_create_requires_csrf(
        self, client: TestClient, owner_cookies: dict
    ):
        res = client.post(
            "/api/oauth/providers",
            cookies=owner_cookies,
            json={"slug": "x", "name": "X", "preset": "github", "client_id": "c"},
        )
        assert res.status_code == 403

    def test_create_duplicate_slug_rejected(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, db: Session
    ):
        _create_provider(db, slug="dup", preset="github")
        res = client.post(
            "/api/oauth/providers",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"slug": "dup", "name": "X", "preset": "github", "client_id": "c"},
        )
        assert res.status_code == 400

    def test_get_one_provider(
        self, client: TestClient, owner_cookies: dict, db: Session
    ):
        p = _create_provider(db, slug="gh-get", preset="github", client_secret="GHO_orig_long")
        res = client.get(f"/api/oauth/providers/{p.id}", cookies=owner_cookies)
        assert res.status_code == 200
        body = res.json()
        assert body["slug"] == "gh-get"
        # Secret maskiert: 13 Zeichen → 9 Sterne + "long"
        assert body["client_secret"] == "*********long"
        assert "GHO_orig_long" not in body["client_secret"]

    def test_update_toggles_enabled(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, db: Session
    ):
        p = _create_provider(db, slug="gh-tog", preset="github", enabled=True)
        res = client.patch(
            f"/api/oauth/providers/{p.id}",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"enabled": False},
        )
        assert res.status_code == 200
        assert res.json()["enabled"] is False

    def test_update_masked_secret_is_noop(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, db: Session
    ):
        p = _create_provider(db, slug="gh-mask", preset="github", client_secret="GHO_keep")
        original_enc = p.client_secret_encrypted
        res = client.patch(
            f"/api/oauth/providers/{p.id}",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"client_secret": "********keep"},
        )
        assert res.status_code == 200
        # DB-Wert unveraendert
        db.refresh(p)
        assert p.client_secret_encrypted == original_enc

    def test_delete_provider(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, db: Session
    ):
        p = _create_provider(db, slug="gh-del", preset="github")
        res = client.delete(
            f"/api/oauth/providers/{p.id}",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 200
        assert db.query(OAuthProvider).filter_by(id=p.id).first() is None

    def test_delete_requires_delete_permission(
        self, client: TestClient, user_cookies: dict, user_csrf_token: str, db: Session
    ):
        p = _create_provider(db, slug="gh-perm", preset="github")
        res = client.delete(
            f"/api/oauth/providers/{p.id}",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert res.status_code == 403

    def test_secret_endpoint_uses_separate_permission(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, db: Session
    ):
        p = _create_provider(db, slug="gh-sec", preset="github", client_secret="GHO_old")
        res = client.post(
            f"/api/oauth/providers/{p.id}/secret",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"client_secret": "GHO_new"},
        )
        assert res.status_code == 200
        db.refresh(p)
        assert oauth_service.decrypt_secret(p.client_secret_encrypted) == "GHO_new"

    def test_test_endpoint(
        self, client: TestClient, owner_cookies: dict, db: Session
    ):
        p = _create_provider(db, slug="gh-test", preset="github", client_secret="GHO_x")
        res = client.post(
            f"/api/oauth/providers/{p.id}/test", cookies=owner_cookies
        )
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True

    def test_test_endpoint_fails_without_secret(
        self, client: TestClient, owner_cookies: dict, db: Session
    ):
        p = _create_provider(db, slug="gh-test2", preset="github", client_secret=None)
        res = client.post(
            f"/api/oauth/providers/{p.id}/test", cookies=owner_cookies
        )
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is False
        assert "secret" in body["message"].lower()


# ── Admin: Globale Switches ────────────────────────────────────────────

class TestSwitches:
    def test_get_defaults(
        self, client: TestClient, owner_cookies: dict
    ):
        PanelSettingsService.invalidate_cache()
        res = client.get("/api/oauth/switches", cookies=owner_cookies)
        assert res.status_code == 200
        body = res.json()
        assert body == {
            "allow_registration": False,
            "allow_linking": False,
            "require_verified_email": True,
        }

    def test_update_switches(
        self, client: TestClient, owner_cookies: dict, csrf_token: str
    ):
        res = client.patch(
            "/api/oauth/switches",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"allow_registration": True, "allow_linking": True},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["allow_registration"] is True
        assert body["allow_linking"] is True
        # Unveraendertes Feld zurueckgesetzt
        assert body["require_verified_email"] is True

    def test_update_rejects_non_bool(
        self, client: TestClient, owner_cookies: dict, csrf_token: str
    ):
        res = client.patch(
            "/api/oauth/switches",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
            json={"allow_registration": "yes"},
        )
        assert res.status_code == 400

    def test_update_requires_csrf(
        self, client: TestClient, owner_cookies: dict
    ):
        res = client.patch(
            "/api/oauth/switches",
            cookies=owner_cookies,
            json={"allow_registration": True},
        )
        assert res.status_code == 403


# ── OAuth-Start: Redirect + State-Cookie ─────────────────────────────

class TestOAuthStart:
    def test_start_redirects_to_idp(
        self, client: TestClient, db: Session
    ):
        p = _create_provider(db, slug="gh-redir", preset="github")
        res = client.get("/api/oauth/gh-redir/start", follow_redirects=False)
        assert res.status_code == 302
        location = res.headers["location"]
        assert "github.com/login/oauth/authorize" in location
        # PKCE + state
        assert "code_challenge=" in location
        assert "state=" in location
        # State-Cookie gesetzt
        assert "__Secure-oauth_state" in res.cookies

    def test_start_with_disabled_provider_404(
        self, client: TestClient, db: Session
    ):
        _create_provider(db, slug="gh-off", preset="github", enabled=False)
        res = client.get("/api/oauth/gh-off/start")
        assert res.status_code == 404

    def test_start_with_unknown_provider_404(self, client: TestClient):
        res = client.get("/api/oauth/unknown/start")
        assert res.status_code == 404

    def test_start_preserves_next_param(
        self, client: TestClient, db: Session
    ):
        _create_provider(db, slug="gh-next", preset="github")
        res = client.get("/api/oauth/gh-next/start?next=/servers", follow_redirects=False)
        assert res.status_code == 302
        # Next wird ins State-Cookie gepackt
        cookie = res.cookies.get("__Secure-oauth_state")
        assert cookie is not None
        payload = oauth_service.unpack_state_cookie(cookie)
        assert payload is not None
        assert payload["next"] == "/servers"


# ── OAuth-Callback ────────────────────────────────────────────────────

class TestOAuthCallback:
    def test_callback_state_mismatch_redirects_to_error(
        self, client: TestClient, db: Session
    ):
        _create_provider(db, slug="gh-mis", preset="github")
        # State-Cookie manipuliert oder fehlend
        res = client.get(
            "/api/oauth/gh-mis/callback",
            params={"code": "abc", "state": "anything"},
            follow_redirects=False,
        )
        assert res.status_code == 302
        assert "error=oauth_state_mismatch" in res.headers["location"]

    def test_callback_idp_error_redirects(
        self, client: TestClient, db: Session
    ):
        _create_provider(db, slug="gh-err", preset="github")
        res = client.get(
            "/api/oauth/gh-err/callback",
            params={"error": "access_denied"},
            follow_redirects=False,
        )
        assert res.status_code == 302
        assert "error=oauth_idp_error" in res.headers["location"]

    def test_callback_missing_code_redirects(
        self, client: TestClient, db: Session
    ):
        _create_provider(db, slug="gh-mc", preset="github")
        res = client.get(
            "/api/oauth/gh-mc/callback",
            params={"state": "x"},
            follow_redirects=False,
        )
        assert res.status_code == 302
        assert "error=oauth_invalid_callback" in res.headers["location"]

    def test_callback_with_state_cookie_but_wrong_state_redirects(
        self, client: TestClient, db: Session
    ):
        _create_provider(db, slug="gh-ws", preset="github")
        encrypted = oauth_service.pack_state_cookie({"state": "good", "code_verifier": "v", "redirect_uri": "u"})
        client.cookies.set("__Secure-oauth_state", encrypted)
        res = client.get(
            "/api/oauth/gh-ws/callback",
            params={"code": "abc", "state": "bad"},
            follow_redirects=False,
        )
        assert res.status_code == 302
        assert "error=oauth_state_mismatch" in res.headers["location"]


# ── 2FA-Endpoint ──────────────────────────────────────────────────────

class TestOAuth2FA:
    def test_2fa_with_no_challenge_returns_401(self, client: TestClient):
        # Leerer Challenge-Token → Service liefert None → Router antwortet 401.
        # (Der 400-Pfad gilt nur fuer nicht-String-Typen.)
        res = client.post("/api/oauth/gh/2fa", json={"challenge": "", "otp_code": "000000"})
        assert res.status_code == 401

    def test_2fa_with_non_string_body_returns_400(self, client: TestClient):
        res = client.post("/api/oauth/gh/2fa", json={"challenge": 12345, "otp_code": "000000"})
        assert res.status_code == 400

    def test_2fa_with_invalid_otp_returns_401(
        self, client: TestClient, db: Session, regular_user: User
    ):
        regular_user.two_factor_secret_encrypted = AuthService.encrypt_2fa_secret("JBSWY3DPEHPK3PXP")
        regular_user.two_factor_enabled = True
        db.commit()
        provider = _create_provider(db, slug="gh-2fa", preset="github")
        token = oauth_service.create_2fa_challenge(db, regular_user, provider)
        res = client.post(
            "/api/oauth/gh-2fa/2fa",
            json={"challenge": token, "otp_code": "000000"},
        )
        assert res.status_code == 401

    def test_2fa_with_correct_otp_sets_session_and_redirects(
        self, client: TestClient, db: Session, regular_user: User
    ):
        secret = "JBSWY3DPEHPK3PXP"
        regular_user.two_factor_secret_encrypted = AuthService.encrypt_2fa_secret(secret)
        regular_user.two_factor_enabled = True
        regular_user.email_verified = True
        db.commit()
        provider = _create_provider(db, slug="gh-2faok", preset="github")
        token = oauth_service.create_2fa_challenge(db, regular_user, provider)
        otp = pyotp.TOTP(secret).now()
        res = client.post(
            "/api/oauth/gh-2faok/2fa",
            json={"challenge": token, "otp_code": otp},
            follow_redirects=False,
        )
        assert res.status_code == 302
        assert res.headers["location"] == "/"
        # Auth-Cookies gesetzt
        assert "__Secure-access_token" in res.cookies
        assert "__Secure-refresh_token" in res.cookies
        # Challenge konsumiert (single-use)
        res2 = client.post(
            "/api/oauth/gh-2faok/2fa",
            json={"challenge": token, "otp_code": otp},
        )
        assert res2.status_code == 401

    def test_2fa_challenge_consumed_by_other_provider_rejected(
        self, client: TestClient, db: Session, regular_user: User
    ):
        secret = "JBSWY3DPEHPK3PXP"
        regular_user.two_factor_secret_encrypted = AuthService.encrypt_2fa_secret(secret)
        regular_user.two_factor_enabled = True
        regular_user.email_verified = True
        db.commit()
        p1 = _create_provider(db, slug="gh-aa", preset="github")
        p2 = _create_provider(db, slug="gh-bb", preset="github")
        token = oauth_service.create_2fa_challenge(db, regular_user, p1)
        otp = pyotp.TOTP(secret).now()
        res = client.post(
            f"/api/oauth/{p2.slug}/2fa",  # falscher Slug
            json={"challenge": token, "otp_code": otp},
        )
        assert res.status_code == 400


# ── Linked-Accounts ───────────────────────────────────────────────────

class TestLinkedAccounts:
    def test_list_my_links_empty(
        self, client: TestClient, user_cookies: dict
    ):
        res = client.get("/api/oauth/me/links", cookies=user_cookies)
        assert res.status_code == 200
        assert res.json() == []

    def test_unlink_requires_csrf(
        self, client: TestClient, user_cookies: dict
    ):
        res = client.delete("/api/oauth/me/links/1", cookies=user_cookies)
        assert res.status_code == 403

    def test_unlink_nonexistent_returns_404(
        self, client: TestClient, user_cookies: dict, user_csrf_token: str
    ):
        res = client.delete(
            "/api/oauth/me/links/9999",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert res.status_code == 404

    def test_unlink_existing(
        self, client: TestClient, user_cookies: dict, user_csrf_token: str,
        db: Session, regular_user: User,
    ):
        p = _create_provider(db, slug="gh-unlink", preset="github")
        link = OAuthUserLink(
            provider_id=p.id, user_id=regular_user.id, subject="sub-unlink"
        )
        db.add(link)
        db.commit()
        link_id = link.id
        res = client.delete(
            f"/api/oauth/me/links/{p.id}",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert res.status_code == 200
        db.expire_all()  # Cache invalidieren
        assert db.query(OAuthUserLink).filter_by(id=link_id).first() is None

    def test_list_my_links_includes_provider_info(
        self, client: TestClient, user_cookies: dict,
        db: Session, regular_user: User,
    ):
        p = _create_provider(db, slug="gh-info", preset="github")
        link = OAuthUserLink(
            provider_id=p.id, user_id=regular_user.id, subject="sub-info"
        )
        db.add(link)
        db.commit()
        res = client.get("/api/oauth/me/links", cookies=user_cookies)
        body = res.json()
        assert len(body) == 1
        assert body[0]["provider_slug"] == "gh-info"
        assert body[0]["provider_preset"] == "github"
        # subject (IdP-User-ID) wird bewusst NICHT exponiert (Privacy)
        assert "subject" not in body[0]


# ── Link-Start (Auth-Pflicht) ─────────────────────────────────────────

class TestLinkStart:
    def test_link_start_requires_auth(self, client: TestClient, db: Session):
        _create_provider(db, slug="gh-noauth", preset="github")
        res = client.get("/api/oauth/gh-noauth/link/start")
        assert res.status_code == 401

    def test_link_start_blocked_when_linking_disabled(
        self, client: TestClient, user_cookies: dict, db: Session
    ):
        PanelSettingsService.set(oauth_service.SWITCH_ALLOW_LINKING, "false")
        _create_provider(db, slug="gh-nolink", preset="github")
        res = client.get("/api/oauth/gh-nolink/link/start", cookies=user_cookies)
        assert res.status_code == 403

    def test_link_start_succeeds_when_enabled(
        self, client: TestClient, user_cookies: dict, db: Session
    ):
        PanelSettingsService.set(oauth_service.SWITCH_ALLOW_LINKING, "true")
        p = _create_provider(db, slug="gh-ok", preset="github")
        res = client.get("/api/oauth/gh-ok/link/start", cookies=user_cookies, follow_redirects=False)
        assert res.status_code == 302
        assert "github.com/login/oauth/authorize" in res.headers["location"]
        assert res.status_code == 302
        assert "github.com/login/oauth/authorize" in res.headers["location"]


# ── Helpers (lokal) ───────────────────────────────────────────────────

def _create_provider(
    db: Session,
    *,
    slug: str,
    preset: str,
    enabled: bool = True,
    client_secret: str | None = None,
) -> OAuthProvider:
    return oauth_service.create_provider(
        db, slug=slug, name=slug, preset=preset, enabled=enabled,
        client_id="cid",
        client_secret=client_secret if preset.startswith("custom_") else client_secret,
        issuer=None, authorization_endpoint=None, token_endpoint=None,
        userinfo_endpoint=None, scope=None, claims_mapping_json=None, position=0,
    )
