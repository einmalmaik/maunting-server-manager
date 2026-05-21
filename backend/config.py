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

    # Steam
    steamcmd_path: str = "/usr/games/steamcmd"

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
