"""MSM Agent — lightweight FastAPI app for remote node operations.

Stateless: no local DB for game servers. Docker + filesystem only.
Auth: static Bearer token (MSM_AGENT_TOKEN) for all routes except GET /health.
No DIS/crypto — secrets stay on the panel.
"""

from __future__ import annotations

import logging
import secrets
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from config import settings
from routers import backup, console, containers, files, health, metrics, postgres, runtime, sources
from services import file_service

# ── Logging (never log Authorization / tokens / env secrets) ──
logging.basicConfig(
    level=getattr(logging, settings.agent_log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("msm-agent")


def _path_from_scope(scope: Scope) -> str:
    return scope.get("path") or ""


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1")
    return ""


def _unauthorized_http() -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    body = b'{"detail":"Unauthorized"}'
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    return 401, headers, body


def _service_unavailable_token() -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    body = b'{"detail":"Agent token not configured"}'
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    return 503, headers, body


def _is_authenticated(scope: Scope) -> tuple[bool, int]:
    """Return (ok, status_if_not_ok). status 503 if token unset, else 401."""
    token = (settings.agent_token or "").strip()
    if not token:
        return False, 503
    auth = _header(scope, b"authorization")
    scheme, _, value = auth.partition(" ")
    provided = value.strip() if scheme.lower() == "bearer" else ""
    if not provided or not secrets.compare_digest(provided, token):
        return False, 401
    return True, 0


class BearerAuthMiddleware:
    """Pure ASGI middleware so WebSocket upgrades stay intact.

    Skips auth only for GET /health (exact path).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        stype = scope["type"]
        if stype not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = _path_from_scope(scope)
        method = (scope.get("method") or "GET").upper()
        if stype == "http" and path == "/health" and method == "GET":
            await self.app(scope, receive, send)
            return

        ok, status = _is_authenticated(scope)
        if ok:
            await self.app(scope, receive, send)
            return

        if stype == "websocket":
            # Reject WS upgrade without accepting the socket
            await send({"type": "websocket.close", "code": 4401})
            return

        if status == 503:
            code, headers, body = _service_unavailable_token()
        else:
            code, headers, body = _unauthorized_http()
        await send({"type": "http.response.start", "status": code, "headers": headers})
        await send({"type": "http.response.body", "body": body})


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    from services.guardian_service import start_guardian_loop, stop_guardian_loop
    file_service.ensure_servers_dir()
    if not (settings.agent_token or "").strip():
        logger.warning(
            "MSM_AGENT_TOKEN is empty — authenticated endpoints will return 503"
        )
    logger.info(
        "MSM Agent v%s starting on %s:%s",
        settings.agent_version,
        settings.agent_host,
        settings.agent_port,
    )
    guardian_task = asyncio.create_task(start_guardian_loop())
    try:
        yield
    finally:
        await stop_guardian_loop()
        guardian_task.cancel()
        try:
            await guardian_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="MSM Agent",
    version=settings.agent_version,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

app.add_middleware(BearerAuthMiddleware)

app.include_router(health.router)
app.include_router(containers.router)
app.include_router(files.router)
app.include_router(metrics.router)
app.include_router(console.router)
app.include_router(backup.router)
app.include_router(postgres.router)
app.include_router(sources.router)
app.include_router(runtime.router)


def main() -> None:
    import uvicorn

    cert = (settings.tls_certfile or "").strip()
    key = (settings.tls_keyfile or "").strip()
    ssl_kwargs: dict = {}
    if cert and key:
        ssl_kwargs["ssl_certfile"] = cert
        ssl_kwargs["ssl_keyfile"] = key
        logger.info("TLS enabled (cert file configured; fingerprint pin on panel)")
    elif cert or key:
        logger.warning("TLS incomplete: set both MSM_TLS_CERTFILE and MSM_TLS_KEYFILE")

    uvicorn.run(
        "main:app",
        host=settings.agent_host,
        port=settings.agent_port,
        log_level=settings.agent_log_level.lower(),
        workers=1,
        **ssl_kwargs,
    )


if __name__ == "__main__":
    main()
    sys.exit(0)
