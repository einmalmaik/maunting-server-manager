import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Save, Send, Github, AlertTriangle, Trash2, Undo2 } from 'lucide-react'
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
  const [clearToken, setClearToken] = useState(false)
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
      if (clearToken) {
        await api('/settings/github-token', { method: 'DELETE' })
        setClearToken(false)
        setNewToken('')
        toast.success(t('settings.githubRemoved'))
      } else if (newToken.trim()) {
        await api('/settings/github-token', {
          method: 'POST',
          body: JSON.stringify({ github_token: newToken.trim() }),
        })
        toast.success(t('settings.githubSaved'))
        setNewToken('')
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
      const res = await api<{ message: string; valid: boolean }>('/settings/github-token/test')
      toast[res.valid ? 'success' : 'error'](res.message)
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

  const hasChanges = Boolean(newToken.trim() || clearToken)
  const sourceLabel = settings.github_token_source === 'env' ? '.env' : 'Panel-DB'

  return (
    <fieldset disabled={!canWrite} className="space-y-6 border-0 p-0 m-0">
      <div className="msm-card p-6">
        <div className="flex items-center gap-2 mb-6">
          <Github className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 className="font-headline text-lg font-semibold text-on-surface">{t('settings.githubTokenTitle')}</h2>
        </div>

        <form onSubmit={handleSave} className="space-y-4">
          <div className="p-3 bg-status-info/10 border border-status-info/30 rounded-md text-sm text-on-surface flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
            <span>{t('settings.githubWhyNeeded')}</span>
          </div>

          <div className="flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-full ${
                settings.github_token_configured ? 'bg-status-success' : 'bg-on-surface-variant'
              }`}
            />
            <span className="font-body-md text-sm text-on-surface">
              {settings.github_token_configured
                ? t('settings.githubConfigured')
                : t('settings.githubNotConfigured')}
              {settings.github_token_source && settings.github_token_source !== 'none' && (
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
                {t('settings.githubNewToken')}
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
              ) : settings.github_token_configured && canWrite ? (
                <button
                  type="button"
                  onClick={() => {
                    setClearToken(true)
                    setNewToken('')
                  }}
                  className="inline-flex items-center gap-1 text-xs text-on-surface-variant hover:text-status-error transition-colors"
                  title={t('settings.githubRemove')}
                  aria-label={t('settings.githubRemove')}
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>{t('settings.githubRemove')}</span>
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
                  : settings.github_token_configured
                    ? t('settings.keyConfiguredHint', {
                        defaultValue: 'Schlüssel hinterlegt; leer lassen, um ihn beizubehalten',
                      })
                    : 'ghp_xxxxxxxxxxxxxxxxxxxx oder github_pat_xxxxxxxx'
              }
            />

            <p className="msm-field-help">
              {t('settings.githubTokenHint')}{' '}
              <a
                href="https://github.com/settings/tokens"
                target="_blank"
                rel="noopener noreferrer"
                className="text-secondary hover:underline"
              >
                github.com/settings/tokens
              </a>
            </p>
          </div>

          <div className="flex gap-3 justify-end flex-wrap pt-2">
            <button
              type="button"
              onClick={handleTest}
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
              {t('settings.githubSave')}
            </button>
          </div>
        </form>
      </div>
    </fieldset>
  )
}
