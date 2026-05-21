from datetime import datetime
from pydantic import BaseModel, Field, EmailStr


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(..., min_length=8)


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    is_active: bool | None = None
    two_factor_enabled: bool | None = None


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    is_owner: bool
    is_active: bool
    email_verified: bool
    two_factor_enabled: bool
    created_at: datetime

    class Config:
        from_attributes = True


class OwnerSetupRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(..., min_length=8)


class SetupVerifyRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6, pattern=r'^\d{6}$')


class AdminUserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(..., min_length=8)
    is_owner: bool = False
