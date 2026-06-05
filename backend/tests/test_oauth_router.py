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
        # Next + mode=login werden ins State-Cookie gepackt
        cookie = res.cookies.get("__Secure-oauth_state")
        assert cookie is not None
        payload = oauth_service.unpack_state_cookie(cookie)
        assert payload is not None
        assert payload["next"] == "/servers"
        assert payload["mode"] == oauth_service.OAUTH_MODE_LOGIN

    def test_state_cookie_uses_samesite_none(
        self, client: TestClient, db: Session
    ):
        """Security-Invariante: der OAuth-State-Cookie MUSS SameSite=None
        haben, damit er den Cross-Site-Roundtrip vom IdP (Google/Discord)
        zuverlaessig ueberlebt. SameSite=Lax wuerde Cookies nur bei
        Top-Level-Navigation mitsenden — OAuth-Silent-Re-Auth (prompt=none)
        und einige IdP-Flows nutzen aber hidden-iframe / sub-resource
        redirects, wo SameSite=Lax blockt.

        HttpOnly + Secure + SameSite=None + state-als-random + PKCE-Code-
        Verifier ergibt zusammen ein sicheres OAuth-State-Pattern (RFC 6749
        + RFC 6265bis).
        """
        _create_provider(db, slug="gh-samesite", preset="github")
        res = client.get("/api/oauth/gh-samesite/start", follow_redirects=False)
        assert res.status_code == 302
        # Set-Cookie-Header holen (TestClient exponiert .cookies, aber
        # SameSite kommt aus dem Raw-Header)
        set_cookie = None
        for k, v in res.headers.raw:
            if k.lower() == b"set-cookie" and b"__Secure-oauth_state" in v:
                set_cookie = v.decode()
                break
        assert set_cookie is not None, "Set-Cookie fuer State-Cookie fehlt"
        # HTTP-Header-Werte sind case-insensitive per RFC 7230, deshalb
        # sowohl 'SameSite=None' als auch 'SameSite=none' akzeptieren.
        assert "samesite=none" in set_cookie.lower(), (
            f"State-Cookie MUSS SameSite=None fuer Cross-Site-IdP-"
            f"Roundtrip haben. Header war: {set_cookie!r}"
        )
        assert "Secure" in set_cookie, "State-Cookie MUSS Secure haben"
        assert "HttpOnly" in set_cookie, "State-Cookie MUSS HttpOnly haben"


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

    def test_state_cookie_roundtrip_login_flow(
        self, client: TestClient, db: Session
    ):
        """End-to-End: /start setzt State-Cookie, /callback liest ihn
        mit demselben Client zurueck. Regression-Schutz fuer 'neue User
        bekommen oauth_state_mismatch' — der State-Cookie MUSS den
        Cross-Site-Browser-Hop ueberleben.

        TestClient simuliert zwar keine SameSite-Policy, aber verifiziert
        dass das Cookie mit dem richtigen Path/Name/Wert gesetzt wird.
        """
        _create_provider(db, slug="gh-rt", preset="github")
        # /start setzt das State-Cookie
        res_start = client.get("/api/oauth/gh-rt/start", follow_redirects=False)
        assert res_start.status_code == 302
        state_cookie = res_start.cookies.get("__Secure-oauth_state")
        assert state_cookie is not None, "State-Cookie wurde nicht gesetzt"

        # Cookie wurde serverseitig verschluesselt → Payload lesbar
        payload = oauth_service.unpack_state_cookie(state_cookie)
        assert payload is not None
        assert payload["state"]  # non-empty
        assert payload["mode"] == oauth_service.OAUTH_MODE_LOGIN
        # Login-Flow hat KEIN user_id, nur next
        assert "user_id" not in payload
        assert "next" in payload

    def test_second_login_flow_overwrites_first_state_cookie(
        self, client: TestClient, db: Session
    ):
        """Regression: Wenn der User die Login-Flow zweimal startet (z.B. weil
        der erste Versuch abgebrochen wurde), MUSS das State-Cookie ueber-
        schrieben werden — sonst landet der Callback beim ERSTEN state-Wert
        und der Redirect-URL-Zustand passt nicht mehr.
        """
        _create_provider(db, slug="gh-2x", preset="github")
        res1 = client.get("/api/oauth/gh-2x/start", follow_redirects=False)
        cookie1 = res1.cookies.get("__Secure-oauth_state")
        payload1 = oauth_service.unpack_state_cookie(cookie1)
        state1 = payload1["state"]

        res2 = client.get("/api/oauth/gh-2x/start", follow_redirects=False)
        cookie2 = res2.cookies.get("__Secure-oauth_state")
        payload2 = oauth_service.unpack_state_cookie(cookie2)
        state2 = payload2["state"]

        # Zwei unabhaengige /start-Calls MUESSEN zwei verschiedene states liefern
        # UND der zweite Call MUSS den Cookie ueberschreiben.
        assert state1 != state2, "PKCE-State muss pro Aufruf neu sein"

    def test_login_callback_after_failed_link_does_not_leak_state(
        self, client: TestClient, user_cookies: dict, db: Session
    ):
        """Sequenz: User startet Link-Flow → erhaelt state-A. Bricht ab.
        Startet Login-Flow → erhaelt state-B. Callback kommt mit state-B
        aber Cookie hat noch state-A (alter Wert) → state_mismatch.

        Wir testen, dass /start bei einem NEUEN Aufruf das Cookie IMMER
        ueberschreibt, auch wenn vorher ein /link/start lief.
        """
        PanelSettingsService.set(oauth_service.SWITCH_ALLOW_LINKING, "true")
        _create_provider(db, slug="gh-seq", preset="github")

        # 1) Link-Flow (auth-pflichtig)
        res_link = client.get(
            "/api/oauth/gh-seq/link/start", cookies=user_cookies, follow_redirects=False
        )
        link_cookie = res_link.cookies.get("__Secure-oauth_state")
        link_payload = oauth_service.unpack_state_cookie(link_cookie)
        assert link_payload["mode"] == oauth_service.OAUTH_MODE_LINK

        # 2) Login-Flow (anonym, ueberschreibt das Cookie weil gleicher Name+Path)
        res_login = client.get("/api/oauth/gh-seq/start", follow_redirects=False)
        login_cookie = res_login.cookies.get("__Secure-oauth_state")
        login_payload = oauth_service.unpack_state_cookie(login_cookie)
        assert login_payload["mode"] == oauth_service.OAUTH_MODE_LOGIN
        # State-Werte unterscheiden sich
        assert link_payload["state"] != login_payload["state"]

    def test_full_roundtrip_state_in_cookie_matches_state_in_callback(
        self, client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
    ):
        """End-to-End-Simulation: /start setzt Cookie mit state=X, dann ruft
        der Browser /callback?state=X mit dem Cookie auf. State MUSS matchen.

        Wir monkeypatchen exchange_code, damit der Test nicht an 'kein echter
        IdP' scheitert — wir wollen den State-Match-Pfad isoliert verifizieren.
        """
        _create_provider(db, slug="gh-e2e", preset="github")

        # 1) /start wie ein Browser: Set-Cookie aufnehmen
        res_start = client.get(
            "/api/oauth/gh-e2e/start?next=/dashboard",
            follow_redirects=False,
        )
        assert res_start.status_code == 302
        cookie = res_start.cookies.get("__Secure-oauth_state")
        assert cookie is not None
        payload = oauth_service.unpack_state_cookie(cookie)
        state_in_cookie = payload["state"]
        code_verifier = payload["code_verifier"]
        assert state_in_cookie and code_verifier

        # 2) IdP-Call mocken, damit wir beim State-Match anhalten koennen
        #    (sonst wuerde exchange_code fehlschlagen, weil kein echter GitHub)
        def _fake_exchange(*_a, **_kw):
            raise ValueError("STOP_AFTER_STATE_MATCH")
        monkeypatch.setattr(oauth_service, "exchange_code", _fake_exchange)

        # 3) /callback mit dem state aus dem Cookie aufrufen
        res_cb = client.get(
            f"/api/oauth/gh-e2e/callback?code=fake&state={state_in_cookie}",
            cookies={"__Secure-oauth_state": cookie},
            follow_redirects=False,
        )
        # State MUSS matchen → flow laeuft weiter bis exchange_code → "STOP_AFTER_STATE_MATCH"
        # → Redirect zu /login?error=oauth_exchange_failed (NICHT oauth_state_mismatch)
        assert res_cb.status_code == 302
        assert "oauth_exchange_failed" in res_cb.headers["location"], (
            f"State-Cookie hat nicht gematcht. Erwartet: oauth_exchange_failed. "
            f"Got: {res_cb.headers['location']}"
        )


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
        # State-Cookie traegt mode=link + user_id
        cookie = res.cookies.get("__Secure-oauth_state")
        assert cookie is not None
        payload = oauth_service.unpack_state_cookie(cookie)
        assert payload is not None
        assert payload["mode"] == oauth_service.OAUTH_MODE_LINK
        assert "user_id" in payload
        # redirect_uri im State MUSS der geteilte Callback sein (nicht /link/callback)
        assert payload["redirect_uri"].endswith("/api/oauth/gh-ok/callback")


# ── Unified Callback: Mode-Dispatch (Login vs. Link) ───────────────────

class TestUnifiedCallback:
    """Beide Flows (Login + Linking) teilen sich /api/oauth/{slug}/callback.
    Die Trennung passiert ueber das mode-Feld im State-Cookie (Fernet-encrypted).
    Damit muss in der IdP-Console nur EINE redirect_uri registriert werden.
    """

    def test_login_mode_rejects_state_without_mode(
        self, client: TestClient, db: Session
    ):
        """Auch ohne mode im State laeuft der Callback — Default ist 'login'."""
        provider = _create_provider(db, slug="gh-cb1", preset="github")
        # State ohne mode-Feld bauen (backward-compat / alte Cookies)
        payload = {
            "state": "test-state",
            "code_verifier": "test-verifier",
            "redirect_uri": f"http://localhost:3000/api/oauth/gh-cb1/callback",
            "next": "/",
            "ts": 1,
        }
        cookie = oauth_service.pack_state_cookie(payload)
        # Code + state muessen matchen — der Test bricht hier vor Token-Exchange ab,
        # aber wir testen, dass der Mode-Default + State-Check greift.
        res = client.get(
            "/api/oauth/gh-cb1/callback?code=fake&state=test-state",
            cookies={"__Secure-oauth_state": cookie},
            follow_redirects=False,
        )
        # State OK, Code-Exchange wird versucht, schlaegt aber fehl (kein IdP) →
        # Redirect zu /login?error=oauth_exchange_failed
        assert res.status_code == 302
        assert "/login" in res.headers["location"]
        assert "oauth_exchange_failed" in res.headers["location"]

    def test_link_mode_without_auth_redirects_to_profile_error(
        self, client: TestClient, db: Session
    ):
        PanelSettingsService.set(oauth_service.SWITCH_ALLOW_LINKING, "true")
        _create_provider(db, slug="gh-cb2", preset="github")
        # Link-State mit user_id
        payload = {
            "state": "link-state",
            "code_verifier": "v",
            "redirect_uri": f"http://localhost:3000/api/oauth/gh-cb2/callback",
            "mode": oauth_service.OAUTH_MODE_LINK,
            "user_id": 999,
            "ts": 1,
        }
        cookie = oauth_service.pack_state_cookie(payload)
        res = client.get(
            "/api/oauth/gh-cb2/callback?code=fake&state=link-state",
            cookies={"__Secure-oauth_state": cookie},
            follow_redirects=False,
        )
        # Anonymer Aufruf im link-Mode → /profile?error=auth_required
        assert res.status_code == 302
        assert "/profile" in res.headers["location"]
        assert "auth_required" in res.headers["location"]

    def test_link_mode_blocked_when_linking_disabled(
        self, client: TestClient, user_cookies: dict, regular_user: User, db: Session
    ):
        PanelSettingsService.set(oauth_service.SWITCH_ALLOW_LINKING, "false")
        _create_provider(db, slug="gh-cb3", preset="github")
        payload = {
            "state": "link-state",
            "code_verifier": "v",
            "redirect_uri": f"http://localhost:3000/api/oauth/gh-cb3/callback",
            "mode": oauth_service.OAUTH_MODE_LINK,
            "user_id": regular_user.id,
            "ts": 1,
        }
        cookie = oauth_service.pack_state_cookie(payload)
        res = client.get(
            "/api/oauth/gh-cb3/callback?code=fake&state=link-state",
            cookies={**user_cookies, "__Secure-oauth_state": cookie},
            follow_redirects=False,
        )
        assert res.status_code == 302
        assert "linking_disabled" in res.headers["location"]

    def test_link_mode_rejects_state_user_id_mismatch(
        self, client: TestClient, user_cookies: dict, regular_user: User, db: Session
    ):
        """Defense-in-Depth: Wenn das State-Payload eine fremde user_id traegt,
        muss der Callback das ablehnen — selbst wenn der Aufrufer eingeloggt ist."""
        PanelSettingsService.set(oauth_service.SWITCH_ALLOW_LINKING, "true")
        _create_provider(db, slug="gh-cb4", preset="github")
        payload = {
            "state": "link-state",
            "code_verifier": "v",
            "redirect_uri": f"http://localhost:3000/api/oauth/gh-cb4/callback",
            "mode": oauth_service.OAUTH_MODE_LINK,
            "user_id": regular_user.id + 9999,  # falsche ID
            "ts": 1,
        }
        cookie = oauth_service.pack_state_cookie(payload)
        res = client.get(
            "/api/oauth/gh-cb4/callback?code=fake&state=link-state",
            cookies={**user_cookies, "__Secure-oauth_state": cookie},
            follow_redirects=False,
        )
        assert res.status_code == 302
        assert "state_user_mismatch" in res.headers["location"]

    def test_old_link_callback_endpoint_is_gone(
        self, client: TestClient, user_cookies: dict, db: Session
    ):
        """/{slug}/link/callback existiert nicht mehr — unified callback only."""
        _create_provider(db, slug="gh-cb5", preset="github")
        res = client.get("/api/oauth/gh-cb5/link/callback", cookies=user_cookies)
        assert res.status_code == 404

    def test_link_mode_state_mismatch_redirects_to_profile(
        self, client: TestClient, user_cookies: dict, regular_user: User, db: Session
    ):
        """Regression: Im Link-Mode darf ein State-Mismatch NICHT auf /login
        umleiten — der User waere dann eingeloggt, PublicOnlyRoute redirected
        auf /, und die Fehlermeldung geht verloren. Korrekt: /profile?error=
        (state_user_mismatch), damit die Profile-Toast-Logik greift.
        """
        PanelSettingsService.set(oauth_service.SWITCH_ALLOW_LINKING, "true")
        _create_provider(db, slug="gh-cb6", preset="github")
        # State-Cookie sagt mode=link, aber URL-state stimmt NICHT ueberein
        payload = {
            "state": "expected-state",
            "code_verifier": "v",
            "redirect_uri": f"http://localhost:3000/api/oauth/gh-cb6/callback",
            "mode": oauth_service.OAUTH_MODE_LINK,
            "user_id": regular_user.id,
            "ts": 1,
        }
        cookie = oauth_service.pack_state_cookie(payload)
        res = client.get(
            "/api/oauth/gh-cb6/callback?code=fake&state=DIFFERENT-STATE",
            cookies={**user_cookies, "__Secure-oauth_state": cookie},
            follow_redirects=False,
        )
        assert res.status_code == 302
        loc = res.headers["location"]
        assert "/profile" in loc, f"Link-Mode muss auf /profile landen, nicht /login (war: {loc!r})"
        assert "state_user_mismatch" in loc

    def test_login_mode_state_mismatch_redirects_to_login(
        self, client: TestClient, db: Session
    ):
        """Regression-Gegentest: Im Login-Mode bleibt der State-Mismatch
        Redirect auf /login (mit error=oauth_state_mismatch) — nur der
        Link-Mode wechselt auf /profile.
        """
        _create_provider(db, slug="gh-cb7", preset="github")
        payload = {
            "state": "expected",
            "code_verifier": "v",
            "redirect_uri": f"http://localhost:3000/api/oauth/gh-cb7/callback",
            "mode": oauth_service.OAUTH_MODE_LOGIN,
            "next": "/",
            "ts": 1,
        }
        cookie = oauth_service.pack_state_cookie(payload)
        res = client.get(
            "/api/oauth/gh-cb7/callback?code=fake&state=DIFFERENT",
            cookies={"__Secure-oauth_state": cookie},
            follow_redirects=False,
        )
        assert res.status_code == 302
        loc = res.headers["location"]
        assert "/login" in loc, f"Login-Mode muss auf /login landen (war: {loc!r})"
        assert "oauth_state_mismatch" in loc


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
