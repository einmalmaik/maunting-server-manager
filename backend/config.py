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

    # Steam — SteamCMD läuft in einem ephemeren Container (cm2network/steamcmd:root per Default
    # als dediziertes Tool-Image mit pre-installed binary). steamcmd_path bleibt nur für Backward-Compat-Tests.
    steamcmd_path: str = "/usr/games/steamcmd"
    steam_api_key: str = ""

    # Auto-Update (GitHub Releases)
    github_owner: str = "einmalmaik"
    github_repo: str = "maunting-server-manager"
    auto_update: bool = False  # true = systemd-Timer installiert Updates automatisch
    auto_update_interval_hours: int = 24  # Prüfintervall

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


def get_effective_cookie_domain() -> str:
    """Return the cookie domain to use for OAuth state cookie.

    If MSM_COOKIE_DOMAIN is explicitly set in .env / env, use it (override).
    Otherwise derive from MSM_PANEL_URL using the exact same logic as install.sh
    (so that install.sh / update.sh remain the single source of truth for the
    domain that was configured at first install or changed later).

    This keeps everything autonomous for self-hosted open-source installs and
    avoids needing a separate new variable in most cases.
    """
    explicit = getattr(settings, "cookie_domain", None)
    if explicit:
        return explicit

    panel_url: str = getattr(settings, "panel_url", "") or ""
    if not panel_url or panel_url == "http://localhost":
        return ""

    # strip protocol + path, keep host only (same as install.sh)
    if "://" in panel_url:
        host = panel_url.split("://", 1)[1].split("/", 1)[0]
    else:
        host = panel_url.split("/", 1)[0]

    if not host:
        return ""

    # exact logic copied from the derivation in install.sh:
    # if the host has a subdomain (at least two dots), take from the first dot onward
    if host.count(".") >= 2:
        return "." + host.split(".", 1)[1]
    return "." + host
