from collections import defaultdict
import time

from fastapi import Request
from fastapi.responses import JSONResponse


# ── In-Memory Rate Limiting Store ──
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 60  # Sekunden
_RATE_MAX = 100    # Requests pro Window (global pro IP)
_AUTH_RATE_MAX = 10  # Auth-Endpunkte: 10 pro Minute


def _get_client_ip(request: Request) -> str:
    """Ermittelt Client-IP, beruecksichtigt X-Forwarded-For bei Reverse-Proxies."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # Erster Eintrag in X-Forwarded-For ist die urspruengliche Client-IP
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _cleanup_store() -> None:
    """Entfernt veraltete Eintraege aus dem Store (housekeeping)."""
    now = time.time()
    expired = [ip for ip, timestamps in _rate_limit_store.items() if all(now - t >= _RATE_WINDOW for t in timestamps)]
    for ip in expired:
        del _rate_limit_store[ip]


async def rate_limit_middleware(request: Request, call_next):
    """FastAPI Middleware fuer In-Memory Rate-Limiting mit Proxy-Support."""
    client_ip = _get_client_ip(request)
    now = time.time()
    is_auth = request.url.path.startswith("/api/auth")
    max_req = _AUTH_RATE_MAX if is_auth else _RATE_MAX

    # Alte Eintraege entfernen
    _rate_limit_store[client_ip] = [
        t for t in _rate_limit_store[client_ip] if now - t < _RATE_WINDOW
    ]

    if len(_rate_limit_store[client_ip]) >= max_req:
        return JSONResponse(
            status_code=429,
            content={"detail": "Zu viele Anfragen. Bitte warten Sie einen Moment."},
            headers={"Retry-After": str(_RATE_WINDOW)},
        )

    _rate_limit_store[client_ip].append(now)
    return await call_next(request)
