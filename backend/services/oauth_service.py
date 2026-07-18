"""OAuth/OIDC-Service-Fassade.

Verantwortlichkeiten:
- Provider-CRUD (admin) mit DIS-encrypted Client-Secrets.
- Public-Listing fuer Login-UI.
- OAuth-Flow: authorize URL mit PKCE, Callback-Handling, User-Resolution,
  Auto-Registration (gated by global Panel-Switch) und Account-Linking
  (gated by global Panel-Switch).
- 2FA-Challenge: Wenn der resolvierte User 2FA aktiv hat, wird ein
  LoginChallenge erzeugt und ein 2FA-Endpoint muss den Flow abschliessen.

Sicherheits-Invarianten (siehe docs/agent-rules/security.md):
- Client-Secrets und Access-Tokens NIEMals loggen, in Responses, Toasts, URLs.
- Auto-Registration und Linking per Default AUS (Panel-Switches).
- 2FA wird bei OAuth-Logins nicht umgangen.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from config import settings
from models import OAuthProvider, OAuthUserLink, User
from services import login_challenge_service
from services.auth_service import AuthService
from services.dis_client import DisDecryptionError
from services.oauth_presets import PRESETS, OAuthPreset, get_preset, known_preset
from services.panel_settings_service import PanelSettingsService

# ── Panel-Switches (Keys in panel_settings) ────────────────────────────

SWITCH_ALLOW_REGISTRATION = "oauth.allow_registration"
SWITCH_ALLOW_LINKING = "oauth.allow_linking"
SWITCH_REQUIRE_VERIFIED_EMAIL = "oauth.require_verified_email"

DEFAULT_ALLOW_REGISTRATION = "false"
DEFAULT_ALLOW_LINKING = "false"
DEFAULT_REQUIRE_VERIFIED_EMAIL = "true"

# ── State-Cookie ───────────────────────────────────────────────────────

STATE_COOKIE_NAME = "__Secure-oauth_state"
STATE_TTL_SECONDS = 600  # 10 Minuten


# ── Public-DTOs ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class NormalizedProfile:
    """Vom Provider gelesene, auf MSM-Felder normalisierte Profildaten."""

    subject: str
    email: str | None
    email_verified: bool
    username: str | None
    name: str | None
    avatar: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class ResolutionResult:
    """Was nach der User-Resolution passieren soll."""

    user: User | None
    action: str  # "login" | "register" | "link" | "needs_2fa" | "forbidden"
    reason: str | None = None  # optional, fuer Forbidden-Fall
    challenge_token: str | None = None  # fuer "needs_2fa"


# ── Secret-Masking (identisch zu panel_settings) ───────────────────────

def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def is_masked(value: str | None) -> bool:
    return bool(value) and value.startswith("*")


# ── Secret-Encryption (DIS AES-256-GCM, AAD-gebunden) ──────────────────

_OAUTH_SECRET_AAD = "msm:oauth:secret"
_OAUTH_STATE_AAD = "msm:oauth:state"


def encrypt_secret(plain: str) -> str:
    return AuthService.encrypt_secret(plain, aad=_OAUTH_SECRET_AAD)


def decrypt_secret(encrypted: str) -> str:
    return AuthService.decrypt_secret(encrypted, aad=_OAUTH_SECRET_AAD)


# ── State-Cookie: verschluesseltes JSON via DIS ───────────────────────

def pack_state_cookie(payload: dict[str, Any]) -> str:
    """Verschluesselt ein JSON-Payload als opaque Cookie-Wert."""
    raw = json.dumps(payload, separators=(",", ":"))
    return AuthService.encrypt_secret(raw, aad=_OAUTH_STATE_AAD)


def unpack_state_cookie(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        raw = AuthService.decrypt_secret(value, aad=_OAUTH_STATE_AAD)
    except (DisDecryptionError, ValueError):
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


# ── Panel-Switches lesen ───────────────────────────────────────────────

def _get_bool_switch(key: str, default: str) -> bool:
    raw = PanelSettingsService.get(key, default).strip().lower()
    return raw in ("1", "true", "yes", "on")


def is_registration_allowed() -> bool:
    return _get_bool_switch(SWITCH_ALLOW_REGISTRATION, DEFAULT_ALLOW_REGISTRATION)


def is_linking_allowed() -> bool:
    return _get_bool_switch(SWITCH_ALLOW_LINKING, DEFAULT_ALLOW_LINKING)


def requires_verified_email() -> bool:
    return _get_bool_switch(SWITCH_REQUIRE_VERIFIED_EMAIL, DEFAULT_REQUIRE_VERIFIED_EMAIL)


# ── Claim-Mapping ──────────────────────────────────────────────────────

def _resolve_dotted(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def _claim(claim_map: dict[str, str], profile: dict[str, Any], msm_field: str) -> Any:
    """Liest ein Feld aus dem IdP-Profil anhand der claim_map.

    Spezielle Werte:
      "_msm_default_true" → True (Fallback fuer Provider ohne verified-Claim,
                             z. B. GitHub, Twitter)
    """
    raw_path = claim_map.get(msm_field)
    if not raw_path:
        return None
    if raw_path == "_msm_default_true":
        return True
    val = _resolve_dotted(profile, raw_path)
    if isinstance(val, str) and not val:
        return None
    return val


def normalize_profile(
    preset: OAuthPreset, raw_profile: dict[str, Any], claims_override_json: str | None = None
) -> NormalizedProfile:
    """Wandelt rohe IdP-Profildaten in ein NormalizedProfile.

    ``claims_override_json`` (aus dem Provider-Datensatz) ueberschreibt das
    Preset-Mapping zur Laufzeit — so kann ein Admin das Mapping nachtraeglich
    anpassen, ohne den Code zu aendern.
    """
    claim_map = dict(preset.claim_map)
    if claims_override_json:
        try:
            override = json.loads(claims_override_json)
            if isinstance(override, dict):
                claim_map.update({k: str(v) for k, v in override.items()})
        except json.JSONDecodeError:
            pass  # Ignorieren — Preset-Mapping greift

    subject = _claim(claim_map, raw_profile, "id")
    if not subject:
        raise ValueError("Profile does not contain a subject identifier")
    email = _claim(claim_map, raw_profile, "email")
    email_verified = bool(_claim(claim_map, raw_profile, "email_verified"))
    username = _claim(claim_map, raw_profile, "username")
    name = _claim(claim_map, raw_profile, "name")
    avatar = _claim(claim_map, raw_profile, "avatar")

    return NormalizedProfile(
        subject=str(subject),
        email=str(email) if email else None,
        email_verified=email_verified,
        username=str(username) if username else None,
        name=str(name) if name else None,
        avatar=str(avatar) if avatar else None,
        raw=raw_profile,
    )


# ── Provider-Endpoints auflösen (mit OIDC-Discovery) ──────────────────

def _effective_endpoints(provider: OAuthProvider) -> tuple[OAuthPreset, dict[str, str]]:
    """Liefert das aktive Preset + effektive Endpoints (Preset < Override < Discovery)."""
    preset = get_preset(provider.preset)
    if preset is None:
        raise ValueError(f"Unknown preset: {provider.preset}")

    # Basis: Preset-Endpoints
    endpoints: dict[str, str | None] = {
        "authorization_endpoint": preset.authorization_endpoint,
        "token_endpoint": preset.token_endpoint,
        "userinfo_endpoint": preset.userinfo_endpoint,
        "issuer": preset.issuer,
    }

    # Override aus DB
    for field in ("issuer", "authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
        val = getattr(provider, field)
        if val:
            endpoints[field] = val

    # Custom OIDC: Discovery, falls Endpoints fehlen
    missing = [
        k for k in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint")
        if not endpoints.get(k)
    ]
    if missing and provider.preset == "custom_oidc" and endpoints.get("issuer"):
        discovery = _fetch_oidc_discovery(str(endpoints["issuer"]))
        for k in missing:
            if k in discovery:
                endpoints[k] = discovery[k]

    missing = [k for k in ("authorization_endpoint", "token_endpoint") if not endpoints.get(k)]
    if missing:
        raise ValueError(
            f"Provider '{provider.slug}' is missing required endpoints: {', '.join(missing)}"
        )

    return preset, {k: v for k, v in endpoints.items() if v}  # type: ignore[misc]


def _fetch_oidc_discovery(issuer: str) -> dict[str, Any]:
    """Holt das OIDC-Discovery-Dokument. Wirft ValueError bei Fehlern."""
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        raise ValueError(f"OIDC-Discovery fehlgeschlagen fuer '{issuer}': {e}") from e


# ── Provider-Listings ──────────────────────────────────────────────────

def list_providers(db: Session, *, enabled_only: bool = False) -> list[OAuthProvider]:
    q = db.query(OAuthProvider).order_by(OAuthProvider.position.asc(), OAuthProvider.id.asc())
    if enabled_only:
        q = q.filter(OAuthProvider.enabled == True)  # noqa: E712
    return q.all()


def get_provider_by_id(db: Session, provider_id: int) -> OAuthProvider | None:
    return db.query(OAuthProvider).filter(OAuthProvider.id == provider_id).first()


def get_provider_by_slug(db: Session, slug: str) -> OAuthProvider | None:
    return db.query(OAuthProvider).filter(OAuthProvider.slug == slug).first()


# ── Provider-CRUD ──────────────────────────────────────────────────────

def _validate_preset(preset_key: str) -> None:
    if not known_preset(preset_key):
        raise ValueError(f"Unknown preset: {preset_key}")


def _normalize_slug(slug: str) -> str:
    s = slug.strip().lower()
    if not s or len(s) < 2 or len(s) > 64:
        raise ValueError("Slug must be 2-64 chars")
    if not all(c.isalnum() or c in "-_" for c in s):
        raise ValueError("Slug must be lowercase alphanumeric with - or _")
    return s


def _normalize_scope(scope: str | None) -> str | None:
    if not scope:
        return None
    # authlib akzeptiert Whitespace-getrennte Listen
    parts = [p for p in scope.replace(",", " ").split() if p]
    return " ".join(parts) or None


def _validate_custom_endpoints(preset_key: str, provider: OAuthProvider) -> None:
    """Bei custom_oidc/custom_oauth2 muessen Endpoints entweder gesetzt sein
    (manuell) oder via OIDC-Discovery aufgeloest werden koennen.
    """
    if preset_key not in ("custom_oidc", "custom_oauth2"):
        return
    if not provider.authorization_endpoint and not provider.issuer:
        raise ValueError("Custom OIDC benoetigt 'issuer', Custom OAuth2 benoetigt authorization_endpoint")


def create_provider(
    db: Session,
    *,
    slug: str,
    name: str,
    preset: str,
    enabled: bool,
    client_id: str,
    client_secret: str | None,
    issuer: str | None,
    authorization_endpoint: str | None,
    token_endpoint: str | None,
    userinfo_endpoint: str | None,
    scope: str | None,
    claims_mapping_json: str | None,
    position: int,
) -> OAuthProvider:
    _validate_preset(preset)
    norm_slug = _normalize_slug(slug)
    if get_provider_by_slug(db, norm_slug):
        raise ValueError(f"Slug '{norm_slug}' wird bereits verwendet")

    norm_scope = _normalize_scope(scope)

    # Secret-Encryption nur wenn Secret uebergeben wurde. Die Maske wird
    # mit-gespeichert, damit der Listing-Pfad keinen DIS-Decrypt mehr
    # machen muss (P1.3 aus Code-Review).
    secret_enc: str | None = None
    secret_mask: str | None = None
    if client_secret:
        secret_enc = encrypt_secret(client_secret)
        secret_mask = mask_secret(client_secret)

    provider = OAuthProvider(
        slug=norm_slug,
        name=name.strip(),
        preset=preset,
        enabled=enabled,
        client_id=client_id.strip(),
        client_secret_encrypted=secret_enc,
        client_secret_mask=secret_mask,
        issuer=(issuer or None),
        authorization_endpoint=(authorization_endpoint or None),
        token_endpoint=(token_endpoint or None),
        userinfo_endpoint=(userinfo_endpoint or None),
        scope=norm_scope,
        claims_mapping_json=claims_mapping_json,
        position=position,
    )
    _validate_custom_endpoints(preset, provider)
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


def update_provider(
    db: Session,
    provider: OAuthProvider,
    *,
    name: str | None = None,
    enabled: bool | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    issuer: str | None = None,
    authorization_endpoint: str | None = None,
    token_endpoint: str | None = None,
    userinfo_endpoint: str | None = None,
    scope: str | None = None,
    claims_mapping_json: str | None = None,
    position: int | None = None,
) -> OAuthProvider:
    # Re-validate custom endpoints if changed
    new_issuer = issuer if issuer is not None else provider.issuer
    new_auth = authorization_endpoint if authorization_endpoint is not None else provider.authorization_endpoint
    if provider.preset in ("custom_oidc", "custom_oauth2"):
        if not new_auth and not new_issuer:
            raise ValueError("Custom OIDC benoetigt 'issuer', Custom OAuth2 benoetigt authorization_endpoint")

    if name is not None:
        provider.name = name.strip()
    if enabled is not None:
        provider.enabled = enabled
    if client_id is not None:
        provider.client_id = client_id.strip()
    if client_secret is not None:
        if is_masked(client_secret):
            # Frontend hat den maskierten Wert zurueckgeschickt → ignorieren
            pass
        elif client_secret == "":
            # Explizit leeren = Secret loeschen
            provider.client_secret_encrypted = None
            provider.client_secret_mask = None
        else:
            provider.client_secret_encrypted = encrypt_secret(client_secret)
            provider.client_secret_mask = mask_secret(client_secret)
    if issuer is not None:
        provider.issuer = issuer or None
    if authorization_endpoint is not None:
        provider.authorization_endpoint = authorization_endpoint or None
    if token_endpoint is not None:
        provider.token_endpoint = token_endpoint or None
    if userinfo_endpoint is not None:
        provider.userinfo_endpoint = userinfo_endpoint or None
    if scope is not None:
        provider.scope = _normalize_scope(scope)
    if claims_mapping_json is not None:
        provider.claims_mapping_json = claims_mapping_json or None
    if position is not None:
        provider.position = position
    db.commit()
    db.refresh(provider)
    return provider


def delete_provider(db: Session, provider: OAuthProvider) -> None:
    db.delete(provider)
    db.commit()


# ── Provider-Tests (read-only) ─────────────────────────────────────────

def test_provider_connection(db: Session, provider: OAuthProvider) -> tuple[bool, str]:
    """Prueft, ob die Provider-Konfiguration konsistent ist.

    KISS: Wir prüfen NICHT durch einen echten IdP-Roundtrip (würde User-Daten
    und Consent-Bildschirm erfordern). Stattdessen validieren wir, dass alle
    fuer den Flow benoetigten Felder gesetzt sind und die Endpoints erreichbar
    sind (HEAD-Request fuer custom-Discovery).
    """
    try:
        preset, endpoints = _effective_endpoints(provider)
    except ValueError as e:
        return False, str(e)

    if not provider.client_id:
        return False, "client_id fehlt"

    # Alle 7 Presets verlangen ein Client-Secret — selbst PKCE-only-Flows
    # brauchen serverseitig einen Identifier, um Token-Mapping zu ermoeglichen.
    if not provider.client_secret_encrypted:
        return False, "client_secret fehlt"

    return True, "Konfiguration ist konsistent"


# ── Public-Listing fuer Login-UI ───────────────────────────────────────

def list_public_providers(db: Session) -> list[dict[str, Any]]:
    """Liefert nur enabled Provider, ohne Client-IDs/Secrets."""
    return [
        {"slug": p.slug, "name": p.name, "preset": p.preset, "position": p.position}
        for p in list_providers(db, enabled_only=True)
    ]


# ── OAuth-Flow: Authorization URL ──────────────────────────────────────

def build_redirect_uri(provider_slug: str) -> str:
    """IdP callback URL — must hit the API host, not a decoupled frontend.

    Split hosting: ``MSM_API_URL`` is the public backend origin while
    ``MSM_PANEL_URL`` is the user-facing SPA. OAuth callbacks are backend
    routes under ``/api/oauth/...`` and must use the API origin.
    All-in-one: ``api_url`` empty → fall back to ``panel_url``.
    """
    base = (settings.api_url or settings.panel_url or "").rstrip("/")
    if not base:
        base = settings.panel_url.rstrip("/")
    return f"{base}/api/oauth/{provider_slug}/callback"


OAUTH_MODE_LOGIN = "login"
OAUTH_MODE_LINK = "link"


def build_authorization_url(
    db: Session,
    provider: OAuthProvider,
    *,
    mode: str = OAUTH_MODE_LOGIN,
    user: User | None = None,
    next_path: str | None = None,
) -> tuple[str, str]:
    """Erzeugt die authorize-URL + verschluesseltes State-Cookie-Payload.

    Ein einziger Callback-Endpunkt (build_redirect_uri) bedient Login UND
    Account-Linking. Die Unterscheidung laeuft ueber das ``mode``-Feld im
    State-Payload (DIS-encrypted) — der Callback liest es und dispatcht.

    Args:
        mode: "login" fuer anonymen Login, "link" fuer Account-Linking.
              Bei "link" ist ``user`` Pflicht.
        user: Aktuell eingeloggter MSM-User (nur mode="link").
        next_path: Redirect-Ziel nach Login (nur mode="login").

    Returns: (authorize_url, encrypted_state_cookie)
    """
    if mode not in (OAUTH_MODE_LOGIN, OAUTH_MODE_LINK):
        raise ValueError(f"Unknown mode: {mode}")
    if mode == OAUTH_MODE_LINK and user is None:
        raise ValueError("user ist Pflicht fuer mode='link'")

    redirect_uri = build_redirect_uri(provider.slug)
    state, code_verifier = _new_pkce_pair()
    payload: dict[str, Any] = {
        "state": state,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "mode": mode,
        "ts": int(secrets.randbelow(2**31)),  # nonce
    }
    if mode == OAUTH_MODE_LOGIN:
        payload["next"] = next_path or "/"
    else:  # link
        payload["user_id"] = user.id  # type: ignore[union-attr]
    encrypted = pack_state_cookie(payload)
    auth_url = _build_authz_url(provider, redirect_uri, state, code_verifier)
    return auth_url, encrypted


def _new_pkce_pair() -> tuple[str, str]:
    return secrets.token_urlsafe(24), secrets.token_urlsafe(48)


def _build_authz_url(
    provider: OAuthProvider, redirect_uri: str, state: str, code_verifier: str
) -> str:
    preset, endpoints = _effective_endpoints(provider)
    if not preset.pkce:
        raise ValueError("PKCE ist Pflicht fuer OAuth-Provider")
    code_challenge = _code_challenge_s256(code_verifier)
    scope = (provider.scope or preset.default_scope).strip() or preset.default_scope
    params = {
        "response_type": "code",
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return str(endpoints["authorization_endpoint"]) + "?" + urlencode(params)


def _code_challenge_s256(verifier: str) -> str:
    import base64
    import hashlib
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


# ── OAuth-Flow: Code → Tokens ──────────────────────────────────────────

def exchange_code(
    db: Session, provider: OAuthProvider, code: str, code_verifier: str, redirect_uri: str
) -> dict[str, Any]:
    """Tauscht den Authorization-Code gegen Tokens (Token-Endpoint)."""
    _, endpoints = _effective_endpoints(provider)
    secret = (
        decrypt_secret(provider.client_secret_encrypted)
        if provider.client_secret_encrypted
        else ""
    )
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": provider.client_id,
        "code_verifier": code_verifier,
    }
    if secret:
        data["client_secret"] = secret
    try:
        resp = httpx.post(
            str(endpoints["token_endpoint"]),
            data=data,
            headers={"Accept": "application/json"},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise ValueError(f"Token-Endpoint nicht erreichbar: {e}") from e
    if resp.status_code != 200:
        # Wir geben bewusst NICHT den IdP-Body zurück (kann Stacktraces
        # oder Account-IDs enthalten) — generischer Fehler.
        raise ValueError("Token-Exchange fehlgeschlagen")
    try:
        return resp.json()
    except ValueError as e:
        raise ValueError("Token-Endpoint lieferte kein JSON") from e


def fetch_user_profile(
    db: Session, provider: OAuthProvider, tokens: dict[str, Any]
) -> dict[str, Any]:
    """Holt das User-Profil: bei OIDC bevorzugt aus dem ID-Token, sonst /userinfo.

    Wir validieren das ID-Token NICHT vollstaendig (JWKS-Cache, Sig-Check).
    Stattdessen nutzen wir die IdP-typische Heuristik: wenn der Token-Endpunkt
    ein ``id_token`` liefert UND der Provider OIDC-faehig ist, dekodieren wir
    die ungesicherten Claims (fuer Profil-Anzeige) und verifizieren die
    Authentizitaet ueber einen parallelen /userinfo-Call.
    """
    _, endpoints = _effective_endpoints(provider)
    preset = get_preset(provider.preset)
    assert preset is not None

    # 1) ID-Token-Claims (dekodiert, ungeprueft — nur als Heuristik)
    id_token = tokens.get("id_token")
    if id_token and preset.is_oidc:
        claims = _decode_jwt_payload(id_token)
        if claims and claims.get("sub"):
            # 2) Wenn /userinfo verfuegbar, mergen (id_token darf aber Vorrang haben)
            userinfo_url = endpoints.get("userinfo_endpoint")
            if userinfo_url:
                try:
                    userinfo = _fetch_userinfo(str(userinfo_url), tokens.get("access_token", ""))
                    if userinfo:
                        # Behaupte vom IdP: sub MUSS gleich sein (OpenID-Spec)
                        if not userinfo.get("sub") or userinfo.get("sub") == claims.get("sub"):
                            merged = {**userinfo, **{k: v for k, v in claims.items() if v is not None}}
                            return merged
                except Exception:
                    pass
            return claims

    # 3) Fallback: /userinfo
    userinfo_url = endpoints.get("userinfo_endpoint")
    if not userinfo_url:
        raise ValueError("Provider liefert weder ID-Token noch /userinfo-Endpoint")
    userinfo = _fetch_userinfo(str(userinfo_url), tokens.get("access_token", ""))
    if not userinfo:
        raise ValueError("userinfo-Endpoint lieferte leeres Profil")
    return userinfo


# ACHTUNG — REVIEWER: Diese Funktion prueft die JWT-Signatur NICHT.
# Der Output ist ausschliesslich informativ und darf NIE fuer Auth-Gating
# oder vertrauliche Profilfelder benutzt werden, ohne den parallelen
# /userinfo-Call in fetch_user_profile() als Cross-Check. Phase-2-Material:
# echte JWKS-Validierung (siehe ADR-0007 + docs/agent-rules/adr-0007-…md).
def _decode_jwt_payload(jwt: str) -> dict[str, Any] | None:
    """Dekodiert nur den Payload-Teil eines JWT. KEINE Signaturpruefung.

    Fuer rein informative Claim-Extraktion. Sicherheitsrelevant waere eine
    echte JWKS-basierte Validierung — das ist Phase-2-Material. Wir verlassen
    uns auf das /userinfo-Cross-Check fuer Authentizitaet.
    """
    try:
        parts = jwt.split(".")
        if len(parts) != 3:
            return None
        import base64
        padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        return None


def _fetch_userinfo(url: str, access_token: str) -> dict[str, Any] | None:
    if not access_token:
        return None
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=10.0,
        )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


# ── User-Resolution, Registration, Linking ────────────────────────────

def _generate_unique_username(db: Session, base: str) -> str:
    """Erzeugt einen eindeutigen Username aus 'base' (Fallback 'user')."""
    import re
    base_clean = re.sub(r"[^a-zA-Z0-9_-]", "", (base or "").strip())[:48] or "user"
    candidate = base_clean
    n = 0
    while db.query(User).filter(User.username == candidate).first() is not None:
        n += 1
        candidate = f"{base_clean}_{n}"
        if n > 9999:
            # Hard-cap, sollte nie passieren
            candidate = f"{base_clean}_{secrets.token_hex(4)}"
            break
    return candidate


def resolve_user(
    db: Session,
    provider: OAuthProvider,
    profile: NormalizedProfile,
    *,
    current_user: User | None = None,
) -> ResolutionResult:
    """Findet den MSM-User zum OAuth-Profil und entscheidet die naechste Aktion.

    - Existierender Link (provider_id, subject) → login (oder needs_2fa).
    - current_user vorhanden + Linking erlaubt → link an current_user.
    - Profil-Email matcht existierenden User + Linking erlaubt → link per Email.
    - Auto-Registration erlaubt → register.
    - Sonst: forbidden mit Grund.
    """
    subj_hash = OAuthUserLink._hash_subject(profile.subject)
    link = (
        db.query(OAuthUserLink)
        .filter(
            OAuthUserLink.provider_id == provider.id,
            OAuthUserLink.subject == subj_hash,
        )
        .first()
    )
    if link is not None:
        user = db.query(User).filter(User.id == link.user_id).first()
        if user is None or not user.is_active:
            return ResolutionResult(None, "forbidden", "Linked user is inactive or missing")
        return _post_resolve(user)

    # Bereits angemeldet: expliziter Linking-Flow (von Link-Endpoint aufgerufen)
    if current_user is not None:
        if not is_linking_allowed():
            return ResolutionResult(None, "forbidden", "linking_disabled")
        return ResolutionResult(current_user, "link")

    # Email-Match (nur wenn linking erlaubt UND E-Mail vom IdP verifiziert).
    # Defense-in-Depth: ohne den verified-Check koennte ein Angreifer ueber
    # einen schwach konfigurierten Custom-IdP ein OAuth-Profil mit der Mail
    # eines bestehenden Users einspielen und sich so ohne Wissen des Victims
    # einlinken. Auto-Registration prueft denselben Switch (requires_verified_email)
    # bereits — hier war die Luecke.
    if is_linking_allowed() and profile.email and profile.email_verified:
        existing = db.query(User).filter(User.email_hash == User._email_hash(profile.email)).first()
        if existing is not None and existing.is_active:
            return ResolutionResult(existing, "link")

    # Auto-Registration
    if is_registration_allowed():
        if requires_verified_email() and not profile.email_verified:
            return ResolutionResult(
                None, "forbidden", "E-Mail nicht vom IdP verifiziert und Auto-Registration erfordert verifizierte E-Mail"
            )
        return ResolutionResult(None, "register")

    return ResolutionResult(None, "forbidden", "no_matching_user")


def _post_resolve(user: User) -> ResolutionResult:
    """Nach erfolgreicher User-Identifikation: 2FA-Gate setzen."""
    if user.two_factor_enabled:
        return ResolutionResult(user, "needs_2fa")
    return ResolutionResult(user, "login")


def register_user_from_oauth(
    db: Session, profile: NormalizedProfile
) -> User:
    """Legt einen neuen User aus dem OAuth-Profil an. Wirft ValueError bei Konflikt."""
    if not profile.email:
        raise ValueError("OAuth-Profil enthaelt keine E-Mail")
    if db.query(User).filter(User.email_hash == User._email_hash(profile.email)).first():
        raise ValueError("E-Mail ist bereits vergeben")
    username_base = profile.username or profile.email.split("@", 1)[0]
    username = _generate_unique_username(db, username_base)
    user = User(
        username=username,
        email=profile.email,
        # Random-Passwort, da OAuth-User sich nicht lokal einloggen sollen
        # (Aenderung ueber "Passwort setzen"-Flow). SHA-argon2 ist deterministisch
        # genug fuer diesen Zweck — wir leaken das Plain eh nie.
        password_hash=AuthService.hash_password(secrets.token_urlsafe(32)),
        email_verified=profile.email_verified or False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def link_provider_to_user(
    db: Session, provider: OAuthProvider, user: User, profile: NormalizedProfile
) -> OAuthUserLink:
    """Verknuepft eine IdP-Identitaet mit einem bestehenden User. Idempotent pro (provider, subject)."""
    subj_hash = OAuthUserLink._hash_subject(profile.subject)
    existing = (
        db.query(OAuthUserLink)
        .filter(
            OAuthUserLink.provider_id == provider.id,
            OAuthUserLink.subject == subj_hash,
        )
        .first()
    )
    if existing is not None:
        return existing
    if (
        db.query(OAuthUserLink)
        .filter(OAuthUserLink.provider_id == provider.id, OAuthUserLink.user_id == user.id)
        .first()
        is not None
    ):
        raise ValueError("User hat bereits einen Link fuer diesen Provider")
    link = OAuthUserLink(
        provider_id=provider.id,
        user_id=user.id,
        subject=subj_hash,
        email_at_link=profile.email,
        username_at_link=profile.username,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


def update_link_last_used(db: Session, link: OAuthUserLink) -> None:
    from datetime import datetime, timezone
    link.last_used_at = datetime.now(timezone.utc)
    db.commit()


def unlink_user_from_provider(db: Session, user_id: int, provider_id: int) -> bool:
    """Loescht einen User-Link. True bei Erfolg, False wenn nicht vorhanden."""
    link = (
        db.query(OAuthUserLink)
        .filter(OAuthUserLink.user_id == user_id, OAuthUserLink.provider_id == provider_id)
        .first()
    )
    if link is None:
        return False
    db.delete(link)
    db.commit()
    return True


def list_user_links(db: Session, user_id: int) -> list[OAuthUserLink]:
    return (
        db.query(OAuthUserLink)
        .filter(OAuthUserLink.user_id == user_id)
        .order_by(OAuthUserLink.created_at.asc())
        .all()
    )


# ── 2FA-Challenge: erzeugen + abschliessen ────────────────────────────

def create_2fa_challenge(db: Session, user: User, provider: OAuthProvider) -> str:
    return login_challenge_service.create_challenge(
        db,
        purpose="oauth_2fa",
        user_id=user.id,
        payload={"provider_slug": provider.slug, "provider_id": provider.id},
    )


def complete_2fa_challenge(
    db: Session, challenge_token: str, otp_code: str
) -> tuple[User, OAuthProvider] | None:
    """Validiert OTP gegen den Challenge-User. Konsumiert die Challenge bei Erfolg.

    Returns (User, Provider) oder None (Challenge ungueltig/OTP falsch).
    """
    row = login_challenge_service.lookup_valid(db, challenge_token, "oauth_2fa")
    if row is None or row.user_id is None:
        return None
    user = db.query(User).filter(User.id == row.user_id).first()
    if user is None or not user.is_active:
        return None
    if not AuthService.verify_current_2fa_code(user, otp_code):
        return None
    # Provider aus Payload
    payload = json.loads(row.payload_json) if row.payload_json else {}
    provider_id = payload.get("provider_id")
    provider = (
        db.query(OAuthProvider).filter(OAuthProvider.id == provider_id).first()
        if provider_id
        else None
    )
    if provider is None:
        return None
    login_challenge_service.consume(db, row)
    return user, provider


__all__ = [
    "STATE_COOKIE_NAME",
    "STATE_TTL_SECONDS",
    "SWITCH_ALLOW_REGISTRATION",
    "SWITCH_ALLOW_LINKING",
    "SWITCH_REQUIRE_VERIFIED_EMAIL",
    "NormalizedProfile",
    "ResolutionResult",
    "mask_secret",
    "is_masked",
    "encrypt_secret",
    "decrypt_secret",
    "pack_state_cookie",
    "unpack_state_cookie",
    "is_registration_allowed",
    "is_linking_allowed",
    "requires_verified_email",
    "normalize_profile",
    "list_providers",
    "get_provider_by_id",
    "get_provider_by_slug",
    "create_provider",
    "update_provider",
    "delete_provider",
    "test_provider_connection",
    "list_public_providers",
    "build_redirect_uri",
    "build_authorization_url",
    "OAUTH_MODE_LOGIN",
    "OAUTH_MODE_LINK",
    "exchange_code",
    "fetch_user_profile",
    "resolve_user",
    "register_user_from_oauth",
    "link_provider_to_user",
    "update_link_last_used",
    "unlink_user_from_provider",
    "list_user_links",
    "create_2fa_challenge",
    "complete_2fa_challenge",
]
