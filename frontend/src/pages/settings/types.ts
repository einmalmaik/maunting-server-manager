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
  curseforge_api_key: string
  curseforge_api_configured: boolean
  curseforge_api_source: 'env' | 'panel' | 'none'
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
  desktop_app_download_enabled: boolean
  calendar_enabled: boolean
  notes_enabled: boolean
  captcha_enabled: boolean
  captcha_provider: 'turnstile' | 'hcaptcha' | 'recaptcha' | 'none'
  captcha_site_key: string
  captcha_secret_key: string
  /** Login/Auth-Anfragen pro Minute pro IP (Default 10, Range 3–50) */
  rate_limit_auth: number
  /** Globale API-Anfragen pro Minute pro IP (Default 100, Range 50–1000) */
  rate_limit_global: number
}

/** Erlaubte Bereiche — spiegeln backend/services/rate_limit_settings.py */
export const RATE_LIMIT_AUTH_MIN = 3
export const RATE_LIMIT_AUTH_MAX = 50
export const RATE_LIMIT_AUTH_DEFAULT = 10
export const RATE_LIMIT_GLOBAL_MIN = 50
export const RATE_LIMIT_GLOBAL_MAX = 1000
export const RATE_LIMIT_GLOBAL_DEFAULT = 100

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
  curseforge_api_key: '',
  curseforge_api_configured: false,
  curseforge_api_source: 'none',
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
  desktop_app_download_enabled: true,
  calendar_enabled: true,
  notes_enabled: true,
  captcha_enabled: false,
  captcha_provider: 'none',
  captcha_site_key: '',
  captcha_secret_key: '',
  rate_limit_auth: RATE_LIMIT_AUTH_DEFAULT,
  rate_limit_global: RATE_LIMIT_GLOBAL_DEFAULT,
}