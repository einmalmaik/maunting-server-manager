import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ExternalLink, Save } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Switch } from '@/components/ui/Switch'
import { publishPublicLegalSettings } from '@/hooks/usePublicLegalSettings'
import { PanelSettings, EMPTY_PANEL_SETTINGS } from './types'

export function ImprintTab() {
  const { t } = useTranslation()
  const canWrite = useHasPermission('panel.settings.write')
  const [settings, setSettings] = useState<PanelSettings>(EMPTY_PANEL_SETTINGS)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let active = true
    api<PanelSettings>('/settings')
      .then((data) => { if (active) setSettings(data) })
      .catch((err) => toast.error(err.message))
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  const handleSave = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    try {
      await api('/settings', {
        method: 'POST',
        body: JSON.stringify({
          imprint_enabled: settings.imprint_enabled,
          imprint_url: settings.imprint_url,
        }),
      })
      publishPublicLegalSettings({
        imprint_enabled: settings.imprint_enabled,
        imprint_url: settings.imprint_enabled ? settings.imprint_url.trim() : '',
      })
      toast.success(t('settings.saved'))
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setSaving(false)
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
    <form onSubmit={handleSave} className="space-y-6">
      <fieldset disabled={!canWrite} className="m-0 space-y-6 border-0 p-0">
        <div className="msm-card p-6">
          <div className="mb-6 flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h2 className="font-headline text-headline-sm text-primary">
                {t('settings.imprint.title')}
              </h2>
              <p className="mt-1 font-body-md text-body-md text-on-surface-variant">
                {t('settings.imprint.description')}
              </p>
            </div>
            <label className="inline-flex items-center gap-3 rounded-lg border border-outline-variant/40 bg-surface-container-high px-3 py-2">
              <span className="font-label-md text-label-md text-on-surface-variant">
                {t('settings.imprint.show')}
              </span>
              <Switch
                checked={settings.imprint_enabled}
                onCheckedChange={(checked) => setSettings({ ...settings, imprint_enabled: checked })}
                aria-label={t('settings.imprint.show')}
              />
            </label>
          </div>

          <div className="max-w-2xl space-y-3">
            <Input
              id="imprint-url"
              type="url"
              label={t('settings.imprint.url')}
              value={settings.imprint_url}
              onChange={(event) => setSettings({ ...settings, imprint_url: event.target.value })}
              placeholder="https://example.com/impressum"
              maxLength={2048}
              disabled={!canWrite}
            />
            <p className="font-body-md text-xs text-on-surface-variant">
              {t('settings.imprint.urlHint')}
            </p>
            {settings.imprint_enabled && settings.imprint_url && (
              <a
                href={settings.imprint_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 font-label-md text-label-md text-secondary hover:text-mint-accent"
              >
                <ExternalLink className="h-4 w-4" />
                {t('settings.imprint.preview')}
              </a>
            )}
          </div>
        </div>

        {canWrite && (
          <div className="flex justify-end">
            <Button type="submit" disabled={saving} className="px-6">
              {saving ? (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-on-primary border-t-transparent" />
              ) : (
                <Save className="h-4 w-4" />
              )}
              {t('settings.save')}
            </Button>
          </div>
        )}
      </fieldset>
    </form>
  )
}
