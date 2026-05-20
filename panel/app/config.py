from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


def _safe_int(value: str, default: int) -> int:
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer env value %r; using default %d.", value, default)
        return default


@dataclass(frozen=True)
class Settings:
    app_name: str
    root_path: str
    secret_key: str
    session_cookie_name: str
    conan_manager_path: str
    dayz_manager_path: str
    database_url: str
    bind_host: str
    bind_port: int
    command_timeout: int
    https_only: bool
    default_server_name: str
    # Email (SMTP or Resend)
    email_provider: str  # "smtp" | "resend" | "none"
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_tls: bool
    smtp_starttls: bool
    resend_api_key: str
    email_from: str
    email_from_name: str
    # Security
    password_reset_token_hours: int
    verification_token_hours: int

    @property
    def game_managers(self) -> dict[str, str]:
        m: dict[str, str] = {}
        if self.conan_manager_path:
            m["conan_exiles"] = self.conan_manager_path
        if self.dayz_manager_path:
            m["dayz"] = self.dayz_manager_path
        return m

    def manager_workdir(self, manager_path: str | None = None) -> Path:
        path = manager_path or self.conan_manager_path
        if not path:
            raise ValueError("Manager path is not configured.")
        return Path(path).resolve().parent

    def resolve_manager_path(self, game_id: str | None = None) -> str:
        game = game_id or "conan_exiles"
        path = self.game_managers.get(game)
        if not path:
            raise ValueError(f"No manager path configured for game: {game!r}")
        return path

    def __repr__(self) -> str:
        return (
            f"Settings(app_name={self.app_name!r}, root_path={self.root_path!r}, "
            f"secret_key='*', session_cookie_name={self.session_cookie_name!r}, "
            f"conan_manager_path={self.conan_manager_path!r}, dayz_manager_path={self.dayz_manager_path!r}, database_url='*', "
            f"bind_host={self.bind_host!r}, bind_port={self.bind_port!r}, "
            f"command_timeout={self.command_timeout!r}, https_only={self.https_only!r}, "
            f"default_server_name={self.default_server_name!r})"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    raw_base_path = os.getenv("PANEL_BASE_PATH", "/panel").strip()
    if not raw_base_path.strip("/"):
        # "/", "//", "", etc. all normalise to "/"
        root_path = "/"
    else:
        root_path = raw_base_path or "/panel"
        if not root_path.startswith("/"):
            root_path = f"/{root_path}"
        root_path = root_path.rstrip("/") or "/panel"

    secret_key = os.getenv("APP_SECRET_KEY", "").strip()
    if not secret_key:
        if app_env in ("production", "prod"):
            raise RuntimeError(
                "APP_SECRET_KEY must be set in production. "
                "Generate one with: openssl rand -hex 32"
            )
        logger.warning(
            "APP_SECRET_KEY is not set; using insecure default. Do NOT use in production."
        )
        secret_key = "change-me"

    raw_https_only = os.getenv("PANEL_HTTPS_ONLY", "").strip().lower()
    if app_env in ("production", "prod"):
        if raw_https_only == "":
            https_only = True
        else:
            https_only = raw_https_only == "true"
            if not https_only:
                logger.warning(
                    "PANEL_HTTPS_ONLY is explicitly disabled in production. "
                    "Session cookies will not use the Secure flag."
                )
    else:
        https_only = raw_https_only == "true"

    email_provider = os.getenv("EMAIL_PROVIDER", "none").strip().lower()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    resend_api_key = os.getenv("RESEND_API_KEY", "").strip()
    if app_env in ("production", "prod") and email_provider != "none":
        if email_provider == "smtp" and not smtp_password:
            logger.warning("EMAIL_PROVIDER is smtp but SMTP_PASSWORD is not set.")
        if email_provider == "resend" and not resend_api_key:
            logger.warning("EMAIL_PROVIDER is resend but RESEND_API_KEY is not set.")

    return Settings(
        app_name="Maunting Server Manager",
        root_path=root_path,
        secret_key=secret_key,
        session_cookie_name=os.getenv("SESSION_COOKIE_NAME", "maunting_panel_session").strip(),
        conan_manager_path=os.getenv("CONAN_MANAGER_PATH", "").strip(),
        dayz_manager_path=os.getenv("DAYZ_MANAGER_PATH", "").strip(),
        database_url=os.getenv("DATABASE_URL", "").strip(),
        bind_host=os.getenv("PANEL_BIND_HOST", "127.0.0.1").strip(),
        bind_port=_safe_int(os.getenv("PANEL_BIND_PORT", "8710"), 8710),
        command_timeout=_safe_int(os.getenv("PANEL_COMMAND_TIMEOUT", "1800"), 1800),
        https_only=https_only,
        default_server_name=os.getenv("CONAN_DEFAULT_SERVER", "default").strip() or "default",
        email_provider=email_provider,
        smtp_host=os.getenv("SMTP_HOST", "").strip(),
        smtp_port=_safe_int(os.getenv("SMTP_PORT", "587"), 587),
        smtp_user=os.getenv("SMTP_USER", "").strip(),
        smtp_password=smtp_password,
        smtp_from=os.getenv("SMTP_FROM", "").strip(),
        smtp_tls=os.getenv("SMTP_TLS", "").strip().lower() == "true",
        smtp_starttls=os.getenv("SMTP_STARTTLS", "true").strip().lower() == "true",
        resend_api_key=resend_api_key,
        email_from=os.getenv("EMAIL_FROM", "").strip(),
        email_from_name=os.getenv("EMAIL_FROM_NAME", "Maunting Server Manager").strip(),
        password_reset_token_hours=_safe_int(os.getenv("PASSWORD_RESET_TOKEN_HOURS", "24"), 24),
        verification_token_hours=_safe_int(os.getenv("VERIFICATION_TOKEN_HOURS", "24"), 24),
    )
