import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Mail, Globe, Save, Send } from 'lucide-react'
import { api } from '@/api/client'

interface PanelSettings {
  panel_url: string
  smtp_host: string
  smtp_port: string
  smtp_user: string
  smtp_password: string
  smtp_from: string
  smtp_tls: string
  resend_api_key: string
  default_language: string
}

export function Settings() {
  const { t, i18n } = useTranslation()
  const [settings, setSettings] = useState<PanelSettings>({
    panel_url: '',
    smtp_host: '',
    smtp_port: '587',
    smtp_user: '',
    smtp_password: '',
    smtp_from: '',
    smtp_tls: 'true',
    resend_api_key: '',
    default_language: 'de',
  })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [savedMsg, setSavedMsg] = useState('')
  const [testEmail, setTestEmail] = useState('')
  const [sendingTest, setSendingTest] = useState(false)
  const [testMsg, setTestMsg] = useState('')

  const fetchSettings = async () => {
    try {
      const data = await api<PanelSettings>('/settings')
      setSettings(data)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchSettings()
  }, [])

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError('')
    setSavedMsg('')
    try {
      await api('/settings', {
        method: 'POST',
        body: JSON.stringify(settings),
      })
      setSavedMsg(t('settings.saved'))
      setTimeout(() => setSavedMsg(''), 3000)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const handleTestEmail = async () => {
    if (!testEmail) return
    setSendingTest(true)
    setTestMsg('')
    setError('')
    try {
      await api('/settings/test-email', {
        method: 'POST',
        body: JSON.stringify({ to: testEmail }),
      })
      setTestMsg(t('settings.testEmailSent'))
      setTimeout(() => setTestMsg(''), 3000)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setSendingTest(false)
    }
  }

  // Explicit provider toggle, independent of field contents
  const [provider, setProvider] = useState<'smtp' | 'resend'>('smtp')
  const [newResendKey, setNewResendKey] = useState('')
  const [savingResend, setSavingResend] = useState(false)
  const [resendMsg, setResendMsg] = useState('')

  useEffect(() => {
    // Derive initial provider from fetched data
    if (settings.resend_api_key) {
      setProvider('resend')
    } else {
      setProvider('smtp')
    }
  }, [settings.resend_api_key])

  const handleSaveResendKey = async () => {
    if (!newResendKey.trim()) return
    setSavingResend(true)
    setError('')
    setResendMsg('')
    try {
      await api('/settings/resend-key', {
        method: 'POST',
        body: JSON.stringify({ resend_api_key: newResendKey.trim() }),
      })
      setResendMsg('Resend API-Key gespeichert')
      setNewResendKey('')
      // Refresh settings to pick up the masked key status
      await fetchSettings()
      setTimeout(() => setResendMsg(''), 3000)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setSavingResend(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-headline text-headline-sm text-primary">{t('nav.settings')}</h1>
        <p className="font-body-md text-body-md text-on-surface-variant mt-1">
          {t('settings.subtitle')}
        </p>
      </div>

      {error && <div className="msm-alert-error text-sm">{error}</div>}
      {savedMsg && <div className="msm-alert-success text-sm">{savedMsg}</div>}

      <form onSubmit={handleSave} className="space-y-6">
        {/* Panel Config */}
        <div className="msm-card p-6">
          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
              <Globe className="w-5 h-5 text-secondary" />
            </div>
            <h2 className="font-headline text-headline-sm text-primary">{t('settings.panelConfig')}</h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('settings.panelUrl')}
              </label>
              <input
                type="url"
                value={settings.panel_url || window.location.origin}
                readOnly
                className="msm-input opacity-60 cursor-not-allowed"
              />
              <p className="font-body-md text-xs text-on-surface-variant mt-1.5">
                Wird automatisch aus der aktuellen Adresse ermittelt.
              </p>
            </div>
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('settings.defaultLanguage')}
              </label>
              <select
                value={settings.default_language}
                onChange={(e) => {
                  const lang = e.target.value
                  setSettings({ ...settings, default_language: lang })
                  i18n.changeLanguage(lang)
                }}
                className="msm-input"
              >
                <option value="de">Deutsch</option>
                <option value="en">English</option>
              </select>
            </div>
          </div>
        </div>

        {/* Email Config */}
        <div className="msm-card p-6">
          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
              <Mail className="w-5 h-5 text-secondary" />
            </div>
            <h2 className="font-headline text-headline-sm text-primary">{t('settings.emailConfig')}</h2>
          </div>

          {/* Provider toggle */}
          <div className="flex gap-2 mb-6">
            <button
              type="button"
              onClick={() => setProvider('smtp')}
              className={`flex-1 py-2 px-4 rounded-md text-sm font-medium transition-colors ${
                provider === 'smtp'
                  ? 'bg-secondary-container text-on-secondary-container'
                  : 'bg-surface-container-high text-on-surface-variant hover:bg-surface-container-highest'
              }`}
            >
              {t('settings.providerSmtp')}
            </button>
            <button
              type="button"
              onClick={() => setProvider('resend')}
              className={`flex-1 py-2 px-4 rounded-md text-sm font-medium transition-colors ${
                provider === 'resend'
                  ? 'bg-secondary-container text-on-secondary-container'
                  : 'bg-surface-container-high text-on-surface-variant hover:bg-surface-container-highest'
              }`}
            >
              {t('settings.providerResend')}
            </button>
          </div>

          {provider === 'smtp' ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('settings.smtpHost')}
                </label>
                <input
                  type="text"
                  value={settings.smtp_host}
                  onChange={(e) => setSettings({ ...settings, smtp_host: e.target.value })}
                  className="msm-input"
                  placeholder="smtp.example.com"
                />
              </div>
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('settings.smtpPort')}
                </label>
                <input
                  type="number"
                  value={settings.smtp_port}
                  onChange={(e) => setSettings({ ...settings, smtp_port: e.target.value })}
                  className="msm-input"
                  placeholder="587"
                />
              </div>
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('settings.smtpUser')}
                </label>
                <input
                  type="text"
                  value={settings.smtp_user}
                  onChange={(e) => setSettings({ ...settings, smtp_user: e.target.value })}
                  className="msm-input"
                  placeholder="user@example.com"
                />
              </div>
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('settings.smtpPassword')}
                </label>
                <input
                  type="password"
                  value={settings.smtp_password}
                  onChange={(e) => setSettings({ ...settings, smtp_password: e.target.value })}
                  className="msm-input"
                  placeholder="••••••••"
                />
              </div>
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('settings.smtpFrom')}
                </label>
                <input
                  type="email"
                  value={settings.smtp_from}
                  onChange={(e) => setSettings({ ...settings, smtp_from: e.target.value })}
                  className="msm-input"
                  placeholder="noreply@example.com"
                />
              </div>
              <div className="flex items-end">
                <label className="flex items-center gap-2 cursor-pointer pb-3">
                  <input
                    type="checkbox"
                    checked={settings.smtp_tls === 'true'}
                    onChange={(e) => setSettings({ ...settings, smtp_tls: e.target.checked ? 'true' : 'false' })}
                    className="w-4 h-4 rounded border-outline bg-surface-container-high"
                  />
                  <span className="font-body-md text-sm text-on-surface-variant">{t('settings.smtpTls')}</span>
                </label>
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              {/* Status indicator */}
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${settings.resend_api_key ? 'bg-status-success' : 'bg-on-surface-variant'}`} />
                <span className="font-body-md text-sm text-on-surface">
                  {settings.resend_api_key ? 'Resend konfiguriert' : 'Resend nicht konfiguriert'}
                </span>
              </div>

              {/* Masked key display */}
              {settings.resend_api_key && (
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                    Aktueller Key
                  </label>
                  <input
                    type="text"
                    value={settings.resend_api_key}
                    readOnly
                    className="msm-input opacity-60 cursor-not-allowed font-mono text-sm"
                  />
                </div>
              )}

              {/* New key input */}
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  Neuer Resend API-Key
                </label>
                <input
                  type="password"
                  value={newResendKey}
                  onChange={(e) => setNewResendKey(e.target.value)}
                  className="msm-input"
                  placeholder="re_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                />
                <p className="font-body-md text-xs text-on-surface-variant mt-2">
                  Resend API-Key von <a href="https://resend.com" target="_blank" rel="noopener noreferrer" className="text-secondary hover:underline">resend.com</a>
                </p>
              </div>

              {resendMsg && <p className="text-sm text-status-success">{resendMsg}</p>}

              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={handleSaveResendKey}
                  disabled={savingResend || !newResendKey.trim()}
                  className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
                >
                  {savingResend ? (
                    <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <Save className="w-4 h-4" />
                  )}
                  API-Key speichern
                </button>
              </div>
            </div>
          )}

          {/* Test Email */}
          <div className="mt-6 pt-6 border-t border-outline-variant/30">
            <div className="flex flex-col sm:flex-row gap-3 items-start sm:items-end">
              <div className="flex-1 w-full">
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('settings.testEmailTo')}
                </label>
                <input
                  type="email"
                  value={testEmail}
                  onChange={(e) => setTestEmail(e.target.value)}
                  className="msm-input"
                  placeholder="test@example.com"
                />
              </div>
              <button
                type="button"
                onClick={handleTestEmail}
                disabled={sendingTest || !testEmail}
                className="msm-btn-secondary px-4 py-2.5 inline-flex items-center gap-2 disabled:opacity-50 whitespace-nowrap"
              >
                {sendingTest ? (
                  <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
                ) : (
                  <Send className="w-4 h-4" />
                )}
                {t('settings.testEmail')}
              </button>
            </div>
            {testMsg && (
              <p className="text-sm text-status-success mt-2">{testMsg}</p>
            )}
          </div>
        </div>

        {/* Save button */}
        <div className="flex justify-end">
          <button
            type="submit"
            disabled={saving}
            className="msm-btn-primary px-6 py-3 inline-flex items-center gap-2 disabled:opacity-50"
          >
            {saving ? (
              <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            {t('settings.save')}
          </button>
        </div>
      </form>
    </div>
  )
}
