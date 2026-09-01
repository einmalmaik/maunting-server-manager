import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Globe, Trash2, Send, Save, Undo2 } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { Dropdown } from '@/Singra/UI'
import type { PanelSettings } from './types'
import { EMPTY_PANEL_SETTINGS } from './types'

export function CloudflareTab() {
  const { t } = useTranslation()
  const canWrite = useHasPermission('panel.settings.write')
  const [settings, setSettings] = useState<PanelSettings>(EMPTY_PANEL_SETTINGS)
  const [loading, setLoading] = useState(true)
  const [newToken, setNewToken] = useState('')
  const [clearToken, setClearToken] = useState(false)
  const [selectedZone, setSelectedZone] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [zones, setZones] = useState<{ id: string; name: string }[]>([])

  const fetchSettings = async () => {
    try {
      const data = await api<PanelSettings>('/settings')
      setSettings({ ...EMPTY_PANEL_SETTINGS, ...(data || {}) })
      setSelectedZone(data?.cloudflare_default_zone || '')
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void fetchSettings()
  }, [])

  const loadZones = async () => {
    try {
      const res = await api<{ zones: { id: string; name: string }[] }>('/settings/cloudflare-zones')
      setZones(res.zones || [])
    } catch {
      // Ignoriere Fehler beim Laden der Zonen wenn Token ungültig/fehlt
    }
  }

  useEffect(() => {
    if (settings.cloudflare_api_configured) {
      void loadZones()
    }
  }, [settings.cloudflare_api_configured])

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      if (clearToken) {
        await api('/settings/cloudflare-token', { method: 'DELETE' })
        setClearToken(false)
        setNewToken('')
      } else if (newToken.trim()) {
        await api('/settings/cloudflare-token', {
          method: 'POST',
          body: JSON.stringify({ cloudflare_api_token: newToken.trim() }),
        })
        setNewToken('')
      }
      if (selectedZone !== settings.cloudflare_default_zone) {
        await api('/settings', {
          method: 'POST',
          body: JSON.stringify({ cloudflare_default_zone: selectedZone.trim() }),
        })
      }
      toast.success(t('settings.cloudflare.saved', { defaultValue: 'Cloudflare-Einstellungen gespeichert' }))
      await fetchSettings()
      await loadZones()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    try {
      const res = await api<{ valid: boolean; message: string }>('/settings/cloudflare-token/test', {
        method: 'POST',
      })
      if (res.valid) {
        toast.success(res.message || t('settings.cloudflare.testSuccess', { defaultValue: 'Cloudflare API-Token ist gültig' }))
      } else {
        toast.error(res.message)
      }
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
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

  const zoneOptions = zones.map((z) => ({ value: z.id, label: z.name }))
  const hasChanges = Boolean(newToken.trim() || clearToken || selectedZone !== settings.cloudflare_default_zone)
  const sourceLabel = settings.cloudflare_api_source === 'env' ? '.env' : 'Panel-DB'

  return (
    <fieldset disabled={!canWrite} className="space-y-6 border-0 p-0 m-0">
      <div className="msm-card p-6">
        <div className="flex items-center gap-2 mb-6">
          <Globe className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 className="font-headline text-lg font-semibold text-on-surface">
            {t('settings.cloudflare.title', { defaultValue: 'Cloudflare DNS' })}
          </h2>
        </div>

        <p className="text-sm text-on-surface-variant mb-4">
          {t('settings.cloudflare.description', {
            defaultValue: 'Verwalte Cloudflare DNS für automatische Subdomains. Die KI nutzt dies proaktiv bei Servererstellungen.',
          })}
        </p>

        {!settings.cloudflare_enabled && (
          <div className="p-3 mb-4 bg-amber-500/10 border border-amber-500/30 rounded-md text-sm text-amber-600">
            {t('settings.cloudflare.disabledHint', { defaultValue: 'Cloudflare DNS ist unter Allgemein deaktiviert.' })}
          </div>
        )}

        <form onSubmit={handleSave} className="space-y-4">
          <div className="flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-full ${
                settings.cloudflare_api_configured ? 'bg-status-success' : 'bg-on-surface-variant'
              }`}
            />
            <span className="font-body-md text-sm text-on-surface">
              {settings.cloudflare_api_configured
                ? t('settings.cloudflare.configured', { defaultValue: 'Konfiguriert' })
                : t('settings.cloudflare.notConfigured', { defaultValue: 'Nicht konfiguriert' })}
              {settings.cloudflare_api_source && settings.cloudflare_api_source !== 'none' && (
                <span className="ml-2 font-mono text-xs text-on-surface-variant">
                  ({sourceLabel})
                </span>
              )}
            </span>
          </div>

          {/* Einheitliches API-Token Eingabefeld */}
          <div className="rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4 space-y-2">
            <div className="flex items-center justify-between">
              <label className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('settings.cloudflare.tokenLabel', { defaultValue: 'Cloudflare API-Token' })}
              </label>
              {clearToken ? (
                <span className="text-xs text-status-warning flex items-center gap-1.5">
                  {t('settings.keyWillBeCleared', { defaultValue: 'Wird beim Speichern entfernt' })}
                  <button
                    type="button"
                    onClick={() => setClearToken(false)}
                    className="font-medium text-primary hover:underline inline-flex items-center gap-1"
                  >
                    <Undo2 className="h-3 w-3" />
                    {t('common.undo', { defaultValue: 'Rückgängig' })}
                  </button>
                </span>
              ) : settings.cloudflare_api_configured && canWrite ? (
                <button
                  type="button"
                  onClick={() => {
                    setClearToken(true)
                    setNewToken('')
                  }}
                  className="inline-flex items-center gap-1 text-xs text-on-surface-variant hover:text-status-error transition-colors"
                  title={t('settings.cloudflare.deleteToken', { defaultValue: 'Token entfernen' })}
                  aria-label={t('settings.cloudflare.deleteToken', { defaultValue: 'Token entfernen' })}
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>{t('settings.cloudflare.deleteToken', { defaultValue: 'Token entfernen' })}</span>
                </button>
              ) : null}
            </div>

            <PasswordInput
              value={newToken}
              disabled={!canWrite || saving || clearToken}
              onChange={(e) => {
                setNewToken(e.target.value)
                setClearToken(false)
              }}
              placeholder={
                clearToken
                  ? t('settings.keyWillBeCleared', { defaultValue: 'Wird beim Speichern entfernt' })
                  : settings.cloudflare_api_configured
                    ? t('settings.keyConfiguredHint', {
                        defaultValue: 'Token konfiguriert; leer lassen, um ihn beizubehalten',
                      })
                    : t('settings.cloudflare.tokenPlaceholder', { defaultValue: 'Bearer Token...' })
              }
            />

            <p className="msm-field-help">
              {t('settings.cloudflare.tokenHint', {
                defaultValue: 'Bearer Token mit Zone:Read und DNS:Edit Rechten. Wird mit dem DIS Sidecar verschlüsselt gespeichert.',
              })}{' '}
              <a
                href="https://dash.cloudflare.com/profile/api-tokens"
                target="_blank"
                rel="noopener noreferrer"
                className="text-secondary hover:underline"
              >
                dash.cloudflare.com
              </a>
            </p>
          </div>

          <div>
            <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('settings.cloudflare.defaultZoneLabel', { defaultValue: 'Standard-Zone (Domain)' })}
            </label>
            {zoneOptions.length > 0 ? (
              <Dropdown
                value={zones.find((z) => z.id === selectedZone)?.id || selectedZone}
                onChange={(v) => setSelectedZone(v)}
                options={zoneOptions}
                placeholder={t('settings.cloudflare.zonePlaceholder', { defaultValue: 'Zone wählen...' })}
              />
            ) : (
              <input
                className="msm-input"
                value={selectedZone}
                onChange={(e) => setSelectedZone(e.target.value)}
                placeholder="example.com"
              />
            )}
            <p className="msm-field-help">
              {t('settings.cloudflare.zoneHint', {
                defaultValue: 'Wird für automatische Subdomains verwendet: {spiel}-{name}.{zone}',
              })}
            </p>
          </div>

          <div className="flex gap-3 justify-end flex-wrap pt-2">
            <button
              type="button"
              onClick={handleTest}
              disabled={testing || !settings.cloudflare_api_configured}
              className="msm-btn-secondary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
            >
              {testing ? (
                <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
              {t('settings.testConnection', { defaultValue: 'Verbindung testen' })}
            </button>
            <button
              type="submit"
              disabled={saving || !hasChanges || !canWrite}
              className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
            >
              {saving ? (
                <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              {t('settings.cloudflare.save', { defaultValue: 'Einstellungen speichern' })}
            </button>
          </div>
        </form>
      </div>
    </fieldset>
  )
}
