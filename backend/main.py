from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

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
)
from middleware.rate_limit import rate_limit_middleware
from services.steam_service import close_steam_service
from services.scheduler_service import start_scheduler, stop_scheduler, init_server_schedules


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    Base.metadata.create_all(bind=engine)

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


# ── Rate Limiting ──
app.middleware("http")(rate_limit_middleware)


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
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(servers_router)
app.include_router(backups_router)
app.include_router(mods_router)
app.include_router(config_editor_router)
app.include_router(system_router)
app.include_router(steam_router)

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
