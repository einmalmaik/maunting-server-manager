from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, EmailStr


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(..., min_length=8)


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    is_active: bool | None = None
    two_factor_enabled: bool | None = None
    email_notifications: bool | None = None


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_owner: bool
    is_active: bool
    email_verified: bool
    two_factor_enabled: bool
    email_notifications: bool
    role_id: int | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class OwnerEmailConfig(BaseModel):
    # Der anonyme First-Run erlaubt bewusst keinen frei waehlbaren SMTP-Host:
    # Resend hat einen festen Ziel-Endpunkt und oeffnet damit keinen SSRF-Pfad.
    provider: Literal["resend"]
    from_address: EmailStr
    resend_api_key: str = Field(..., min_length=8, max_length=512)


class OwnerSetupRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(..., min_length=8)
    email_config: OwnerEmailConfig | None = None


class SetupVerifyRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6, pattern=r'^\d{6}$')


class AdminUserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(..., min_length=8)
    is_owner: bool = False
    auto_verify: bool = False
