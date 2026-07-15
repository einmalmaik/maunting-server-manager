from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from services.tls_pinning import normalize_fingerprint


class NodeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    host: str = Field(..., min_length=1, max_length=255)
    # Plaintext agent token — encrypted before DB store; never returned in responses
    auth_token: str = Field(..., min_length=16, max_length=512)
    # SHA-256 cert fingerprint (hex). Required for remote https:// agents.
    tls_fingerprint: str | None = Field(default=None, max_length=128)

    @field_validator("tls_fingerprint", mode="before")
    @classmethod
    def _norm_fp(cls, v: object) -> str | None:
        if v is None or v == "":
            return None
        fp = normalize_fingerprint(str(v))
        if fp and (len(fp) != 64 or any(c not in "0123456789abcdef" for c in fp)):
            raise ValueError("tls_fingerprint must be SHA-256 hex (64 chars)")
        return fp or None


class NodeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    auth_token: str | None = Field(default=None, min_length=16, max_length=512)
    tls_fingerprint: str | None = Field(default=None, max_length=128)

    @field_validator("tls_fingerprint", mode="before")
    @classmethod
    def _norm_fp(cls, v: object) -> str | None:
        if v is None:
            return None
        if v == "":
            return None
        fp = normalize_fingerprint(str(v))
        if fp and (len(fp) != 64 or any(c not in "0123456789abcdef" for c in fp)):
            raise ValueError("tls_fingerprint must be SHA-256 hex (64 chars)")
        return fp or None


class NodeOut(BaseModel):
    id: int
    name: str
    host: str
    is_local: bool
    status: str
    tls_fingerprint: str | None = None
    cpu_total: float | None = None
    ram_total: int | None = None
    disk_total: int | None = None
    last_heartbeat: datetime | None = None
    server_count: int = 0
    # Optional live metrics from agent (GET /api/nodes/{id})
    metrics: dict | None = None

    class Config:
        from_attributes = True
