import { useEffect, useState } from 'react'
import { Globe, Save, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

/**
 * Suchschlüssel für die KI-Websuche.
 *
 * Der Schlüssel verlässt das Backend nie — die API meldet ausschließlich, *ob*
 * einer hinterlegt ist. Deshalb gibt es hier auch keinen Hinweis auf die
 * letzten Zeichen wie bei den Provider-Keys: bei einem Suchdienst hilft das
 * beim Wiedererkennen nicht, und jedes Fragment ist ein Fragment zu viel.
 */
export function AiWebSearchSettings({ canWrite }: { canWrite: boolean }) {
  const { t } = useTranslation()
  const [hasApiKey, setHasApiKey] = useState(false)
  const [currentSearxngUrl, setCurrentSearxngUrl] = useState('')
  const [defaultSearxngUrl, setDefaultSearxngUrl] = useState('http://127.0.0.1:8888')
  const [isDefaultSearxng, setIsDefaultSearxng] = useState(true)
  const [sidecarRunning, setSidecarRunning] = useState(false)
  const [apiKey, setApiKey] = useState('')
  const [searxngUrl, setSearxngUrl] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    aiApi.getWebSearchStatus()
      .then((status) => {
        if (active) {
          setHasApiKey(Boolean(status.has_api_key))
          const customUrl = status.custom_searxng_url || ''
          setCurrentSearxngUrl(customUrl)
          setSearxngUrl(customUrl)
          if (status.default_searxng_url) {
            setDefaultSearxngUrl(status.default_searxng_url)
          }
          setIsDefaultSearxng(status.is_default_searxng ?? !customUrl)
          setSidecarRunning(Boolean(status.sidecar_running))
        }
      })
      .catch(() => { if (active) toast.error(t('ai.webSearch.errors.load')) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [t])

  const saveBraveKey = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!canWrite || busy || !apiKey.trim()) return
    setBusy(true)
    try {
      const status = await aiApi.setWebSearchConfig({ apiKey: apiKey.trim() })
      setHasApiKey(Boolean(status.has_api_key))
      setApiKey('')
      toast.success(t('ai.webSearch.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.webSearch.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  const removeBraveKey = async () => {
    if (!canWrite || busy) return
    if (!await confirm({
      message: t('ai.webSearch.remove'),
      confirmText: t('common.delete'),
      danger: true,
    })) return
    setBusy(true)
    try {
      const status = await aiApi.setWebSearchConfig({ apiKey: '' })
      setHasApiKey(Boolean(status.has_api_key))
      toast.success(t('ai.webSearch.removed'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.webSearch.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  const saveSearxng = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!canWrite || busy) return
    setBusy(true)
    try {
      const status = await aiApi.setWebSearchConfig({ searxngUrl: searxngUrl.trim() })
      const customUrl = status.custom_searxng_url || ''
      setCurrentSearxngUrl(customUrl)
      setSearxngUrl(customUrl)
      if (status.default_searxng_url) {
        setDefaultSearxngUrl(status.default_searxng_url)
      }
      setIsDefaultSearxng(status.is_default_searxng ?? !customUrl)
      setSidecarRunning(Boolean(status.sidecar_running))
      toast.success(t('ai.webSearch.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.webSearch.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  const removeSearxng = async () => {
    if (!canWrite || busy) return
    if (!await confirm({
      message: t('ai.webSearch.removeSearxng'),
      confirmText: t('common.delete'),
      danger: true,
    })) return
    setBusy(true)
    try {
      const status = await aiApi.setWebSearchConfig({ searxngUrl: '' })
      const customUrl = status.custom_searxng_url || ''
      setCurrentSearxngUrl(customUrl)
      setSearxngUrl(customUrl)
      if (status.default_searxng_url) {
        setDefaultSearxngUrl(status.default_searxng_url)
      }
      setIsDefaultSearxng(status.is_default_searxng ?? true)
      setSidecarRunning(Boolean(status.sidecar_running))
      toast.success(t('ai.webSearch.removed'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.webSearch.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  if (loading) return null

  return (
    <section className="msm-card space-y-5 p-6" aria-labelledby="ai-web-search-title">
      <div className="flex items-center gap-2">
        <Globe className="h-5 w-5 text-secondary" aria-hidden="true" />
        <h3 id="ai-web-search-title" className="font-headline text-lg font-semibold text-on-surface">
          {t('ai.webSearch.title')}
        </h3>
      </div>
      <p className="max-w-3xl text-sm text-on-surface-variant">{t('ai.webSearch.description')}</p>

      {/* 1. Brave Search API Key */}
      <form className="flex flex-wrap items-end gap-3" onSubmit={saveBraveKey}>
        <label className="min-w-[16rem] flex-1 space-y-1.5">
          <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
            {t('ai.webSearch.apiKey')}
          </span>
          <input
            className="msm-input"
            type="password"
            autoComplete="new-password"
            maxLength={512}
            value={apiKey}
            disabled={!canWrite || busy}
            placeholder={hasApiKey ? t('ai.webSearch.configured') : t('ai.webSearch.placeholder')}
            onChange={(event) => setApiKey(event.target.value)}
            aria-label={t('ai.webSearch.apiKey')}
          />
        </label>
        {canWrite && (
          <>
            <Button type="submit" disabled={busy || !apiKey.trim()}>
              <Save className="h-4 w-4" aria-hidden="true" />
              {t('settings.save')}
            </Button>
            {hasApiKey && (
              <Button type="button" variant="destructive" disabled={busy} onClick={() => void removeBraveKey()}>
                <Trash2 className="h-4 w-4" aria-hidden="true" />
                {t('common.delete')}
              </Button>
            )}
          </>
        )}
      </form>

      {/* 2. SearXNG URL (Self-Hosted / Sidecar) */}
      <div className="space-y-3 border-t border-outline-variant/30 pt-4">
        {isDefaultSearxng ? (
          sidecarRunning ? (
            <div
              data-testid="searxng-sidecar-status"
              className="flex items-center gap-2 rounded-lg border border-success/30 bg-success/10 px-3 py-2 text-xs text-success"
            >
              <span className="h-2 w-2 rounded-full bg-success animate-pulse" aria-hidden="true" />
              <span>{t('ai.webSearch.sidecarActive', { url: defaultSearxngUrl })}</span>
            </div>
          ) : (
            <div
              data-testid="searxng-sidecar-status"
              className="flex items-center gap-2 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning"
            >
              <span className="h-2 w-2 rounded-full bg-warning" aria-hidden="true" />
              <span>{t('ai.webSearch.sidecarInactive', { url: defaultSearxngUrl })}</span>
            </div>
          )
        ) : (
          <div
            data-testid="searxng-sidecar-status"
            className="flex items-center gap-2 rounded-lg border border-secondary/30 bg-secondary/10 px-3 py-2 text-xs text-secondary"
          >
            <span className="h-2 w-2 rounded-full bg-secondary" aria-hidden="true" />
            <span>
              {t('ai.webSearch.customEndpointActive', {
                url: currentSearxngUrl,
                defaultUrl: defaultSearxngUrl,
              })}
            </span>
          </div>
        )}

        <form className="flex flex-wrap items-end gap-3" onSubmit={saveSearxng}>
          <label className="min-w-[16rem] flex-1 space-y-1.5">
            <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('ai.webSearch.searxngUrl')}
            </span>
            <input
              className="msm-input"
              type="text"
              maxLength={512}
              value={searxngUrl}
              disabled={!canWrite || busy}
              placeholder={t('ai.webSearch.searxngDefaultPlaceholder', { url: defaultSearxngUrl })}
              onChange={(event) => setSearxngUrl(event.target.value)}
              aria-label={t('ai.webSearch.searxngUrl')}
            />
          </label>
          {canWrite && (
            <>
              <Button type="submit" disabled={busy || searxngUrl.trim() === currentSearxngUrl}>
                <Save className="h-4 w-4" aria-hidden="true" />
                {t('settings.save')}
              </Button>
              {Boolean(currentSearxngUrl) && (
                <Button
                  type="button"
                  variant="destructive"
                  disabled={busy}
                  onClick={() => void removeSearxng()}
                  title={t('ai.webSearch.resetToDefault')}
                >
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                  {t('common.delete')}
                </Button>
              )}
            </>
          )}
        </form>

        <p className="text-xs text-on-surface-variant">
          {isDefaultSearxng
            ? t('ai.webSearch.searxngDefaultHint')
            : t('ai.webSearch.customEndpointActive', {
                url: currentSearxngUrl,
                defaultUrl: defaultSearxngUrl,
              })}
        </p>
      </div>

      <p className="text-xs text-on-surface-variant">{t('ai.webSearch.hint')}</p>
    </section>
  )
}
