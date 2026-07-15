from fastapi import Response

from config import get_effective_cookie_domain, settings


# Default SameSite pro Cookie im Single-Host-Modus (cookie_cross_site=False).
# Access=Lax: OAuth top-level redirect vom IdP muss das Cookie mitsenden.
# Refresh/CSRF=Strict: kein Cross-Site-Subresource-Zugriff noetig bei same-origin.
_COOKIE_DEFAULTS: dict[str, dict] = {
    "__Secure-access_token": {
        "httponly": True,
        "secure": True,
        "samesite": "lax",
        "path": "/api",
    },
    "__Secure-refresh_token": {
        "httponly": True,
        "secure": True,
        "samesite": "strict",
        "path": "/api/auth",
    },
    "__Secure-csrf_token": {
        "httponly": False,  # JS muss lesen koennen fuer Double-Submit (same-origin)
        "secure": True,
        "samesite": "strict",
        "path": "/",
    },
}


def _samesite_for(key: str) -> str:
    """SameSite fuer Cross-Domain (Vercel) vs. Single-Host.

    Cross-Site: alle Auth-Cookies brauchen SameSite=None + Secure, sonst
    sendet der Browser sie bei fetch(credentials) von einer anderen Origin nicht.
    OAuth Top-Level-Nav funktioniert mit None ebenfalls.
    """
    if settings.cookie_cross_site:
        return "none"
    return _COOKIE_DEFAULTS[key]["samesite"]


def _cookie_cfg(key: str) -> dict:
    base = _COOKIE_DEFAULTS[key]
    return {
        "httponly": base["httponly"],
        "secure": base["secure"],  # immer True (__Secure- Prefix + SameSite=None)
        "samesite": _samesite_for(key),
        "path": base["path"],
    }


# Rueckwaertskompatibel fuer Tests, die _COOKIE_CONFIG lesen.
# Werte spiegeln den Default-Modus (cookie_cross_site=False); dynamische
# SameSite-Entscheidung laeuft ueber _cookie_cfg / _samesite_for.
_COOKIE_CONFIG = {
    k: {**v, "samesite": v["samesite"]} for k, v in _COOKIE_DEFAULTS.items()
}


# Pfade, an denen frühere Releases das CSRF-Cookie gesetzt haben. Wenn ein
# Browser von einer früheren Version noch ein Cookie unter einem dieser Pfade
# hat, wird es bei jedem Request auf /api/* mitgeschickt und kann das aktuelle
# Cookie unter Path=/ verdrängen. Daher beim Setzen/Löschen explizit räumen.
_LEGACY_CSRF_PATHS = ("/api",)


def _set_cookie(response: Response, key: str, value: str, max_age: int | None = None) -> None:
    cfg = _cookie_cfg(key)
    # Konsistente Domain mit dem OAuth-State-Cookie: wenn get_effective_cookie_domain()
    # einen Parent-Domain-Wert liefert (z. B. ".mauntingstudios.de"), setzen wir
    # die Auth-Cookies ebenfalls mit Domain=. Parent-Domain. Sonst sind die
    # __Secure-access_token / __Secure-refresh_token / __Secure-csrf_token
    # host-only (z. B. nur "panel.mauntingstudios.de"). Browser handhaben das
    # unterschiedlich streng — gleiche Attribute für alle drei Cookies
    # verhindern subtile Mismatch-Bugs beim IdP-Callback-Roundtrip.
    cookie_domain = get_effective_cookie_domain() or None
    response.set_cookie(
        key=key,
        value=value,
        httponly=cfg["httponly"],
        secure=cfg["secure"],
        samesite=cfg["samesite"],
        path=cfg["path"],
        max_age=max_age,
        domain=cookie_domain,
    )


def _clear_legacy_csrf_cookies(response: Response) -> None:
    """Löscht CSRF-Cookies, die unter alten Pfaden im Browser liegen können.

    Hintergrund: in einem früheren Release lag das CSRF-Cookie unter Path=/api.
    Browser, die damals eingeloggt waren, haben es noch — und schicken es bei
    jedem Request auf /api/* zusammen mit dem aktuellen Cookie unter Path=/.
    Wenn der Server beim Parsen den falschen Wert sieht, schlägt die
    Double-Submit-Prüfung mit "CSRF-Token ungueltig" fehl.

    Domain MUSS mit dem Set-Cookie uebereinstimmen, sonst verwirft der Browser
    das Delete und das Legacy-Cookie bleibt als Geister-Cookie zurueck.
    """
    cfg = _cookie_cfg("__Secure-csrf_token")
    cookie_domain = get_effective_cookie_domain() or None
    for legacy_path in _LEGACY_CSRF_PATHS:
        response.delete_cookie(
            key="__Secure-csrf_token",
            path=legacy_path,
            secure=cfg["secure"],
            samesite=cfg["samesite"],
            domain=cookie_domain,
        )


def _clear_auth_cookies(response: Response) -> None:
    # Wichtig: beim Löschen MUSS die Domain mit dem Set-Cookie übereinstimmen,
    # sonst verwirft der Browser das Delete. Wir leiten die Domain aus
    # get_effective_cookie_domain() ab, damit Logout/Refresh-401-Rotation
    # zuverlässig funktioniert.
    cookie_domain = get_effective_cookie_domain() or None
    for key in ("__Secure-access_token", "__Secure-refresh_token", "__Secure-csrf_token"):
        cfg = _cookie_cfg(key)
        response.delete_cookie(
            key=key,
            path=cfg["path"],
            secure=cfg["secure"],
            samesite=cfg["samesite"],
            domain=cookie_domain,
        )
    _clear_legacy_csrf_cookies(response)


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str, csrf_token: str) -> None:
    # Legacy-CSRF-Cookies zuerst räumen, damit der Browser nach Login/Refresh
    # nur noch das aktuelle Cookie unter Path=/ hat.
    _clear_legacy_csrf_cookies(response)
    _set_cookie(response, "__Secure-access_token", access_token, max_age=settings.access_token_expire_minutes * 60)
    _set_cookie(response, "__Secure-refresh_token", refresh_token, max_age=settings.refresh_token_expire_days * 24 * 60 * 60)
    _set_cookie(response, "__Secure-csrf_token", csrf_token, max_age=settings.csrf_token_expire_minutes * 60)
    # Cross-Origin SPA kann document.cookie der API-Domain nicht lesen.
    # Header + CORS expose_headers erlauben Double-Submit ohne Cookie-Lesezugriff.
    response.headers["X-CSRF-Token"] = csrf_token
