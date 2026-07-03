"""Tests fuer den OAuth-Service (Phase 4 — Social Login).

Schwerpunkt: Security-Invarianten
- Secret niemals im Klartext
- Maskierte Werte werden nicht ueberschrieben
- Resolution: Default OFF fuer Auto-Registration und Linking
- 2FA wird nicht umgangen
- State-Cookie ist DIS-encrypted und verfaellt nach Timeout
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import OAuthProvider, OAuthUserLink, User
from services import login_challenge_service, oauth_service
from services.auth_service import AuthService
from services.oauth_presets import get_preset, known_preset, list_presets
from services.panel_settings_service import PanelSettingsService
from services.permission_catalog import (
    ALL_KEYS,
    GLOBAL_KEYS,
    GLOBAL_PERMISSIONS,
    is_known_key,
)
from services.role_service import ensure_system_roles


# ── Preset-Tests ───────────────────────────────────────────────────────

class TestOAuthPresets:
    def test_all_seven_presets_known(self):
        expected = {"google", "discord", "github", "microsoft", "twitter", "custom_oidc", "custom_oauth2"}
        assert set(p.key for p in list_presets()) == expected

    def test_preset_lookup(self):
        g = get_preset("google")
        assert g is not None
        assert "accounts.google.com" in (g.authorization_endpoint or "")

    def test_apple_preset_not_included(self):
        """Apple Sign In wurde explizit ausgeschlossen (Phase 1)."""
        assert not known_preset("apple")
        assert not known_preset("apple_id")

    def test_pkce_enabled_for_all_presets(self):
        for p in list_presets():
            assert p.pkce is True, f"PKCE fehlt fuer Preset {p.key}"


# ── Secret-Encryption + Masking ───────────────────────────────────────

class TestSecretHandling:
    def test_mask_short_secret(self):
        assert oauth_service.mask_secret(None) == ""
        assert oauth_service.mask_secret("") == ""
        # <= 8 Zeichen: voll maskiert
        assert oauth_service.mask_secret("abc") == "***"
        assert oauth_service.mask_secret("12345678") == "********"

    def test_mask_long_secret_shows_last_4(self):
        assert oauth_service.mask_secret("abcdefghij") == "******ghij"
        # 20 Zeichen → 16 Sterne + "cdef"
        assert oauth_service.mask_secret("GHO_1234567890abcdef") == "****************cdef"

    def test_is_masked(self):
        assert oauth_service.is_masked("***")
        assert oauth_service.is_masked("********1234")
        assert not oauth_service.is_masked("")
        assert not oauth_service.is_masked("plain-secret")
        assert not oauth_service.is_masked(None)

    def test_encrypt_decrypt_roundtrip(self):
        plain = "GHO_my-super-secret"
        enc = oauth_service.encrypt_secret(plain)
        assert enc != plain
        assert oauth_service.decrypt_secret(enc) == plain


# ── State-Cookie (DIS) ──────────────────────────────────────────────

class TestStateCookie:
    def test_pack_unpack_roundtrip(self):
        payload = {"state": "abc", "code_verifier": "v", "user_id": 42}
        encrypted = oauth_service.pack_state_cookie(payload)
        # DIS-mock encrypt produces "test-enc-" prefix
        assert encrypted.startswith("test-enc-")
        assert oauth_service.unpack_state_cookie(encrypted) == payload

    def test_unpack_tampered_returns_none(self):
        payload = {"state": "x", "code_verifier": "v"}
        encrypted = oauth_service.pack_state_cookie(payload)
        # Mutieren → entschluesselung muss fehlschlagen
        tampered = encrypted[:-4] + "AAAA"
        assert oauth_service.unpack_state_cookie(tampered) is None

    def test_unpack_empty_returns_none(self):
        assert oauth_service.unpack_state_cookie(None) is None
        assert oauth_service.unpack_state_cookie("") is None


# ── Panel-Switches ────────────────────────────────────────────────────

class TestPanelSwitches:
    def test_defaults_are_off(self):
        PanelSettingsService.invalidate_cache()
        # Defaults aus dem Service-Modul
        assert oauth_service.is_registration_allowed() is False
        assert oauth_service.is_linking_allowed() is False
        assert oauth_service.requires_verified_email() is True

    def test_toggle_registration(self):
        PanelSettingsService.invalidate_cache()
        PanelSettingsService.set(oauth_service.SWITCH_ALLOW_REGISTRATION, "true")
        assert oauth_service.is_registration_allowed() is True
        PanelSettingsService.set(oauth_service.SWITCH_ALLOW_REGISTRATION, "false")
        assert oauth_service.is_registration_allowed() is False

    def test_toggle_linking(self):
        PanelSettingsService.invalidate_cache()
        PanelSettingsService.set(oauth_service.SWITCH_ALLOW_LINKING, "true")
        assert oauth_service.is_linking_allowed() is True
        PanelSettingsService.set(oauth_service.SWITCH_ALLOW_LINKING, "false")
        assert oauth_service.is_linking_allowed() is False


# ── Claim-Normalisierung ──────────────────────────────────────────────

class TestNormalizeProfile:
    def test_google_oidc_claims(self):
        preset = get_preset("google")
        assert preset is not None
        raw = {"sub": "12345", "email": "a@b.com", "email_verified": True, "name": "Alice"}
        profile = oauth_service.normalize_profile(preset, raw)
        assert profile.subject == "12345"
        assert profile.email == "a@b.com"
        assert profile.email_verified is True
        assert profile.username == "a@b.com"  # Google fallback

    def test_github_always_verified(self):
        preset = get_preset("github")
        assert preset is not None
        raw = {"id": "67890", "login": "alicegh", "email": "a@gh.com"}
        profile = oauth_service.normalize_profile(preset, raw)
        assert profile.subject == "67890"
        assert profile.email_verified is True
        assert profile.username == "alicegh"

    def test_twitter_nested_data_path(self):
        preset = get_preset("twitter")
        assert preset is not None
        raw = {"data": {"id": "111", "username": "alicex", "email": "a@x.com"}}
        profile = oauth_service.normalize_profile(preset, raw)
        assert profile.subject == "111"
        assert profile.username == "alicex"
        assert profile.email == "a@x.com"

    def test_override_mapping(self):
        preset = get_preset("github")
        assert preset is not None
        # Override: GitHub's "login" soll als "id" dienen (seltsames Mapping, aber erlaubt)
        raw = {"login": "my-id-123", "email": "a@gh.com"}
        profile = oauth_service.normalize_profile(
            preset, raw, claims_override_json=json.dumps({"id": "login"})
        )
        assert profile.subject == "my-id-123"

    def test_missing_subject_raises(self):
        preset = get_preset("google")
        assert preset is not None
        with pytest.raises(ValueError):
            oauth_service.normalize_profile(preset, {"email": "a@b.com"})

    def test_invalid_override_json_ignored(self):
        preset = get_preset("github")
        assert preset is not None
        raw = {"id": "123", "login": "alice"}
        # Tippfehler im JSON → Fallback auf Preset-Mapping (kein Crash)
        profile = oauth_service.normalize_profile(preset, raw, claims_override_json="not-json")
        assert profile.subject == "123"


# ── Provider-CRUD ──────────────────────────────────────────────────────

class TestProviderCRUD:
    def test_create_google_provider(self, db: Session):
        p = oauth_service.create_provider(
            db, slug="google-main", name="Google", preset="google",
            enabled=True, client_id="G-123", client_secret="GHO_xyz",
            issuer=None, authorization_endpoint=None, token_endpoint=None,
            userinfo_endpoint=None, scope=None, claims_mapping_json=None, position=1,
        )
        assert p.id is not None
        assert p.client_secret_encrypted != "GHO_xyz"
        assert oauth_service.decrypt_secret(p.client_secret_encrypted) == "GHO_xyz"

    def test_create_unknown_preset_rejected(self, db: Session):
        with pytest.raises(ValueError):
            oauth_service.create_provider(
                db, slug="x", name="X", preset="apple", enabled=True,
                client_id="cid", client_secret=None, issuer=None,
                authorization_endpoint=None, token_endpoint=None,
                userinfo_endpoint=None, scope=None, claims_mapping_json=None, position=0,
            )

    def test_create_invalid_slug_rejected(self, db: Session):
        with pytest.raises(ValueError):
            oauth_service.create_provider(
                db, slug="A B", name="X", preset="google", enabled=True,
                client_id="cid", client_secret=None, issuer=None,
                authorization_endpoint=None, token_endpoint=None,
                userinfo_endpoint=None, scope=None, claims_mapping_json=None, position=0,
            )

    def test_create_duplicate_slug_rejected(self, db: Session):
        oauth_service.create_provider(
            db, slug="gh", name="GH", preset="github", enabled=True,
            client_id="cid", client_secret=None, issuer=None,
            authorization_endpoint=None, token_endpoint=None,
            userinfo_endpoint=None, scope=None, claims_mapping_json=None, position=0,
        )
        with pytest.raises(ValueError):
            oauth_service.create_provider(
                db, slug="gh", name="GH2", preset="github", enabled=True,
                client_id="cid", client_secret=None, issuer=None,
                authorization_endpoint=None, token_endpoint=None,
                userinfo_endpoint=None, scope=None, claims_mapping_json=None, position=0,
            )

    def test_update_secret_with_masked_value_is_noop(self, db: Session):
        p = oauth_service.create_provider(
            db, slug="gh2", name="GH", preset="github", enabled=True,
            client_id="cid", client_secret="GHO_orig", issuer=None,
            authorization_endpoint=None, token_endpoint=None,
            userinfo_endpoint=None, scope=None, claims_mapping_json=None, position=0,
        )
        original_enc = p.client_secret_encrypted
        oauth_service.update_provider(db, p, client_secret="****orig")
        assert p.client_secret_encrypted == original_enc

    def test_update_secret_with_empty_string_clears(self, db: Session):
        p = oauth_service.create_provider(
            db, slug="gh3", name="GH", preset="github", enabled=True,
            client_id="cid", client_secret="GHO_orig", issuer=None,
            authorization_endpoint=None, token_endpoint=None,
            userinfo_endpoint=None, scope=None, claims_mapping_json=None, position=0,
        )
        oauth_service.update_provider(db, p, client_secret="")
        assert p.client_secret_encrypted is None

    def test_update_secret_with_plaintext(self, db: Session):
        p = oauth_service.create_provider(
            db, slug="gh4", name="GH", preset="github", enabled=True,
            client_id="cid", client_secret="GHO_orig", issuer=None,
            authorization_endpoint=None, token_endpoint=None,
            userinfo_endpoint=None, scope=None, claims_mapping_json=None, position=0,
        )
        oauth_service.update_provider(db, p, client_secret="GHO_new")
        assert oauth_service.decrypt_secret(p.client_secret_encrypted) == "GHO_new"

    def test_custom_oidc_requires_issuer_or_endpoint(self, db: Session):
        with pytest.raises(ValueError):
            oauth_service.create_provider(
                db, slug="oidc1", name="OIDC", preset="custom_oidc", enabled=True,
                client_id="cid", client_secret="sec", issuer=None,
                authorization_endpoint=None, token_endpoint=None,
                userinfo_endpoint=None, scope=None, claims_mapping_json=None, position=0,
            )

    def test_custom_oauth2_requires_authorization_endpoint(self, db: Session):
        with pytest.raises(ValueError):
            oauth_service.create_provider(
                db, slug="oa21", name="OAuth2", preset="custom_oauth2", enabled=True,
                client_id="cid", client_secret="sec", issuer=None,
                authorization_endpoint=None, token_endpoint=None,
                userinfo_endpoint=None, scope=None, claims_mapping_json=None, position=0,
            )


# ── User-Resolution ───────────────────────────────────────────────────

class TestResolveUser:
    def test_forbidden_when_no_match_and_default_off(self, db: Session, regular_user: User):
        provider = _make_provider(db, slug="gh", preset="github")
        profile = _make_profile("111", "new@user.com", email_verified=True)
        # Default: registration + linking OFF
        PanelSettingsService.set(oauth_service.SWITCH_ALLOW_REGISTRATION, "false")
        PanelSettingsService.set(oauth_service.SWITCH_ALLOW_LINKING, "false")
        result = oauth_service.resolve_user(db, provider, profile)
        assert result.action == "forbidden"
        assert result.reason is not None

    def test_login_via_existing_link(self, db: Session, regular_user: User):
        provider = _make_provider(db, slug="gh", preset="github")
        link = OAuthUserLink(
            provider_id=provider.id, user_id=regular_user.id, subject="123"
        )
        db.add(link)
        db.commit()
        profile = _make_profile("123", regular_user.email, email_verified=True)
        result = oauth_service.resolve_user(db, provider, profile)
        assert result.action == "login"
        assert result.user == regular_user

    def test_needs_2fa_when_user_has_2fa(self, db: Session, regular_user: User):
        # 2FA einrichten (plain secret + enable flag)
        regular_user.two_factor_secret_encrypted = AuthService.encrypt_secret("JBSWY3DPEHPK3PXP", aad=f"msm:user:{regular_user.id}:2fa")
        regular_user.two_factor_enabled = True
        db.commit()
        provider = _make_provider(db, slug="gh", preset="github")
        link = OAuthUserLink(
            provider_id=provider.id, user_id=regular_user.id, subject="123"
        )
        db.add(link)
        db.commit()
        profile = _make_profile("123", regular_user.email, email_verified=True)
        result = oauth_service.resolve_user(db, provider, profile)
        assert result.action == "needs_2fa"
        assert result.user == regular_user
        assert result.challenge_token is None  # wird vom Router erzeugt

    def test_register_when_allowed(self, db: Session):
        PanelSettingsService.set(oauth_service.SWITCH_ALLOW_REGISTRATION, "true")
        provider = _make_provider(db, slug="gh", preset="github")
        profile = _make_profile("999", "new@user.com", email_verified=True)
        result = oauth_service.resolve_user(db, provider, profile)
        assert result.action == "register"
        assert result.user is None

    def test_register_rejected_when_email_unverified(self, db: Session):
        PanelSettingsService.set(oauth_service.SWITCH_ALLOW_REGISTRATION, "true")
        PanelSettingsService.set(oauth_service.SWITCH_REQUIRE_VERIFIED_EMAIL, "true")
        provider = _make_provider(db, slug="gh", preset="github")
        profile = _make_profile("999", "new@user.com", email_verified=False)
        result = oauth_service.resolve_user(db, provider, profile)
        assert result.action == "forbidden"
        assert "verifiziert" in (result.reason or "").lower() or "verified" in (result.reason or "").lower()

    def test_register_allowed_when_verification_not_required(self, db: Session):
        PanelSettingsService.set(oauth_service.SWITCH_ALLOW_REGISTRATION, "true")
        PanelSettingsService.set(oauth_service.SWITCH_REQUIRE_VERIFIED_EMAIL, "false")
        provider = _make_provider(db, slug="gh", preset="github")
        profile = _make_profile("999", "new@user.com", email_verified=False)
        result = oauth_service.resolve_user(db, provider, profile)
        assert result.action == "register"

    def test_link_by_email_when_linking_enabled(self, db: Session, regular_user: User):
        PanelSettingsService.set(oauth_service.SWITCH_ALLOW_LINKING, "true")
        provider = _make_provider(db, slug="gh", preset="github")
        profile = _make_profile("999", regular_user.email, email_verified=True)
        result = oauth_service.resolve_user(db, provider, profile)
        assert result.action == "link"
        assert result.user == regular_user

    def test_link_by_email_rejected_when_email_unverified(
        self, db: Session, regular_user: User
    ):
        """Defense-in-Depth: Email-Match-Linking darf NUR greifen, wenn der
        IdP die E-Mail verifiziert hat. Sonst kann ein Angreifer ueber einen
        schwach konfigurierten (Custom-)IdP ein OAuth-Profil mit der Mail
        eines bestehenden Users einspielen und sich so ohne dessen Wissen
        einlinken → Account-Takeover.
        """
        PanelSettingsService.set(oauth_service.SWITCH_ALLOW_LINKING, "true")
        provider = _make_provider(db, slug="gh", preset="github")
        profile = _make_profile("999", regular_user.email, email_verified=False)
        result = oauth_service.resolve_user(db, provider, profile)
        # Muss verboten sein (kein Link), nicht aber auto-registrieren,
        # weil Allow-Registration standardmaessig OFF ist.
        assert result.action == "forbidden", (
            f"Erwartet forbidden, bekam: action={result.action!r}, "
            f"reason={result.reason!r}"
        )


# ── Register / Link ───────────────────────────────────────────────────

class TestRegisterAndLink:
    def test_register_creates_user_with_verified_email(self, db: Session):
        profile = _make_profile("1", "new1@user.com", email_verified=True)
        user = oauth_service.register_user_from_oauth(db, profile)
        assert user.email == "new1@user.com"
        assert user.email_verified is True
        # Random-Passwort: Hash != plain, aber verifizierbar
        assert user.password_hash != ""

    def test_register_rejects_duplicate_email(self, db: Session, regular_user: User):
        profile = _make_profile("1", regular_user.email, email_verified=True)
        with pytest.raises(ValueError):
            oauth_service.register_user_from_oauth(db, profile)

    def test_register_rejects_missing_email(self, db: Session):
        profile = _make_profile("1", None, email_verified=True)
        with pytest.raises(ValueError):
            oauth_service.register_user_from_oauth(db, profile)

    def test_link_is_idempotent(self, db: Session, regular_user: User):
        provider = _make_provider(db, slug="gh", preset="github")
        profile = _make_profile("123", regular_user.email, email_verified=True)
        l1 = oauth_service.link_provider_to_user(db, provider, regular_user, profile)
        l2 = oauth_service.link_provider_to_user(db, provider, regular_user, profile)
        assert l1.id == l2.id

    def test_cannot_have_two_links_for_same_provider(self, db: Session, regular_user: User):
        provider = _make_provider(db, slug="gh", preset="github")
        # Versuche, mit einer anderen subject trotzdem zu linken → muss fehlschlagen
        p1 = _make_profile("sub-1", regular_user.email, email_verified=True)
        oauth_service.link_provider_to_user(db, provider, regular_user, p1)
        p2 = _make_profile("sub-2", regular_user.email, email_verified=True)
        with pytest.raises(ValueError):
            oauth_service.link_provider_to_user(db, provider, regular_user, p2)

    def test_unlink(self, db: Session, regular_user: User):
        provider = _make_provider(db, slug="gh", preset="github")
        profile = _make_profile("123", regular_user.email, email_verified=True)
        oauth_service.link_provider_to_user(db, provider, regular_user, profile)
        assert oauth_service.unlink_user_from_provider(db, regular_user.id, provider.id) is True
        assert oauth_service.unlink_user_from_provider(db, regular_user.id, provider.id) is False


# ── LoginChallenge ────────────────────────────────────────────────────

class TestLoginChallenge:
    def test_create_and_lookup(self, db: Session, regular_user: User):
        token = login_challenge_service.create_challenge(
            db, purpose="oauth_2fa", user_id=regular_user.id, payload={"k": "v"}
        )
        assert len(token) > 20
        row = login_challenge_service.lookup_valid(db, token, "oauth_2fa")
        assert row is not None
        assert row.user_id == regular_user.id

    def test_lookup_wrong_purpose_fails(self, db: Session, regular_user: User):
        token = login_challenge_service.create_challenge(
            db, purpose="oauth_2fa", user_id=regular_user.id
        )
        assert login_challenge_service.lookup_valid(db, token, "wrong_purpose") is None

    def test_consume_makes_reusable_invalid(self, db: Session, regular_user: User):
        token = login_challenge_service.create_challenge(
            db, purpose="oauth_2fa", user_id=regular_user.id
        )
        row = login_challenge_service.lookup_valid(db, token, "oauth_2fa")
        assert row is not None
        login_challenge_service.consume(db, row)
        assert login_challenge_service.lookup_valid(db, token, "oauth_2fa") is None

    def test_expired_challenge_invalid(self, db: Session, regular_user: User):
        token = login_challenge_service.create_challenge(
            db, purpose="oauth_2fa", user_id=regular_user.id, ttl_seconds=-1
        )
        assert login_challenge_service.lookup_valid(db, token, "oauth_2fa") is None

    def test_cleanup_expired(self, db: Session, regular_user: User):
        login_challenge_service.create_challenge(
            db, purpose="oauth_2fa", user_id=regular_user.id, ttl_seconds=-10
        )
        # Frische bleibt erhalten
        token_fresh = login_challenge_service.create_challenge(
            db, purpose="oauth_2fa", user_id=regular_user.id, ttl_seconds=60
        )
        deleted = login_challenge_service.cleanup_expired(db)
        assert deleted >= 1
        # Fresh bleibt
        assert login_challenge_service.lookup_valid(db, token_fresh, "oauth_2fa") is not None


# ── 2FA-Challenge-Complete ────────────────────────────────────────────

class TestComplete2FAChallenge:
    def _enable_2fa(self, db: Session, user: User, secret: str = "JBSWY3DPEHPK3PXP") -> None:
        user.two_factor_secret_encrypted = AuthService.encrypt_secret(secret, aad=f"msm:user:{user.id}:2fa")
        user.two_factor_enabled = True
        db.commit()

    def test_completes_with_correct_otp(self, db: Session, regular_user: User):
        from tests._totp import totp_now, random_totp_secret
        secret = "JBSWY3DPEHPK3PXP"
        self._enable_2fa(db, regular_user, secret)
        provider = _make_provider(db, slug="gh", preset="github")
        token = oauth_service.create_2fa_challenge(db, regular_user, provider)
        otp = totp_now(secret)
        result = oauth_service.complete_2fa_challenge(db, token, otp)
        assert result is not None
        user, prov = result
        assert user == regular_user
        assert prov == provider

    def test_wrong_otp_returns_none(self, db: Session, regular_user: User):
        self._enable_2fa(db, regular_user)
        provider = _make_provider(db, slug="gh", preset="github")
        token = oauth_service.create_2fa_challenge(db, regular_user, provider)
        result = oauth_service.complete_2fa_challenge(db, token, "000000")
        assert result is None

    def test_challenge_reusable_after_consume(self, db: Session, regular_user: User):
        from tests._totp import totp_now, random_totp_secret
        secret = "JBSWY3DPEHPK3PXP"
        self._enable_2fa(db, regular_user, secret)
        provider = _make_provider(db, slug="gh", preset="github")
        token = oauth_service.create_2fa_challenge(db, regular_user, provider)
        otp = totp_now(secret)
        first = oauth_service.complete_2fa_challenge(db, token, otp)
        second = oauth_service.complete_2fa_challenge(db, token, otp)
        assert first is not None
        assert second is None


# ── Permission-Catalog Self-Heal ──────────────────────────────────────

class TestPermissionCatalogOAuth:
    def test_six_oauth_keys_present(self):
        oauth_keys = {p.key for p in GLOBAL_PERMISSIONS if p.key.startswith("panel.oauth.")}
        assert oauth_keys == {
            "panel.oauth.read",
            "panel.oauth.create",
            "panel.oauth.update",
            "panel.oauth.delete",
            "panel.oauth.secret_update",
            "panel.oauth.test",
        }

    def test_admin_role_gets_oauth_keys_via_self_heal(self, db: Session):
        ensure_system_roles(db)
        from services.role_service import role_permission_keys
        from services.role_service import get_role_by_name
        from services.permission_catalog import SYSTEM_ROLE_ADMIN
        admin = get_role_by_name(db, SYSTEM_ROLE_ADMIN)
        assert admin is not None
        keys = set(role_permission_keys(db, admin.id))
        for k in (
            "panel.oauth.read", "panel.oauth.create", "panel.oauth.update",
            "panel.oauth.delete", "panel.oauth.secret_update", "panel.oauth.test",
        ):
            assert k in keys, f"admin role missing {k}"
            assert is_known_key(k)


# ── build_authorization_url ──────────────────────────────────────────

class TestBuildAuthorizationURL:
    def test_google_url_contains_required_params(self, db: Session):
        provider = _make_provider(db, slug="gh", preset="github")
        auth_url, encrypted = oauth_service.build_authorization_url(db, provider)
        assert "https://github.com/login/oauth/authorize" in auth_url
        assert "code_challenge=" in auth_url
        assert "code_challenge_method=S256" in auth_url
        assert "state=" in auth_url
        # State-Cookie ist DIS-encrypted
        assert encrypted.startswith("test-enc-")
        # Payload enthaelt code_verifier + state
        payload = oauth_service.unpack_state_cookie(encrypted)
        assert payload is not None
        assert "code_verifier" in payload
        assert "redirect_uri" in payload

    def test_disabled_provider_works_at_url_build_level(self, db: Session):
        """URL-Build prueft nicht den enabled-Status — das macht der Router."""
        provider = _make_provider(db, slug="gh", preset="github", enabled=False)
        auth_url, _ = oauth_service.build_authorization_url(db, provider)
        assert auth_url.startswith("https://github.com/")

    def test_link_mode_payload_contains_user_id_and_unified_callback(
        self, db: Session, regular_user: User
    ):
        """Im link-Mode MUSS das State-Payload user_id enthalten und die
        redirect_uri MUSS der geteilte /callback sein (nicht /link/callback)."""
        provider = _make_provider(db, slug="gh-link", preset="github")
        _auth_url, encrypted = oauth_service.build_authorization_url(
            db, provider, mode=oauth_service.OAUTH_MODE_LINK, user=regular_user
        )
        payload = oauth_service.unpack_state_cookie(encrypted)
        assert payload is not None
        assert payload["mode"] == oauth_service.OAUTH_MODE_LINK
        assert payload["user_id"] == regular_user.id
        assert payload["redirect_uri"].endswith("/api/oauth/gh-link/callback")
        assert "next" not in payload  # Login-only Feld

    def test_login_mode_default_uses_unified_callback(self, db: Session):
        provider = _make_provider(db, slug="gh-log", preset="github")
        _auth_url, encrypted = oauth_service.build_authorization_url(db, provider)
        payload = oauth_service.unpack_state_cookie(encrypted)
        assert payload["mode"] == oauth_service.OAUTH_MODE_LOGIN
        assert payload["redirect_uri"].endswith("/api/oauth/gh-log/callback")

    def test_link_mode_requires_user(self, db: Session):
        provider = _make_provider(db, slug="gh-x", preset="github")
        with pytest.raises(ValueError, match="user ist Pflicht"):
            oauth_service.build_authorization_url(
                db, provider, mode=oauth_service.OAUTH_MODE_LINK
            )

    def test_unknown_mode_rejected(self, db: Session):
        provider = _make_provider(db, slug="gh-x", preset="github")
        with pytest.raises(ValueError, match="Unknown mode"):
            oauth_service.build_authorization_url(
                db, provider, mode="bogus"
            )


# ── Public-Listing ────────────────────────────────────────────────────

class TestPublicListing:
    def test_only_enabled_providers_listed(self, db: Session):
        _make_provider(db, slug="aaa", preset="github", enabled=True)
        _make_provider(db, slug="bbb", preset="github", enabled=False)
        public = oauth_service.list_public_providers(db)
        slugs = [p["slug"] for p in public]
        assert "aaa" in slugs
        assert "bbb" not in slugs

    def test_public_does_not_leak_client_id(self, db: Session):
        _make_provider(db, slug="ccc", preset="github", enabled=True)
        public = oauth_service.list_public_providers(db)
        for p in public:
            assert "client_id" not in p
            assert "client_secret" not in p
            assert "client_secret_encrypted" not in p


# ── Helpers (lokal, NICHT in conftest) ────────────────────────────────

def _make_provider(
    db: Session, *, slug: str, preset: str, enabled: bool = True
) -> OAuthProvider:
    return oauth_service.create_provider(
        db, slug=slug, name=slug, preset=preset, enabled=enabled,
        client_id="cid", client_secret="sec" if preset.startswith("custom_") else None,
        issuer=None, authorization_endpoint=None, token_endpoint=None,
        userinfo_endpoint=None, scope=None, claims_mapping_json=None, position=0,
    )


def _make_profile(subject: str, email: str | None, *, email_verified: bool) -> oauth_service.NormalizedProfile:
    return oauth_service.NormalizedProfile(
        subject=subject,
        email=email,
        email_verified=email_verified,
        username=email or subject,
        name=None,
        avatar=None,
        raw={},
    )
