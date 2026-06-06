from fastapi import Response

from config import get_effective_cookie_domain, settings


_COOKIE_CONFIG = {
    # Access-Token MUSS SameSite=Lax sein, nicht Strict: der OAuth-Callback
    # kommt als Cross-Site-Top-Level-Navigation von Google/Discord/etc. zurueck.
    # Bei Strict wuerde der Browser das Cookie auf diesem Redirect NICHT mitsenden
    # → 401 / "Nicht authentifiziert" direkt nach dem Login-/Link-Start.
    # Lax schuetzt weiterhin: keine Subresource-Requests (AJAX/fetch von fremden
    # Origins), keine Cross-Site-POSTs (die State-Mutationen ausloesen wuerden).
    # Alle State-mutierenden Endpoints in MSM sind POST/PATCH/DELETE + CSRF-geschuetzt.
    "__Secure-access_token": {
        "httponly": True,
        "secure": True,
        "samesite": "lax",
        "path": "/api",
    },
    # Refresh-Token bleibt strict: nur same-origin POST /api/auth/refresh
    # braucht es, kein Cross-Site-Pfad beteiligt.
    "__Secure-refresh_token": {
        "httponly": True,
        "secure": True,
        "samesite": "strict",
        "path": "/api/auth",
    },
    # CSRF-Token bleibt strict: das ist genau der Sinn — Cross-Site-Requests
    # duerfen das Token NICHT lesen/duplizieren.
    "__Secure-csrf_token": {
        "httponly": False,  # JS muss lesen koennen fuer Double-Submit
        "secure": True,
        "samesite": "strict",
        "path": "/",
    },
}

# Pfade, an denen frühere Releases das CSRF-Cookie gesetzt haben. Wenn ein
# Browser von einer früheren Version noch ein Cookie unter einem dieser Pfade
# hat, wird es bei jedem Request auf /api/* mitgeschickt und kann das aktuelle
# Cookie unter Path=/ verdrängen. Daher beim Setzen/Löschen explizit räumen.
_LEGACY_CSRF_PATHS = ("/api",)


def _set_cookie(response: Response, key: str, value: str, max_age: int | None = None) -> None:
    cfg = _COOKIE_CONFIG[key]
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
    cfg = _COOKIE_CONFIG["__Secure-csrf_token"]
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
        cfg = _COOKIE_CONFIG[key]
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
