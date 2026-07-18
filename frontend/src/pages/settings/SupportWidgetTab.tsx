import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Copy, LifeBuoy, Play, Save, Trash2 } from 'lucide-react'
import { notifySupportWidgetUpdated } from '@/lib/supportWidgetLoader'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Switch } from '@/components/ui/Switch'
import { Input } from '@/components/ui/Input'
import { Button } from '@/components/ui/Button'
import { Dropdown } from '@/components/ui/Dropdown'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { PanelSettings, EMPTY_PANEL_SETTINGS, type SupportWidgetProvider } from './types'
import { API_ORIGIN } from '@/config/api'

export function SupportWidgetTab() {
  const { t } = useTranslation()
  const canWrite = useHasPermission('panel.settings.write')
  const [settings, setSettings] = useState<PanelSettings>(EMPTY_PANEL_SETTINGS)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [newWebhookSecret, setNewWebhookSecret] = useState('')
  const [savingWebhookSecret, setSavingWebhookSecret] = useState(false)
  const [newInstallId, setNewInstallId] = useState('')
  const [savingInstallId, setSavingInstallId] = useState(false)

  const provider = settings.support_widget_mode as SupportWidgetProvider

  const webhookUrl = useMemo(() => {
    const base = API_ORIGIN || (typeof window !== 'undefined' ? window.location.origin : '')
    return base ? `${base.replace(/\/$/, '')}/api/singra-webhook` : ''
  }, [])

  const providerOptions = useMemo(
    () => [
      { value: 'singra', label: t('settings.supportWidget.providers.singra'), hint: t('settings.supportWidget.providers.singraHint') },
      { value: 'crisp', label: t('settings.supportWidget.providers.crisp'), hint: t('settings.supportWidget.providers.crispHint') },
      { value: 'tawk', label: t('settings.supportWidget.providers.tawk'), hint: t('settings.supportWidget.providers.tawkHint') },
      { value: 'custom', label: t('settings.supportWidget.providers.custom'), hint: t('settings.supportWidget.providers.customHint') },
    ],
    [t],
  )


  const reload = () =>
    api<PanelSettings>('/settings')
      .then((data) => setSettings(data))
      .catch((err) => toast.error(err.message))

  useEffect(() => {
    let active = true
    reload().finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    try {
      await api('/settings', {
        method: 'POST',
        body: JSON.stringify({
          support_widget_enabled: settings.support_widget_enabled,
          support_widget_mode: settings.support_widget_mode,
          support_widget_crisp_website_id: settings.support_widget_crisp_website_id,
          support_widget_tawk_property_id: settings.support_widget_tawk_property_id,
          support_widget_tawk_widget_id: settings.support_widget_tawk_widget_id,
          support_widget_custom_snippet: settings.support_widget_custom_snippet,
        }),
      })
      toast.success(t('settings.saved'))
      notifySupportWidgetUpdated()
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setSaving(false)
    }
  }

  const saveInstallId = async () => {
    if (!newInstallId.trim()) return
    setSavingInstallId(true)
    try {
      await api('/settings/singra-widget-install-id', {
        method: 'POST',
        body: JSON.stringify({ install_id: newInstallId.trim() }),
      })
      toast.success(t('settings.supportWidget.installIdSaved'))
      setNewInstallId('')
      await reload()
      notifySupportWidgetUpdated()
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setSavingInstallId(false)
    }
  }

  const removeInstallId = async () => {
    try {
      await api('/settings/singra-widget-install-id', { method: 'DELETE' })
      toast.success(t('settings.supportWidget.installIdRemoved'))
      await reload()
    } catch (err: any) {
      toast.error(err.message)
    }
  }

  const copyWebhook = async () => {
    if (!webhookUrl) return
    await navigator.clipboard.writeText(webhookUrl)
    toast.success(t('settings.supportWidget.webhookCopied'))
  }

  const saveWebhookSecret = async () => {
    if (!newWebhookSecret.trim()) return
    setSavingWebhookSecret(true)
    try {
      await api('/settings/singra-webhook-secret', {
        method: 'POST',
        body: JSON.stringify({ webhook_secret: newWebhookSecret.trim() }),
      })
      toast.success(t('settings.supportWidget.webhookSecretSaved'))
      setNewWebhookSecret('')
      await reload()
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setSavingWebhookSecret(false)
    }
  }

  const removeWebhookSecret = async () => {
    try {
      await api('/settings/singra-webhook-secret', { method: 'DELETE' })
      toast.success(t('settings.supportWidget.webhookSecretRemoved'))
      await reload()
    } catch (err: any) {
      toast.error(err.message)
    }
  }

  const testWebhook = async () => {
    setTesting(true)
    try {
      const res = await api<{ valid: boolean; message: string }>('/settings/singra-webhook/test', { method: 'POST' })
      if (res.valid) toast.success(res.message)
      else toast.error(res.message)
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setTesting(false)
    }
  }

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    )
  }

  return (
    <form onSubmit={save} className="space-y-6">
      <fieldset disabled={!canWrite} className="m-0 space-y-6 border-0 p-0">
        <div className="msm-card p-6">
          <div className="mb-4 flex items-start gap-3">
            <LifeBuoy className="mt-0.5 h-5 w-5 text-primary" aria-hidden />
            <div>
              <h2 className="font-headline text-headline-sm text-primary">{t('settings.supportWidget.title')}</h2>
              <p className="mt-1 text-sm text-on-surface-variant">{t('settings.supportWidget.description')}</p>
            </div>
          </div>

          <label className="mb-6 flex items-center justify-between gap-4 rounded-lg border border-outline-variant/40 bg-surface-container-high px-3 py-2">
            <span className="text-sm text-on-surface">{t('settings.supportWidget.enabled')}</span>
            <Switch
              checked={settings.support_widget_enabled}
              onCheckedChange={(checked) => setSettings({ ...settings, support_widget_enabled: checked })}
            />
          </label>

          <div className="mb-6">
            <span className="mb-1.5 block text-sm text-on-surface-variant">{t('settings.supportWidget.provider')}</span>
            <Dropdown
              value={provider}
              onChange={(value) => setSettings({ ...settings, support_widget_mode: value as SupportWidgetProvider })}
              options={providerOptions}
              disabled={!canWrite}
              aria-label={t('settings.supportWidget.provider')}
            />
          </div>

          {provider === 'singra' && (
            <div className="space-y-4 rounded-lg border border-outline-variant/30 bg-surface-container-high/50 p-4">
              <p className="text-sm text-on-surface-variant">{t('settings.supportWidget.singraAutoInject')}</p>
              <div className="flex items-center gap-2 text-sm">
                <span
                  className={`h-2 w-2 rounded-full ${settings.singra_widget_install_configured ? 'bg-status-success' : 'bg-on-surface-variant'}`}
                />
                {settings.singra_widget_install_configured
                  ? t('settings.supportWidget.installIdConfigured')
                  : t('settings.supportWidget.installIdMissing')}
                <span className="text-on-surface-variant/80">
                  · {t(`settings.supportWidget.installIdSource.${settings.singra_widget_install_source}`)}
                </span>
              </div>
              {settings.singra_widget_install_masked && (
                <Input readOnly value={settings.singra_widget_install_masked} className="font-mono text-sm opacity-70" />
              )}
              <div>
                <label className="mb-1.5 block text-sm text-on-surface-variant">
                  {t('settings.supportWidget.installIdNew')}
                </label>
                <PasswordInput
                  value={newInstallId}
                  onChange={(e) => setNewInstallId(e.target.value)}
                  placeholder={t('settings.supportWidget.installIdPlaceholder')}
                />
              </div>
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="secondary" disabled={savingInstallId} onClick={() => void saveInstallId()}>
                  {t('settings.supportWidget.installIdSave')}
                </Button>
                {settings.singra_widget_install_source === 'panel' && (
                  <Button type="button" variant="ghost" onClick={() => void removeInstallId()} className="gap-2 text-status-error">
                    <Trash2 className="h-4 w-4" />
                    {t('settings.supportWidget.installIdRemove')}
                  </Button>
                )}
              </div>
            </div>
          )}

          {provider === 'crisp' && (
            <label className="block text-sm">
              <span className="mb-1.5 block text-on-surface-variant">{t('settings.supportWidget.crispWebsiteId')}</span>
              <Input
                value={settings.support_widget_crisp_website_id}
                onChange={(e) => setSettings({ ...settings, support_widget_crisp_website_id: e.target.value })}
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
              />
              <p className="mt-1.5 text-xs text-on-surface-variant">{t('settings.supportWidget.crispHint')}</p>
            </label>
          )}

          {provider === 'tawk' && (
            <div className="grid gap-4 md:grid-cols-2">
              <label className="block text-sm">
                <span className="mb-1.5 block text-on-surface-variant">{t('settings.supportWidget.tawkPropertyId')}</span>
                <Input
                  value={settings.support_widget_tawk_property_id}
                  onChange={(e) => setSettings({ ...settings, support_widget_tawk_property_id: e.target.value })}
                />
              </label>
              <label className="block text-sm">
                <span className="mb-1.5 block text-on-surface-variant">{t('settings.supportWidget.tawkWidgetId')}</span>
                <Input
                  value={settings.support_widget_tawk_widget_id}
                  onChange={(e) => setSettings({ ...settings, support_widget_tawk_widget_id: e.target.value })}
                />
              </label>
              <p className="text-xs text-on-surface-variant md:col-span-2">{t('settings.supportWidget.tawkHint')}</p>
            </div>
          )}

          {provider === 'custom' && (
            <label className="block text-sm">
              <span className="mb-1.5 block text-on-surface-variant">{t('settings.supportWidget.customSnippet')}</span>
              <textarea
                className="msm-input min-h-[140px] w-full font-mono text-xs"
                value={settings.support_widget_custom_snippet}
                onChange={(e) => setSettings({ ...settings, support_widget_custom_snippet: e.target.value })}
                placeholder={t('settings.supportWidget.customSnippetPlaceholder')}
              />
              <p className="mt-1.5 text-xs text-on-surface-variant">{t('settings.supportWidget.customHint')}</p>
            </label>
          )}
        </div>

        {provider === 'singra' && (
          <div className="msm-card space-y-4 p-6">
            <h3 className="font-headline text-base text-primary">{t('settings.supportWidget.webhookTitle')}</h3>
            <p className="text-sm text-on-surface-variant">{t('settings.supportWidget.webhookDescription')}</p>

            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <Input readOnly value={webhookUrl} className="font-mono text-xs" />
              <Button type="button" variant="secondary" onClick={() => void copyWebhook()} className="shrink-0 gap-2">
                <Copy className="h-4 w-4" />
                {t('settings.supportWidget.copyWebhook')}
              </Button>
            </div>

            <p className="text-sm text-on-surface-variant">
              {settings.singra_webhook_secret_configured
                ? t('settings.supportWidget.secretConfigured')
                : t('settings.supportWidget.secretMissing')}
              {' · '}
              {t(`settings.supportWidget.secretSource.${settings.singra_webhook_secret_source}`)}
            </p>

            <div>
              <label className="mb-1.5 block text-sm text-on-surface-variant">
                {t('settings.supportWidget.webhookSecretNew')}
              </label>
              <PasswordInput
                value={newWebhookSecret}
                onChange={(e) => setNewWebhookSecret(e.target.value)}
                placeholder={t('settings.supportWidget.webhookSecretPlaceholder')}
              />
              <p className="mt-1.5 text-xs text-on-surface-variant">{t('settings.supportWidget.webhookSecretHint')}</p>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="secondary"
                disabled={savingWebhookSecret}
                onClick={() => void saveWebhookSecret()}
              >
                {t('settings.supportWidget.webhookSecretSave')}
              </Button>
              {settings.singra_webhook_secret_source === 'panel' && settings.singra_webhook_secret_configured && (
                <Button type="button" variant="ghost" onClick={() => void removeWebhookSecret()} className="text-status-error">
                  {t('settings.supportWidget.webhookSecretRemove')}
                </Button>
              )}
              <Button type="button" variant="secondary" disabled={testing} onClick={() => void testWebhook()} className="gap-2">
                <Play className="h-4 w-4" />
                {testing ? t('common.loading') : t('settings.supportWidget.testWebhook')}
              </Button>
            </div>
          </div>
        )}

        {canWrite && (
          <div className="flex justify-end">
            <button type="submit" disabled={saving} className="msm-btn-primary inline-flex items-center gap-2 px-6 py-3 disabled:opacity-50">
              {saving ? <span className="h-4 w-4 animate-spin rounded-full border-2 border-on-primary border-t-transparent" /> : <Save className="h-4 w-4" />}
              {t('settings.save')}
            </button>
          </div>
        )}
      </fieldset>
    </form>
  )
}