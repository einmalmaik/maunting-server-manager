import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Save, Send, Mail } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { PanelSettings, EMPTY_PANEL_SETTINGS } from './types'

export function EmailTab() {
  const { t } = useTranslation()
  const canWrite = useHasPermission('panel.settings.write')
  const [settings, setSettings] = useState<PanelSettings>(EMPTY_PANEL_SETTINGS)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [provider, setProvider] = useState<'smtp' | 'resend'>('smtp')
  const [newResendKey, setNewResendKey] = useState('')
  const [savingResend, setSavingResend] = useState(false)
  const [testEmail, setTestEmail] = useState('')
  const [sendingTest, setSendingTest] = useState(false)

  const fetchSettings = async () => {
    try {
      const data = await api<PanelSettings>('/settings')
      setSettings(data)
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchSettings() }, [])

  useEffect(() => {
    setProvider(settings.email_provider === 'resend' ? 'resend' : 'smtp')
  }, [settings.email_provider])

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      await api('/settings', { method: 'POST', body: JSON.stringify(settings) })
      toast.success(t('settings.saved'))
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setSaving(false)
    }
  }

  const handleTestEmail = async () => {
    if (!testEmail) return
    setSendingTest(true)
    try {
      await api('/settings/test-email', {
        method: 'POST',
        body: JSON.stringify({ to: testEmail }),
      })
      toast.success(t('settings.testEmailSent'))
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setSendingTest(false)
    }
  }

  const handleSaveResendKey = async () => {
    if (!newResendKey.trim()) return
    setSavingResend(true)
    try {
      await api('/settings/resend-key', {
        method: 'POST',
        body: JSON.stringify({ resend_api_key: newResendKey.trim() }),
      })
      toast.success(t('settings.saved'))
      setNewResendKey('')
      await fetchSettings()
    } catch (err: any) {
      toast.error(err.message)
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
    <form onSubmit={handleSave} className="space-y-6">
      <fieldset disabled={!canWrite} className="space-y-6 border-0 p-0 m-0">
        <div className="msm-card p-6">
          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
              <Mail className="w-5 h-5 text-secondary" />
            </div>
            <h2 className="font-headline text-headline-sm text-primary">{t('settings.emailConfig')}</h2>
          </div>

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
                <PasswordInput
                  value={settings.smtp_password}
                  onChange={(e) => setSettings({ ...settings, smtp_password: e.target.value })}
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
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${settings.email_configured ? 'bg-status-success' : 'bg-on-surface-variant'}`} />
                <span className="font-body-md text-sm text-on-surface">
                  {settings.email_configured ? t('settings.steamConfigured') : t('settings.steamNotConfigured')}
                </span>
              </div>

              {settings.resend_api_key && (
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                    {t('settings.steamCurrentKey')}
                  </label>
                  <input
                    type="text"
                    value={settings.resend_api_key}
                    readOnly
                    className="msm-input opacity-60 cursor-not-allowed font-mono text-sm"
                  />
                </div>
              )}

              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('settings.resendApiKey')}
                </label>
                <PasswordInput
                  value={newResendKey}
                  onChange={(e) => setNewResendKey(e.target.value)}
                  placeholder="re_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                />
                <p className="font-body-md text-xs text-on-surface-variant mt-2">
                  Resend API-Key von <a href="https://resend.com" target="_blank" rel="noopener noreferrer" className="text-secondary hover:underline">resend.com</a>
                </p>
              </div>

              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={handleSaveResendKey}
                  disabled={savingResend || !newResendKey.trim() || !canWrite}
                  className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
                >
                  {savingResend ? (
                    <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <Save className="w-4 h-4" />
                  )}
                  {t('settings.steamSaveKey')}
                </button>
              </div>
            </div>
          )}

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
          </div>
        </div>

        {canWrite && provider === 'smtp' && (
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
        )}
      </fieldset>
    </form>
  )
}
