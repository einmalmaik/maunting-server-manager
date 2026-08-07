"""Schemas fuer benutzereigene Zugangsdaten und Server-Bindungen.

Kein Schema gibt jemals ein Geheimnis zurueck — nur `configured`, ein Label und
einen nicht umkehrbaren Hinweis auf die letzten Zeichen.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, SecretStr


class UserCredentialWrite(BaseModel):
    kind: str = Field(..., pattern=r"^(github_token|steam_account)$")
    label: str = Field(..., min_length=1, max_length=64)
    # Nur fuer steam_account relevant; bei github_token wird der Wert ignoriert.
    username: str | None = Field(None, max_length=256)
    secret: SecretStr = Field(..., min_length=1, max_length=4096)


class UserCredentialResponse(BaseModel):
    id: int
    kind: str
    label: str
    username: str | None
    secret_hint: str | None
    updated_at: datetime


class ServerCredentialBindingWrite(BaseModel):
    kind: str = Field(..., pattern=r"^(github_token|steam_account)$")
    # ``null`` loest die Bindung; der Server faellt dann auf die Panel-Ebene
    # zurueck, sofern der Betreiber das erlaubt.
    credential_id: int | None = Field(None, ge=1)


class ServerCredentialStatus(BaseModel):
    """Secret-freie Auskunft fuer die Serveroberflaeche."""

    kind: str
    required: bool
    source: str
    configured: bool
    credential_id: int | None
    label: str | None
    username: str | None
    hint: str | None


class PanelFallbackSetting(BaseModel):
    allow_panel_fallback: bool
