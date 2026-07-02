from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PostgresOneTimeCredential(BaseModel):
    database_id: int | None = None
    database_name: str
    username: str
    password: str
    host: str
    port: int
    is_superuser: bool = False


class PostgresDatabaseResponse(BaseModel):
    id: int
    name: str
    owner_role: str
    is_superuser: bool = False
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


class PostgresCreateTableColumn(BaseModel):
    name: str = Field(..., min_length=1, max_length=63)
    type: str = Field(..., min_length=1, max_length=32)
    primary_key: bool = False
    not_null: bool = False


class PostgresCreateTableRequest(BaseModel):
    database_id: int
    schema_name: str = Field("public", min_length=1, max_length=63)
    table_name: str = Field(..., min_length=1, max_length=63)
    columns: list[PostgresCreateTableColumn] = Field(..., min_length=1, max_length=64)


class PostgresTableRequest(BaseModel):
    database_id: int
    schema_name: str = Field("public", min_length=1, max_length=63)
    table_name: str = Field(..., min_length=1, max_length=63)


class PostgresDropTableRequest(PostgresTableRequest):
    confirm_name: str = Field(..., min_length=1, max_length=63)


class PostgresRowsRequest(PostgresTableRequest):
    limit: int = Field(500, ge=1, le=500)
    offset: int = Field(0, ge=0)
    search: str | None = Field(None, max_length=128)


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


class PostgresExtensionInfo(BaseModel):
    name: str
    version: str | None = None
    trusted: bool = True


class PostgresExtensionRequest(BaseModel):
    database_id: int = Field(..., ge=1)
    name: str = Field(..., min_length=1, max_length=63)


class PostgresExtensionDropRequest(BaseModel):
    database_id: int = Field(..., ge=1)
    confirm_name: str = Field(..., min_length=1, max_length=63)


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


class PostgresDumpRequest(BaseModel):
    """Auswahl des Dump-Umfangs fuer ``pg_dump``.

    Default: ``scope=all_dbs`` erfasst alle DBs des Servers (Power-User sieht
    mehrere; Owner sieht eine). ``scope=database`` ist deprecated -- der Server
    hat genau einen Postgres-Container mit allen DBs drin, daher macht
    ``scope=all_dbs`` immer Sinn.
    """
    confirm_text: str | None = Field(
        None,
        max_length=128,
        description="Sicherheits-Bestaetigung -- muss mit Server-Namen uebereinstimmen wenn Dump loeschend wirkt.",
    )


class PostgresRestoreRequest(BaseModel):
    """Restore-Request fuer ``psql``-Restore aus hochgeladenem SQL-Dump.

    Verhalten: SQL wird IMMER in ALLE DBs des Servers geschrieben
    (alle existierenden Tabellen werden vorher gedroppt, damit ein sauberer
    Restore gelingt). Wenn der Server nur eine DB hat, ist der Effekt identisch.
    """
    sql: str = Field(..., min_length=1, max_length=200_000_000)
    confirm_text: str | None = Field(None, max_length=128)


class PostgresDumpResponse(BaseModel):
    """Metadata zu einem pg_dump-Lauf.

    Das eigentliche SQL wird als ``text/plain`` (Stream) zurueckgegeben,
    nicht als JSON. Dies ist nur die Status-Antwort.
    """
    database_names: list[str]
    byte_size: int
    sha256: str
    duration_ms: int
