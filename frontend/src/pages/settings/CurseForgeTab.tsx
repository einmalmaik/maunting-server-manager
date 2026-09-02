import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Save, Send, Flame, Trash2, Undo2 } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { PanelSettings, EMPTY_PANEL_SETTINGS } from './types'

export function CurseForgeTab() {
  const { t } = useTranslation()
  const canWrite = useHasPermission('panel.settings.write')
  const [settings, setSettings] = useState<PanelSettings>(EMPTY_PANEL_SETTINGS)
  const [loading, setLoading] = useState(true)
  const [newKey, setNewKey] = useState('')
  const [clearKey, setClearKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)

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

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      if (clearKey) {
        await api('/settings/curseforge-api-key', { method: 'DELETE' })
        setClearKey(false)
        setNewKey('')
        toast.success(t('settings.curseforgeRemoved', { defaultValue: 'CurseForge API-Schlüssel entfernt' }))
      } else if (newKey.trim()) {
        await api('/settings/curseforge-api-key', {
          method: 'POST',
          body: JSON.stringify({ curseforge_api_key: newKey.trim() }),
        })
        toast.success(t('settings.curseforgeSaved', { defaultValue: 'CurseForge API-Schlüssel gespeichert' }))
        setNewKey('')
      }
      await fetchSettings()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
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
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setTesting(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const hasChanges = Boolean(newKey.trim() || clearKey)
  const sourceLabel = settings.curseforge_api_source === 'env' ? '.env' : 'Panel-DB'

  return (
    <fieldset disabled={!canWrite} className="space-y-6 border-0 p-0 m-0">
      <div className="msm-card p-6">
        <div className="flex items-center gap-2 mb-6">
          <Flame className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 className="font-headline text-lg font-semibold text-on-surface">
            {t('settings.curseforgeApiKey', { defaultValue: 'CurseForge API-Schlüssel' })}
          </h2>
        </div>

        <form onSubmit={handleSave} className="space-y-4">
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
                  ({sourceLabel})
                </span>
              )}
            </span>
          </div>

          {/* Einheitliches API-Key Eingabefeld */}
          <div className="rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4 space-y-2">
            <div className="flex items-center justify-between">
              <label className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('settings.curseforgeApiKey', { defaultValue: 'CurseForge API-Schlüssel' })}
              </label>
              {clearKey ? (
                <span className="text-xs text-status-warning flex items-center gap-1.5">
                  {t('settings.keyWillBeCleared', { defaultValue: 'Wird beim Speichern entfernt' })}
                  <button
                    type="button"
                    onClick={() => setClearKey(false)}
                    className="font-medium text-primary hover:underline inline-flex items-center gap-1"
                  >
                    <Undo2 className="h-3 w-3" />
                    {t('common.undo', { defaultValue: 'Rückgängig' })}
                  </button>
                </span>
              ) : settings.curseforge_api_configured && canWrite ? (
                <button
                  type="button"
                  onClick={() => {
                    setClearKey(true)
                    setNewKey('')
                  }}
                  className="inline-flex items-center gap-1 text-xs text-on-surface-variant hover:text-status-error transition-colors"
                  title={t('settings.curseforgeDeleteKey', { defaultValue: 'Schlüssel entfernen' })}
                  aria-label={t('settings.curseforgeDeleteKey', { defaultValue: 'Schlüssel entfernen' })}
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>{t('settings.curseforgeDeleteKey', { defaultValue: 'Schlüssel entfernen' })}</span>
                </button>
              ) : null}
            </div>

            <PasswordInput
              value={newKey}
              disabled={!canWrite || saving || clearKey}
              onChange={(e) => {
                setNewKey(e.target.value)
                setClearKey(false)
              }}
              placeholder={
                clearKey
                  ? t('settings.keyWillBeCleared', { defaultValue: 'Wird beim Speichern entfernt' })
                  : settings.curseforge_api_configured
                    ? t('settings.keyConfiguredHint', {
                        defaultValue: 'Schlüssel hinterlegt; leer lassen, um ihn beizubehalten',
                      })
                    : 'XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX'
              }
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

          <div className="flex gap-3 justify-end flex-wrap pt-2">
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
              type="submit"
              disabled={saving || !hasChanges || !canWrite}
              className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
            >
              {saving ? (
                <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              {t('settings.curseforgeSaveKey', { defaultValue: 'Einstellungen speichern' })}
            </button>
          </div>
        </form>
      </div>
    </fieldset>
  )
}
