from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from limits import parse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from config import TAURI_ORIGINS, get_cors_origins, settings
from database import engine, Base
from routers import (
    auth_router,
    admin_router,
    servers_router,
    backups_router,
    mods_router,
    system_router,
    steam_router,
    curseforge_router,
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
    incidents_router,
    change_timeline_router,
    guardian_router,
    ai_settings_router,
    tasks_router,
    ai_providers_router,
    ai_chat_router,
    ai_voice_router,
    ai_actions_router,
    desktop_router,
    ai_approvals_router,
    ai_autonomy_router,
    ai_memory_router,
    ai_tasks_router,
    ai_skills_router,
    ai_attachments_router,
    credentials_router,
    teams_router,
    hoster_admin_router,
    hoster_api_router,
    hoster_handoff_router,
)
from middleware.rate_limit import limiter
from services.rate_limit_settings import current_auth_limit_from_settings
from services.steam_service import close_steam_service
from services.scheduler_service import start_scheduler, stop_scheduler, init_server_schedules
from services.server_lifecycle_service import reconcile_orphaned_lifecycle_statuses


# ── Auth-Endpunkte: dynamisches Limit aus panel_settings (Default 10/min) ──
# Warum pro Request neu lesen: Admin kann Login-Schutz unter Einstellungen →
# Sicherheit anpassen (Firmen-IP / akuter Angriff), ohne Backend-Neustart.
# parse() ist billig; bei Settings-Lesefehler greift resolve_* den Default.


def auth_rate_limit(request: Request) -> None:
    """Strenges Rate-Limit für Login/2FA/Passwort-Reset/Setup pro IP.

    Liest rate_limit_auth aus den Panel-Settings (3–50, Default 10).
    Bei ungültigen/fehlenden Werten fail-closed auf Default — nie unlimitiert.
    """
    key = get_remote_address(request)
    try:
        per_minute = current_auth_limit_from_settings()
    except Exception:
        # DB/Cache-Fehler dürfen Auth nie ungeschützt lassen
        per_minute = 10
    limit_item = parse(f"{per_minute}/minute")
    if not limiter.limiter.hit(limit_item, key):
        raise HTTPException(
            status_code=429,
            detail="Zu viele Anfragen. Bitte warten Sie einen Moment.",
            headers={"Retry-After": "60"},
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    import httpx
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
    app.state.http_client = httpx.AsyncClient(limits=limits, timeout=5.0)
    app.state.ai_http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        timeout=httpx.Timeout(connect=5.0, read=90.0, write=10.0, pool=5.0),
        follow_redirects=False,
    )

    # Ein KI-Lauf haengt nicht mehr am Request, der ihn ausgeloest hat. Damit er
    # ueberhaupt irgendwo arbeiten kann, braucht er zwei Dinge aus dem Prozess:
    # die Ereignisschleife der Anwendung (auf der er geplant wird — auch aus
    # einem synchronen Endpunkt heraus, wenn ein Mensch eine Aktion bestaetigt)
    # und den HTTP-Client fuer den Anbieter.
    #
    # Ohne diesen Aufruf plant `ai_run_service` nichts und sagt das auch — der
    # Zustand in der Testsuite, in der es keine Anwendung gibt.
    import asyncio as _asyncio

    from services import ai_run_service as _ai_run_service

    _ai_run_service.laufzeit_setzen(
        _asyncio.get_running_loop(), app.state.ai_http_client
    )


    os.makedirs(settings.servers_dir, exist_ok=True)
    os.makedirs("/opt/msm/backups", exist_ok=True)

    # DIS Sidecar health check — fail-closed in production and debug (no own crypto)
    # Only bypassed if explicitly testing (e.g. pytest)
    import sys
    is_testing = os.getenv("MSM_TESTING") == "true" or "pytest" in sys.modules

    # Der Modellkatalog bekommt denselben langlebigen Client. Er frischt sich
    # kuenftig im Hintergrund auf und darf dafuer nicht den Client einer Anfrage
    # benutzen — der ist geschlossen, sobald sie beantwortet ist.
    #
    # Und dann einmal holen, bevor ihn jemand braucht. Das ist der Unterschied
    # zwischen "die erste Nachricht nach dem Neustart dauert eine Minute" und
    # "sie dauert so lange wie jede andere": ohne Vorwaermen faellt der Abruf
    # beim Anbieter genau in den Sendepfad des ersten Chats.
    #
    # In der Testsuite unterbleibt beides. Ein Vorwaermen dort waere ein echter
    # Aufruf an OpenRouter bei jedem Hochfahren der Anwendung, und ein gesetzter
    # Client liesse Tests im Hintergrund abrufen, die von nichts dergleichen
    # wissen.
    if not is_testing:
        from services import ai_model_catalog as _ai_model_catalog
        from services import ai_provider_service as _ai_provider_service

        _ai_model_catalog.laufzeit_setzen(app.state.ai_http_client)
        # Und woher ein schluesselpflichtiger Katalog seinen Schluessel bekommt.
        # Eine eingehaengte Funktion statt eines Imports: der Katalog soll von
        # Datenbank und DIS-Sidecar nichts wissen. Muss **vor** dem Vorwaermen
        # stehen, sonst laeuft dessen erster Durchgang ohne sie.
        _ai_model_catalog.schluesselquelle_setzen(
            _ai_provider_service.katalogschluessel
        )
        _ai_model_catalog.vorwaermen_anstossen()

    # Der Ausgangskorb der KI-Mails. Eine einzige Aufgabe auf dieser Schleife
    # loest den Fall ab, in dem jede faellige Mail einen eigenen Thread mit
    # eigener Ereignisschleife bekam — bei zehntausend gleichzeitig faelligen
    # Auftraegen waren das zehntausend Threads und ebenso viele frische
    # SMTP-Verbindungen.
    #
    # In der Testsuite unterbleibt der Start, aus demselben Grund wie beim
    # Katalog daneben: ein Arbeiter, der im Hintergrund die Datenbank abfragt
    # und Mails verschickt, gehoert nicht in Tests, die von nichts dergleichen
    # wissen. Die Tests des Korbs starten ihn selbst.
    if not is_testing:
        from services import ai_mail_outbox as _ai_mail_outbox

        _ai_mail_outbox.arbeiter_starten()

    from services.dis_client import DisClient
    if not is_testing and not DisClient.health_check():
        raise RuntimeError(
            "CRITICAL: DIS Sidecar nicht erreichbar. "
            "Starte den Sidecar zuerst (systemctl start msm-dis-sidecar). "
            "Das Panel enthaelt keine eigene Kryptographie und kann "
            "ohne DIS nicht operieren."
        )

    # Ensure database schema is up-to-date (automatically apply pending migrations in production)
    if not is_testing:
        from services.schema_manager import initialize_or_upgrade_schema
        try:
            status = initialize_or_upgrade_schema(engine)
            import logging
            logging.getLogger(__name__).info("PostgreSQL-Datenbankschema: %s", status)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).critical(
                "Datenbankschema-Initialisierung/Upgrade fehlgeschlagen: %s. "
                "Bitte führe 'install.sh' aus oder führe die Alembic-Migrationen manuell aus.",
                exc,
            )
            raise

    # The web process synchronizes runtime registration and checks that
    # the schema contains the required tables/columns.
    from database import SessionLocal
    from services.multi_node_migration_service import sync_multi_node_registration

    sync_multi_node_registration(
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
        from services.operation_task_service import recover_interrupted_tasks

        recovered_tasks = recover_interrupted_tasks(db)
        if recovered_tasks:
            import logging

            logging.getLogger(__name__).info(
                "%d offene Backend-Aufgabe(n) nach Panel-Start abgeglichen.",
                recovered_tasks,
            )
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

    # Dasselbe fuer die E-Mail-Freigaben. Sie sind kurzlebig und werden nach
    # dem Verbrauch nicht mehr gebraucht — die Tatsache, dass jemand zugestimmt
    # hat, steht im Audit. Hier stuende sie ein zweites Mal, mit einem
    # Tokenhash daneben.
    try:
        from database import SessionLocal as _SessionLocal3
        from services.ai_approval_service import abgelaufene_aufraeumen
        _approval_db = _SessionLocal3()
        try:
            abgelaufene_aufraeumen(_approval_db)
        finally:
            _approval_db.close()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("KI-Freigaben-Cleanup fehlgeschlagen: %s", exc)


    from services.ai_proposal_service import reconcile_interrupted_actions
    from services.ai_chat_service import reconcile_interrupted_ai_streams
    from services.ai_usage_service import verwaiste_reservierungen_abgleichen

    from services.ai_run_service import unterbrochene_laeufe_abgleichen

    _ai_recovery_db = SessionLocal()
    try:
        reconcile_interrupted_ai_streams(_ai_recovery_db)
        reconcile_interrupted_actions(_ai_recovery_db)
        # Nach den nachrichtengebundenen Zeilen: die Kontextverdichtung
        # reserviert Kontingent ohne Nachricht, ihre Zeile findet der Abgleich
        # oben deshalb nie. Bliebe sie auf `reserved`, wuerde sie einen
        # Nebenlaeufigkeitsplatz dauerhaft belegen — der Zaehler kennt kein
        # Zeitfenster und vergisst nichts.
        verwaist = verwaiste_reservierungen_abgleichen(_ai_recovery_db)
        if verwaist:
            import logging

            logging.getLogger(__name__).info(
                "%d verwaiste KI-Reservierung(en) nach Panel-Start abgerechnet.",
                verwaist,
            )
        # Ein Lauf im Zustand `running` hat den Neustart nicht ueberlebt: sein
        # Arbeitsgedaechtnis endet mitten in einer Anbieterantwort, und ob ein
        # Werkzeug schon lief, ist nicht mehr feststellbar. Ein halber
        # Werkzeugaufruf, blind wiederholt, waere schlimmer als ein ehrlicher
        # Abbruch. Geparkte Laeufe bleiben unangetastet — die warten auf einen
        # Menschen und haben nichts in der Luft.
        unterbrochene = unterbrochene_laeufe_abgleichen(_ai_recovery_db)
        if unterbrochene:
            import logging

            logging.getLogger(__name__).info(
                "%d unterbrochene(r) KI-Lauf/Laeufe nach Panel-Start abgeschlossen.",
                unterbrochene,
            )
        # Unterbrochene **Worker** bekommen genau einen automatischen
        # Wiederanlauf: ein neuer Lauf im selben Fenster mit Pruefauftrag —
        # die persistierte Unterhaltung ist der Checkpoint
        # (docs/agentic-framework.md). Die Laufzeit steht hier bereits
        # (`laufzeit_setzen` frueh im Lifespan), der Vorflug kann also fliegen.
        from services.ai_run_service import worker_wiederanlauf_saehen

        gesaet = await worker_wiederanlauf_saehen(_ai_recovery_db)
        if gesaet:
            import logging

            logging.getLogger(__name__).info(
                "%d Worker nach Panel-Start wiederangelaufen.", gesaet
            )
    finally:
        _ai_recovery_db.close()

    yield

    # Shutdown
    #
    # Der Modellkatalog zuerst, und zwar **vor** den Clients: eine noch laufende
    # Auffrischung benutzt `ai_http_client`. Wird der geschlossen, waehrend sie
    # laeuft, endet sie in einem RuntimeError auf einem geschlossenen Client —
    # ein Fehler beim Herunterfahren, der nichts bedeutet und trotzdem im Log
    # steht. Aufraeumen heisst hier: abbrechen und abwarten.
    if not is_testing:
        from services import ai_model_catalog as _ai_model_catalog

        await _ai_model_catalog.aufraeumen()

        # Der Ausgangskorb ebenfalls vor den Clients, und ebenfalls mit Abwarten:
        # `cancel()` bittet nur. Eine Mail, die dabei abgebrochen wird, ist nicht
        # verloren — ihre Zeile steht weiter auf `offen` und faellt nach der
        # Uebernahmefrist zurueck in die Warteschlange. Genau dafuer gibt es die
        # Tabelle.
        from services import ai_mail_outbox as _ai_mail_outbox

        await _ai_mail_outbox.aufraeumen()

    await app.state.http_client.aclose()
    await app.state.ai_http_client.aclose()
    stop_scheduler()
    await close_steam_service()


# Die automatischen Doku-Routen sind bewusst abgeschaltet und werden weiter
# unten unter /api/* neu registriert. Zwei Gruende:
#
# 1. FastAPI registriert /docs bereits im Konstruktor, der SPA-Mount auf "/"
#    kommt erst am Dateiende. Starlette matcht in Registrierungsreihenfolge —
#    im Single-Host-Betrieb lieferte ein Aufruf von https://panel/docs deshalb
#    die Swagger-UI statt der Doku-Seite des Panels.
# 2. /openapi.json haette keinerlei Auth-Dependency und gaebe damit anonym das
#    vollstaendige Schema aller Endpunkte inklusive der Hoster-Verwaltung heraus.
app = FastAPI(
    title=settings.app_name,
    description="Maunting Service Manager — Universeller Game Server Manager",
    version="3.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# ── CORS: Explizite Origins (panel_url + MSM_CORS_ALLOWED_ORIGINS + Dev) ──
_cors_origins = get_cors_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-CSRF-Token",
        "Idempotency-Key",
        "X-Task-Retry-Of",
    ],
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
        # Desktop-Origins (Tauri) sind CORS-erlaubt, aber der Browser des
        # Panels verbindet sich nie dorthin — in der CSP waeren sie nur Rauschen.
        if origin in TAURI_ORIGINS:
            continue
        if origin and origin not in parts:
            parts.append(origin)
        # ws/wss counterpart for console streams when SPA is same CSP host
        if origin.startswith("https://"):
            parts.append("wss://" + origin[len("https://") :])
        elif origin.startswith("http://"):
            parts.append("ws://" + origin[len("http://") :])
    return " ".join(parts)


# Die Swagger- und ReDoc-Oberflaechen laden ihre Assets von jsdelivr. Die
# Freigabe gilt ausschliesslich fuer diese beiden Pfade — jede andere Antwort
# behaelt die enge Standard-CSP. Ohne diese Ausnahme waere die Seite leer, und
# eine leere Seite haette man leicht fuer einen Serverfehler gehalten.
_API_DOCS_PATHS = frozenset({"/api/docs", "/api/redoc"})
_DOCS_CDN = "https://cdn.jsdelivr.net"


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    docs_page = request.url.path in _API_DOCS_PATHS
    csp = (
        "default-src 'self'; "
        f"script-src 'self'{' ' + _DOCS_CDN if docs_page else ''} https://singrabot.mauntingstudios.de https://client.crisp.chat https://embed.tawk.to; "
        f"style-src 'self' 'unsafe-inline'{' ' + _DOCS_CDN if docs_page else ''} https://singrabot.mauntingstudios.de; "
        f"img-src 'self' data:{' ' + _DOCS_CDN if docs_page else ''} https://singrabot.mauntingstudios.de; "
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
    # `setdefault` und nicht Zuweisung — wie zwei Zeilen tiefer bei
    # `Cache-Control`, und aus demselben Grund. Es gibt Endpunkte, deren Pfad
    # **selbst** das Geheimnis ist: der Hoster-Handoff und die KI-Freigabe
    # tragen ein Einmal-Token in der URL. Beide setzen deshalb ausdruecklich
    # `no-referrer`, damit das Token nicht im `Referer` jeder nachgeladenen
    # Ressource landet — und beide wurden hier bis eben wieder auf die
    # allgemeine, laxere Regel zurueckgesetzt. Der strengere Wert des Handlers
    # gewinnt jetzt; wo keiner gesetzt ist, gilt weiterhin die Vorgabe.
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

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
app.include_router(curseforge_router)
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
app.include_router(incidents_router)
app.include_router(change_timeline_router)
app.include_router(guardian_router)
app.include_router(ai_settings_router)
app.include_router(ai_providers_router)
app.include_router(ai_chat_router)
app.include_router(ai_voice_router)
app.include_router(ai_actions_router)
app.include_router(desktop_router)
# Ohne Anmeldung, aber unter dem strengen Auth-Limit: das Token im Pfad ist
# die ganze Berechtigung, und ein Endpunkt, der Token gegen einen Bestand
# prueft, gehoert hinter dieselbe Drossel wie der Login.
app.include_router(ai_approvals_router, dependencies=[Depends(auth_rate_limit)])
app.include_router(ai_memory_router)
app.include_router(ai_tasks_router)
app.include_router(ai_autonomy_router)
app.include_router(ai_skills_router)
app.include_router(ai_attachments_router)
app.include_router(tasks_router)
# Hoster-Anbindung (Phase 6). Panel-Verwaltung mit Cookie-Auth + CSRF,
# externe Shop-API ausschliesslich per API-Key, Handoff-Einloesung per
# Einmal-Token im Browser des Kunden.
app.include_router(hoster_admin_router)
app.include_router(hoster_api_router)
app.include_router(hoster_handoff_router)
# Zugangsdaten auf Benutzer- und Serverebene (Phase 7).
app.include_router(credentials_router)
app.include_router(teams_router)



# ── OpenAPI: unter /api/*, angemeldet und rechtegebunden ──
# Das Schema beschreibt jeden Endpunkt inklusive der Hoster-Verwaltung und der
# erwarteten Header. Das ist kein Geheimnis, aber auch nichts, was ohne Login
# herausgehen muss. `panel.settings.read` ist dasselbe Recht, das auch die
# Provider- und Integrationsansichten oeffnet.
from dependencies import require_global  # noqa: E402  (nach der App-Definition noetig)


@app.get("/api/openapi.json", include_in_schema=False)
def openapi_schema(_: object = Depends(require_global("panel.settings.read"))):
    return JSONResponse(app.openapi())


@app.get("/api/docs", include_in_schema=False)
def swagger_ui(_: object = Depends(require_global("panel.settings.read"))):
    return get_swagger_ui_html(
        openapi_url="/api/openapi.json",
        title=f"{settings.app_name} — API",
    )


@app.get("/api/redoc", include_in_schema=False)
def redoc_ui(_: object = Depends(require_global("panel.settings.read"))):
    return get_redoc_html(
        openapi_url="/api/openapi.json",
        title=f"{settings.app_name} — API",
    )


@app.get("/api/version")
def app_version():
    return {"name": settings.app_name, "version": "2.0.0"}


@app.get("/api/health")
def health():
    return {"status": "ok"}

# Static Frontend (Single-Host Produktion). Phase 4: abschaltbar fuer API-only.
# Wichtig: Mount NACH allen API-Routern und expliziten Routes hinzufügen,
# damit /api/* und Health nicht vom SPA-Static-Fallback geschluckt werden.
# /assets/* ohne html-Fallback: fehlende JS-Chunks liefern 404 (text/plain),
# nicht index.html — verhindert „MIME type text/html“ bei veralteten Lazy-Chunks.
_FRONTEND_DIST = "/opt/msm/frontend/dist"
if settings.serve_frontend and os.path.exists(_FRONTEND_DIST):
    app.mount(
        "/assets",
        StaticFiles(directory=_FRONTEND_DIST, html=False),
        name="frontend-assets",
    )
    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
