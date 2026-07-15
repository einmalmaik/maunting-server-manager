export interface PanelSettings {
  panel_url: string
  smtp_host: string
  smtp_port: string
  smtp_user: string
  smtp_password: string
  smtp_from: string
  smtp_tls: string
  resend_api_key: string
  default_language: string
  email_configured: boolean
  email_provider: string
  steam_api_key: string
  steam_api_configured: boolean
  steam_account_username: string
  steam_account_configured: boolean
  github_token_configured: boolean
  github_token_source: 'env' | 'panel' | 'none'
  time_format: '24h' | '12h'
  imprint_enabled: boolean
  imprint_url: string
  support_widget_enabled: boolean
  support_widget_mode: 'singra' | 'custom'
  support_widget_singra_id: string
  support_widget_custom_snippet: string
  support_widget_notify_email: string
  singra_webhook_secret_configured: boolean
  singra_webhook_secret_source: 'env' | 'panel' | 'none'
}

export const EMPTY_PANEL_SETTINGS: PanelSettings = {
  panel_url: '',
  smtp_host: '',
  smtp_port: '587',
  smtp_user: '',
  smtp_password: '',
  smtp_from: '',
  smtp_tls: 'true',
  resend_api_key: '',
  default_language: 'de',
  email_configured: false,
  email_provider: 'none',
  steam_api_key: '',
  steam_api_configured: false,
  steam_account_username: '',
  steam_account_configured: false,
  github_token_configured: false,
  github_token_source: 'none',
  time_format: '24h',
  imprint_enabled: false,
  imprint_url: '',
  support_widget_enabled: false,
  support_widget_mode: 'singra',
  support_widget_singra_id: '',
  support_widget_custom_snippet: '',
  support_widget_notify_email: '',
  singra_webhook_secret_configured: false,
  singra_webhook_secret_source: 'none',
}
