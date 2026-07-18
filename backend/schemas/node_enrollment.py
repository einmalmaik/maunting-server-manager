from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from services.tls_pinning import normalize_fingerprint


class EnrollmentBegin(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    agent_token: str = Field(..., min_length=32, max_length=512)
    tls_fingerprint: str = Field(..., min_length=64, max_length=128)
    port: int = Field(default=9000, ge=1, le=65535)

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: object) -> str:
        name = str(value).strip()
        if not name:
            raise ValueError("name darf nicht leer sein")
        return name

    @field_validator("tls_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        fingerprint = normalize_fingerprint(value)
        if len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
            raise ValueError("tls_fingerprint must be SHA-256 hex")
        return fingerprint


class EnrollmentBeginOut(BaseModel):
    claim_secret: str | None = None
    display_code: str | None = None
    expires_at: datetime | None = None
    already_enrolled: bool = False
    node_id: int | None = None


class EnrollmentPendingOut(BaseModel):
    id: int
    display_code: str
    name: str
    host: str
    expires_at: datetime


class EnrollmentPollOut(BaseModel):
    status: str
    node_id: int | None = None
