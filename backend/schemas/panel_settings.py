from pydantic import BaseModel


class PanelSettingsResponse(BaseModel):
    panel_url: str = ""
    smtp_host: str = ""
    smtp_port: str = "587"
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: str = "true"
    resend_api_key: str = ""
    default_language: str = "de"
    email_configured: bool = False
    email_provider: str = "none"
    steam_api_key: str = ""
    steam_api_configured: bool = False


class PanelSettingsUpdate(BaseModel):
    panel_url: str | None = None
    smtp_host: str | None = None
    smtp_port: str | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_tls: str | None = None
    resend_api_key: str | None = None
    default_language: str | None = None


class TestEmailRequest(BaseModel):
    to: str


class ResendKeyRequest(BaseModel):
    resend_api_key: str


class SteamApiKeyRequest(BaseModel):
    steam_api_key: str
