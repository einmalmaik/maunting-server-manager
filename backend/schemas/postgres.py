from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class PostgresOneTimeCredential(BaseModel):
    database_id: int | None = None
    database_name: str
    username: str
    password: str
    host: str
    port: int
    is_power_user: bool = False


class PostgresDatabaseResponse(BaseModel):
    id: int
    name: str
    owner_role: str
    # True = elevated owner credentials for this database only (not cluster SUPERUSER).
    is_power_user: bool = False
    power_credentials_issued_at: datetime | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class PostgresUserResponse(BaseModel):
    id: int
    username: str
    password_mask: str
    created_at: datetime
    last_rotated_at: datetime | None = None

    class Config:
        from_attributes = True


class PostgresResourcesResponse(BaseModel):
    databases: list[PostgresDatabaseResponse]
    users: list[PostgresUserResponse]


class PostgresBootstrapRequest(BaseModel):
    database_count: int = Field(1, ge=1, le=20)


class PostgresCreateDatabaseRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=63)


class PostgresCreateUserRequest(BaseModel):
    database_id: int
    username: str | None = Field(None, min_length=1, max_length=63)


class PostgresConfirmRequest(BaseModel):
    confirm_name: str = Field(..., min_length=1, max_length=128)


class PostgresDatabaseRequest(BaseModel):
    database_id: int


class PostgresTableRequest(BaseModel):
    database_id: int
    schema_name: str = Field("public", min_length=1, max_length=63)
    table_name: str = Field(..., min_length=1, max_length=63)


class PostgresRowsRequest(PostgresTableRequest):
    limit: int = Field(500, ge=1, le=500)
    offset: int = Field(0, ge=0)
    search: str | None = Field(None, max_length=128)


class PostgresUpdateRowRequest(BaseModel):
    database_id: int | None = None
    schema_name: str = Field("public", min_length=1, max_length=63)
    table_name: str = Field(..., min_length=1, max_length=63)
    key_conditions: dict[str, Any] = Field(..., min_length=1)
    updates: dict[str, Any] = Field(..., min_length=1)


class PostgresDeleteRowsRequest(BaseModel):
    database_id: int | None = None
    schema_name: str = Field("public", min_length=1, max_length=63)
    table_name: str = Field(..., min_length=1, max_length=63)
    row_conditions: list[dict[str, Any]] = Field(..., min_length=1)


class PostgresInsertRowRequest(BaseModel):
    database_id: int | None = None
    schema_name: str = Field("public", min_length=1, max_length=63)
    table_name: str = Field(..., min_length=1, max_length=63)
    row_data: dict[str, Any] = Field(..., min_length=1)


class PostgresSqlRequest(BaseModel):
    database_id: int
    sql: str = Field(..., min_length=1, max_length=20000)
    limit: int = Field(500, ge=1, le=500)


class PostgresRowsResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    limit: int | None = None
    offset: int | None = None
    row_count: int | None = None
    status: str | None = None


class PostgresTableListItem(BaseModel):
    schema: str
    name: str
    row_estimate: int | None = None
    size_bytes: int | None = None


class PostgresDatabaseStats(BaseModel):
    status: str
    latency_ms: int | None = None
    size_bytes: int | None = None
    table_count: int = 0
    active_connections: int | None = None
    max_connections: int | None = None
    database_name: str
    engine: str = "PostgreSQL"


class PostgresColumnInfo(BaseModel):
    name: str
    data_type: str
    nullable: bool
    default: str | None = None
    primary_key: bool = False


class PostgresIndexInfo(BaseModel):
    name: str
    definition: str


class PostgresForeignKeyInfo(BaseModel):
    name: str
    column_name: str
    foreign_table: str
    foreign_column: str


class PostgresTableInfo(BaseModel):
    schema: str
    name: str
    columns: list[PostgresColumnInfo]
    indexes: list[PostgresIndexInfo]
    foreign_keys: list[PostgresForeignKeyInfo]
    size_bytes: int | None = None
    row_estimate: int | None = None


class PostgresSqlStatementResult(BaseModel):
    statement: str
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    row_count: int | None = None
    status: str | None = None
    error: str | None = None
    duration_ms: int | None = None


class PostgresRotatePasswordResponse(BaseModel):
    username: str
    password: str
    host: str
    port: int


class PostgresSqlResponse(BaseModel):
    statements: list[PostgresSqlStatementResult]
    total_duration_ms: int
    statement_timeout_ms: int


class PostgresPowerUserResponse(BaseModel):
    username: str
    password: str
    host: str
    port: int
    database_name: str


class PostgresPowerUserDemoteRequest(BaseModel):
    database_id: int = Field(..., ge=1)
    username: str = Field(..., min_length=1, max_length=63)
    confirm_name: str = Field(..., min_length=1, max_length=63)


class PostgresDumpResponse(BaseModel):
    """Metadata zu einem pg_dump-Lauf.

    Das eigentliche SQL wird als ``text/plain`` (Stream) zurueckgegeben,
    nicht als JSON. Dies ist nur die Status-Antwort.
    """
    database_names: list[str]
    byte_size: int
    sha256: str
    duration_ms: int


# ── Verbindungs-Hub ────────────────────────────────────────────────────────


class PostgresHubUser(BaseModel):
    id: int
    username: str
    # False bei Nutzern von vor 09/2026: nie gespeichert, erst Rotieren hilft.
    password_stored: bool


class PostgresHubDatabase(BaseModel):
    id: int
    name: str
    owner_role: str
    # Owner-Zugang abrufbar: bei eigener Instanz immer, sonst nach Power-User.
    owner_revealable: bool
    users: list[PostgresHubUser]


class PostgresHubEndpoint(BaseModel):
    host: str
    port: int


class PostgresHubExternal(PostgresHubEndpoint):
    reachable: bool
    # Warum nicht: "loopback" (Port liegt auf 127.0.0.1) oder "no_networks"
    # (kein erlaubtes Netz in pg_hba.conf). None, wenn erreichbar.
    blocked_by: Literal["loopback", "no_networks"] | None = None
    ssl_required: bool
    allowed_cidrs: list[str]


class PostgresConnectionInfo(BaseModel):
    kind: str  # "shared" | "dedicated"
    internal: PostgresHubEndpoint
    external: PostgresHubExternal | None = None
    ssl_certificate: str | None = None
    bootstrap_pending: bool = False
    databases: list[PostgresHubDatabase]


class PostgresRevealRequest(BaseModel):
    database_id: int
    # None: Owner der Datenbank.
    user_id: int | None = None


class PostgresRevealResponse(BaseModel):
    username: str
    password: str


class PostgresInstanceNetworkRequest(BaseModel):
    allowed_cidrs: list[str] = Field(default_factory=list, max_length=50)
    ssl_required: bool = True

