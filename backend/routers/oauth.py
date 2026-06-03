"""OAuth/OIDC-Router (Phase 4 — Social Login).

Drei Bereiche, klare Security-Gates:

1) Admin-CRUD auf /api/oauth/providers
   - Erfordert panel.oauth.*-Permissions
   - Audit-Log fuer created/updated/deleted/toggled/secret.updated
   - Secrets werden im Response maskiert

2) Public-Flow auf /api/oauth/{slug}/{start,callback,2fa}
   - Anonym, mit State-Cookie (Fernet-encrypted)
   - PKCE
   - Kein direkter Zugriff auf IdP-Secrets zur Laufzeit noetig
   - 2FA-Gate ueber LoginChallenge

3) User-Self-Linking auf /api/oauth/me/links[/{slug}/...]
   - Erfordert eingeloggten User
   - Account-Linking benoetigt die globale Panel-Switch ``oauth.allow_linking``
     ODER die Admin-Permission ``panel.oauth.delete``
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from config import settings
from cookies import _set_auth_cookies
from database import get_db
from dependencies import get_current_user, require_global, verify_csrf
from models import AuditLog, OAuthProvider, OAuthUserLink, User
from schemas.oauth import (
    OAuthProviderCreate,
    OAuthProviderPublic,
    OAuthProviderUpdate,
    OAuthTestResult,
)
from services import oauth_service
from services.auth_service import AuthService
from services.panel_settings_service import PanelSettingsService

router = APIRouter(prefix="/api/oauth", tags=["oauth"])

_log = logging.getLogger("msm.oauth")


# ── Helpers ────────────────────────────────────────────────────────────

def _provider_to_response(p: OAuthProvider) -> dict[str, Any]:
    # Wir entschluesseln NUR fuer das Response-Masking — der Plain-Text wird
    # nicht persistiert oder geloggt. Die "****1234"-Maske auf dem verschluesselten
    # Blob waere unleserlich; die Plain-Maske kommuniziert dem Admin, was er
    # hinterlegt hat. Performance: 1x Fernet-Decrypt pro Provider-Listing.
    plain = ""
    if p.client_secret_encrypted:
        try:
            plain = oauth_service.decrypt_secret(p.client_secret_encrypted)
        except Exception as exc:
            _log.warning("OAuth provider %s secret decryption failed: %s", p.slug, exc)
            plain = ""  # Defekt nach SECRET_KEY-Rotation → leer maskieren
    return {
        "id": p.id,
        "slug": p.slug,
        "name": p.name,
        "preset": p.preset,
        "enabled": p.enabled,
        "client_id": p.client_id,
        "client_secret": oauth_service.mask_secret(plain),
        "issuer": p.issuer,
        "authorization_endpoint": p.authorization_endpoint,
        "token_endpoint": p.token_endpoint,
        "userinfo_endpoint": p.userinfo_endpoint,
        "scope": p.scope,
        "claims_mapping_json": p.claims_mapping_json,
        "position": p.position,
        "created_at": p.created_at.isoformat() if p.created_at else "",
        "updated_at": p.updated_at.isoformat() if p.updated_at else "",
    }


def _audit(db: Session, user_id: int | None, action: str, target_id: int | None, details: str | None = None) -> None:
    """Schreibt einen Audit-Log-Eintrag. NIEMals Secret-Werte in `details`."""
    entry = AuditLog(
        user_id=user_id,
        action=action,
        target_type="oauth_provider",
        target_id=target_id,
        details=details,
    )
    db.add(entry)
    db.commit()


def _set_login_session(response: Response, db: Session, user: User) -> None:
    access_token = AuthService.create_access_token({"sub": user.username, "user_id": user.id, "jti": str(uuid.uuid4())})
    refresh_token = AuthService.create_refresh_token(db, user.id)
    csrf_token = AuthService.create_csrf_token()
    _set_auth_cookies(response, access_token, refresh_token, csrf_token)


def _set_oauth_state_cookie(response: Response, encrypted: str) -> None:
    response.set_cookie(
        key=oauth_service.STATE_COOKIE_NAME,
        value=encrypted,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/api/oauth",
        max_age=oauth_service.STATE_TTL_SECONDS,
    )


def _clear_oauth_state_cookie(response: Response) -> None:
    response.delete_cookie(
        key=oauth_service.STATE_COOKIE_NAME,
        path="/api/oauth",
        secure=True,
        samesite="lax",
    )


# ── Public: Public-Provider-Listing fuer Login-UI ─────────────────────

@router.get("/public/providers", response_model=list[OAuthProviderPublic])
def public_list_providers(db: Session = Depends(get_db)) -> list[OAuthProviderPublic]:
    rows = oauth_service.list_public_providers(db)
    return [OAuthProviderPublic(**r) for r in rows]


# ── Admin: Provider-CRUD ──────────────────────────────────────────────

@router.get("/providers")
def list_providers(
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.oauth.read")),
) -> list[dict[str, Any]]:
    return [_provider_to_response(p) for p in oauth_service.list_providers(db)]


@router.get("/providers/{provider_id}")
def get_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.oauth.read")),
) -> dict[str, Any]:
    p = oauth_service.get_provider_by_id(db, provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Provider nicht gefunden")
    return _provider_to_response(p)


@router.post("/providers", status_code=201)
def create_provider(
    body: OAuthProviderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("panel.oauth.create")),
    __=Depends(verify_csrf),
) -> dict[str, Any]:
    try:
        p = oauth_service.create_provider(
            db,
            slug=body.slug,
            name=body.name,
            preset=body.preset,
            enabled=body.enabled,
            client_id=body.client_id,
            client_secret=body.client_secret,
            issuer=body.issuer,
            authorization_endpoint=body.authorization_endpoint,
            token_endpoint=body.token_endpoint,
            userinfo_endpoint=body.userinfo_endpoint,
            scope=body.scope,
            claims_mapping_json=body.claims_mapping_json,
            position=body.position,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _audit(db, user.id, "oauth_provider.created", p.id, f"slug={p.slug} preset={p.preset}")
    return _provider_to_response(p)


@router.patch("/providers/{provider_id}")
def update_provider(
    provider_id: int,
    body: OAuthProviderUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("panel.oauth.update")),
    __=Depends(verify_csrf),
) -> dict[str, Any]:
    p = oauth_service.get_provider_by_id(db, provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Provider nicht gefunden")
    try:
        p = oauth_service.update_provider(
            db, p,
            name=body.name,
            enabled=body.enabled,
            client_id=body.client_id,
            client_secret=body.client_secret,
            issuer=body.issuer,
            authorization_endpoint=body.authorization_endpoint,
            token_endpoint=body.token_endpoint,
            userinfo_endpoint=body.userinfo_endpoint,
            scope=body.scope,
            claims_mapping_json=body.claims_mapping_json,
            position=body.position,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _audit(db, user.id, "oauth_provider.updated", p.id, f"slug={p.slug}")
    return _provider_to_response(p)


@router.delete("/providers/{provider_id}", status_code=200)
def delete_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("panel.oauth.delete")),
    __=Depends(verify_csrf),
) -> dict[str, str]:
    p = oauth_service.get_provider_by_id(db, provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Provider nicht gefunden")
    slug = p.slug
    oauth_service.delete_provider(db, p)
    _audit(db, user.id, "oauth_provider.deleted", provider_id, f"slug={slug}")
    return {"message": "Provider geloescht"}


@router.post("/providers/{provider_id}/secret", status_code=200)
def update_provider_secret(
    provider_id: int,
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("panel.oauth.secret_update")),
    __=Depends(verify_csrf),
) -> dict[str, str]:
    """Setzt NUR das Client-Secret. Separate Permission.

    Body: ``{"client_secret": "..."}``. Leeren String = Secret loeschen.
    """
    p = oauth_service.get_provider_by_id(db, provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Provider nicht gefunden")
    secret = body.get("client_secret", "")
    if not isinstance(secret, str):
        raise HTTPException(status_code=400, detail="client_secret muss String sein")
    oauth_service.update_provider(db, p, client_secret=secret)
    _audit(db, user.id, "oauth_provider.secret.updated", p.id, f"slug={p.slug}")
    return {"message": "Secret aktualisiert"}


@router.post("/providers/{provider_id}/test", response_model=OAuthTestResult)
def test_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_global("panel.oauth.test")),
) -> OAuthTestResult:
    p = oauth_service.get_provider_by_id(db, provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Provider nicht gefunden")
    ok, message = oauth_service.test_provider_connection(db, p)
    return OAuthTestResult(ok=ok, message=message)


# ── Admin: Globale OAuth-Switches ──────────────────────────────────────

@router.get("/switches")
def get_switches(
    _=Depends(require_global("panel.oauth.read")),
) -> dict[str, bool]:
    return {
        "allow_registration": oauth_service.is_registration_allowed(),
        "allow_linking": oauth_service.is_linking_allowed(),
        "require_verified_email": oauth_service.requires_verified_email(),
    }


@router.patch("/switches", status_code=200)
def update_switches(
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("panel.oauth.update")),
    __=Depends(verify_csrf),
) -> dict[str, bool]:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body muss ein Objekt sein")
    changed: list[str] = []
    for field, key in (
        ("allow_registration", oauth_service.SWITCH_ALLOW_REGISTRATION),
        ("allow_linking", oauth_service.SWITCH_ALLOW_LINKING),
        ("require_verified_email", oauth_service.SWITCH_REQUIRE_VERIFIED_EMAIL),
    ):
        if field in body:
            val = body[field]
            if not isinstance(val, bool):
                raise HTTPException(status_code=400, detail=f"{field} muss boolean sein")
            PanelSettingsService.set(key, "true" if val else "false")
            changed.append(field)
    if changed:
        _audit(db, user.id, "oauth_switches.updated", None, ",".join(changed))
    return {
        "allow_registration": oauth_service.is_registration_allowed(),
        "allow_linking": oauth_service.is_linking_allowed(),
        "require_verified_email": oauth_service.requires_verified_email(),
    }


# ── Public-Flow: Login mit OAuth ──────────────────────────────────────

@router.get("/{slug}/start")
def oauth_start(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    next: str = "/",  # noqa: A002 — FastAPI query param
) -> Response:
    """Erzeugt die authorize-URL und setzt das State-Cookie. Redirect dorthin."""
    provider = oauth_service.get_provider_by_slug(db, slug)
    if provider is None or not provider.enabled:
        raise HTTPException(status_code=404, detail="Provider nicht verfuegbar")
    try:
        auth_url, encrypted = oauth_service.build_authorization_url(db, provider, next_path=next)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    resp = RedirectResponse(url=auth_url, status_code=302)
    _set_oauth_state_cookie(resp, encrypted)
    return resp


@router.get("/{slug}/callback")
def oauth_callback(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """Verarbeitet den IdP-Callback. Bei Erfolg: Login-Cookies setzen.

    Bei 2FA: Redirect auf ``/login?step=oauth_2fa&challenge=...&slug=...``.
    Bei Forbidden/Fehler: Redirect auf ``/login?error=oauth``.
    """
    provider = oauth_service.get_provider_by_slug(db, slug)
    if provider is None or not provider.enabled:
        return _redirect_login_error("oauth_provider_unavailable")

    if error:
        _log.info("OAuth callback error from IdP: %s (slug=%s)", error, slug)
        return _redirect_login_error("oauth_idp_error")
    if not code or not state:
        return _redirect_login_error("oauth_invalid_callback")

    state_cookie = request.cookies.get(oauth_service.STATE_COOKIE_NAME)
    payload = oauth_service.unpack_state_cookie(state_cookie)
    if payload is None or payload.get("state") != state:
        _log.warning("OAuth state mismatch (slug=%s)", slug)
        return _redirect_login_error("oauth_state_mismatch")

    # Code einloesen
    try:
        tokens = oauth_service.exchange_code(
            db, provider, code, payload["code_verifier"], payload["redirect_uri"]
        )
    except ValueError as e:
        _log.warning("OAuth code exchange failed: %s (slug=%s)", e, slug)
        return _redirect_login_error("oauth_exchange_failed")

    # Profil holen
    try:
        raw_profile = oauth_service.fetch_user_profile(db, provider, tokens)
    except ValueError as e:
        _log.warning("OAuth profile fetch failed: %s (slug=%s)", e, slug)
        return _redirect_login_error("oauth_profile_fetch_failed")

    preset = oauth_service.get_preset(provider.preset)
    if preset is None:
        return _redirect_login_error("oauth_preset_unknown")
    try:
        profile = oauth_service.normalize_profile(preset, raw_profile, provider.claims_mapping_json)
    except ValueError as e:
        _log.warning("OAuth profile normalization failed: %s (slug=%s)", e, slug)
        return _redirect_login_error("oauth_profile_invalid")

    # User resolven
    result = oauth_service.resolve_user(db, provider, profile)

    if result.action == "forbidden":
        _log.info("OAuth resolution forbidden: %s (slug=%s)", result.reason, slug)
        return _redirect_login_error(result.reason or "oauth_forbidden")

    if result.action == "register":
        try:
            user = oauth_service.register_user_from_oauth(db, profile)
        except ValueError as e:
            return _redirect_login_error("oauth_registration_failed")
        # Link direkt anlegen
        oauth_service.link_provider_to_user(db, provider, user, profile)
        _audit(db, user.id, "oauth_user.registered", provider.id, f"slug={provider.slug}")
        result = oauth_service._post_resolve(user)  # type: ignore[attr-defined]

    if result.action == "link":
        # Sollte im anonymen Login-Flow nicht passieren (current_user=None),
        # aber defensiv: fallback auf forbidden.
        if result.user is None:
            return _redirect_login_error("oauth_link_invalid")
        user = result.user
        try:
            oauth_service.link_provider_to_user(db, provider, user, profile)
        except ValueError:
            return _redirect_login_error("oauth_link_failed")
        _audit(db, user.id, "oauth_user.linked", provider.id, f"slug={provider.slug}")
        result = oauth_service._post_resolve(user)  # type: ignore[attr-defined]

    if result.action == "needs_2fa" and result.user is not None:
        challenge = oauth_service.create_2fa_challenge(db, result.user, provider)
        _audit(db, result.user.id, "oauth_login.2fa_required", provider.id, f"slug={provider.slug}")
        return _redirect_oauth_2fa(slug, challenge)

    # Aktion "login" → Session setzen
    if result.action == "login" and result.user is not None:
        user = result.user
        # last_used_at auf Link aktualisieren
        link = (
            db.query(OAuthUserLink)
            .filter(
                OAuthUserLink.provider_id == provider.id,
                OAuthUserLink.user_id == user.id,
            )
            .first()
        )
        if link is not None:
            oauth_service.update_link_last_used(db, link)
        _audit(db, user.id, "oauth_login.success", provider.id, f"slug={provider.slug}")
        next_path = payload.get("next") or "/"
        # Open-Redirect-Schutz: nur relative Pfade
        if not next_path.startswith("/") or next_path.startswith("//"):
            next_path = "/"
        resp = _redirect_ok(next_path)
        _set_login_session(resp, db, user)
        _clear_oauth_state_cookie(resp)
        return resp

    return _redirect_login_error("oauth_unknown")


@router.post("/{slug}/2fa")
def oauth_2fa(
    slug: str,
    body: dict,
    db: Session = Depends(get_db),
) -> Response:
    """Vervollstaendigt einen OAuth-Login, bei dem der User 2FA aktiv hat.

    Body: ``{"challenge": "...", "otp_code": "123456"}``
    """
    challenge = (body or {}).get("challenge", "")
    otp_code = (body or {}).get("otp_code", "")
    if not isinstance(challenge, str) or not isinstance(otp_code, str):
        raise HTTPException(status_code=400, detail="Ungueltige Anfrage")
    completed = oauth_service.complete_2fa_challenge(db, challenge, otp_code)
    if completed is None:
        raise HTTPException(status_code=401, detail="Ungueltige oder abgelaufene Challenge / falscher Code")
    user, provider = completed
    if provider.slug != slug:
        raise HTTPException(status_code=400, detail="Challenge-Provider stimmt nicht")
    _audit(db, user.id, "oauth_login.2fa_success", provider.id, f"slug={provider.slug}")
    resp = _redirect_ok("/")
    _set_login_session(resp, db, user)
    _clear_oauth_state_cookie(resp)
    return resp


# ── User-Self: Linked-Accounts ────────────────────────────────────────

@router.get("/me/links")
def list_my_links(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    links = oauth_service.list_user_links(db, user.id)
    result: list[dict[str, Any]] = []
    for link in links:
        provider = oauth_service.get_provider_by_id(db, link.provider_id)
        if provider is None:
            continue
        result.append({
            "id": link.id,
            "provider_id": link.provider_id,
            "provider_slug": provider.slug,
            "provider_name": provider.name,
            "provider_preset": provider.preset,
            "created_at": link.created_at.isoformat() if link.created_at else "",
            "last_used_at": link.last_used_at.isoformat() if link.last_used_at else None,
        })
    return result


@router.delete("/me/links/{provider_id}", status_code=200)
def unlink_my_account(
    provider_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _=Depends(verify_csrf),
) -> dict[str, str]:
    ok = oauth_service.unlink_user_from_provider(db, user.id, provider_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Kein Link fuer diesen Provider")
    _audit(db, user.id, "oauth_user.unlinked", provider_id)
    return {"message": "Verknuepfung aufgehoben"}


@router.get("/{slug}/link/start")
def oauth_link_start(
    slug: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Startet einen Linking-Flow fuer den aktuell eingeloggten User."""
    if not oauth_service.is_linking_allowed():
        raise HTTPException(status_code=403, detail="Account-Linking ist deaktiviert")
    provider = oauth_service.get_provider_by_slug(db, slug)
    if provider is None or not provider.enabled:
        raise HTTPException(status_code=404, detail="Provider nicht verfuegbar")
    try:
        auth_url, encrypted = oauth_service.build_link_authorization_url(db, provider, user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    resp = RedirectResponse(url=auth_url, status_code=302)
    _set_oauth_state_cookie(resp, encrypted)
    return resp


@router.get("/{slug}/link/callback")
def oauth_link_callback(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """Verarbeitet den IdP-Callback fuer den Linking-Flow."""
    if not oauth_service.is_linking_allowed():
        raise HTTPException(status_code=403, detail="Account-Linking ist deaktiviert")
    provider = oauth_service.get_provider_by_slug(db, slug)
    if provider is None or not provider.enabled:
        return _redirect_profile_error("provider_unavailable")

    if error or not code or not state:
        return _redirect_profile_error("invalid_callback")

    state_cookie = request.cookies.get(oauth_service.STATE_COOKIE_NAME)
    payload = oauth_service.unpack_state_cookie(state_cookie)
    if payload is None or payload.get("state") != state or payload.get("user_id") != user.id:
        _log.warning("OAuth link state mismatch (slug=%s, user=%s)", slug, user.id)
        return _redirect_profile_error("state_mismatch")

    try:
        tokens = oauth_service.exchange_code(
            db, provider, code, payload["code_verifier"], payload["redirect_uri"]
        )
        raw_profile = oauth_service.fetch_user_profile(db, provider, tokens)
    except ValueError as e:
        _log.warning("OAuth link exchange/profile failed: %s", e)
        return _redirect_profile_error("exchange_failed")

    preset = oauth_service.get_preset(provider.preset)
    if preset is None:
        return _redirect_profile_error("preset_unknown")
    try:
        profile = oauth_service.normalize_profile(preset, raw_profile, provider.claims_mapping_json)
    except ValueError:
        return _redirect_profile_error("profile_invalid")

    try:
        link = oauth_service.link_provider_to_user(db, provider, user, profile)
    except ValueError:
        return _redirect_profile_error("link_failed")
    _audit(db, user.id, "oauth_user.linked", provider.id, f"slug={provider.slug}")
    resp = _redirect_profile_ok()
    _clear_oauth_state_cookie(resp)
    return resp


# ── Redirect-Helpers ───────────────────────────────────────────────────

def _login_redirect_path() -> str:
    return "/login"


def _profile_redirect_path() -> str:
    return "/profile"


def _redirect_login_error(reason: str) -> Response:
    url = f"{_login_redirect_path()}?error={reason}"
    resp = RedirectResponse(url=url, status_code=302)
    _clear_oauth_state_cookie(resp)
    return resp


def _redirect_oauth_2fa(slug: str, challenge: str) -> Response:
    from urllib.parse import urlencode
    url = f"{_login_redirect_path()}?{urlencode({'step': 'oauth_2fa', 'slug': slug, 'challenge': challenge})}"
    resp = RedirectResponse(url=url, status_code=302)
    _clear_oauth_state_cookie(resp)
    return resp


def _redirect_ok(next_path: str) -> Response:
    resp = RedirectResponse(url=next_path, status_code=302)
    return resp


def _redirect_profile_error(reason: str) -> Response:
    resp = RedirectResponse(url=f"{_profile_redirect_path()}?error={reason}", status_code=302)
    _clear_oauth_state_cookie(resp)
    return resp


def _redirect_profile_ok() -> Response:
    resp = RedirectResponse(url=f"{_profile_redirect_path()}?linked=1", status_code=302)
    return resp
