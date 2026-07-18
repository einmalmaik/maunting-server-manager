"""OAuth/OIDC-Router (Phase 4 — Social Login).

Drei Bereiche, klare Security-Gates:

1) Admin-CRUD auf /api/oauth/providers
   - Erfordert panel.oauth.*-Permissions
   - Audit-Log fuer created/updated/deleted/toggled/secret.updated
   - Secrets werden im Response maskiert

2) Public-Flow auf /api/oauth/{slug}/{start,callback,2fa}
   - Anonym, mit State-Cookie (DIS-encrypted)
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

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from config import settings, get_effective_cookie_domain
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
from services.email_service import EmailService
from services.panel_settings_service import PanelSettingsService

router = APIRouter(prefix="/api/oauth", tags=["oauth"])

_log = logging.getLogger("msm.oauth")

def _no_cache_redirect(url: str, status_code: int = 302) -> "RedirectResponse":
    """Erzeugt RedirectResponse mit strikten no-cache Headers.

    Kritisch fuer OAuth: Der Redirect zur IdP-Authorize-URL (mit state + PKCE)
    darf niemals vom Browser, Proxy oder SW gecached werden. Sonst wird ein
    alter State wiederverwendet -> state_mismatch auch bei aktivem Klick.
    """
    resp = RedirectResponse(url=url, status_code=status_code)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def _truncate_for_log(value: str | None, max_len: int = 16) -> str:
    """Kuerzt einen String fuer Log-Output. Verhindert, dass extrem lange
    (potenziell boseartige) Werte Log-Aggregatoren (Loki/Splunk/ELK) zumuellen
    oder PII-Tokens enthalten. Format: "abcdef1234..." bzw. original bei
    bereits kurzem Wert.
    """
    if value is None:
        return "<none>"
    s = str(value)
    if len(s) <= max_len:
        return s
    return s[:max_len] + "..."


# ── Helpers ────────────────────────────────────────────────────────────

def _provider_to_response(p: OAuthProvider) -> dict[str, Any]:
    # Maske kommt direkt aus der DB-Spalte `client_secret_mask` (wird beim
    # Create/Update berechnet). Kein DIS-Decrypt im Read-Pfad mehr noetig.
    # Fuer sehr alte Provider ohne gesetzte Maske (vor der P1.3-Migration):
    # Fallback "****" signalisiert "Secret ist gesetzt, aber Maske unbekannt".
    # Der Admin kann durch erneutes Speichern die Maske neu berechnen.
    secret_mask = p.client_secret_mask
    if secret_mask is None and p.client_secret_encrypted:
        secret_mask = "****"  # Legacy: nicht-migrierter Provider
    return {
        "id": p.id,
        "slug": p.slug,
        "name": p.name,
        "preset": p.preset,
        "enabled": p.enabled,
        "client_id": p.client_id,
        "client_secret": secret_mask or "",
        "issuer": p.issuer,
        "authorization_endpoint": p.authorization_endpoint,
        "token_endpoint": p.token_endpoint,
        "userinfo_endpoint": p.userinfo_endpoint,
        "scope": p.scope,
        "claims_mapping_json": p.claims_mapping_json,
        "position": p.position,
        # Die tatsaechlich vom Backend an den IdP gesendete redirect_uri.
        # Wird im Admin-UI read-only + Copy-Button angezeigt, damit die
        # IdP-Konfiguration (Google/Discord/...) nicht versehentlich auf
        # einen nicht existierenden Pfad wie /api/oauth/{slug}/link/callback
        # zeigt. KISS: ein Endpunkt, eine Quelle der Wahrheit.
        "redirect_uri": oauth_service.build_redirect_uri(p.slug),
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
    # SameSite=None + Secure: der State-Cookie MUSS den IdP-Cross-Site-
    # Roundtrip (z. B. Google → msm.mauntingstudios.de) zuverlaessig
    # ueberleben. SameSite=Lax schickt Cookies NUR bei Top-Level-Navigation;
    # fuer OAuth-Callbacks (vor allem bei `prompt=none` silent re-auth ueber
    # hidden iframe) braucht es None. Security: der State-Wert ist 24 Bytes
    # random + PKCE-Code-Verifier, beide nicht erratbar. HttpOnly verhindert
    # JS-Zugriff. Domain-Attribut wird weiterhin aus get_effective_cookie_domain()
    # abgeleitet, damit Subdomain-Setups (z. B. app.X.example.com) korrekt
    # funktionieren.
    cookie_kwargs: dict[str, Any] = {
        "key": oauth_service.STATE_COOKIE_NAME,
        "value": encrypted,
        "httponly": True,
        "secure": True,
        "samesite": "none",
        "path": "/",
        "max_age": oauth_service.STATE_TTL_SECONDS,
    }
    cookie_domain = get_effective_cookie_domain()
    if cookie_domain:
        cookie_kwargs["domain"] = cookie_domain
    response.set_cookie(**cookie_kwargs)


def _clear_oauth_state_cookie(response: Response) -> None:
    # Gleiche Domain/Path/SameSite-Attribute wie beim Setzen → Cookie wird
    # zuverlaessig geloescht (auch bei Mismatch-Fehlern oder nach erfolgreichem
    # Login). SameSite=None beim Delete ist zulaessig — Browser matchen das
    # gegen das urspruenglich gesetzte Cookie.
    delete_kwargs: dict[str, Any] = {
        "key": oauth_service.STATE_COOKIE_NAME,
        "path": "/",
        "secure": True,
        "samesite": "none",
    }
    cookie_domain = get_effective_cookie_domain()
    if cookie_domain:
        delete_kwargs["domain"] = cookie_domain
    response.delete_cookie(**delete_kwargs)


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
        auth_url, encrypted = oauth_service.build_authorization_url(
            db, provider, mode=oauth_service.OAUTH_MODE_LOGIN, next_path=next
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    cookie_domain = get_effective_cookie_domain()
    # NICHT die volle auth_url loggen — sie enthaelt state + code_challenge,
    # mit denen ein Angreifer (Log-Reader) den Token-Exchange replizieren
    # koennte. Wir loggen nur den Host des IdP + slug + Domain-Info.
    from urllib.parse import urlparse
    idp_host = urlparse(auth_url).netloc
    _log.info(
        "OAuth start (slug=%s) → IdP=%s (cookie_domain=%r, panel_url=%s)",
        slug, idp_host, cookie_domain or "(host-only)", settings.panel_url,
    )
    resp = _no_cache_redirect(auth_url)
    _set_oauth_state_cookie(resp, encrypted)
    return resp


@router.get("/{slug}/callback")
def oauth_callback(
    slug: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """Verarbeitet den IdP-Callback fuer Login UND Account-Linking.

    Beide Flows nutzen denselben Callback — die Unterscheidung laeuft ueber
    ``mode`` im State-Payload. Damit muss in der IdP-Console (Google, Discord,
    ...) nur EINE redirect_uri registriert werden: ``/api/oauth/{slug}/callback``.
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
    # WICHTIG: Mode ZUERST lesen, BEVOR wir auf Mismatch prüfen — sonst landet
    # ein Link-Mode-Fehler auf /login (was PublicOnlyRoute für eingeloggte User
    # auf / redirected → der User sieht nie die Fehlermeldung in /profile).
    # Bei kaputtem Payload ist der Mode unbekannt → Default login als sicherer
    # Fallback (Login-Fehler ist weniger schlimm als stilles Verschwinden).
    mode = (payload or {}).get("mode", oauth_service.OAUTH_MODE_LOGIN)
    if mode not in (oauth_service.OAUTH_MODE_LOGIN, oauth_service.OAUTH_MODE_LINK):
        _log.warning("OAuth callback: unknown mode=%r (slug=%s)", mode, slug)
        return _redirect_login_error("oauth_invalid_callback")

    if payload is None or payload.get("state") != state:
        # Diagnose-Helfer: bei 'neuer User bekommt state_mismatch' ist die Frage
        # immer 'was ist mit dem State-Cookie passiert?'. Wir loggen den genauen
        # Grund statt nur 'mismatch' — sieht der Server 'no_cookie', fehlt das
        # Cookie komplett (Browser-Block / SameSite / Path-Problem). Sieht er
        # 'state_mismatch' aber Cookie ist da, ist der State-Wert selbst falsch.
        if not state_cookie:
            _log.warning("OAuth state mismatch (slug=%s, mode=%s): no __Secure-oauth_state cookie on request", slug, mode)
        elif payload is None:
            _log.warning("OAuth state mismatch (slug=%s, mode=%s): cookie present but DIS decrypt failed", slug, mode)
        else:
            # State-Werte koennen potenziell lang sein (URL-kontrolliert) —
            # fuer den Log auf 16 Zeichen kuerzen, damit Log-Aggregatoren
            # nicht zugemuellt werden und keine PII leakt.
            _log.warning(
                "OAuth state mismatch (slug=%s, mode=%s): state value differs (cookie has %r, URL has %r)",
                slug, mode,
                _truncate_for_log(payload.get("state")),
                _truncate_for_log(state),
            )
        # Modus-spezifisches Redirect-Ziel: Link-Mode-Fehler gehören auf /profile,
        # damit der User (der noch eingeloggt ist) die Toast-Meldung tatsächlich
        # sieht. Login-Mode-Fehler bleiben auf /login.
        if mode == oauth_service.OAUTH_MODE_LINK:
            return _redirect_profile_error("state_user_mismatch")
        return _redirect_login_error("oauth_state_mismatch")

    # ── Link-Mode: Auth + Switch-Check VOR externem Token-Call ──
    if mode == oauth_service.OAUTH_MODE_LINK:
        link_user = _resolve_link_user(request, db, payload)
        if isinstance(link_user, Response):
            return link_user  # Redirect-Response auf /profile?error=...

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

    # ── Link-Mode: Provider an authentifizierten User haengen ──
    if mode == oauth_service.OAUTH_MODE_LINK:
        try:
            oauth_service.link_provider_to_user(db, provider, link_user, profile)
        except ValueError:
            return _redirect_profile_error("link_failed")
        _audit(db, link_user.id, "oauth_user.linked", provider.id, f"slug={provider.slug}")

        # Email via BackgroundTasks (reliable, runs after redirect response)
        if EmailService.is_configured() and link_user.email_notifications:
            background_tasks.add_task(
                EmailService.send_oauth_linked_notification,
                link_user.email, link_user.username, provider.name
            )

        resp = _redirect_profile_ok()
        _clear_oauth_state_cookie(resp)
        return resp

    # ── Login-Flow (anonym) ──
    return _handle_login_callback(db, provider, profile, payload, slug, background_tasks)


def _resolve_link_user(
    request: Request, db: Session, payload: dict[str, Any]
) -> User | Response:
    """Validiert Auth + Linking-Switch + state.user_id — VOR externem IdP-Call.

    Liefert den authentifizierten User bei Erfolg oder einen Redirect-Response
    bei jedem Auth/Switch/Mismatch-Fehler. Pattern: 'return early with error
    page' statt verschachtelter ifs.
    """
    if not oauth_service.is_linking_allowed():
        return _redirect_profile_error("linking_disabled")
    token = request.cookies.get("__Secure-access_token")
    if not token:
        return _redirect_profile_error("auth_required")
    try:
        current_user = get_current_user(request, db)
    except HTTPException:
        return _redirect_profile_error("auth_required")
    state_user_id = payload.get("user_id")
    if state_user_id is None or int(state_user_id) != current_user.id:
        _log.warning(
            "OAuth link state user_id mismatch (payload=%s, current=%s)",
            state_user_id, current_user.id,
        )
        return _redirect_profile_error("state_user_mismatch")
    return current_user


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
async def unlink_my_account(
    provider_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _=Depends(verify_csrf),
) -> dict[str, str]:
    ok = oauth_service.unlink_user_from_provider(db, user.id, provider_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Kein Link fuer diesen Provider")

    provider = oauth_service.get_provider_by_id(db, provider_id)
    provider_name = provider.name if provider else f"Provider {provider_id}"

    _audit(db, user.id, "oauth_user.unlinked", provider_id)

    # Email-Benachrichtigung (respektiert user.email_notifications)
    # Await directly (route is now async) — reliable delivery, consistent with change-password etc.
    if EmailService.is_configured() and user.email_notifications:
        await EmailService.send_oauth_unlinked_notification(user.email, user.username, provider_name)

    return {"message": "Verknuepfung aufgehoben"}


@router.get("/{slug}/link/start")
def oauth_link_start(
    slug: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Startet einen Linking-Flow fuer den aktuell eingeloggten User.

    Der IdP redirected am Ende auf den geteilten ``/{slug}/callback`` — der
    Mode wird ueber das (DIS-encrypted) State-Cookie transportiert.
    """
    if not oauth_service.is_linking_allowed():
        raise HTTPException(status_code=403, detail="Account-Linking ist deaktiviert")
    provider = oauth_service.get_provider_by_slug(db, slug)
    if provider is None or not provider.enabled:
        raise HTTPException(status_code=404, detail="Provider nicht verfuegbar")
    try:
        auth_url, encrypted = oauth_service.build_authorization_url(
            db, provider, mode=oauth_service.OAUTH_MODE_LINK, user=user
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    cookie_domain = get_effective_cookie_domain()
    # IdP-Host statt voller URL loggen (siehe oauth_start).
    from urllib.parse import urlparse
    idp_host = urlparse(auth_url).netloc
    _log.info(
        "OAuth link/start (slug=%s) → IdP=%s (cookie_domain=%r, panel_url=%s)",
        slug, idp_host, cookie_domain or "(host-only)", settings.panel_url,
    )
    resp = _no_cache_redirect(auth_url)
    _set_oauth_state_cookie(resp, encrypted)
    return resp


# ── Redirect-Helpers ───────────────────────────────────────────────────

def _handle_login_callback(
    db: Session,
    provider: OAuthProvider,
    profile: "oauth_service.NormalizedProfile",
    payload: dict[str, Any],
    slug: str,
    background_tasks: BackgroundTasks,
) -> Response:
    """Anonymer Login-Pfad des geteilten /callback-Endpunkts (alter Flow)."""
    result = oauth_service.resolve_user(db, provider, profile)

    if result.action == "forbidden":
        _log.info("OAuth resolution forbidden: %s (slug=%s)", result.reason, slug)
        return _redirect_login_error(result.reason or "oauth_forbidden")

    if result.action == "register":
        try:
            user = oauth_service.register_user_from_oauth(db, profile)
        except ValueError:
            return _redirect_login_error("oauth_registration_failed")
        oauth_service.link_provider_to_user(db, provider, user, profile)
        _audit(db, user.id, "oauth_user.registered", provider.id, f"slug={provider.slug}")

        # Willkommens-Benachrichtigung für OAuth-Registrierung (via BackgroundTasks)
        if EmailService.is_configured() and user.email_notifications:
            background_tasks.add_task(
                EmailService.send_account_registered_notification,
                user.email, user.username
            )

        result = oauth_service._post_resolve(user)  # type: ignore[attr-defined]

    if result.action == "link":
        if result.user is None:
            return _redirect_login_error("oauth_link_invalid")
        user = result.user
        try:
            oauth_service.link_provider_to_user(db, provider, user, profile)
        except ValueError:
            return _redirect_login_error("oauth_link_failed")
        _audit(db, user.id, "oauth_user.linked", provider.id, f"slug={provider.slug}")

        # Email-Benachrichtigung für Link (auch über Login-Flow) via BackgroundTasks
        if EmailService.is_configured() and user.email_notifications:
            background_tasks.add_task(
                EmailService.send_oauth_linked_notification,
                user.email, user.username, provider.name
            )

        result = oauth_service._post_resolve(user)  # type: ignore[attr-defined]

    if result.action == "needs_2fa" and result.user is not None:
        challenge = oauth_service.create_2fa_challenge(db, result.user, provider)
        _audit(db, result.user.id, "oauth_login.2fa_required", provider.id, f"slug={provider.slug}")
        return _redirect_oauth_2fa(slug, challenge)

    if result.action == "login" and result.user is not None:
        user = result.user
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
        if not next_path.startswith("/") or next_path.startswith("//"):
            next_path = "/"
        resp = _redirect_ok(next_path)
        _set_login_session(resp, db, user)
        _clear_oauth_state_cookie(resp)
        return resp

    return _redirect_login_error("oauth_unknown")


def _login_redirect_path() -> str:
    return _frontend_url("/login")


def _profile_redirect_path() -> str:
    return _frontend_url("/profile")


def _frontend_url(path: str) -> str:
    """Browser landing URL after OAuth — always the user-facing SPA origin.

    Relative redirects after a callback on ``api.*`` would keep the user on
    the API host (404). Absolute panel_url fixes split hosting; all-in-one
    still works (panel_url is the same public origin).
    """
    if not path.startswith("/"):
        path = f"/{path}"
    # only same-site paths — never open-redirect to arbitrary hosts
    if path.startswith("//") or "://" in path.split("?", 1)[0]:
        path = "/"
    base = (settings.panel_url or "").rstrip("/")
    if not base:
        return path
    return f"{base}{path}"


def _redirect_login_error(reason: str) -> Response:
    url = f"{_login_redirect_path()}?error={reason}"
    resp = _no_cache_redirect(url)
    _clear_oauth_state_cookie(resp)
    return resp


def _redirect_oauth_2fa(slug: str, challenge: str) -> Response:
    from urllib.parse import urlencode
    url = f"{_login_redirect_path()}?{urlencode({'step': 'oauth_2fa', 'slug': slug, 'challenge': challenge})}"
    resp = _no_cache_redirect(url)
    _clear_oauth_state_cookie(resp)
    return resp


def _redirect_ok(next_path: str) -> Response:
    # next_path is usually "/" or a relative SPA route from state
    if not next_path or not next_path.startswith("/") or next_path.startswith("//"):
        next_path = "/"
    if "://" in next_path.split("?", 1)[0]:
        next_path = "/"
    resp = _no_cache_redirect(_frontend_url(next_path))
    return resp


def _redirect_profile_error(reason: str) -> Response:
    resp = _no_cache_redirect(f"{_profile_redirect_path()}?error={reason}")
    _clear_oauth_state_cookie(resp)
    return resp


def _redirect_profile_ok() -> Response:
    resp = _no_cache_redirect(f"{_profile_redirect_path()}?linked=1")
    return resp
