import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Save } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Switch } from '@/components/ui/Switch'
import { Dropdown } from '@/components/ui/Dropdown'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { PanelSettings, EMPTY_PANEL_SETTINGS } from './types'

export function CaptchaTab() {
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
          captcha_enabled: settings.captcha_enabled,
          captcha_provider: settings.captcha_provider,
          captcha_site_key: settings.captcha_site_key,
          captcha_secret_key: settings.captcha_secret_key,
        }),
      })
      toast.success(t('settings.saved', { defaultValue: 'Einstellungen gespeichert' }))
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
                {t('settings.captcha.title', { defaultValue: 'CAPTCHA-Schutz' })}
              </h2>
              <p className="mt-1 font-body-md text-body-md text-on-surface-variant">
                {t('settings.captcha.description', { defaultValue: 'Schütze Registrierung, Anmeldung und Passwortwiederherstellung vor Spam und Bruteforce.' })}
              </p>
            </div>
            <label className="inline-flex items-center gap-3 rounded-lg border border-outline-variant/40 bg-surface-container-high px-3 py-2">
              <span className="font-label-md text-label-md text-on-surface-variant">
                {t('settings.captcha.enable', { defaultValue: 'CAPTCHA aktivieren' })}
              </span>
              <Switch
                checked={settings.captcha_enabled}
                onCheckedChange={(checked) => setSettings({ ...settings, captcha_enabled: checked })}
                aria-label={t('settings.captcha.enable', { defaultValue: 'CAPTCHA aktivieren' })}
              />
            </label>
          </div>

          {settings.captcha_enabled && (
            <div className="max-w-2xl space-y-6 border-t border-border/40 pt-6">
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('settings.captcha.provider', { defaultValue: 'Anbieter' })}
                </label>
                <Dropdown
                  value={settings.captcha_provider}
                  onChange={(val) => setSettings({ ...settings, captcha_provider: val as any })}
                  options={[
                    { value: 'turnstile', label: 'Cloudflare Turnstile' },
                    { value: 'hcaptcha', label: 'hCaptcha' },
                    { value: 'recaptcha', label: 'Google reCAPTCHA v2 (Checkbox)' },
                  ]}
                  disabled={!canWrite}
                />
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <Input
                    id="captcha-site-key"
                    type="text"
                    label={t('settings.captcha.siteKey', { defaultValue: 'Site Key (Website-Schlüssel)' })}
                    value={settings.captcha_site_key}
                    onChange={(event) => setSettings({ ...settings, captcha_site_key: event.target.value })}
                    placeholder="e.g. 0x4AAAAAA..."
                    disabled={!canWrite}
                  />
                  <p className="font-body-md text-xs text-on-surface-variant mt-1.5">
                    {t('settings.captcha.siteKeyHint', { defaultValue: 'Öffentlicher Schlüssel zur Anzeige des Widgets im Browser.' })}
                  </p>
                </div>

                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                    {t('settings.captcha.secretKey', { defaultValue: 'Secret Key (Geheimer Schlüssel)' })}
                  </label>
                  <PasswordInput
                    id="captcha-secret-key"
                    value={settings.captcha_secret_key}
                    onChange={(event) => setSettings({ ...settings, captcha_secret_key: event.target.value })}
                    placeholder={settings.captcha_secret_key ? '••••••••' : 'e.g. 0x4AAAAAA...'}
                    disabled={!canWrite}
                  />
                  <p className="font-body-md text-xs text-on-surface-variant mt-1.5">
                    {t('settings.captcha.secretKeyHint', { defaultValue: 'Privater Schlüssel zur serverseitigen Token-Validierung.' })}
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>

        {canWrite && (
          <div className="flex justify-end">
            <Button type="submit" disabled={saving} className="px-6">
              {saving ? (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-on-primary border-t-transparent" />
              ) : (
                <Save className="h-4 w-4" />
              )}
              {t('settings.save', { defaultValue: 'Speichern' })}
            </Button>
          </div>
        )}
      </fieldset>
    </form>
  )
}
