import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Save, Send, Gamepad2, AlertTriangle, Trash2, Undo2 } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { PanelSettings, EMPTY_PANEL_SETTINGS } from './types'

export function SteamTab() {
  const { t } = useTranslation()
  const canWrite = useHasPermission('panel.settings.write')
  const [settings, setSettings] = useState<PanelSettings>(EMPTY_PANEL_SETTINGS)
  const [loading, setLoading] = useState(true)
  const [steamAccountUsername, setSteamAccountUsername] = useState('')
  const [steamAccountPassword, setSteamAccountPassword] = useState('')
  const [savingSteamAccount, setSavingSteamAccount] = useState(false)
  const [newSteamKey, setNewSteamKey] = useState('')
  const [clearSteamKey, setClearSteamKey] = useState(false)
  const [savingSteam, setSavingSteam] = useState(false)
  const [testingSteam, setTestingSteam] = useState(false)

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

  useEffect(() => { fetchSettings() }, [])

  const handleSaveSteamAccount = async () => {
    if (!steamAccountUsername.trim() || !steamAccountPassword) return
    setSavingSteamAccount(true)
    try {
      await api('/settings/steam-account', {
        method: 'POST',
        body: JSON.stringify({
          username: steamAccountUsername.trim(),
          password: steamAccountPassword,
        }),
      })
      toast.success(t('settings.steamAccountSaved'))
      setSteamAccountUsername('')
      setSteamAccountPassword('')
      await fetchSettings()
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setSavingSteamAccount(false)
    }
  }

  const handleRemoveSteamAccount = async () => {
    try {
      await api('/settings/steam-account', { method: 'DELETE' })
      toast.success(t('settings.steamAccountRemoved'))
      await fetchSettings()
    } catch (err: any) {
      toast.error(err.message)
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
      {/* Steam API */}
      <div className="msm-card p-6">
        <div className="flex items-center gap-2 mb-6">
          <Gamepad2 className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 className="font-headline text-lg font-semibold text-on-surface">{t('settings.steamApiKey')}</h2>
        </div>

        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${settings.steam_api_configured ? 'bg-status-success' : 'bg-on-surface-variant'}`} />
            <span className="font-body-md text-sm text-on-surface">
              {settings.steam_api_configured ? t('settings.steamConfigured') : t('settings.steamNotConfigured')}
              {settings.steam_api_source && settings.steam_api_source !== 'none' && (
                <span className="ml-2 font-mono text-xs text-on-surface-variant">
                  ({settings.steam_api_source === 'env' ? '.env' : 'Panel-DB'})
                </span>
              )}
            </span>
          </div>

          {/* Einheitliches API-Key Eingabefeld */}
          <div className="rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4 space-y-2">
            <div className="flex items-center justify-between">
              <label className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t('settings.steamApiKey')}
              </label>
              {clearSteamKey ? (
                <span className="text-xs text-status-warning flex items-center gap-1.5">
                  {t('settings.keyWillBeCleared', { defaultValue: 'Wird beim Speichern entfernt' })}
                  <button
                    type="button"
                    onClick={() => setClearSteamKey(false)}
                    className="font-medium text-primary hover:underline inline-flex items-center gap-1"
                  >
                    <Undo2 className="h-3 w-3" />
                    {t('common.undo', { defaultValue: 'Rückgängig' })}
                  </button>
                </span>
              ) : settings.steam_api_configured && canWrite ? (
                <button
                  type="button"
                  onClick={() => {
                    setClearSteamKey(true)
                    setNewSteamKey('')
                  }}
                  className="inline-flex items-center gap-1 text-xs text-on-surface-variant hover:text-status-error transition-colors"
                  title={t('settings.steamDeleteKey', { defaultValue: 'Schlüssel entfernen' })}
                  aria-label={t('settings.steamDeleteKey', { defaultValue: 'Schlüssel entfernen' })}
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>{t('settings.steamDeleteKey', { defaultValue: 'Schlüssel entfernen' })}</span>
                </button>
              ) : null}
            </div>

            <PasswordInput
              value={newSteamKey}
              disabled={!canWrite || savingSteam || clearSteamKey}
              onChange={(e) => {
                setNewSteamKey(e.target.value)
                setClearSteamKey(false)
              }}
              placeholder={
                clearSteamKey
                  ? t('settings.keyWillBeCleared', { defaultValue: 'Wird beim Speichern entfernt' })
                  : settings.steam_api_configured
                    ? t('settings.keyConfiguredHint', {
                        defaultValue: 'Schlüssel hinterlegt; leer lassen, um ihn beizubehalten',
                      })
                    : 'XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX'
              }
            />

            <p className="msm-field-help">
              {t('settings.steamKeyHint')}{' '}
              <a href="https://steamcommunity.com/dev/apikey" target="_blank" rel="noopener noreferrer" className="text-secondary hover:underline">
                steamcommunity.com/dev/apikey
              </a>
            </p>
          </div>

          <div className="flex gap-3 justify-end flex-wrap pt-2">
            <button
              type="button"
              onClick={async () => {
                setTestingSteam(true)
                try {
                  const res = await api<{ message: string; valid: boolean }>('/settings/steam-key/test', {
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
                  setTestingSteam(false)
                }
              }}
              disabled={testingSteam || !settings.steam_api_configured}
              className="msm-btn-secondary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
            >
              {testingSteam ? (
                <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
              {t('settings.steamTest')}
            </button>
            <button
              type="button"
              onClick={async () => {
                if (!newSteamKey.trim() && !clearSteamKey) return
                setSavingSteam(true)
                try {
                  if (clearSteamKey) {
                    await api('/settings/steam-key', {
                      method: 'POST',
                      body: JSON.stringify({ steam_api_key: '' }),
                    })
                    setClearSteamKey(false)
                    setNewSteamKey('')
                  } else if (newSteamKey.trim()) {
                    await api('/settings/steam-key', {
                      method: 'POST',
                      body: JSON.stringify({ steam_api_key: newSteamKey.trim() }),
                    })
                    setNewSteamKey('')
                  }
                  toast.success(t('settings.steamSaved'))
                  await fetchSettings()
                } catch (err: unknown) {
                  toast.error(err instanceof Error ? err.message : String(err))
                } finally {
                  setSavingSteam(false)
                }
              }}
              disabled={savingSteam || (!newSteamKey.trim() && !clearSteamKey) || !canWrite}
              className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
            >
              {savingSteam ? (
                <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              {t('settings.steamSaveKey')}
            </button>
          </div>
        </div>
      </div>

      {/* Steam Account */}
      <div className="msm-card p-6">
        <div className="flex items-center gap-2 mb-6">
          <Gamepad2 className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 className="font-headline text-lg font-semibold text-on-surface">{t('settings.steamAccountTitle')}</h2>
        </div>

        <div className="space-y-4">
          <div className="p-3 bg-status-error/10 border border-status-error/30 rounded-md text-sm text-status-error flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
            <span>{t('settings.steamAccountWarning')}</span>
          </div>

          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${settings.steam_account_configured ? 'bg-status-success' : 'bg-on-surface-variant'}`} />
            <span className="font-body-md text-sm text-on-surface">
              {settings.steam_account_configured
                ? `${t('settings.steamAccountConfigured')} (${settings.steam_account_username})`
                : t('settings.steamAccountNotConfigured')}
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('settings.steamAccountUsername')}
              </label>
              <input
                type="text"
                value={steamAccountUsername}
                onChange={(e) => setSteamAccountUsername(e.target.value)}
                className="msm-input"
                placeholder="steamuser"
              />
            </div>
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('settings.steamAccountPassword')}
              </label>
              <PasswordInput
                value={steamAccountPassword}
                onChange={(e) => setSteamAccountPassword(e.target.value)}
                placeholder="••••••••"
              />
            </div>
          </div>

          <div className="flex gap-3 justify-end">
            {settings.steam_account_configured && (
              <button
                type="button"
                onClick={handleRemoveSteamAccount}
                disabled={!canWrite}
                className="msm-btn-secondary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
              >
                {t('settings.steamAccountRemove')}
              </button>
            )}
            <button
              type="button"
              onClick={handleSaveSteamAccount}
              disabled={savingSteamAccount || !steamAccountUsername.trim() || !steamAccountPassword || !canWrite}
              className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
            >
              {savingSteamAccount ? (
                <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              {t('settings.steamAccountSave')}
            </button>
          </div>
        </div>
      </div>
    </fieldset>
  )
}
