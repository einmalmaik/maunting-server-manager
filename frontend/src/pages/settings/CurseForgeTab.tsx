import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Save, Send, Flame, Trash2 } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { PanelSettings, EMPTY_PANEL_SETTINGS } from './types'

export function CurseForgeTab() {
  const { t } = useTranslation()
  const canWrite = useHasPermission('panel.settings.write')
  const [settings, setSettings] = useState<PanelSettings>(EMPTY_PANEL_SETTINGS)
  const [loading, setLoading] = useState(true)
  const [newKey, setNewKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [deleting, setDeleting] = useState(false)

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

  useEffect(() => {
    void fetchSettings()
  }, [])

  const handleSave = async () => {
    if (!newKey.trim()) return
    setSaving(true)
    try {
      await api('/settings/curseforge-api-key', {
        method: 'POST',
        body: JSON.stringify({ curseforge_api_key: newKey.trim() }),
      })
      toast.success(t('settings.curseforgeSaved', { defaultValue: 'CurseForge API-Schlüssel gespeichert' }))
      setNewKey('')
      await fetchSettings()
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    try {
      const res = await api<{ message: string; valid: boolean }>('/settings/curseforge-key/test', {
        method: 'POST',
      })
      if (res.valid) {
        toast.success(res.message)
      } else {
        toast.error(res.message)
      }
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setTesting(false)
    }
  }

  const handleDelete = async () => {
    const ok = await confirm({
      message: t('settings.curseforgeConfirmDelete', {
        defaultValue: 'Möchtest du den gespeicherten CurseForge API-Schlüssel wirklich entfernen?',
      }),
      danger: true,
      confirmText: t('common.delete', { defaultValue: 'Löschen' }),
    })
    if (!ok) return

    setDeleting(true)
    try {
      await api('/settings/curseforge-api-key', { method: 'DELETE' })
      toast.success(t('settings.curseforgeRemoved', { defaultValue: 'CurseForge API-Schlüssel entfernt' }))
      await fetchSettings()
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setDeleting(false)
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
    <fieldset disabled={!canWrite} className="space-y-6 border-0 p-0 m-0">
      <div className="msm-card p-6">
        <div className="flex items-center gap-2 mb-6">
          <Flame className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 className="font-headline text-lg font-semibold text-on-surface">
            {t('settings.curseforgeApiKey', { defaultValue: 'CurseForge API-Schlüssel' })}
          </h2>
        </div>

        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-full ${
                settings.curseforge_api_configured ? 'bg-status-success' : 'bg-on-surface-variant'
              }`}
            />
            <span className="font-body-md text-sm text-on-surface">
              {settings.curseforge_api_configured
                ? t('settings.curseforgeConfigured', { defaultValue: 'Konfiguriert' })
                : t('settings.curseforgeNotConfigured', { defaultValue: 'Nicht konfiguriert' })}
              {settings.curseforge_api_source && settings.curseforge_api_source !== 'none' && (
                <span className="ml-2 font-mono text-xs text-on-surface-variant">
                  ({settings.curseforge_api_source === 'env' ? '.env' : 'Panel-DB'})
                </span>
              )}
            </span>
          </div>

          {settings.curseforge_api_key && (
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('settings.curseforgeCurrentKey', { defaultValue: 'Aktueller API-Schlüssel' })}
              </label>
              <input
                type="text"
                value={settings.curseforge_api_key}
                readOnly
                className="msm-input opacity-60 cursor-not-allowed font-mono text-sm"
              />
            </div>
          )}

          <div>
            <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('settings.curseforgeNewKey', { defaultValue: 'Neuer API-Schlüssel' })}
            </label>
            <PasswordInput
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              placeholder="XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
            />
            <p className="msm-field-help">
              {t('settings.curseforgeKeyHint', {
                defaultValue:
                  'Wird für Spiele mit CurseForge-Mod-Integration (z.B. ARK: Survival Ascended, Minecraft) benötigt. Registrierung & API-Schlüssel:',
              })}{' '}
              <a
                href="https://console.curseforge.com"
                target="_blank"
                rel="noopener noreferrer"
                className="text-secondary hover:underline"
              >
                console.curseforge.com
              </a>
            </p>
          </div>

          <div className="flex gap-3 justify-end flex-wrap">
            {settings.curseforge_api_configured && (
              <button
                type="button"
                onClick={handleDelete}
                disabled={deleting || !canWrite}
                className="msm-btn-secondary px-4 py-2 inline-flex items-center gap-2 text-status-destructive hover:bg-status-destructive/10 disabled:opacity-50"
              >
                <Trash2 className="w-4 h-4" />
                {t('settings.curseforgeDeleteKey', { defaultValue: 'Schlüssel entfernen' })}
              </button>
            )}
            <button
              type="button"
              onClick={handleTest}
              disabled={testing || !settings.curseforge_api_configured}
              className="msm-btn-secondary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
            >
              {testing ? (
                <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
              {t('settings.curseforgeTest', { defaultValue: 'Verbindung testen' })}
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving || !newKey.trim() || !canWrite}
              className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
            >
              {saving ? (
                <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              {t('settings.curseforgeSaveKey', { defaultValue: 'API-Schlüssel speichern' })}
            </button>
          </div>
        </div>
      </div>
    </fieldset>
  )
}
