from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Maunting Server Manager"
    debug: bool = False

    # Datenbank
    database_url: str = "sqlite:///./msm.db"
    database_url_async: str = "sqlite+aiosqlite:///./msm.db"

    # Sicherheit
    secret_key: str = "change-me-in-production-please-use-a-256-bit-key"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 15  # 15 Min — kurzlebig fuer Rotation
    refresh_token_expire_days: int = 30  # 30 Tage Refresh-Token
    csrf_token_expire_minutes: int = 60 * 24  # 24h CSRF-Token

    # DIS Sidecar (lokaler Node-Prozess, wrappt @msdis/shield)
    # Alle Krypto-Operationen (AES-256-GCM, Argon2id, TOTP) laufen ueber DIS.
    # Das Panel selbst enthaelt keine eigene Kryptographie.
    dis_sidecar_url: str = "http://127.0.0.1:9100"
    dis_sidecar_token: str = ""

    # Email — SMTP oder Resend (resend.com)
    # Resend API-Key hat Vorrang vor SMTP wenn beides gesetzt
    email_provider: str = "smtp"  # "smtp" | "resend"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_tls: bool = True
    smtp_from: str = "noreply@mauntingstudios.de"
    resend_api_key: str = ""

    # Panel
    panel_url: str = "http://localhost"
    setup_completed_file: Path = Path("/opt/msm/.setup_completed")

    # Cookie-Domain fuer cross-subdomain Setups (z.B. app.X.example.com + api.X.example.com).
    # Optionaler Override. Wenn nicht gesetzt (oder leer), wird automatisch aus
    # panel_url / MSM_PANEL_URL abgeleitet (genau die gleiche Logik wie in install.sh).
    # Das macht die Cookie-Domain-Konfiguration autonom für self-hosted Open-Source-Installationen:
    # die Domain, die bei der ersten Installation (oder später per install.sh geändert) hinterlegt wurde,
    # ist die einzige Quelle der Wahrheit (zusammen mit dem schon existierenden MSM_PANEL_URL).
    # LEER lassen für Single-Domain / localhost (dann host-only Cookie).
    # Mit führendem Punkt für Subdomains (Cloudflare, Reverse-Proxy etc.).
    cookie_domain: str = ""

    # Cross-Site Cookies (Phase 4: Frontend auf anderer Domain, z. B. Vercel).
    # true → Session-Cookies mit SameSite=None (erfordert Secure=True / HTTPS).
    # false (Default) → SameSite=Lax/Strict wie bisher (Single-Host / reverse-proxy).
    # Lokale Split-Dev (localhost:3000 → :8000): true setzen, CORS-Origins pflegen.
    cookie_cross_site: bool = False

    # Zusaetzliche CORS-Origins (Komma-separiert), z. B. https://maunting-panel.vercel.app
    # panel_url ist immer erlaubt. In debug=True kommen zusaetzlich lokale Dev-Origins.
    cors_allowed_origins: str = ""

    # Backend dient das gebaute React-SPA (StaticFiles unter /opt/msm/frontend/dist).
    # false = reines API-Backend (Vercel / separates Frontend-Hosting). Default true
    # fuer Abwaertskompatibilitaet von Single-Host-Installationen.
    serve_frontend: bool = True

    # Logo — absolute URL used in email templates.
    # Falls back to panel_url + /logo.png when empty.
    logo_url: str = ""

    # Redis (fuer verteiltes Rate-Limiting via slowapi)
    redis_url: str = ""

    # Server-Verzeichnis (Install-Dir pro Server)
    # Produktion: /opt/msm/servers  |  Dev: ./servers
    servers_dir: str = "/opt/msm/servers"

    # Panel-Backup-Konfiguration
    # Produktion: Config-Dateien (.env, install.sh, ...) liegen unter /opt/msm/
    # und Panel-Backups werden unter /opt/msm/backups/panel/ gespeichert.
    # Dev/Test kann beides ueber env ueberschreiben (z.B. auf tmp_path).
    panel_config_dir: str = "/opt/msm"
    panel_backup_dir: str = "/opt/msm/backups/panel"

    # Verwaltetes PostgreSQL fuer Game-Server-Datenbanken.
    # Der Host-Port ist absichtlich nur an Loopback gebunden. Game-Container
    # erreichen PostgreSQL ueber das interne Docker-Netz und msm-postgres:5432.
    managed_postgres_image: str = "postgres:17-alpine"
    managed_postgres_container_name: str = "msm-postgres"
    managed_postgres_network: str = "msm-internal"
    managed_postgres_host: str = "127.0.0.1"
    managed_postgres_port: int = 15432
    managed_postgres_data_dir: str = "/opt/msm/postgres"
    managed_postgres_statement_timeout_ms: int = 5000
    managed_postgres_row_limit: int = 500

    # Trusted Postgres extensions, die Game-Server-Owner in ihrer DB selbst
    # installieren duerfen (pgcrypto fuer UUID/Crypto, pg_trgm fuer Volltextsuche,
    # citext fuer case-insensitive Vergleiche, ...). Diese Liste ist statisch im
    # Code -- Erweiterung erfordert Code-Change + Review, damit kein User ueber
    # eine dynamische Allowlist an eine nicht-trusted Extension kommt.
    # Alle hier gelisteten Extensions sind in Postgres 17 als ``trusted`` markiert,
    # d. h. der Owner einer DB darf sie ohne Superuser mit CREATE-Privileg installieren.
    trusted_postgres_extensions: set[str] = {
        "pgcrypto",        # UUID-Gen, Digest, Symmetric Encryption (haeufig gebraucht)
        "uuid-ossp",       # alternative UUID-Implementierung
        "citext",          # case-insensitive Text
        "btree_gin",       # GIN-Index auf B-Tree-Typen
        "btree_gist",      # GiST-Index auf B-Tree-Typen
        "fuzzystrmatch",   # Levenshtein, Soundex, Metaphone
        "hstore",          # Key/Value-Spalten
        "pg_trgm",         # Trigramm-Index fuer LIKE/ILIKE-Suche
        "tablefunc",       # crosstab() u. a.
        "unaccent",        # Akzent-unabhaengige Textsuche
        "isn",             # ISBN/ISSN/EAN-Validierung
        "lo",              # Large Objects (Bilder, Bloobs in der DB)
        "ltree",           # hierarchische Baumstrukturen
        "tcn",             # Trigger-basiertes Change Notification
    }

    # Rootless Docker. Produktion: unix:///run/user/<msm_uid>/docker.sock
    # Leer = docker_service berechnet den Rootless-Default fuer den laufenden User.
    docker_host: str = ""

    # Blueprint-Verzeichnis (Community-Imports, getrennt von Repo-Code).
    # Produktion: /opt/msm/blueprints/community  |  Dev/Test ggf. via MSM_BLUEPRINTS_DIR
    blueprints_dir: str = "/opt/msm/blueprints/community"

    # Steam — SteamCMD läuft in einem ephemeren Container (cm2network/steamcmd:root per Default
    # als dediziertes Tool-Image mit pre-installed binary). steamcmd_path bleibt nur für Backward-Compat-Tests.
    steamcmd_path: str = "/usr/games/steamcmd"
    steam_api_key: str = ""
    github_clone_token: str = ""
    """Optional: MSM_GITHUB_CLONE_TOKEN für private GitHub-Repos (source.type=github)."""

    # Auto-Update (GitHub Releases)
    github_owner: str = "einmalmaik"
    github_repo: str = "maunting-server-manager"
    auto_update: bool = False  # true = systemd-Timer installiert Updates automatisch
    auto_update_interval_hours: int = 24  # Prüfintervall

    model_config = SettingsConfigDict(
        env_prefix="MSM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

# Harte Fail-Fast für schwachen Default (Security-Finding #14): verhindert JWT-Forgery in Prod bei vergessener .env.
# Dev (debug=True) erlaubt Default für schnellen Start; Prod (debug=False) bricht sofort.
if settings.secret_key == "change-me-in-production-please-use-a-256-bit-key" and not settings.debug:
    raise RuntimeError(
        "CRITICAL SECURITY: MSM_SECRET_KEY (or secret_key) must be overridden with a strong >=32 char value in production. "
        "Default placeholder allows JWT forgery. Set in .env or env (prefixed MSM_)."
    )

# Harte Fail-Fast für unkonfigurierten panel_url in Production.
# Hintergrund: panel_url treibt OAuth-redirect_uri, E-Mail-Links (Password-Reset,
# OAuth-Link-Bestaetigungen) und CORS. Mit dem http://localhost-Default in Prod
# wuerden OAuth-Callbacks gegen einen falschen Host laufen, E-Mails zeigen auf
# localhost, und Browser wuerden Mixed-Content-Fehler werfen. install.sh setzt
# panel_url aus der Domain — wenn es nicht gelaufen ist oder .env verloren
# ging, bricht die App hier sauber ab statt spaet in der OAuth-Flow.
# Tests setzen debug=True via conftest und ueberschreiben panel_url explizit.
if settings.panel_url == "http://localhost" and not settings.debug:
    raise RuntimeError(
        "CRITICAL: MSM_PANEL_URL is the 'http://localhost' default in production. "
        "Set it to your HTTPS panel URL via .env or env (prefixed MSM_). "
        "The install.sh script writes it automatically on first install. "
        "Default would break OAuth redirect_uri, email links and CORS."
    )


def get_cors_origins() -> list[str]:
    """Explizite CORS allowlist: panel_url + MSM_CORS_ALLOWED_ORIGINS + Dev-Defaults.

    Kein Wildcard. Credentials (Cookies) erfordern exakte Origins.
    """
    origins: list[str] = []
    panel = (settings.panel_url or "").rstrip("/")
    if panel:
        origins.append(panel)

    extra = (settings.cors_allowed_origins or "").strip()
    if extra:
        for part in extra.split(","):
            o = part.strip().rstrip("/")
            if o:
                origins.append(o)

    if settings.debug:
        for dev in (
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost",
            "http://127.0.0.1",
        ):
            origins.append(dev)

    # Deduplizieren, Reihenfolge behalten
    seen: set[str] = set()
    result: list[str] = []
    for o in origins:
        if o not in seen:
            seen.add(o)
            result.append(o)
    return result


def get_effective_cookie_domain() -> str:
    """Return the cookie domain to use for OAuth state cookie.

    If MSM_COOKIE_DOMAIN is explicitly set in .env / env, use it (override).
    Otherwise derive from MSM_PANEL_URL using the same parent-domain logic
    that install.sh used for the DOMAIN at first install (or on reinstall "keep").

    Self-hosted autonomy: MSM_PANEL_URL (written by install.sh) is the single
    source of truth. No extra variables needed for the common case.

    Special cases:
    - localhost / 127.0.0.1 (any port): return "" → no Domain attr (host-only cookie)
    - ports are always stripped (Domain= must not contain :port)
    """
    explicit = getattr(settings, "cookie_domain", None)
    if explicit:
        return explicit

    panel_url: str = getattr(settings, "panel_url", "") or ""
    if not panel_url:
        return ""

    # robust host extraction (strip scheme, path, port)
    if "://" in panel_url:
        host = panel_url.split("://", 1)[1].split("/", 1)[0]
    else:
        host = panel_url.split("/", 1)[0]
    host = host.split(":", 1)[0].strip().lower()

    if not host:
        return ""

    # loopback / local dev: never set Domain (browsers + TestClient are strict;
    # host-only cookie is correct and makes res.cookies visible in tests)
    if host in ("localhost", "127.0.0.1", "::1"):
        return ""

    # parent domain logic (mirrors original install.sh derivation):
    # "msm.mauntingstudios.de" → ".mauntingstudios.de"
    # "app.example.com" → ".example.com"
    # "example.com" → ".example.com"
    if host.count(".") >= 2:
        return "." + host.split(".", 1)[1]
    return "." + host
