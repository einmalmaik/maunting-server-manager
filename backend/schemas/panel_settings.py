from pydantic import BaseModel


class PanelSettingsResponse(BaseModel):
    panel_url: str = ""
    imprint_enabled: bool = False
    imprint_url: str = ""
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
    steam_api_source: str = "none"  # "env" | "panel" | "none"
    steam_account_username: str = ""
    steam_account_configured: bool = False
    github_token_configured: bool = False
    github_token_source: str = "none"  # "env" | "panel" | "none"
    time_format: str = "24h"
    support_widget_enabled: bool = False
    support_widget_mode: str = "singra"
    support_widget_crisp_website_id: str = ""
    support_widget_tawk_property_id: str = ""
    support_widget_tawk_widget_id: str = ""
    support_widget_custom_snippet: str = ""
    singra_widget_install_configured: bool = False
    singra_widget_install_masked: str = ""
    singra_widget_install_source: str = "none"
    singra_webhook_secret_configured: bool = False
    singra_webhook_secret_source: str = "none"


class PanelSettingsUpdate(BaseModel):
    panel_url: str | None = None
    imprint_enabled: bool | None = None
    imprint_url: str | None = None
    smtp_host: str | None = None
    smtp_port: str | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_tls: str | None = None
    resend_api_key: str | None = None
    default_language: str | None = None
    time_format: str | None = None
    support_widget_enabled: bool | None = None
    support_widget_mode: str | None = None
    support_widget_crisp_website_id: str | None = None
    support_widget_tawk_property_id: str | None = None
    support_widget_tawk_widget_id: str | None = None
    support_widget_custom_snippet: str | None = None


class TestEmailRequest(BaseModel):
    to: str


class ResendKeyRequest(BaseModel):
    resend_api_key: str


class SteamApiKeyRequest(BaseModel):
    steam_api_key: str


class SteamAccountRequest(BaseModel):
    username: str
    password: str


class GitHubTokenRequest(BaseModel):
    """GitHub Personal Access Token (klassisch mit ``repo``-Scope oder
    fine-grained mit ``Contents: read``) für private Repos in
    ``source.type=github``-Blueprints.
    """

    github_token: str


class GitHubTokenStatus(BaseModel):
    configured: bool
    source: str  # "env" | "panel" | "none"


class SingraWidgetInstallIdRequest(BaseModel):
    install_id: str


class SingraWebhookSecretRequest(BaseModel):
    webhook_secret: str
