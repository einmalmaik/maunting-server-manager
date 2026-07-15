"""Agent configuration from environment variables.

No secrets are logged. The agent holds only a static bearer token for
panel→agent auth; it never performs DIS/crypto operations.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MSM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Auth — required in production; empty only for test fixtures that inject env.
    agent_token: str = ""

    # Bind
    agent_host: str = "127.0.0.1"
    agent_port: int = 9000

    # Filesystem root for all server data (path-traversal boundary)
    servers_dir: str = "/opt/msm/servers"

    # Docker — empty means rootless default unix:///run/user/UID/docker.sock
    docker_host: str = ""

    # Misc
    agent_log_level: str = "INFO"
    agent_version: str = "1.0.0"

    # Container name prefix — only containers with this prefix are manageable
    container_name_prefix: str = "msm-srv-"

    # Defaults for container stop grace period (seconds)
    default_stop_timeout: int = 30

    # File limits
    max_upload_size: int = 100 * 1024 * 1024  # 100 MB
    max_read_size: int = 5 * 1024 * 1024  # 5 MB text reads

    def servers_path(self) -> Path:
        return Path(self.servers_dir).resolve(strict=False)


settings = Settings()
