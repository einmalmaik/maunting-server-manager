from fastapi import Response

from config import settings


_COOKIE_CONFIG = {
    "__Secure-access_token": {
        "httponly": True,
        "secure": not settings.debug,
        "samesite": "strict",
        "path": "/api",
    },
    "__Secure-refresh_token": {
        "httponly": True,
        "secure": not settings.debug,
        "samesite": "strict",
        "path": "/api/auth",
    },
    "__Secure-csrf_token": {
        "httponly": False,  # JS muss lesen koennen fuer Double-Submit
        "secure": not settings.debug,
        "samesite": "strict",
        "path": "/",
    },
}


def _set_cookie(response: Response, key: str, value: str, max_age: int | None = None) -> None:
    cfg = _COOKIE_CONFIG[key]
    response.set_cookie(
        key=key,
        value=value,
        httponly=cfg["httponly"],
        secure=cfg["secure"],
        samesite=cfg["samesite"],
        path=cfg["path"],
        max_age=max_age,
    )


def _clear_auth_cookies(response: Response) -> None:
    for key in ("__Secure-access_token", "__Secure-refresh_token", "__Secure-csrf_token"):
        cfg = _COOKIE_CONFIG[key]
        response.delete_cookie(
            key=key,
            path=cfg["path"],
            secure=cfg["secure"],
            samesite=cfg["samesite"],
        )


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str, csrf_token: str) -> None:
    _set_cookie(response, "__Secure-access_token", access_token, max_age=settings.access_token_expire_minutes * 60)
    _set_cookie(response, "__Secure-refresh_token", refresh_token, max_age=settings.refresh_token_expire_days * 24 * 60 * 60)
    _set_cookie(response, "__Secure-csrf_token", csrf_token, max_age=settings.csrf_token_expire_minutes * 60)
