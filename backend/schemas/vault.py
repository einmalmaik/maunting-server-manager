from __future__ import annotations

from datetime import datetime
import re
from typing import List
from pydantic import BaseModel, Field, field_validator


HEX_64_REGEX = re.compile(r"^[0-9a-fA-F]{64}$")


class VaultMutation(BaseModel):
    id: str = Field(..., min_length=1, max_length=64, description="Eindeutige ID des Eintrags (Client-seitig generiert)")
    ciphertext: str = Field(..., max_length=1048576, description="Vollstaendig verschluesselter AES-GCM Ciphertext-Envelope (sv-vault-v1:)")
    revision: int = Field(..., ge=0, description="Lokale Revisionsnummer")
    is_deleted: bool = Field(default=False, description="Tombstone-Flag fuer Loeschungen")


class VaultSyncRequest(BaseModel):
    bucket_id: str = Field(..., min_length=64, max_length=64, description="Blinde 64-Hex Bucket-ID, abgeleitet aus dem Client-Master-Secret")
    since_revision: int = Field(default=0, ge=0, description="Revisions-Wasserzeichen des Clients")
    mutations: List[VaultMutation] = Field(default_factory=list, description="Neue oder aktualisierte verschluesselte Eintraege")

    @field_validator("bucket_id")
    @classmethod
    def validate_bucket_id(cls, v: str) -> str:
        if not HEX_64_REGEX.match(v):
            raise ValueError("bucket_id must be a 64-character hex string")
        return v.lower()


class VaultEntryOut(BaseModel):
    id: str
    ciphertext: str
    revision: int
    is_deleted: bool
    updated_at: datetime


class VaultSyncResponse(BaseModel):
    server_revision: int
    entries: List[VaultEntryOut]


class VaultNodeAssignment(BaseModel):
    node_id: str | None = Field(None, description="ID des dedizierten Nodes fuer den Passwort-Manager oder None fuer zentral")
    assigned_node_name: str | None = None
    is_multi_node_active: bool = False
    migrated_entries: int = 0


class VaultHintSetRequest(BaseModel):
    hint: str = Field(..., min_length=1, max_length=512, description="Passwort-Hinweis fuer das Master-Passwort")


class VaultHintStatusResponse(BaseModel):
    has_hint: bool
    last_requested_at: datetime | None = None
    can_request: bool = True
    cooldown_seconds_remaining: int = 0


class VaultSaltResponse(BaseModel):
    kdf_salt: str | None = None
    bucket_id: str | None = None
    has_vault: bool = False


class VaultSaltSetRequest(BaseModel):
    kdf_salt: str = Field(..., min_length=16, max_length=128, description="Base64- oder Hex-kodierter KDF-Salt")
    bucket_id: str = Field(..., min_length=64, max_length=64, description="64-Hex Bucket-ID")

    @field_validator("bucket_id")
    @classmethod
    def validate_bucket_id(cls, v: str) -> str:
        if not HEX_64_REGEX.match(v):
            raise ValueError("bucket_id must be a 64-character hex string")
        return v.lower()


