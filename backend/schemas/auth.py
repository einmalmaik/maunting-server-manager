from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)
    otp_code: str | None = Field(None, pattern=r"^(\d{6}|[A-Z0-9]{4}-[A-Z0-9]{4})$")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    requires_2fa: bool = False
    requires_verification: bool = False
    email: str = ""


class ResendVerificationRequest(BaseModel):
    email: str


class PasswordResetRequest(BaseModel):
    email: str


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)
    otp_code: str | None = Field(None, pattern=r"^(\d{6}|[A-Z0-9]{4}-[A-Z0-9]{4})$")


class ChangeEmailRequest(BaseModel):
    email: str = Field(..., pattern=r"^[^@]+@[^@]+\.[^@]+$")
    otp_code: str | None = Field(None, pattern=r"^(\d{6}|[A-Z0-9]{4}-[A-Z0-9]{4})$")
