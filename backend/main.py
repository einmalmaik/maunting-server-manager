from contextlib import asynccontextmanager
from datetime import datetime, timezone
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
    system_router,
    steam_router,
    panel_settings_router,
    files_router,
    roles_router,
    permissions_router,
    blueprints_router,
    oauth_router,
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

    # Migration: server_ports Tabelle anlegen & Daten migrieren
    if 'server_ports' not in inspector.get_table_names():
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE server_ports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER NOT NULL,
                    role VARCHAR(64) NOT NULL,
                    port INTEGER NOT NULL,
                    protocol VARCHAR(16) NOT NULL,
                    FOREIGN KEY(server_id) REFERENCES servers(id) ON DELETE CASCADE
                )
            """))
            conn.execute(text("CREATE INDEX ix_server_ports_id ON server_ports (id)"))

    # Migration: Backup-Scheduling-Spalten + Phase-1 Docker-Spalten
    if 'servers' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('servers')]
        # Falls game_port noch in servers existiert, migrieren wir die Daten zuerst in server_ports
        if 'game_port' in cols:
            with engine.begin() as conn:
                servers_data = conn.execute(text("SELECT id, game_port, query_port, rcon_port FROM servers")).fetchall()
                for row in servers_data:
                    srv_id = row[0]
                    g_port = row[1]
                    q_port = row[2]
                    r_port = row[3]
                    
                    if g_port:
                        conn.execute(
                            text("INSERT INTO server_ports (server_id, role, port, protocol) VALUES (:sid, 'game', :port, 'udp')"),
                            {"sid": srv_id, "port": g_port}
                        )
                    if q_port:
                        conn.execute(
                            text("INSERT INTO server_ports (server_id, role, port, protocol) VALUES (:sid, 'query', :port, 'udp')"),
                            {"sid": srv_id, "port": q_port}
                        )
                    if r_port:
                        conn.execute(
                            text("INSERT INTO server_ports (server_id, role, port, protocol) VALUES (:sid, 'rcon', :port, 'tcp')"),
                            {"sid": srv_id, "port": r_port}
                        )
                try:
                    conn.execute(text("ALTER TABLE servers DROP COLUMN game_port"))
                    conn.execute(text("ALTER TABLE servers DROP COLUMN query_port"))
                    conn.execute(text("ALTER TABLE servers DROP COLUMN rcon_port"))
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning("Konnte alte Port-Spalten nicht droppen: %s", exc)
                # Nach dem Droppen muessen wir cols neu laden, damit die folgenden checks nicht fehlschlagen
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
            if 'restart_times_utc' not in cols:
                conn.execute(text("ALTER TABLE servers ADD COLUMN restart_times_utc VARCHAR(256)"))
            if 'last_auto_restart_attempt_at' not in cols:
                conn.execute(text("ALTER TABLE servers ADD COLUMN last_auto_restart_attempt_at TIMESTAMP"))
            if 'last_auto_restart_completed_at' not in cols:
                conn.execute(text("ALTER TABLE servers ADD COLUMN last_auto_restart_completed_at TIMESTAMP"))
            if 'last_auto_restart_status' not in cols:
                conn.execute(text("ALTER TABLE servers ADD COLUMN last_auto_restart_status VARCHAR(16)"))
            if 'last_started_at' not in cols:
                conn.execute(text("ALTER TABLE servers ADD COLUMN last_started_at TIMESTAMP"))
            # Phase 1 — Legacy-Spalte linux_user entfernen (Server laufen jetzt
            # in Docker-Containern, kein POSIX-User-pro-Server mehr).
            if 'linux_user' in cols:
                conn.execute(text("ALTER TABLE servers DROP COLUMN linux_user"))

    # Migration: Mod enabled-Spalte
    if 'mods' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('mods')]
        with engine.begin() as conn:
            if 'enabled' not in cols:
                conn.execute(text("ALTER TABLE mods ADD COLUMN enabled BOOLEAN DEFAULT true"))
            if 'install_status' not in cols:
                conn.execute(text("ALTER TABLE mods ADD COLUMN install_status VARCHAR(24) NOT NULL DEFAULT 'installed'"))
            if 'install_action' not in cols:
                conn.execute(text("ALTER TABLE mods ADD COLUMN install_action VARCHAR(24)"))
            if 'install_progress' not in cols:
                conn.execute(text("ALTER TABLE mods ADD COLUMN install_progress INTEGER"))
            if 'install_eta_seconds' not in cols:
                conn.execute(text("ALTER TABLE mods ADD COLUMN install_eta_seconds INTEGER"))
            if 'install_started_at' not in cols:
                conn.execute(text("ALTER TABLE mods ADD COLUMN install_started_at TIMESTAMP"))
            if 'install_completed_at' not in cols:
                conn.execute(text("ALTER TABLE mods ADD COLUMN install_completed_at TIMESTAMP"))
            if 'install_error' not in cols:
                conn.execute(text("ALTER TABLE mods ADD COLUMN install_error TEXT"))
            if 'update_status' not in cols:
                conn.execute(text("ALTER TABLE mods ADD COLUMN update_status VARCHAR(24) NOT NULL DEFAULT 'unknown'"))
            if 'update_reason' not in cols:
                conn.execute(text("ALTER TABLE mods ADD COLUMN update_reason VARCHAR(128)"))
            if 'update_checked_at' not in cols:
                conn.execute(text("ALTER TABLE mods ADD COLUMN update_checked_at TIMESTAMP"))

    # Migration: Backup name-Spalte
    if 'backups' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('backups')]
        if 'name' not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE backups ADD COLUMN name VARCHAR(256)"))

    # Phase 3 — RBAC: users.role_id-Spalte (Tabellen `roles`/`role_permissions`/
    # `server_permissions` werden von `Base.metadata.create_all` angelegt) und
    # einmalige Migration der alten `permissions`-Tabelle in `server_permissions`.
    if 'users' in inspector.get_table_names():
        user_cols = [c['name'] for c in inspector.get_columns('users')]
        if 'role_id' not in user_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN role_id INTEGER"))

    # Built-in Rollen seeden + admin-Rolle auf aktuellen Katalog syncen.
    from database import SessionLocal as _SessionLocal
    from services.role_service import ensure_system_roles, get_role_by_name
    from services.permission_catalog import (
        LEGACY_PERMISSION_MAPPING,
        SYSTEM_ROLE_USER,
    )
    _seed_db = _SessionLocal()
    try:
        ensure_system_roles(_seed_db)
        user_role = get_role_by_name(_seed_db, SYSTEM_ROLE_USER)
        # Bestehende Nicht-Owner ohne Rolle bekommen `user` als sicheren Default.
        if user_role is not None:
            _seed_db.execute(
                text(
                    "UPDATE users SET role_id = :rid "
                    "WHERE role_id IS NULL AND is_owner = :is_owner"
                ),
                {"rid": user_role.id, "is_owner": False},
            )
            _seed_db.commit()
    finally:
        _seed_db.close()

    # Datenmigration: alte `permissions`-Tabelle -> `server_permissions`.
    # Idempotent: prueft jeweils, ob Ziel-Rows bereits existieren. Danach wird
    # die Legacy-Tabelle gedroppt (nur, wenn sie existiert).
    inspector = inspect(engine)
    if 'permissions' in inspector.get_table_names():
        import logging as _logging
        _log_mig = _logging.getLogger(__name__)
        legacy_cols = {c['name'] for c in inspector.get_columns('permissions')}
        select_cols = [c for c in LEGACY_PERMISSION_MAPPING.keys() if c in legacy_cols]
        if not select_cols:
            # Keine bekannten can_*-Spalten in der Legacy-Tabelle vorhanden.
            # Nichts zu migrieren -> Tabelle einfach droppen.
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE permissions"))
            migrated = 0
        else:
            with engine.begin() as conn:
                rows = conn.execute(
                    text(
                        "SELECT id, user_id, server_id, "
                        + ", ".join(select_cols)
                        + " FROM permissions"
                    )
                ).fetchall()
                migrated = 0
                for row in rows:
                    user_id = row.user_id
                    server_id = row.server_id
                    desired_keys: set[str] = set()
                    for col in select_cols:
                        if getattr(row, col):
                            desired_keys.update(LEGACY_PERMISSION_MAPPING[col])
                    # In der alten Welt konnte jeder User mit irgendeiner
                    # `Permission`-Row den Server in der Liste sehen. Ohne
                    # explizites `server.view` waere er nach Migration aber aus
                    # `list_visible_servers` / `get_server` ausgesperrt → wir
                    # ziehen die Sichtbarkeit immer mit, sobald irgendeine
                    # Permission migriert wird.
                    if desired_keys:
                        desired_keys.add("server.view")
                    for key in desired_keys:
                        exists = conn.execute(
                            text(
                                "SELECT id FROM server_permissions "
                                "WHERE user_id = :uid AND server_id = :sid "
                                "AND permission_key = :key"
                            ),
                            {"uid": user_id, "sid": server_id, "key": key},
                        ).first()
                        if exists is None:
                            # `granted_at` ist NOT NULL und der Model-Default ist
                            # Python-seitig (greift bei Raw-SQL nicht) -> explizit setzen.
                            conn.execute(
                                text(
                                    "INSERT INTO server_permissions "
                                    "(user_id, server_id, permission_key, granted_at) "
                                    "VALUES (:uid, :sid, :key, :ts)"
                                ),
                                {
                                    "uid": user_id,
                                    "sid": server_id,
                                    "key": key,
                                    "ts": datetime.now(timezone.utc),
                                },
                            )
                            migrated += 1
                conn.execute(text("DROP TABLE permissions"))
        if migrated:
            _log_mig.info("Phase-3 RBAC-Migration: %d Permission-Eintraege migriert.", migrated)

    # Phase 2 — Port-Manager-Initialisierung:
    # 1. Legacy-MSM-Port-Ranges (z. B. 27015:27999/udp) aus UFW entfernen.
    #    Wir loeschen NUR Eintraege mit MSM-Comment-Praefix; SSH/Caddy/Custom
    #    Regeln bleiben unangetastet (siehe firewall_service.cleanup_legacy_msm_ranges).
    # 2. DOCKER-USER iptables Baseline-DROP fuer die MSM-Port-Range setzen
    #    (Defense-in-Depth gegen Docker-UFW-Bypass). Idempotent.
    try:
        from services.firewall_service import cleanup_legacy_msm_ranges
        from services.docker_iptables_service import ensure_baseline_drop
        removed = cleanup_legacy_msm_ranges()
        if removed:
            import logging
            logging.getLogger(__name__).info(
                "Port-Manager: %d Legacy-MSM-Range(s) aus UFW entfernt.", removed,
            )
        ensure_baseline_drop()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "Phase-2 Port-Manager-Init partiell fehlgeschlagen: %s", exc,
        )

    # Initialize scheduler and load existing schedules
    start_scheduler()
    from database import SessionLocal
    db = SessionLocal()
    try:
        init_server_schedules(db)
    finally:
        db.close()

    # OAuth: abgelaufene Login-Challenges aufraeumen (idempotent, low-cost).
    # Kein Hard-Fail, wenn der Cleanup scheitert — der naechste Startup macht
    # es wieder.
    try:
        from database import SessionLocal as _SessionLocal2
        from services.login_challenge_service import cleanup_expired
        _cleanup_db = _SessionLocal2()
        try:
            cleanup_expired(_cleanup_db)
        finally:
            _cleanup_db.close()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("OAuth-LoginChallenge-Cleanup fehlgeschlagen: %s", exc)


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

    # ── Cache-Control: Vite erzeugt content-gehashte Asset-Pfade ──
    # /assets/* → 1 Jahr immutable (Hash aendert sich bei jeder neuen Version)
    # /index.html und alle HTML-Routen → kein Cache (Browser fragt immer beim Server nach)
    # Alles andere (Icons, Fonts, etc.) → 1 Tag
    path = request.url.path
    if path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path == "/" or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    else:
        response.headers.setdefault("Cache-Control", "public, max-age=86400")

    return response


# Router
app.include_router(auth_router, dependencies=[Depends(auth_rate_limit)])
app.include_router(admin_router)
app.include_router(servers_router)
app.include_router(backups_router)
app.include_router(mods_router)
app.include_router(system_router)
app.include_router(steam_router)
app.include_router(panel_settings_router)
app.include_router(files_router)
app.include_router(roles_router)
app.include_router(permissions_router)
app.include_router(blueprints_router)
# OAuth-Endpoints liegen absichtlich NICHT unter auth_rate_limit, weil das
# Rate-Limit pro IP und pro Minute gilt (10/min). Bei Shared-IPs (Unternehmen,
# Schulen, mobile Carrier) wuerde der Login-Flow sonst regelmaessig 429
# liefern. Stattdessen schuetzen die State-Cookie-Validierung + PKCE + 5-Min
# LoginChallenge gegen Brute-Force auf dem OAuth-Pfad.
app.include_router(oauth_router)

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
