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

from config import get_cors_origins, settings
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
    databases_router,
    webhooks_outbound_router,
    singra_webhook_router,
    backup_config_router,
    panel_backups_router,
    panel_database_router,
    nodes_router,
)
from middleware.rate_limit import limiter
from services.steam_service import close_steam_service
from services.scheduler_service import start_scheduler, stop_scheduler, init_server_schedules
from services.server_lifecycle_service import reconcile_orphaned_lifecycle_statuses


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

    # DIS Sidecar health check — fail-closed in production and debug (no own crypto)
    # Only bypassed if explicitly testing (e.g. pytest)
    import sys
    is_testing = os.getenv("MSM_TESTING") == "true" or "pytest" in sys.modules
    from services.dis_client import DisClient
    if not is_testing and not DisClient.health_check():
        raise RuntimeError(
            "CRITICAL: DIS Sidecar nicht erreichbar. "
            "Starte den Sidecar zuerst (systemctl start msm-dis-sidecar). "
            "Das Panel enthaelt keine eigene Kryptographie und kann "
            "ohne DIS nicht operieren."
        )

    # Schema must exist before local-node registration (servers.node_id, etc.).
    # prepare_phase8 / Alembic should have run at install/update; ensure_multi_node_schema
    # is idempotent and covers in-place multi-node upgrades that skipped DB init.
    from database import SessionLocal
    from services.multi_node_migration_service import migrate_multi_node_schema

    migrate_multi_node_schema(
        engine,
        SessionLocal,
        allow_missing_local_token=is_testing,
        local_agent_enabled=settings.local_agent_enabled,
    )

    # Migration: fehlende Spalten nachträglich hinzufügen
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    # Phase 8: Sobald Alembic die Datenbank verwaltet, darf der Webprozess
    # keinerlei Schema mehr veraendern. Die folgenden historischen Bruecken
    # bleiben nur fuer einen ungeversionierten Altstart erhalten.
    legacy_schema_bridge = "alembic_version" not in inspector.get_table_names()
    if 'users' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('users')]
        if legacy_schema_bridge and 'email_notifications' not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN email_notifications BOOLEAN DEFAULT true"))
        # E-Mail-Verschluesselung: email_encrypted + email_hash Spalten
        if legacy_schema_bridge and 'email_encrypted' not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN email_encrypted VARCHAR(4096)"))
                conn.execute(text("ALTER TABLE users ADD COLUMN email_hash VARCHAR(64)"))
                conn.execute(text("CREATE INDEX ix_users_email_hash ON users (email_hash)"))

        # Bestehende Klartext-E-Mails immer nachziehen. Das ist auch fuer den
        # SQLite->PostgreSQL-Import noetig: das Zielschema besitzt die neuen
        # Spalten bereits, die importierten Legacy-Zeilen aber noch nicht.
        from database import SessionLocal as _SL
        from models import User as _U
        _db = _SL()
        try:
            for _u in _db.query(_U).filter(_U.email_encrypted.is_(None)).all():
                if _u.email_plain:
                    _u.email = _u.email_plain  # setter verschluesselt + hasht
            _db.commit()
        finally:
            _db.close()

    # Migration: webhook_subscriptions.secret_encrypted Spalte hinzufuegen
    if legacy_schema_bridge and 'webhook_subscriptions' in inspector.get_table_names():
        wh_cols = [c['name'] for c in inspector.get_columns('webhook_subscriptions')]
        if 'secret_encrypted' not in wh_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE webhook_subscriptions ADD COLUMN secret_encrypted VARCHAR(4096)"))

    if "singra_webhook_events" not in inspector.get_table_names():
        from models.singra_webhook_event import SingraWebhookEvent  # noqa: F401
        SingraWebhookEvent.__table__.create(bind=engine, checkfirst=True)

    # Migration: servers.auth_required Spalte hinzufuegen (interaktive Auth-Recovery)
    if legacy_schema_bridge and 'servers' in inspector.get_table_names():
        srv_cols = [c['name'] for c in inspector.get_columns('servers')]
        if 'auth_required' not in srv_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE servers ADD COLUMN auth_required BOOLEAN NOT NULL DEFAULT false"))

    # Migration: email_verifications table cleanup for hashing
    if legacy_schema_bridge and 'email_verifications' in inspector.get_table_names():
        ev_cols = [c['name'] for c in inspector.get_columns('email_verifications')]
        if 'email' in ev_cols and 'email_hash' not in ev_cols:
            # Ephemerale Tabelle neu aufbauen
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE email_verifications"))
            Base.metadata.create_all(bind=engine)

    # Migration: oauth_user_links encryption columns
    if legacy_schema_bridge and 'oauth_user_links' in inspector.get_table_names():
        ol_cols = [c['name'] for c in inspector.get_columns('oauth_user_links')]
        if 'email_at_link_encrypted' not in ol_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE oauth_user_links ADD COLUMN email_at_link_encrypted VARCHAR(4096)"))
        if 'username_at_link_encrypted' not in ol_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE oauth_user_links ADD COLUMN username_at_link_encrypted VARCHAR(4096)"))

    # Migration: server_ports Tabelle anlegen & Daten migrieren
    if legacy_schema_bridge and 'server_ports' not in inspector.get_table_names():
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
    if legacy_schema_bridge and 'servers' in inspector.get_table_names():
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
    if legacy_schema_bridge and 'mods' in inspector.get_table_names():
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
    if legacy_schema_bridge and 'backups' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('backups')]
        if 'name' not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE backups ADD COLUMN name VARCHAR(256)"))
        # S3-Cloud-Backup-Erweiterung (M1). Drei Spalten, alle nullable ausser
        # ``encrypted`` (Default false). Bei Migration jeweils idempotent
        # pruefen, bevor ALTER TABLE ausgefuehrt wird.
        # Hintergrund: die S3-Features wurden im Code commited (Model +
        # Orchestrator), aber die Schema-Migration fuer die bestehende
        # DB wurde vergessen — jede Query auf den Backup-Endpoint schlug
        # deshalb mit ``column backups.s3_key does not exist`` fehl.
        if 's3_key' not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE backups ADD COLUMN s3_key VARCHAR(512)"))
        if 's3_bucket' not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE backups ADD COLUMN s3_bucket VARCHAR(255)"))
        if 'encrypted' not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE backups ADD COLUMN encrypted BOOLEAN NOT NULL DEFAULT false"))

    # Phase 3 — RBAC: users.role_id-Spalte (Tabellen `roles`/`role_permissions`/
    # `server_permissions` werden von `Base.metadata.create_all` angelegt) und
    # einmalige Migration der alten `permissions`-Tabelle in `server_permissions`.
    if legacy_schema_bridge and 'users' in inspector.get_table_names():
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
    if legacy_schema_bridge and 'permissions' in inspector.get_table_names():
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
    if settings.local_agent_enabled:
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

    # Managed PostgreSQL: on local node agent only (Phase 7 — no panel psycopg2).
    if settings.local_agent_enabled:
        try:
            from database import SessionLocal
            from services.postgres_service import ensure_internal_postgres

            _pg_db = SessionLocal()
            try:
                ensure_internal_postgres(_pg_db)
            finally:
                _pg_db.close()
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "Managed-PostgreSQL beim Panel-Start nicht bereit: %s", exc,
            )

    # Initialize scheduler and load existing schedules
    start_scheduler()
    from database import SessionLocal
    db = SessionLocal()
    try:
        reconciled = reconcile_orphaned_lifecycle_statuses(db)
        if reconciled:
            import logging
            logging.getLogger(__name__).info(
                "Lifecycle-Status für %d Server nach Panel-Start mit Docker abgeglichen.",
                reconciled,
            )
        init_server_schedules(db)
    finally:
        db.close()

    # Migration: oauth_providers.client_secret_mask (P1.3) — vermeidet
    # DIS-Decrypt im Listing-Pfad. Die Spalte wird beim naechsten
    # Create/Update des Providers automatisch befuellt; alte Provider
    # bekommen NULL (Fallback im Response-Builder).
    if legacy_schema_bridge and 'oauth_providers' in inspector.get_table_names():
        cols = [c['name'] for c in inspector.get_columns('oauth_providers')]
        if 'client_secret_mask' not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE oauth_providers ADD COLUMN client_secret_mask VARCHAR(64)"))

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
    version="1.7.9",
    lifespan=lifespan,
)

# ── CORS: Explizite Origins (panel_url + MSM_CORS_ALLOWED_ORIGINS + Dev) ──
_cors_origins = get_cors_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
    expose_headers=["X-CSRF-Token"],
)


# ── Rate Limiting (slowapi) ──
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


# ── CSP + Security Headers Middleware ──
def _csp_connect_src() -> str:
    """connect-src: 'self' plus panel/CORS origins (split FE + API / Vercel)."""
    parts = ["'self'"]
    for origin in _cors_origins:
        if origin and origin not in parts:
            parts.append(origin)
        # ws/wss counterpart for console streams when SPA is same CSP host
        if origin.startswith("https://"):
            parts.append("wss://" + origin[len("https://") :])
        elif origin.startswith("http://"):
            parts.append("ws://" + origin[len("http://") :])
    return " ".join(parts)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    csp = (
        "default-src 'self'; "
        "script-src 'self' https://singrabot.mauntingstudios.de https://client.crisp.chat https://embed.tawk.to; "
        "style-src 'self' 'unsafe-inline' https://singrabot.mauntingstudios.de; "
        "img-src 'self' data: https://singrabot.mauntingstudios.de; "
        f"connect-src {_csp_connect_src()} https://singrabot.mauntingstudios.de https://client.crisp.chat wss://client.relay.crisp.chat https://va.tawk.to; "
        "font-src 'self' https://singrabot.mauntingstudios.de; "
        "frame-src 'self' https://singrabot.mauntingstudios.de; "
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
app.include_router(nodes_router)
app.include_router(files_router)
app.include_router(roles_router)
app.include_router(permissions_router)
app.include_router(blueprints_router)
app.include_router(databases_router)
# Ausgehende Webhooks (MSM → Drittsystem wie Discord-Bot): per-Server
# Subscriptions mit Secret-Auth ueber X-Webhook-Secret-Header.
app.include_router(webhooks_outbound_router)
app.include_router(singra_webhook_router)
# OAuth-Endpoints liegen absichtlich NICHT unter auth_rate_limit, weil das
# Rate-Limit pro IP und pro Minute gilt (10/min). Bei Shared-IPs (Unternehmen,
# Schulen, mobile Carrier) wuerde der Login-Flow sonst regelmaessig 429
# liefern. Stattdessen schuetzen die State-Cookie-Validierung + PKCE + 5-Min
# LoginChallenge gegen Brute-Force auf dem OAuth-Pfad.
app.include_router(oauth_router)
# Backup-Config (S3-Settings + Backup-Passwort). Admin-only (panel.settings.write),
# CSRF auf allen Write-Endpunkten. Credentials verschluesselt via DIS.
app.include_router(backup_config_router)
app.include_router(panel_backups_router)
app.include_router(panel_database_router)



@app.get("/api/version")
def app_version():
    return {"name": settings.app_name, "version": "1.7.10"}


@app.get("/api/health")
def health():
    return {"status": "ok"}

# Static Frontend (Single-Host Produktion). Phase 4: abschaltbar fuer API-only.
# Wichtig: Mount NACH allen API-Routern und expliziten Routes hinzufügen,
# damit /api/* und Health nicht vom SPA-Static-Fallback geschluckt werden.
# /assets/* ohne html-Fallback: fehlende JS-Chunks liefern 404 (text/plain),
# nicht index.html — verhindert „MIME type text/html“ bei veralteten Lazy-Chunks.
import os
_FRONTEND_DIST = "/opt/msm/frontend/dist"
if settings.serve_frontend and os.path.exists(_FRONTEND_DIST):
    app.mount(
        "/assets",
        StaticFiles(directory=_FRONTEND_DIST, html=False),
        name="frontend-assets",
    )
    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
