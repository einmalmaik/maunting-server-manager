from contextlib import asynccontextmanager
import os

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from limits import parse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from config import settings
from database import engine, Base
from routers import (
    auth_router,
    admin_router,
    servers_router,
    backups_router,
    mods_router,
    config_editor_router,
    system_router,
    steam_router,
    panel_settings_router,
    files_router,
)
from middleware.rate_limit import limiter
from services.steam_service import close_steam_service
from services.scheduler_service import start_scheduler, stop_scheduler, init_server_schedules


# ── Auth-Endpunkte: 10/minute (strenger als global) ──
_auth_limit_item = parse("10/minute")


def auth_rate_limit(request: Request) -> None:
    key = get_remote_address(request)
    if not limiter.limiter.hit(_auth_limit_item, key):
        raise HTTPException(
            status_code=429,
            detail="Zu viele Anfragen. Bitte warten Sie einen Moment.",
            headers={"Retry-After": "60"},
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    os.makedirs(settings.servers_dir, exist_ok=True)
    os.makedirs("/opt/msm/backups", exist_ok=True)
    Base.metadata.create_all(bind=engine)

    # Migration: fehlende Spalten nachträglich hinzufügen
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    if 'users' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('users')]
        if 'email_notifications' not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN email_notifications BOOLEAN DEFAULT true"))

    # Migration: Backup-Scheduling-Spalten + Phase-1 Docker-Spalten
    if 'servers' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('servers')]
        with engine.begin() as conn:
            if 'backup_on_start' not in cols:
                conn.execute(text("ALTER TABLE servers ADD COLUMN backup_on_start BOOLEAN DEFAULT false"))
            if 'backup_interval_hours' not in cols:
                conn.execute(text("ALTER TABLE servers ADD COLUMN backup_interval_hours INTEGER"))
            if 'backup_retention_count' not in cols:
                conn.execute(text("ALTER TABLE servers ADD COLUMN backup_retention_count INTEGER DEFAULT 5"))
            # Phase 1 — Docker-Runtime: container_name + public_bind_ip + disk_usage_mb
            if 'container_name' not in cols:
                conn.execute(text("ALTER TABLE servers ADD COLUMN container_name VARCHAR(64)"))
            if 'public_bind_ip' not in cols:
                conn.execute(text("ALTER TABLE servers ADD COLUMN public_bind_ip VARCHAR(64)"))
            if 'disk_usage_mb' not in cols:
                conn.execute(text("ALTER TABLE servers ADD COLUMN disk_usage_mb INTEGER"))
            # Phase 1 — Legacy-Spalte linux_user entfernen (Server laufen jetzt
            # in Docker-Containern, kein POSIX-User-pro-Server mehr).
            if 'linux_user' in cols:
                conn.execute(text("ALTER TABLE servers DROP COLUMN linux_user"))

    # Migration: Mod enabled-Spalte
    if 'mods' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('mods')]
        if 'enabled' not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE mods ADD COLUMN enabled BOOLEAN DEFAULT true"))

    # Migration: Backup name-Spalte
    if 'backups' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('backups')]
        if 'name' not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE backups ADD COLUMN name VARCHAR(256)"))

    # Initialize scheduler and load existing schedules
    start_scheduler()
    from database import SessionLocal
    db = SessionLocal()
    try:
        init_server_schedules(db)
    finally:
        db.close()

    yield

    # Shutdown
    stop_scheduler()
    await close_steam_service()


app = FastAPI(
    title=settings.app_name,
    description="Maunting Server Manager — Universeller Game Server Manager",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS: Nicht mehr wildcard, sondern explizite Origins ──
_cors_origins = [settings.panel_url]
if settings.debug:
    _cors_origins.extend(["http://localhost:5173", "http://localhost", "http://127.0.0.1:5173"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
)


# ── Rate Limiting (slowapi) ──
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


# ── CSP + Security Headers Middleware ──
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    csp = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )
    response.headers["Content-Security-Policy"] = csp
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# Router
app.include_router(auth_router, dependencies=[Depends(auth_rate_limit)])
app.include_router(admin_router)
app.include_router(servers_router)
app.include_router(backups_router)
app.include_router(mods_router)
app.include_router(config_editor_router)
app.include_router(system_router)
app.include_router(steam_router)
app.include_router(panel_settings_router)
app.include_router(files_router)

# Static Frontend (nur in Produktion)
import os
if os.path.exists("/opt/msm/frontend/dist"):
    app.mount("/", StaticFiles(directory="/opt/msm/frontend/dist", html=True), name="frontend")


@app.get("/")
def root():
    return {"name": settings.app_name, "version": "1.0.0"}


@app.get("/api/health")
def health():
    return {"status": "ok"}
