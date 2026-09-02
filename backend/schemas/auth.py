from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)
    otp_code: str | None = Field(None, pattern=r"^(\d{6}|[A-Z0-9]{4}-[A-Z0-9]{4})$")
    captcha_token: str | None = None
    # Native Clients (Smart-System-Desktop-App) können keine httponly-Cookies
    # verwalten und bitten hiermit um die Tokens im Response-Body. Das ist
    # keine Rechteerweiterung: der Aufrufer bekommt nur seine eigenen Tokens,
    # die er per Cookie ohnehin bekäme. Das Panel-Frontend setzt das Feld nie.
    native_client: bool = False


class LoginVerifyRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    otp_code: str | None = Field(None, pattern=r"^(\d{6}|[A-Z0-9]{4}-[A-Z0-9]{4})$")
    native_client: bool = False


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    requires_2fa: bool = False
    requires_verification: bool = False
    email: str = ""
    # Nur für native Clients gefüllt (native_client=True im Request); der
    # Browser-Flow bekommt weiterhin ausschliesslich Cookies und leere Strings.
    refresh_token: str = ""
    # Lebensdauer des Access-Tokens in Sekunden, damit der native Client die
    # Rotation planen kann, ohne die Serverkonfiguration zu kennen.
    expires_in: int = 0


class NativeRefreshRequest(BaseModel):
    """Refresh für native Clients: das Token kommt im Body statt im Cookie."""

    refresh_token: str = Field(..., min_length=8)


class LogoutRequest(BaseModel):
    """Logout für native Clients: das Refresh-Token kommt im Body statt im Cookie."""

    refresh_token: str | None = Field(None, min_length=8)


class RegistrationResponse(BaseModel):
    email: str
    requires_verification: bool = True


class ResendVerificationRequest(BaseModel):
    email: str


class PasswordResetRequest(BaseModel):
    email: str
    captcha_token: str | None = None


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)
    captcha_token: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)
    otp_code: str | None = Field(None, pattern=r"^(\d{6}|[A-Z0-9]{4}-[A-Z0-9]{4})$")


class ChangeEmailRequest(BaseModel):
    email: str = Field(..., pattern=r"^[^@]+@[^@]+\.[^@]+$")
    otp_code: str | None = Field(None, pattern=r"^(\d{6}|[A-Z0-9]{4}-[A-Z0-9]{4})$")


class DeleteAccountRequest(BaseModel):
    # password is required only for accounts without OAuth links (local password accounts).
    # For social-only accounts (created/linked via OAuth) it is skipped.
    password: str | None = Field(None, min_length=1)
    # Always required: user must type the exact word "delete". Frontend prevents paste.
    confirmation: str = Field(..., min_length=5)
    otp_code: str | None = Field(None, pattern=r"^\d{6}$")


    @field_validator("password", mode="before")
    @classmethod
    def _empty_password_to_none(cls, v: str | None) -> str | None:
        """Treat empty string (from forms) as None so social-only deletion works cleanly.
        Local accounts will always have a real value from the input.
        """
        if v == "" or v is None:
            return None
        return v
