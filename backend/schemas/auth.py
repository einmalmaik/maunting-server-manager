from pydantic import BaseModel, Field, field_validator

from schemas.passkey import PasskeyNachweis


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
    # Zwischenschein aus dem ersten Schritt (requires_2fa) — ersetzt im zweiten
    # Schritt das Captcha-Token, das nur einmal gilt.
    login_challenge: str | None = Field(None, max_length=128)
    # Die unterschriebene Antwort des Passkeys. Bis 09/2026 stand hier
    # `passkey_verified: bool` — ein Feld, das jeder selbst setzen konnte.
    passkey: PasskeyNachweis | None = None


class LoginVerifyRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    otp_code: str | None = Field(None, pattern=r"^(\d{6}|[A-Z0-9]{4}-[A-Z0-9]{4})$")
    native_client: bool = False
    # Zwischenschein aus dem ersten Schritt (requires_2fa) — ersetzt im zweiten
    # Schritt das Captcha-Token, das nur einmal gilt.
    login_challenge: str | None = Field(None, max_length=128)
    # Die unterschriebene Antwort des Passkeys. Bis 09/2026 stand hier
    # `passkey_verified: bool` — ein Feld, das jeder selbst setzen konnte.
    passkey: PasskeyNachweis | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    requires_2fa: bool = False
    requires_verification: bool = False
    email: str = ""
    # Nur bei requires_2fa: welcher Faktor gilt ("totp" / "passkey"), und für
    # Passkey-Konten gleich die Optionen für `navigator.credentials.get()`.
    two_factor_method: str | None = None
    passkey_options: dict | None = None
    login_challenge: str = ""
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
    passkey: PasskeyNachweis | None = None


class ChangeEmailRequest(BaseModel):
    email: str = Field(..., pattern=r"^[^@]+@[^@]+\.[^@]+$")
    current_password: str = Field(..., min_length=1)
    otp_code: str | None = Field(None, pattern=r"^(\d{6}|[A-Z0-9]{4}-[A-Z0-9]{4})$")
    passkey: PasskeyNachweis | None = None


class DataExportRequest(BaseModel):
    """Nachweis fuer den Datenexport: Passwort oder der eingerichtete zweite Faktor."""
    password: str = Field("", max_length=256)
    otp_code: str = Field("", max_length=16)
    passkey: PasskeyNachweis | None = None


class DeleteAccountRequest(BaseModel):
    # password is required only for accounts without OAuth links (local password accounts).
    # For social-only accounts (created/linked via OAuth) it is skipped.
    password: str | None = Field(None, min_length=1)
    # Always required: user must type the exact word "delete". Frontend prevents paste.
    confirmation: str = Field(..., min_length=5)
    otp_code: str | None = Field(None, pattern=r"^\d{6}$")
    passkey: PasskeyNachweis | None = None


    @field_validator("password", mode="before")
    @classmethod
    def _empty_password_to_none(cls, v: str | None) -> str | None:
        """Treat empty string (from forms) as None so social-only deletion works cleanly.
        Local accounts will always have a real value from the input.
        """
        if v == "" or v is None:
            return None
        return v
