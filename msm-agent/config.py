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

    # Guardian operational metadata is deliberately outside workload mounts.
    guardian_state_dir: str = "/var/lib/msm-agent/guardian"
    guardian_loop_interval_seconds: float = 5.0

    # Docker — empty means rootless default unix:///run/user/UID/docker.sock
    docker_host: str = ""

    # Misc
    agent_log_level: str = "INFO"
    agent_version: str = "1.0.0"

    # TLS (self-signed). When both set, uvicorn serves HTTPS.
    # Panel pins cert SHA-256 via node.tls_fingerprint.
    tls_certfile: str = ""
    tls_keyfile: str = ""

    # Container name prefix — only game containers with this prefix are manageable
    # (managed postgres uses a fixed name below, separate from this prefix)
    container_name_prefix: str = "msm-srv-"

    # Defaults for container stop grace period (seconds)
    default_stop_timeout: int = 30

    # File limits
    max_upload_size: int = 100 * 1024 * 1024  # 100 MB
    max_read_size: int = 5 * 1024 * 1024  # 5 MB text reads

    # Managed Postgres (Phase 7) — per-node container; panel passes passwords in RAM only
    managed_postgres_image: str = "postgres:17-alpine"
    managed_postgres_container_name: str = "msm-postgres"
    managed_postgres_network: str = "msm-internal"
    managed_postgres_host: str = "127.0.0.1"
    managed_postgres_port: int = 15432
    managed_postgres_data_dir: str = "/opt/msm/postgres"
    managed_postgres_statement_timeout_ms: int = 5000
    managed_postgres_row_limit: int = 500
    # Trusted extensions (must match panel allowlist for owner CREATE EXTENSION)
    trusted_postgres_extensions: str = (
        "pgcrypto,uuid-ossp,citext,btree_gin,btree_gist,fuzzystrmatch,"
        "hstore,pg_trgm,tablefunc,unaccent,isn,lo,ltree,tcn"
    )

    def servers_path(self) -> Path:
        return Path(self.servers_dir).resolve(strict=False)

    def guardian_path(self) -> Path:
        return Path(self.guardian_state_dir).resolve(strict=False)

    def trusted_extensions_set(self) -> set[str]:
        return {
            part.strip().lower()
            for part in (self.trusted_postgres_extensions or "").split(",")
            if part.strip()
        }


settings = Settings()
