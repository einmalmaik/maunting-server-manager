"""Secret-minimierte API-Vertraege fuer AI-Provider."""

from datetime import datetime

from pydantic import BaseModel, Field, SecretStr


class AiProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=8, max_length=1024)
    default_model: str = Field(min_length=1, max_length=256)
    enabled: bool = True
    requires_api_key: bool = True
    allow_private_network: bool = False
    operator_api_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)


class AiProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=8, max_length=1024)
    default_model: str | None = Field(default=None, min_length=1, max_length=256)
    enabled: bool | None = None
    requires_api_key: bool | None = None
    allow_private_network: bool | None = None
    operator_api_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    clear_operator_api_key: bool = False


class AiProviderResponse(BaseModel):
    id: int
    name: str
    base_url: str
    default_model: str
    enabled: bool
    requires_api_key: bool
    allow_private_network: bool
    operator_key_configured: bool
    operator_key_hint: str | None
    updated_at: datetime


class AiProviderAvailableResponse(BaseModel):
    id: int
    name: str
    default_model: str
    requires_api_key: bool
    user_key_configured: bool
    operator_key_available: bool
    available: bool


class AiUserCredentialUpdate(BaseModel):
    api_key: SecretStr = Field(min_length=1, max_length=4096)


class AiUserCredentialResponse(BaseModel):
    provider_id: int
    configured: bool
    key_hint: str | None
