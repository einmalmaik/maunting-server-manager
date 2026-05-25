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

    # Logo — absolute URL used in email templates.
    # Falls back to panel_url + /logo.svg when empty.
    logo_url: str = ""

    # Redis (fuer verteiltes Rate-Limiting via slowapi)
    redis_url: str = ""

    # Server-Verzeichnis (Install-Dir pro Server)
    # Produktion: /opt/msm/servers  |  Dev: ./servers
    servers_dir: str = "/opt/msm/servers"

    # Blueprint-Verzeichnis (Community-Imports, getrennt von Repo-Code).
    # Produktion: /opt/msm/blueprints/community  |  Dev/Test ggf. via MSM_BLUEPRINTS_DIR
    blueprints_dir: str = "/opt/msm/blueprints/community"

    # Steam — SteamCMD läuft in einem ephemeren Container (cm2network/steamcmd:root),
    # nicht mehr auf dem Host. steamcmd_path bleibt nur für Backward-Compat-Tests
    # (sollte nirgendwo im Code mehr verwendet werden).
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
