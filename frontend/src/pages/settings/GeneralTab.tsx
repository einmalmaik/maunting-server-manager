import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Clock, Save } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { LanguageSwitcher } from '@/components/ui/LanguageSwitcher'
import { Dropdown } from '@/components/ui/Dropdown'
import { normalizePanelLanguage } from '@/config/panelLocales'
import { PanelSettings, EMPTY_PANEL_SETTINGS } from './types'

export function GeneralTab() {
  const { t, i18n } = useTranslation()
  const canWrite = useHasPermission('panel.settings.write')
  const [settings, setSettings] = useState<PanelSettings>(EMPTY_PANEL_SETTINGS)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let active = true
    api<PanelSettings>('/settings')
      .then((data) => {
        if (!active) return
        setSettings(data)
        void i18n.changeLanguage(normalizePanelLanguage(data.default_language))
      })
      .catch((err) => toast.error(err.message))
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      await api('/settings', {
        method: 'POST',
        body: JSON.stringify({
          panel_url: settings.panel_url,
          default_language: normalizePanelLanguage(i18n.language),
          time_format: settings.time_format,
          updates_automatic: settings.updates_automatic,
        }),
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
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <form onSubmit={handleSave} className="space-y-6">
      <fieldset disabled={!canWrite} className="space-y-6 border-0 p-0 m-0">
        <div className="msm-card p-6">
          <h2 className="font-headline text-headline-sm text-primary mb-6">
            {t('settings.panelConfig')}
          </h2>
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
                {t('settings.panelUrlHint')}
              </p>
            </div>
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('settings.defaultLanguage')}
              </label>
              <LanguageSwitcher
                className={!canWrite ? 'pointer-events-none opacity-60' : ''}
                onLanguageChange={(code) => setSettings({ ...settings, default_language: code })}
              />
              <p className="font-body-md text-xs text-on-surface-variant mt-1.5">
                {t('settings.defaultLanguageHint')}
              </p>
            </div>
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('settings.timeFormat')}
              </label>
              <Dropdown
                value={settings.time_format}
                onChange={(value) => setSettings({ ...settings, time_format: value as '24h' | '12h' })}
                options={[
                  { value: '24h', label: t('settings.timeFormat24') },
                  { value: '12h', label: t('settings.timeFormat12') },
                ]}
                disabled={!canWrite}
              />
              <p className="font-body-md text-xs text-on-surface-variant mt-1.5 inline-flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5" />
                {t('settings.timeFormatHint')}
              </p>
            </div>
            <div className="md:col-span-2 border-t border-border mt-4 pt-4">
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={settings.updates_automatic}
                  onChange={(e) => setSettings({ ...settings, updates_automatic: e.target.checked })}
                  disabled={!canWrite}
                  className="w-4 h-4 rounded border-border text-primary focus:ring-primary focus:ring-offset-background"
                />
                <div>
                  <span className="block font-headline text-body-md text-primary font-semibold">
                    {t('settings.updatesAutomatic', 'Automatische Updates')}
                  </span>
                  <span className="block font-body text-xs text-on-surface-variant">
                    {t('settings.updatesAutomaticHint', 'Das Panel und die remote Nodes aktualisieren sich automatisch, sobald ein neues Commit auf GitHub verfügbar ist.')}
                  </span>
                </div>
              </label>
            </div>
          </div>
        </div>

        {canWrite && (
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
