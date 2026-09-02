import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Save, Send, Mail, Trash2, Undo2 } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { Button } from '@/components/ui/Button'
import { NumberStepper } from '@/components/ui/NumberStepper'
import { Switch } from '@/components/ui/Switch'
import { PanelSettings, EMPTY_PANEL_SETTINGS } from './types'

export function EmailTab() {
  const { t } = useTranslation()
  const canWrite = useHasPermission('panel.settings.write')
  const [settings, setSettings] = useState<PanelSettings>(EMPTY_PANEL_SETTINGS)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [provider, setProvider] = useState<'smtp' | 'resend'>('smtp')
  const [newResendKey, setNewResendKey] = useState('')
  const [clearResendKey, setClearResendKey] = useState(false)
  const [savingResend, setSavingResend] = useState(false)
  const [testEmail, setTestEmail] = useState('')
  const [sendingTest, setSendingTest] = useState(false)

  const fetchSettings = async () => {
    try {
      const data = await api<PanelSettings>('/settings')
      setSettings(data)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void fetchSettings()
  }, [])

  useEffect(() => {
    setProvider(settings.email_provider === 'resend' ? 'resend' : 'smtp')
  }, [settings.email_provider])

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      await api('/settings', { method: 'POST', body: JSON.stringify(settings) })
      toast.success(t('settings.saved'))
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
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
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setSendingTest(false)
    }
  }

  const handleSaveResendKey = async () => {
    if (!newResendKey.trim() && !clearResendKey) return
    setSavingResend(true)
    try {
      if (clearResendKey) {
        await api('/settings/resend-key', {
          method: 'POST',
          body: JSON.stringify({ resend_api_key: '' }),
        })
        setClearResendKey(false)
        setNewResendKey('')
      } else if (newResendKey.trim()) {
        await api('/settings/resend-key', {
          method: 'POST',
          body: JSON.stringify({ resend_api_key: newResendKey.trim() }),
        })
        setNewResendKey('')
      }
      toast.success(t('settings.saved'))
      await fetchSettings()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
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
          <div className="flex items-center gap-2 mb-6">
            <Mail className="h-5 w-5 text-secondary" aria-hidden="true" />
            <h2 className="font-headline text-lg font-semibold text-on-surface">{t('settings.emailTitle')}</h2>
          </div>

          <div className="flex gap-4 mb-6">
            <button
              type="button"
              onClick={() => {
                setProvider('smtp')
                setSettings({ ...settings, email_provider: 'smtp' })
              }}
              className={`px-4 py-2 rounded-lg font-headline text-sm font-medium transition-colors ${
                provider === 'smtp'
                  ? 'bg-primary text-on-primary'
                  : 'bg-surface-container-high text-on-surface-variant hover:text-on-surface'
              }`}
            >
              SMTP
            </button>
            <button
              type="button"
              onClick={() => {
                setProvider('resend')
                setSettings({ ...settings, email_provider: 'resend' })
              }}
              className={`px-4 py-2 rounded-lg font-headline text-sm font-medium transition-colors ${
                provider === 'resend'
                  ? 'bg-primary text-on-primary'
                  : 'bg-surface-container-high text-on-surface-variant hover:text-on-surface'
              }`}
            >
              Resend
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
                <NumberStepper
                  value={settings.smtp_port || '587'}
                  onValueChange={(val) => setSettings({ ...settings, smtp_port: val })}
                  min={1}
                  max={65535}
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
                <label className="flex items-center gap-3 pb-3">
                  <span className="font-body-md text-sm text-on-surface-variant">{t('settings.smtpTls')}</span>
                  <Switch
                    checked={settings.smtp_tls === 'true'}
                    onCheckedChange={(checked) => setSettings({ ...settings, smtp_tls: checked ? 'true' : 'false' })}
                    aria-label={t('settings.smtpTls')}
                  />
                </label>
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <span
                  className={`w-2 h-2 rounded-full ${
                    settings.email_configured ? 'bg-status-success' : 'bg-on-surface-variant'
                  }`}
                />
                <span className="font-body-md text-sm text-on-surface">
                  {settings.email_configured
                    ? t('settings.emailConfigured', { defaultValue: 'Konfiguriert' })
                    : t('settings.emailNotConfigured', { defaultValue: 'Nicht konfiguriert' })}
                </span>
              </div>

              {/* Einheitliches API-Key Eingabefeld */}
              <div className="rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <label className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                    {t('settings.resendApiKey')}
                  </label>
                  {clearResendKey ? (
                    <span className="text-xs text-status-warning flex items-center gap-1.5">
                      {t('settings.keyWillBeCleared', { defaultValue: 'Wird beim Speichern entfernt' })}
                      <button
                        type="button"
                        onClick={() => setClearResendKey(false)}
                        className="font-medium text-primary hover:underline inline-flex items-center gap-1"
                      >
                        <Undo2 className="h-3 w-3" />
                        {t('common.undo', { defaultValue: 'Rückgängig' })}
                      </button>
                    </span>
                  ) : settings.email_configured && canWrite ? (
                    <button
                      type="button"
                      onClick={() => {
                        setClearResendKey(true)
                        setNewResendKey('')
                      }}
                      className="inline-flex items-center gap-1 text-xs text-on-surface-variant hover:text-status-error transition-colors"
                      title={t('common.delete', { defaultValue: 'Entfernen' })}
                      aria-label={t('common.delete', { defaultValue: 'Entfernen' })}
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                      <span>{t('settings.clearKey', { defaultValue: 'Key entfernen' })}</span>
                    </button>
                  ) : null}
                </div>

                <PasswordInput
                  value={newResendKey}
                  disabled={!canWrite || savingResend || clearResendKey}
                  onChange={(e) => {
                    setNewResendKey(e.target.value)
                    setClearResendKey(false)
                  }}
                  placeholder={
                    clearResendKey
                      ? t('settings.keyWillBeCleared', { defaultValue: 'Wird beim Speichern entfernt' })
                      : settings.email_configured
                        ? t('settings.keyConfiguredHint', {
                            defaultValue: 'Schlüssel hinterlegt; leer lassen, um ihn beizubehalten',
                          })
                        : 're_xxxxxxxxxxxxxxxxxxxxxxxxxxxx'
                  }
                />

                <p className="msm-field-help">
                  Resend API-Key von{' '}
                  <a
                    href="https://resend.com"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-secondary hover:underline"
                  >
                    resend.com
                  </a>
                </p>
              </div>

              <div className="flex justify-end pt-2">
                <button
                  type="button"
                  onClick={handleSaveResendKey}
                  disabled={savingResend || (!newResendKey.trim() && !clearResendKey) || !canWrite}
                  className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
                >
                  {savingResend ? (
                    <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <Save className="w-4 h-4" />
                  )}
                  {t('settings.save', { defaultValue: 'Speichern' })}
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
            <Button type="submit" disabled={saving}>
              {saving ? (
                <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              {t('settings.save')}
            </Button>
          </div>
        )}
      </fieldset>
    </form>
  )
}
