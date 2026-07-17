export type SupportWidgetProvider = 'singra' | 'crisp' | 'tawk' | 'custom'

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
  support_widget_mode: SupportWidgetProvider
  support_widget_crisp_website_id: string
  support_widget_tawk_property_id: string
  support_widget_tawk_widget_id: string
  support_widget_custom_snippet: string
  singra_widget_install_configured: boolean
  singra_widget_install_masked: string
  singra_widget_install_source: 'env' | 'panel' | 'none'
  singra_webhook_secret_configured: boolean
  singra_webhook_secret_source: 'env' | 'panel' | 'none'
  updates_automatic: boolean
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
  support_widget_crisp_website_id: '',
  support_widget_tawk_property_id: '',
  support_widget_tawk_widget_id: '',
  support_widget_custom_snippet: '',
  singra_widget_install_configured: false,
  singra_widget_install_masked: '',
  singra_widget_install_source: 'none',
  singra_webhook_secret_configured: false,
  singra_webhook_secret_source: 'none',
  updates_automatic: false,
}