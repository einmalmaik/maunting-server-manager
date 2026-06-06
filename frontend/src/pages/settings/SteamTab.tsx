import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Save, Send, Gamepad2, AlertTriangle } from 'lucide-react'
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
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
            <Gamepad2 className="w-5 h-5 text-secondary" />
          </div>
          <h2 className="font-headline text-headline-sm text-primary">{t('settings.steamApiKey')}</h2>
        </div>

        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${settings.steam_api_configured ? 'bg-status-success' : 'bg-on-surface-variant'}`} />
            <span className="font-body-md text-sm text-on-surface">
              {settings.steam_api_configured ? t('settings.steamConfigured') : t('settings.steamNotConfigured')}
            </span>
          </div>

          {settings.steam_api_key && (
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('settings.steamCurrentKey')}
              </label>
              <input
                type="text"
                value={settings.steam_api_key}
                readOnly
                className="msm-input opacity-60 cursor-not-allowed font-mono text-sm"
              />
            </div>
          )}

          <div>
            <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('settings.steamNewKey')}
            </label>
            <PasswordInput
              value={newSteamKey}
              onChange={(e) => setNewSteamKey(e.target.value)}
              placeholder="XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
            />
            <p className="font-body-md text-xs text-on-surface-variant mt-2">
              {t('settings.steamKeyHint')}{' '}
              <a href="https://steamcommunity.com/dev/apikey" target="_blank" rel="noopener noreferrer" className="text-secondary hover:underline">
                steamcommunity.com/dev/apikey
              </a>
            </p>
          </div>

          <div className="flex gap-3 justify-end">
            <button
              type="button"
              onClick={async () => {
                setTestingSteam(true)
                try {
                  const res = await api<{ message: string; valid: boolean }>('/settings/steam-key/test')
                  toast.success(res.message)
                } catch (err: any) {
                  toast.error(err.message)
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
                if (!newSteamKey.trim()) return
                setSavingSteam(true)
                try {
                  await api('/settings/steam-key', {
                    method: 'POST',
                    body: JSON.stringify({ steam_api_key: newSteamKey.trim() }),
                  })
                  toast.success(t('settings.steamSaved'))
                  setNewSteamKey('')
                  await fetchSettings()
                } catch (err: any) {
                  toast.error(err.message)
                } finally {
                  setSavingSteam(false)
                }
              }}
              disabled={savingSteam || !newSteamKey.trim() || !canWrite}
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
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
            <Gamepad2 className="w-5 h-5 text-secondary" />
          </div>
          <h2 className="font-headline text-headline-sm text-primary">{t('settings.steamAccountTitle')}</h2>
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
