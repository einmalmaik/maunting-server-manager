import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Clock, Copy, Save } from 'lucide-react'
import { api } from '@/api/client'
import { API_ORIGIN } from '@/config/api'
import { toast } from '@/stores/toastStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { LanguageSwitcher } from '@/components/ui/LanguageSwitcher'
import { Button } from '@/components/ui/Button'
import { Dropdown } from '@/components/ui/Dropdown'
import { Switch } from '@/components/ui/Switch'
import { normalizePanelLanguage } from '@/config/panelLocales'
import { PanelSettings, EMPTY_PANEL_SETTINGS } from './types'

import { Spinner } from '@/components/ui/Spinner'
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
        const normalizedSettings = {
          ...EMPTY_PANEL_SETTINGS,
          ...(data && typeof data === 'object' ? data : {}),
        }
        setSettings(normalizedSettings)
        void i18n.changeLanguage(normalizePanelLanguage(normalizedSettings.default_language))
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
          desktop_app_download_enabled: settings.desktop_app_download_enabled,
          story_fable_download_enabled: settings.story_fable_download_enabled,
          calendar_enabled: settings.calendar_enabled,
          notes_enabled: settings.notes_enabled,
          // Fehlte hier, obwohl der Schalter darunter existiert und schaltet: Der Nutzer legte
          // den Social-Hub um, bekam "Gespeichert" zu sehen und fand beim naechsten Laden alles
          // beim Alten. Ein stummer Schalter ist schlimmer als gar keiner.
          social_enabled: settings.social_enabled,
          vault_enabled: settings.vault_enabled,
          cloudflare_enabled: settings.cloudflare_enabled,
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
        <Spinner size="lg" className="text-primary" />
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
              <p className="msm-field-help">
                {t('settings.panelUrlHint')}
              </p>
            </div>
            {/* Abgelesen, nicht gepflegt: das laufende Frontend spricht ohnehin
                mit dieser Adresse (VITE_API_URL, sonst der eigene Origin). Ein
                eigenes Einstellungsfeld daneben könnte falsch gepflegt werden
                und wäre dann eine zweite, unwahre Wahrheit. */}
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('settings.apiUrl')}
              </label>
              <div className="flex items-center gap-2">
                <input
                  type="url"
                  value={API_ORIGIN}
                  readOnly
                  className="msm-input opacity-60 cursor-not-allowed"
                />
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => {
                    void navigator.clipboard?.writeText(API_ORIGIN)
                    toast.success(t('hoster.copied'))
                  }}
                >
                  <Copy className="w-4 h-4" />
                </Button>
              </div>
              <p className="msm-field-help">
                {t('settings.apiUrlHint')}
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
              <p className="msm-field-help">
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
              <p className="msm-field-help inline-flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5" />
                {t('settings.timeFormatHint')}
              </p>
            </div>
            <div className="md:col-span-2 border-t border-outline-variant/30 mt-6 pt-6">
              <label className="flex items-center justify-between gap-4">
                <span className="block">
                  <span className="block font-headline text-body-md text-primary font-semibold">
                    {t('settings.updatesAutomatic')}
                  </span>
                  <span className="block font-body text-xs text-on-surface-variant">
                    {t('settings.updatesAutomaticHint')}
                  </span>
                </span>
                <Switch
                  checked={settings.updates_automatic}
                  onCheckedChange={(checked) => setSettings({ ...settings, updates_automatic: checked })}
                  disabled={!canWrite}
                  aria-label={t('settings.updatesAutomatic')}
                />
              </label>
            </div>
            <div className="md:col-span-2 border-t border-outline-variant/30 pt-6">
              <label className="flex items-center justify-between gap-4">
                <span className="block">
                  <span className="block font-headline text-body-md text-primary font-semibold">
                    {t('settings.desktopDownloadPromo')}
                  </span>
                  <span className="block font-body text-xs text-on-surface-variant">
                    {t('settings.desktopDownloadPromoHint')}
                  </span>
                </span>
                <Switch
                  checked={settings.desktop_app_download_enabled}
                  onCheckedChange={(checked) => setSettings({ ...settings, desktop_app_download_enabled: checked })}
                  disabled={!canWrite}
                  aria-label={t('settings.desktopDownloadPromo')}
                />
              </label>
            </div>
            <div className="md:col-span-2 border-t border-outline-variant/30 pt-6">
              <label className="flex items-center justify-between gap-4">
                <span className="block">
                  <span className="block font-headline text-body-md text-primary font-semibold">
                    {t('settings.storyFablePromo')}
                  </span>
                  <span className="block font-body text-xs text-on-surface-variant">
                    {t('settings.storyFablePromoHint')}
                  </span>
                </span>
                <Switch
                  checked={settings.story_fable_download_enabled}
                  onCheckedChange={(checked) => setSettings({ ...settings, story_fable_download_enabled: checked })}
                  disabled={!canWrite}
                  aria-label={t('settings.storyFablePromo')}
                />
              </label>
            </div>
            <div className="md:col-span-2 border-t border-outline-variant/30 pt-6">
              <label className="flex items-center justify-between gap-4">
                <span className="block">
                  <span className="block font-headline text-body-md text-primary font-semibold">
                    {t('settings.calendarEnabled')}
                  </span>
                  <span className="block font-body text-xs text-on-surface-variant">
                    {t('settings.calendarEnabledHint')}
                  </span>
                </span>
                <Switch
                  checked={settings.calendar_enabled}
                  onCheckedChange={(checked) => setSettings({ ...settings, calendar_enabled: checked })}
                  disabled={!canWrite}
                  aria-label={t('settings.calendarEnabled')}
                />
              </label>
            </div>
            <div className="md:col-span-2 border-t border-outline-variant/30 pt-6">
              <label className="flex items-center justify-between gap-4">
                <span className="block">
                  <span className="block font-headline text-body-md text-primary font-semibold">
                    {t('settings.notesEnabled')}
                  </span>
                  <span className="block font-body text-xs text-on-surface-variant">
                    {t('settings.notesEnabledHint')}
                  </span>
                </span>
                <Switch
                  checked={settings.notes_enabled}
                  onCheckedChange={(checked) => setSettings({ ...settings, notes_enabled: checked })}
                  disabled={!canWrite}
                  aria-label={t('settings.notesEnabled')}
                />
              </label>
            </div>
            <div className="md:col-span-2 border-t border-outline-variant/30 pt-6">
              <label className="flex items-center justify-between gap-4">
                <span className="block">
                  <span className="block font-headline text-body-md text-primary font-semibold">
                    {t('settings.vaultEnabled')}
                  </span>
                  <span className="block font-body text-xs text-on-surface-variant">
                    {t('settings.vaultEnabledHint')}
                  </span>
                </span>
                <Switch
                  checked={settings.vault_enabled}
                  onCheckedChange={(checked) => setSettings({ ...settings, vault_enabled: checked })}
                  disabled={!canWrite}
                  aria-label={t('settings.vaultEnabled')}
                />
              </label>
            </div>
            <div className="md:col-span-2 border-t border-outline-variant/30 pt-6">
              <label className="flex items-center justify-between gap-4">
                <span className="block">
                  <span className="block font-headline text-body-md text-primary font-semibold">
                    {t('settings.socialEnabled')}
                  </span>
                  <span className="block font-body text-xs text-on-surface-variant">
                    {t('settings.socialEnabledHint')}
                  </span>
                </span>
                <Switch
                  checked={settings.social_enabled}
                  onCheckedChange={(checked) => setSettings({ ...settings, social_enabled: checked })}
                  disabled={!canWrite}
                  aria-label={t('settings.socialEnabled')}
                />
              </label>
            </div>
            <div className="md:col-span-2 border-t border-outline-variant/30 pt-6">
              <label className="flex items-center justify-between gap-4">
                <span className="block">
                  <span className="block font-headline text-body-md text-primary font-semibold">
                    {t('settings.cloudflareEnabled')}
                  </span>
                  <span className="block font-body text-xs text-on-surface-variant">
                    {t('settings.cloudflareEnabledHint')}
                  </span>
                </span>
                <Switch
                  checked={settings.cloudflare_enabled}
                  onCheckedChange={(checked) => setSettings({ ...settings, cloudflare_enabled: checked })}
                  disabled={!canWrite}
                  aria-label={t('settings.cloudflareEnabled')}
                />
              </label>
            </div>
          </div>
        </div>

        {canWrite && (
          <div className="flex justify-end">
            <Button type="submit" disabled={saving}>
              {saving ? (
                <Spinner />
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
