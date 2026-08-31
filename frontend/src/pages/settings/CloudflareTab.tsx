import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Button, Switch, Dropdown } from '@/Singra/UI'
import type { PanelSettings } from './types'
import { EMPTY_PANEL_SETTINGS } from './types'
import { toast } from '@/stores/toastStore'

export function CloudflareTab() {
  const { t } = useTranslation()
  const canWrite = useHasPermission('panel.settings.write')
  const [settings, setSettings] = useState<PanelSettings>(EMPTY_PANEL_SETTINGS)
  const [loading, setLoading] = useState(true)
  const [token, setToken] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [zones, setZones] = useState<{ id: string; name: string }[]>([])
  useEffect(() => {
    let active = true
    api<PanelSettings>('/settings')
      .then((d) => { if (!active) return; setSettings({ ...EMPTY_PANEL_SETTINGS, ...(d || {}) }) })
      .catch((err: unknown) => toast.error(err instanceof Error ? err.message : String(err)))
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  const loadZones = async () => {
    try {
      const res = await api<{ zones: { id: string; name: string }[] }>('/settings/cloudflare-zones')
      setZones(res.zones || [])
    } catch { /* ignore */ }
  }

  useEffect(() => { if (settings.cloudflare_api_configured) loadZones() }, [settings.cloudflare_api_configured])

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      if (token.trim()) {
        await api('/settings/cloudflare-token', { method: 'POST', body: JSON.stringify({ cloudflare_api_token: token.trim() }) })
        setToken('')
      }
      await api('/settings', { method: 'POST', body: JSON.stringify({ cloudflare_enabled: settings.cloudflare_enabled, cloudflare_default_zone: settings.cloudflare_default_zone, proactive_enabled: settings.proactive_enabled }) })
      toast.success(t('settings.saved'))
      const fresh = await api<PanelSettings>('/settings')
      setSettings({ ...EMPTY_PANEL_SETTINGS, ...(fresh || {}) })
      loadZones()
    } catch (err: unknown) { toast.error(err instanceof Error ? err.message : String(err)) }
    finally { setSaving(false) }
  }

  const handleTest = async () => {
    setTesting(true)
    try {
      const res = await api<{ valid: boolean; message: string }>('/settings/cloudflare-token/test', { method: 'POST' })
      if (res.valid) toast.success(res.message)
      else toast.error(res.message)
    } catch (err: unknown) { toast.error(err instanceof Error ? err.message : String(err)) }
    finally { setTesting(false) }
  }

  const handleDelete = async () => {
    try {
      await api('/settings/cloudflare-token', { method: 'DELETE' })
      toast.success(t('settings.cloudflare.deleted'))
      setZones([])
      const fresh = await api<PanelSettings>('/settings')
      setSettings({ ...EMPTY_PANEL_SETTINGS, ...(fresh || {}) })
    } catch (err: unknown) { toast.error(err instanceof Error ? err.message : String(err)) }
  }

  if (loading) return <div className="h-64 flex items-center justify-center"><div className="h-8 w-8 rounded-full border-2 border-primary border-t-transparent animate-spin" /></div>

  const zoneOptions = zones.map((z) => ({ value: z.id, label: z.name }))

  return (
    <form onSubmit={handleSave} className="space-y-6">
      <fieldset disabled={!canWrite} className="space-y-6">
        <div className="msm-card p-6 space-y-6">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h3 className="font-label-lg text-on-surface">{t('settings.cloudflare.title')}</h3>
              <p className="text-sm text-on-surface-variant mt-1">{t('settings.cloudflare.description')}</p>
            </div>
            <Switch checked={settings.cloudflare_enabled} onCheckedChange={(v) => setSettings({ ...settings, cloudflare_enabled: v })} disabled={!canWrite} />
          </div>

          <div className="grid gap-6">
            <label className="grid gap-2">
              <span className="font-label-md text-sm text-on-surface">{t('settings.cloudflare.tokenLabel')}</span>
              <div className="flex gap-4">
                <input className="msm-input flex-1" type="password" placeholder={settings.cloudflare_api_configured ? '••••••••' + (settings.cloudflare_api_token || '').slice(-4) : 'Cloudflare API Token'} value={token} onChange={(e) => setToken(e.target.value)} />
                <Button type="button" variant="secondary" onClick={handleTest} disabled={testing || (!settings.cloudflare_api_configured && !token.trim())}>{testing ? '...' : t('settings.testConnection')}</Button>
              </div>
              <span className="text-xs text-on-surface-variant">{t('settings.cloudflare.tokenHint')}</span>
              {settings.cloudflare_api_configured && (
                <button type="button" onClick={handleDelete} className="text-xs text-error hover:underline self-start">{t('settings.cloudflare.deleteToken')}</button>
              )}
            </label>

            <label className="grid gap-2">
              <span className="font-label-md text-sm text-on-surface">{t('settings.cloudflare.defaultZoneLabel')}</span>
              {zoneOptions.length > 0 ? (
                <Dropdown value={zones.find((z) => z.id === settings.cloudflare_default_zone)?.id || settings.cloudflare_default_zone} onChange={(v) => setSettings({ ...settings, cloudflare_default_zone: v })} options={zoneOptions} placeholder={t('settings.cloudflare.zonePlaceholder')} />
              ) : (
                <input className="msm-input" value={settings.cloudflare_default_zone} onChange={(e) => setSettings({ ...settings, cloudflare_default_zone: e.target.value })} placeholder="example.com" />
              )}
              <span className="text-xs text-on-surface-variant">{t('settings.cloudflare.zoneHint')}</span>
            </label>

            <label className="flex items-center justify-between gap-4 p-4 rounded-lg border border-outline-variant/40 bg-surface-container-low/40">
              <div>
                <span className="font-label-md text-sm text-on-surface">{t('settings.proactive.title')}</span>
                <p className="text-xs text-on-surface-variant mt-0.5">{t('settings.proactive.description')}</p>
              </div>
              <Switch checked={settings.proactive_enabled} onCheckedChange={(v) => setSettings({ ...settings, proactive_enabled: v })} disabled={!canWrite} />
            </label>
          </div>
        </div>

        <div className="flex justify-end gap-4">
          <Button type="submit" disabled={saving || !canWrite}>{saving ? t('common.saving') : t('common.save')}</Button>
        </div>
      </fieldset>
    </form>
  )
}
