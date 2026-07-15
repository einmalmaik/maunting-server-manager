import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Copy, LifeBuoy, Play, RefreshCw, Save } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Switch } from '@/components/ui/Switch'
import { Input } from '@/components/ui/Input'
import { Button } from '@/components/ui/Button'
import { PanelSettings, EMPTY_PANEL_SETTINGS } from './types'

const SINGRA_SNIPPET_TEMPLATE = `<!-- Singra Support Widget -->
<script
  src="https://singrabot.mauntingstudios.de/widget.js"
  data-widget-id="HIER_WIDGET_ID_EINSETZEN"
  defer
></script>`

export function SupportWidgetTab() {
  const { t } = useTranslation()
  const canWrite = useHasPermission('panel.settings.write')
  const [settings, setSettings] = useState<PanelSettings>(EMPTY_PANEL_SETTINGS)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [rotating, setRotating] = useState(false)
  const [testing, setTesting] = useState(false)
  const [revealedSecret, setRevealedSecret] = useState('')

  const webhookUrl = useMemo(() => {
    const base = settings.panel_url || (typeof window !== 'undefined' ? window.location.origin : '')
    return base ? `${base.replace(/\/$/, '')}/api/singra-webhook` : ''
  }, [settings.panel_url])

  useEffect(() => {
    let active = true
    api<PanelSettings>('/settings')
      .then((data) => { if (active) setSettings(data) })
      .catch((err) => toast.error(err.message))
      .finally(() => { if (active) setLoading(false) })
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
          support_widget_singra_id: settings.support_widget_singra_id,
          support_widget_custom_snippet: settings.support_widget_custom_snippet,
          support_widget_notify_email: settings.support_widget_notify_email,
        }),
      })
      toast.success(t('settings.saved'))
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setSaving(false)
    }
  }

  const copyWebhook = async () => {
    if (!webhookUrl) return
    await navigator.clipboard.writeText(webhookUrl)
    toast.success(t('settings.supportWidget.webhookCopied'))
  }

  const rotateSecret = async () => {
    setRotating(true)
    try {
      const res = await api<{ secret: string }>('/settings/singra-webhook-secret/rotate', { method: 'POST' })
      setRevealedSecret(res.secret)
      setSettings((s) => ({
        ...s,
        singra_webhook_secret_configured: true,
        singra_webhook_secret_source: 'panel',
      }))
      toast.success(t('settings.supportWidget.secretRotated'))
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setRotating(false)
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

  const snippetPreview =
    settings.support_widget_mode === 'singra'
      ? SINGRA_SNIPPET_TEMPLATE.replace(
          'HIER_WIDGET_ID_EINSETZEN',
          settings.support_widget_singra_id || 'HIER_WIDGET_ID_EINSETZEN',
        )
      : settings.support_widget_custom_snippet

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

          <div className="mb-4 grid gap-4 md:grid-cols-2">
            <label className="block text-sm">
              <span className="mb-1.5 block text-on-surface-variant">{t('settings.supportWidget.mode')}</span>
              <select
                className="msm-input w-full"
                value={settings.support_widget_mode}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    support_widget_mode: e.target.value as 'singra' | 'custom',
                  })
                }
              >
                <option value="singra">{t('settings.supportWidget.modeSingra')}</option>
                <option value="custom">{t('settings.supportWidget.modeCustom')}</option>
              </select>
            </label>
            {settings.support_widget_mode === 'singra' ? (
              <label className="block text-sm">
                <span className="mb-1.5 block text-on-surface-variant">{t('settings.supportWidget.widgetId')}</span>
                <Input
                  value={settings.support_widget_singra_id}
                  onChange={(e) => setSettings({ ...settings, support_widget_singra_id: e.target.value })}
                  placeholder={t('settings.supportWidget.widgetIdPlaceholder')}
                />
              </label>
            ) : (
              <label className="block text-sm md:col-span-2">
                <span className="mb-1.5 block text-on-surface-variant">{t('settings.supportWidget.customSnippet')}</span>
                <textarea
                  className="msm-input min-h-[120px] w-full font-mono text-xs"
                  value={settings.support_widget_custom_snippet}
                  onChange={(e) => setSettings({ ...settings, support_widget_custom_snippet: e.target.value })}
                  placeholder={SINGRA_SNIPPET_TEMPLATE}
                />
              </label>
            )}
          </div>

          <label className="mb-6 block text-sm">
            <span className="mb-1.5 block text-on-surface-variant">{t('settings.supportWidget.notifyEmail')}</span>
            <Input
              type="email"
              value={settings.support_widget_notify_email}
              onChange={(e) => setSettings({ ...settings, support_widget_notify_email: e.target.value })}
              placeholder={t('settings.supportWidget.notifyEmailHint')}
            />
          </label>

          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-on-surface-variant">
            {t('settings.supportWidget.embedTitle')}
          </p>
          <pre className="overflow-x-auto rounded-lg border border-outline-variant/40 bg-surface-container-high p-3 text-xs text-on-surface">
            {snippetPreview}
          </pre>
          <p className="mt-2 text-xs text-on-surface-variant">{t('settings.supportWidget.embedHint')}</p>
        </div>

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

          {revealedSecret && (
            <div className="rounded-lg border border-primary/30 bg-primary/5 p-3 font-mono text-xs break-all">
              {revealedSecret}
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="secondary" disabled={rotating} onClick={() => void rotateSecret()} className="gap-2">
              <RefreshCw className={`h-4 w-4 ${rotating ? 'animate-spin' : ''}`} />
              {t('settings.supportWidget.rotateSecret')}
            </Button>
            <Button type="button" variant="secondary" disabled={testing} onClick={() => void testWebhook()} className="gap-2">
              <Play className="h-4 w-4" />
              {testing ? t('common.loading') : t('settings.supportWidget.testWebhook')}
            </Button>
          </div>
        </div>

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