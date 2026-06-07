from pathlib import Path

from pydantic_settings import BaseSettings


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

    # Logo — absolute URL used in email templates.
    # Falls back to panel_url + /logo.png when empty.
    logo_url: str = ""

    # Redis (fuer verteiltes Rate-Limiting via slowapi)
    redis_url: str = ""

    # Server-Verzeichnis (Install-Dir pro Server)
    # Produktion: /opt/msm/servers  |  Dev: ./servers
    servers_dir: str = "/opt/msm/servers"

    # Rootless Docker. Produktion: unix:///run/user/<msm_uid>/docker.sock
    # Leer = docker_service berechnet den Rootless-Default fuer den laufenden User.
    docker_host: str = ""

    # Blueprint-Verzeichnis (Community-Imports, getrennt von Repo-Code).
    # Produktion: /opt/msm/blueprints/community  |  Dev/Test ggf. via MSM_BLUEPRINTS_DIR
    blueprints_dir: str = "/opt/msm/blueprints/community"

    # ── Backup-Storage (Schritt 1: nur local; weitere Provider folgen) ──
    # Werte: "local" (default) | spaeter: "s3" | "sftp" | "dropbox" | "gcs" | "azure"
    backup_provider: str = "local"
    # Lokaler Backup-Root (heutiges Verhalten, /opt/msm/backups)
    backup_local_dir: str = "/opt/msm/backups"
    # Master-Key fuer Client-seitige AES-256-GCM Verschluesselung der Backups.
    # Wird beim ersten Cloud-Enable vom Installer generiert (base64-32-Bytes).
    # Leer = Backups werden UNVERSCHLUESSELT geschrieben (nur sinnvoll fuer local-Provider).
    backup_encryption_key: str = ""

    # ── S3-Provider (Schritt 2) ──
    # S3-kompatibel: AWS S3, Hetzner S3, Cloudflare R2, Backblaze B2,
    # MinIO, Wasabi, DigitalOcean Spaces (mit S3-Endpoint).
    backup_s3_bucket: str = ""
    backup_s3_region: str = "us-east-1"  # AWS-Default
    backup_s3_endpoint: str = ""  # leer = AWS-Default; Hetzner/R2/MinIO: spezifischer Endpoint
    backup_s3_access_key: str = ""
    backup_s3_secret_key: str = ""

    # ── SFTP-Provider (Schritt 3) ──
    # Hetzner Storage Box + jeder generische SFTP-Server. Auth v1: Passwort
    # only (SSH-Key spaeter moeglich, paramiko unterstuetzt beides).
    backup_sftp_host: str = ""
    backup_sftp_port: int = 22
    backup_sftp_user: str = ""
    backup_sftp_password: str = ""
    backup_sftp_path: str = "/msm-backups"  # absoluter Remote-Pfad, wird auto-mkdir-p

    # ── Dropbox-Provider (Schritt 4) ──
    # Auth via App-Key + App-Secret + manuell generierter Refresh-Token
    # (Standard server-zu-server, OAuth-Flow einmalig in Dropbox-App-Konsole).
    # Refresh-Token laeuft nicht ab und wird vom SDK fuer Auto-Refresh genutzt.
    backup_dropbox_app_key: str = ""
    backup_dropbox_app_secret: str = ""
    backup_dropbox_refresh_token: str = ""
    backup_dropbox_path: str = "/msm-backups"  # absoluter Pfad mit fuehrendem /

    # ── GCS-Provider (Schritt 5) ──
    # Auth via Service-Account-JSON-Datei. Pfad in .env, Datei vom User
    # angelegt mit chmod 600. Service-Account braucht
    # ``roles/storage.objectAdmin`` auf den Bucket.
    backup_gcs_bucket: str = ""
    backup_gcs_sa_file: str = ""  # z. B. /opt/msm/secrets/gcs-sa.json
    backup_gcs_path_prefix: str = "msm-backups"  # logischer Prefix im Bucket

    # ── Azure-Provider (Schritt 6) ──
    # Auth via Connection-String (kein Azure-AD-Setup — simpelster
    # Self-Hosted-Pfad). Connection-String liegt mit chmod 600 in .env.
    # Container wird auto-erstellt beim ersten Upload wenn noetig.
    backup_azure_account: str = ""  # Storage-Account-Name (auch im Conn-String)
    backup_azure_connection_string: str = ""  # z. B. DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net
    backup_azure_container: str = "msm-backups"  # Default-Container-Name
    backup_azure_path_prefix: str = ""  # logischer Prefix im Container (default: keiner)

    # Steam — SteamCMD läuft in einem ephemeren Container (cm2network/steamcmd:root per Default
    # als dediziertes Tool-Image mit pre-installed binary). steamcmd_path bleibt nur für Backward-Compat-Tests.
    steamcmd_path: str = "/usr/games/steamcmd"
    steam_api_key: str = ""

    # Auto-Update (GitHub Releases)
    github_owner: str = "einmalmaik"
    github_repo: str = "maunting-server-manager"
    auto_update: bool = False  # true = systemd-Timer installiert Updates automatisch
    auto_update_interval_hours: int = 24  # Prüfintervall

    # ── Backup-Migration Pending-Flags (Schritt 8 install.sh, gelesen in 9.2/9.4) ──
    # install.sh setzt diese Flags, wenn der User den Backup-Provider wechselt.
    # Der main.py lifespan-Hook liest sie beim Startup:
    # - pending_auto_migration=1: local->Cloud Wechsel, alte lokale Backups
    #   hochladen.
    # - pending_cross_cloud_migration=1: Cloud A -> Cloud B Wechsel, alte
    #   Cloud-A-Backups in neuen Provider kopieren.
    # Beide triggern einen Reset von .msm/state.json::cloud_migration_done,
    # damit der Auto-Migration-Hook (Schritt 9.2) laeuft.
    pending_auto_migration: bool = False
    pending_cross_cloud_migration: bool = False
    cross_cloud_target: str = ""  # Ziel-Provider bei Cross-Cloud (z.B. "gcs")

    class Config:
        env_prefix = "MSM_"
        env_file = ".env"
        env_file_encoding = "utf-8"


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
