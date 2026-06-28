import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Save, Send, Github, AlertTriangle, Trash2 } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { PanelSettings, EMPTY_PANEL_SETTINGS } from './types'

export function GitHubTab() {
  const { t } = useTranslation()
  const canWrite = useHasPermission('panel.settings.write')
  const [settings, setSettings] = useState<PanelSettings>(EMPTY_PANEL_SETTINGS)
  const [loading, setLoading] = useState(true)
  const [newToken, setNewToken] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)

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

  const handleSave = async () => {
    if (!newToken.trim()) return
    setSaving(true)
    try {
      await api('/settings/github-token', {
        method: 'POST',
        body: JSON.stringify({ github_token: newToken.trim() }),
      })
      toast.success(t('settings.githubSaved'))
      setNewToken('')
      await fetchSettings()
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setSaving(false)
    }
  }

  const handleRemove = async () => {
    try {
      await api('/settings/github-token', { method: 'DELETE' })
      toast.success(t('settings.githubRemoved'))
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

  const sourceLabel =
    settings.github_token_source === 'env'
      ? t('settings.githubSourceEnv')
      : settings.github_token_source === 'panel'
        ? t('settings.githubSourcePanel')
        : t('settings.githubSourceNone')

  return (
    <fieldset disabled={!canWrite} className="space-y-6 border-0 p-0 m-0">
      <div className="msm-card p-6">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
            <Github className="w-5 h-5 text-secondary" />
          </div>
          <h2 className="font-headline text-headline-sm text-primary">{t('settings.githubTokenTitle')}</h2>
        </div>

        <div className="space-y-4">
          <div className="p-3 bg-status-info/10 border border-status-info/30 rounded-md text-sm text-on-surface flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
            <span>{t('settings.githubWhyNeeded')}</span>
          </div>

          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${settings.github_token_configured ? 'bg-status-success' : 'bg-on-surface-variant'}`} />
            <span className="font-body-md text-sm text-on-surface">
              {settings.github_token_configured
                ? `${t('settings.githubConfigured')} (${sourceLabel})`
                : t('settings.githubNotConfigured')}
            </span>
          </div>

          <div>
            <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
              {t('settings.githubNewToken')}
            </label>
            <PasswordInput
              value={newToken}
              onChange={(e) => setNewToken(e.target.value)}
              placeholder="ghp_xxxxxxxxxxxxxxxxxxxx oder github_pat_xxxxxxxx"
            />
            <p className="font-body-md text-xs text-on-surface-variant mt-2">
              {t('settings.githubTokenHint')}{' '}
              <a href="https://github.com/settings/tokens" target="_blank" rel="noopener noreferrer" className="text-secondary hover:underline">
                github.com/settings/tokens
              </a>
            </p>
          </div>

          <div className="flex gap-3 justify-end">
            <button
              type="button"
              onClick={async () => {
                setTesting(true)
                try {
                  const res = await api<{ message: string; valid: boolean }>('/settings/github-token/test')
                  toast[res.valid ? 'success' : 'error'](res.message)
                } catch (err: any) {
                  toast.error(err.message)
                } finally {
                  setTesting(false)
                }
              }}
              disabled={testing || !settings.github_token_configured}
              className="msm-btn-secondary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
            >
              {testing ? (
                <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
              {t('settings.githubTest')}
            </button>
            {settings.github_token_configured && settings.github_token_source === 'panel' && (
              <button
                type="button"
                onClick={handleRemove}
                disabled={!canWrite}
                className="msm-btn-secondary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
              >
                <Trash2 className="w-4 h-4" />
                {t('settings.githubRemove')}
              </button>
            )}
            <button
              type="button"
              onClick={handleSave}
              disabled={saving || !newToken.trim() || !canWrite}
              className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
            >
              {saving ? (
                <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              {t('settings.githubSave')}
            </button>
          </div>
        </div>
      </div>
    </fieldset>
  )
}
