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
    database_url: str
    bind_host: str
    bind_port: int
    command_timeout: int
    https_only: bool
    default_server_name: str

    @property
    def manager_workdir(self) -> Path:
        if not self.conan_manager_path:
            raise ValueError("CONAN_MANAGER_PATH is not configured.")
        return Path(self.conan_manager_path).resolve().parent

    def __repr__(self) -> str:
        return (
            f"Settings(app_name={self.app_name!r}, root_path={self.root_path!r}, "
            f"secret_key='*', session_cookie_name={self.session_cookie_name!r}, "
            f"conan_manager_path={self.conan_manager_path!r}, database_url='*', "
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

    return Settings(
        app_name="Conan Exiles Enhanced Server Panel",
        root_path=root_path,
        secret_key=secret_key,
        session_cookie_name=os.getenv("SESSION_COOKIE_NAME", "conan_panel_session").strip(),
        conan_manager_path=os.getenv("CONAN_MANAGER_PATH", "").strip(),
        database_url=os.getenv("DATABASE_URL", "").strip(),
        bind_host=os.getenv("PANEL_BIND_HOST", "127.0.0.1").strip(),
        bind_port=_safe_int(os.getenv("PANEL_BIND_PORT", "8710"), 8710),
        command_timeout=_safe_int(os.getenv("PANEL_COMMAND_TIMEOUT", "1800"), 1800),
        https_only=https_only,
        default_server_name=os.getenv("CONAN_DEFAULT_SERVER", "default").strip() or "default",
    )
