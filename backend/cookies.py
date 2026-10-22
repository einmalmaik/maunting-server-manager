from urllib.parse import urlsplit

from fastapi import Response

from config import get_effective_cookie_domain, settings


# ── Entwicklungsbetrieb ueber einfaches HTTP auf Loopback ────────────────
#
# Ein `__Secure-`-Cookie nimmt ein Browser nur ueber HTTPS an. Ueber
# `http://localhost` verwirft Chromium es samt `Secure`-Flag — auch das
# CSRF-Cookie, obwohl es lesbar waere. Der Login antwortete deshalb 200, und
# `/api/auth/me` danach 401, weil gar kein Cookie ankam: die lokale
# Entwicklung (Vite auf :3000, Backend auf :8000, beides http) konnte sich
# nicht anmelden.
#
# Die Ausnahme ist ausdruecklich einzuschalten (`MSM_DEV_HTTP_COOKIES=true`)
# und gilt nur zusammen mit `MSM_DEBUG` und einer Loopback-Adresse. Sie nimmt
# das Praefix aus dem Namen und laesst `Secure` weg; alles andere bleibt, wie
# es ist. In Produktion ist der Schalter wirkungslos.
_DEV_NAMEN = {
    "__Secure-access_token": "msm_dev_access_token",
    "__Secure-refresh_token": "msm_dev_refresh_token",
    "__Secure-csrf_token": "msm_dev_csrf_token",
}


def _ist_loopback(url: str) -> bool:
    try:
        host = (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return False
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")


def dev_http_cookies() -> bool:
    """Laeuft dieses Panel als lokale Entwicklung ueber einfaches HTTP?"""
    if not settings.dev_http_cookies or not settings.debug:
        return False
    return _ist_loopback(settings.panel_url) or _ist_loopback(settings.api_url)


def cookie_name(key: str) -> str:
    """Der Name, unter dem das Cookie im Browser liegen soll."""
    return _DEV_NAMEN[key] if dev_http_cookies() else key


def lies_zugangstoken(cookies) -> str | None:
    return cookies.get(cookie_name("__Secure-access_token"))


def lies_refresh_token(cookies) -> str | None:
    return cookies.get(cookie_name("__Secure-refresh_token"))


def csrf_cookienamen() -> tuple[str, ...]:
    """Alle Namen, unter denen das CSRF-Cookie liegen kann.

    Im Entwicklungsmodus wird auch der alte Name gelesen: ein Browser kann aus
    einer frueheren Sitzung noch eines unter `__Secure-csrf_token` haben.
    """
    if dev_http_cookies():
        return (_DEV_NAMEN["__Secure-csrf_token"], "__Secure-csrf_token")
    return ("__Secure-csrf_token",)


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
        # Immer True (__Secure- Prefix + SameSite=None) — ausser im lokalen
        # Entwicklungsbetrieb ueber http, wo der Browser ein Secure-Cookie
        # verwirft.
        "secure": base["secure"] and not dev_http_cookies(),
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
        key=cookie_name(key),
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
        for name in csrf_cookienamen():
            response.delete_cookie(
                key=name,
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
        # Beide Namen: der aktive und das Praefix-Cookie aus einer frueheren
        # Sitzung. Ein Delete auf ein nicht vorhandenes Cookie ist folgenlos.
        for name in {cookie_name(key), key}:
            response.delete_cookie(
                key=name,
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
