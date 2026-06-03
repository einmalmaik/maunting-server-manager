"""OAuth/OIDC-Provider-Presets (reine Konfigurations-Konstanten).

Endpunkte, Scopes und Claim-Mappings fuer die 7 festen Presets. Custom-Presets
nutzen die Overrides aus dem Provider-Datensatz.

Sicherheit: Hier stehen KEINE Secrets. Nur oeffentliche, dokumentierte Endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OAuthPreset:
    key: str
    label: str
    # Endpoints: None = "muss via Override gesetzt werden" (nur custom_*)
    authorization_endpoint: str | None
    token_endpoint: str | None
    userinfo_endpoint: str | None
    issuer: str | None  # nur fuer OIDC relevant
    default_scope: str
    # Mapping IdP-Claim -> MSM-Feld. Werte:
    #   "id"        — eindeutige User-ID (Pflicht)
    #   "email"     — E-Mail-Adresse
    #   "email_verified" — bool, ob die E-Mail vom IdP bestaetigt ist
    #   "username"  — bevorzugter Username
    #   "name"      — Vollname (Anzeige)
    #   "avatar"    — Avatar-URL
    claim_map: dict[str, str]
    # PKCE ist bei allen Presets aktiv (Sicherheits-Default)
    pkce: bool = True
    # Unterstuetzt der Provider OIDC (ID-Token) oder nur OAuth2 (userinfo)
    is_oidc: bool = True
    # Discovery-URL fuer OIDC (nur relevant bei is_oidc und fehlenden Endpoints)
    discovery_url: str | None = None
    # Default-Berechtigungen dieses Presets bei Updates
    extra_scopes: tuple[str, ...] = field(default_factory=tuple)


PRESETS: dict[str, OAuthPreset] = {
    "google": OAuthPreset(
        key="google",
        label="Google",
        authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
        token_endpoint="https://oauth2.googleapis.com/token",
        userinfo_endpoint="https://openidconnect.googleapis.com/v1/userinfo",
        issuer="https://accounts.google.com",
        default_scope="openid email profile",
        claim_map={
            "id": "sub",
            "email": "email",
            "email_verified": "email_verified",
            "username": "email",  # Fallback: E-Mail als Username-Basis
            "name": "name",
            "avatar": "picture",
        },
    ),
    "discord": OAuthPreset(
        key="discord",
        label="Discord",
        authorization_endpoint="https://discord.com/api/oauth2/authorize",
        token_endpoint="https://discord.com/api/oauth2/token",
        userinfo_endpoint="https://discord.com/api/users/@me",
        issuer=None,  # Discord ist OAuth2-only, kein OIDC
        default_scope="identify email",
        claim_map={
            "id": "id",
            "email": "email",
            "email_verified": "verified",  # Discord liefert nur "verified": bool
            "username": "username",
            "name": "global_name",
            "avatar": "avatar",
        },
        is_oidc=False,
    ),
    "github": OAuthPreset(
        key="github",
        label="GitHub",
        authorization_endpoint="https://github.com/login/oauth/authorize",
        token_endpoint="https://github.com/login/oauth/access_token",
        userinfo_endpoint="https://api.github.com/user",
        issuer=None,
        default_scope="read:user user:email",
        claim_map={
            "id": "id",
            "email": "email",  # kann null sein, dann ueber /user/emails fallback
            "email_verified": "_msm_default_true",  # GitHub verifiziert selbst
            "username": "login",
            "name": "name",
            "avatar": "avatar_url",
        },
        is_oidc=False,
    ),
    "microsoft": OAuthPreset(
        key="microsoft",
        label="Microsoft",
        authorization_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        userinfo_endpoint="https://graph.microsoft.com/oidc/userinfo",
        issuer="https://login.microsoftonline.com/common/v2.0",
        default_scope="openid email profile",
        claim_map={
            "id": "sub",
            "email": "email",
            "email_verified": "email_verified",
            "username": "preferred_username",
            "name": "name",
        },
    ),
    "twitter": OAuthPreset(
        key="twitter",
        label="X (Twitter)",
        authorization_endpoint="https://twitter.com/i/oauth2/authorize",
        token_endpoint="https://api.twitter.com/2/oauth2/token",
        userinfo_endpoint="https://api.twitter.com/2/users/me",
        issuer=None,
        default_scope="tweet.read users.read",
        claim_map={
            "id": "data.id",
            "email": "data.email",  # nur vorhanden, wenn User die Permission erteilt
            "email_verified": "_msm_default_true",
            "username": "data.username",
            "name": "data.name",
            "avatar": "data.profile_image_url",
        },
        is_oidc=False,
    ),
    "custom_oidc": OAuthPreset(
        key="custom_oidc",
        label="Custom OIDC (Keycloak, Authentik, Zitadel, ...)",
        authorization_endpoint=None,
        token_endpoint=None,
        userinfo_endpoint=None,
        issuer=None,
        default_scope="openid email profile",
        claim_map={
            "id": "sub",
            "email": "email",
            "email_verified": "email_verified",
            "username": "preferred_username",
            "name": "name",
        },
        # Bei Custom OIDC wird der Discovery-Endpoint abgefragt
        discovery_url=None,  # = "{issuer}/.well-known/openid-configuration"
    ),
    "custom_oauth2": OAuthPreset(
        key="custom_oauth2",
        label="Custom OAuth2",
        authorization_endpoint=None,
        token_endpoint=None,
        userinfo_endpoint=None,
        issuer=None,
        default_scope="",
        claim_map={
            "id": "sub",  # Fallback; meistens nicht passend — Admin muss Mapping pflegen
            "email": "email",
            "username": "username",
            "name": "name",
        },
        is_oidc=False,
    ),
}


def get_preset(key: str) -> OAuthPreset | None:
    return PRESETS.get(key)


def known_preset(key: str) -> bool:
    return key in PRESETS


def list_presets() -> list[OAuthPreset]:
    return list(PRESETS.values())
