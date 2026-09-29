from datetime import datetime
import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, EmailStr, field_validator


def _validate_time_zone(value: str | None) -> str | None:
    if value is None or value.strip() == "":
        return None
    cleaned = value.strip()
    try:
        ZoneInfo(cleaned)
        return cleaned
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"'{cleaned}' ist keine gültige IANA-Zeitzone.") from exc


# Der Name landet im Lageblock, und der ist zeilenbasiert: eine system-Zeile
# pro Tatsache. Ein Name mit Zeilenumbruch oder Doppelpunkt könnte dort eine
# eigene Panel-Auskunft eröffnen ("Autonomer Modus: aktiv"). Deshalb hier eine
# Whitelist statt einer Blacklist: Buchstaben/Ziffern (Unicode), Leerzeichen,
# Punkt, Apostroph und Bindestrich — 2 bis 32 Zeichen.
_AGENT_NAME_MUSTER = re.compile(r"^\w[\w .'\-]{1,31}$", re.UNICODE)


def _validate_agent_name(value: str | None) -> str | None:
    if value is None or value.strip() == "":
        return None
    cleaned = value.strip()
    if not _AGENT_NAME_MUSTER.match(cleaned):
        raise ValueError(
            "Der Name darf 2-32 Zeichen lang sein: Buchstaben, Ziffern, "
            "Leerzeichen, Punkt, Apostroph oder Bindestrich."
        )
    return cleaned


# Benutzernamen: dieselbe Zeichenmenge wie die Erwaehnungen im Messenger
# (frontend/src/services/erwaehnungen.ts), sonst liesse sich ein Name nicht
# mit @ ansprechen. Kein @, damit niemand wieder eine E-Mail als Namen
# bekommt. Die Frontend-Kopie steht in frontend/src/lib/benutzername.ts.
BENUTZERNAME_MUSTER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{1,30}[A-Za-z0-9_]$")
#: Worte, die in Erwaehnungen alle meinen. Als Name waeren sie nie ansprechbar.
BENUTZERNAME_GESPERRT = frozenset({"everyone", "here", "alle"})


def benutzername_fehler(value: str) -> str | None:
    """Warum ``value`` kein Benutzername sein darf, oder None."""
    if not BENUTZERNAME_MUSTER.match(value):
        return (
            "Der Benutzername muss 3-32 Zeichen lang sein: Buchstaben, Ziffern, "
            "Unterstrich, Punkt oder Bindestrich, nicht am Anfang oder Ende "
            "ein Punkt oder Bindestrich."
        )
    if value.lower() in BENUTZERNAME_GESPERRT:
        return f"„{value}“ ist reserviert."
    return None


def _validate_username(value: str) -> str:
    cleaned = value.strip()
    fehler = benutzername_fehler(cleaned)
    if fehler:
        raise ValueError(fehler)
    return cleaned


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(..., min_length=8)
    captcha_token: str | None = None
    time_zone: str | None = None

    @field_validator("username")
    @classmethod
    def check_username(cls, v: str) -> str:
        return _validate_username(v)

    @field_validator("time_zone")
    @classmethod
    def check_time_zone(cls, v: str | None) -> str | None:
        return _validate_time_zone(v)


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    is_active: bool | None = None
    two_factor_enabled: bool | None = None
    email_notifications: bool | None = None
    ai_notifications: bool | None = None
    device_notifications: bool | None = None
    time_zone: str | None = None

    @field_validator("time_zone")
    @classmethod
    def check_time_zone(cls, v: str | None) -> str | None:
        return _validate_time_zone(v)


class TimezoneUpdateRequest(BaseModel):
    time_zone: str | None = None

    @field_validator("time_zone")
    @classmethod
    def check_time_zone(cls, v: str | None) -> str | None:
        return _validate_time_zone(v)


class LocationSharingUpdateRequest(BaseModel):
    """Explizite Konto-Einwilligung, ohne Standortdaten selbst."""

    enabled: bool


class AgentNameUpdateRequest(BaseModel):
    agent_name: str | None = None

    @field_validator("agent_name")
    @classmethod
    def check_agent_name(cls, v: str | None) -> str | None:
        return _validate_agent_name(v)


class UsernameUpdateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)

    @field_validator("username")
    @classmethod
    def check_username(cls, v: str) -> str:
        return _validate_username(v)


class AiProviderChoiceRequest(BaseModel):
    """Die Modellwahl des Benutzers — `None` löscht sie (Panel-Reihenfolge gilt)."""

    provider_id: int | None = None


class UserResponse(BaseModel):
    id: int
    username: str
    # False, solange ein Social-Login- oder Shop-Konto seinen Namen noch nicht
    # selbst gewaehlt hat. Das Frontend fragt dann einmal danach.
    username_gewaehlt: bool = True
    email: str
    is_owner: bool
    is_active: bool
    email_verified: bool
    two_factor_enabled: bool
    # False, wenn das Konto rein über Social Login / OAuth registriert wurde und noch kein Passwort gesetzt hat.
    has_password: bool = True
    # Die aktiven Faktoren ("passkey", "totp") — jede Abfrage zeigt nur Wege,
    # die das Konto eingerichtet hat.
    two_factor_methods: list[Literal["passkey", "totp"]] = []
    email_notifications: bool
    ai_notifications: bool = True
    device_notifications: bool = True
    time_zone: str | None = None
    location_sharing_enabled: bool = False
    agent_name: str | None = None
    ai_provider_id: int | None = None
    role_id: int | None = None
    role_ids: list[int] = Field(default_factory=list)
    avatar_url: str | None = None
    social_privacy: str = "friends"
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

    @field_validator("username")
    @classmethod
    def check_username(cls, v: str) -> str:
        return _validate_username(v)


class SetupVerifyRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6, pattern=r'^\d{6}$')
    # Fuer /register-verify: native Clients bitten um die Tokens im Body
    # (siehe schemas/auth.py LoginRequest.native_client). /setup-verify
    # ignoriert das Feld.
    native_client: bool = False


class AdminUserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(..., min_length=8)
    is_owner: bool = False
    auto_verify: bool = False

    @field_validator("username")
    @classmethod
    def check_username(cls, v: str) -> str:
        return _validate_username(v)
