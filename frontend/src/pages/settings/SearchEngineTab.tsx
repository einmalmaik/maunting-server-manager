import { useEffect, useState } from 'react'
import { Globe, KeyRound, Save, Search, ShieldCheck, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiWebSearchStatus } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Badge, Button, Input, Kurzinfo, PasswordInput, Spinner, Switch } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

/**
 * Zentraler Einstellungen-Tab „Suchmaschine“ (Maunting Search).
 *
 * Hier verwaltet der Betreiber an genau einer Stelle:
 * 1. Den SearXNG-Metasuch-Endpunkt (lokaler Sidecar `127.0.0.1:8888` oder
 *    eigene Instanz-URL).
 * 2. Offizielle Such-API-Schlüssel (Brave Search API, Google Programmable
 *    Search / CSE), von denen sowohl Maunting Search im Secure Browser als
 *    auch Singra (KI-Websuche) profitieren.
 * 3. Den Schutzschalter für fremde SearXNG-Instanzen, damit bei einer externen
 *    Instanz nicht ungewollt eigenes API-Guthaben verbraucht wird.
 */
export function SearchEngineTab() {
  const { t } = useTranslation()
  const canRead = useHasPermission('panel.settings.read')
  const canWrite = useHasPermission('panel.settings.write')

  const [loading, setLoading] = useState(canRead)
  const [busy, setBusy] = useState(false)

  const [hasBraveKey, setHasBraveKey] = useState(false)
  const [braveKey, setBraveKey] = useState('')

  const [hasGoogleCse, setHasGoogleCse] = useState(false)
  const [googleApiKey, setGoogleApiKey] = useState('')
  const [googleCx, setGoogleCx] = useState('')
  const [savedGoogleCx, setSavedGoogleCx] = useState('')

  const [currentSearxngUrl, setCurrentSearxngUrl] = useState('')
  const [defaultSearxngUrl, setDefaultSearxngUrl] = useState('http://127.0.0.1:8888')
  const [isDefaultSearxng, setIsDefaultSearxng] = useState(true)
  const [sidecarRunning, setSidecarRunning] = useState(false)
  const [searxngUrl, setSearxngUrl] = useState('')
  const [externalApisOnCustom, setExternalApisOnCustom] = useState(false)

  const applyStatus = (status: AiWebSearchStatus) => {
    setHasBraveKey(Boolean(status.has_api_key))
    setHasGoogleCse(Boolean(status.has_google_cse))
    const cx = status.google_cse_cx || ''
    setSavedGoogleCx(cx)
    setGoogleCx(cx)
    const customUrl = status.custom_searxng_url || ''
    setCurrentSearxngUrl(customUrl)
    setSearxngUrl(customUrl)
    if (status.default_searxng_url) {
      setDefaultSearxngUrl(status.default_searxng_url)
    }
    setIsDefaultSearxng(status.is_default_searxng ?? !customUrl)
    setSidecarRunning(Boolean(status.sidecar_running))
    setExternalApisOnCustom(Boolean(status.external_apis_on_custom))
  }

  useEffect(() => {
    if (!canRead) {
      setLoading(false)
      return
    }
    let active = true
    aiApi
      .getWebSearchStatus()
      .then((status) => {
        if (active) applyStatus(status)
      })
      .catch(() => {
        if (active) toast.error(t('ai.webSearch.errors.load'))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [canRead, t])

  const saveSearxng = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!canWrite || busy) return
    setBusy(true)
    try {
      const status = await aiApi.setWebSearchConfig({ searxngUrl: searxngUrl.trim() })
      applyStatus(status)
      toast.success(t('ai.webSearch.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.webSearch.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  const removeSearxng = async () => {
    if (!canWrite || busy) return
    if (
      !(await confirm({
        message: t('ai.webSearch.removeSearxng'),
        confirmText: t('common.delete'),
        danger: true,
      }))
    )
      return
    setBusy(true)
    try {
      const status = await aiApi.setWebSearchConfig({ searxngUrl: '' })
      applyStatus(status)
      toast.success(t('ai.webSearch.removed'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.webSearch.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  const saveBraveKey = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!canWrite || busy || !braveKey.trim()) return
    setBusy(true)
    try {
      const status = await aiApi.setWebSearchConfig({ apiKey: braveKey.trim() })
      applyStatus(status)
      setBraveKey('')
      toast.success(t('ai.webSearch.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.webSearch.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  const removeBraveKey = async () => {
    if (!canWrite || busy) return
    if (
      !(await confirm({
        message: t('ai.webSearch.remove'),
        confirmText: t('common.delete'),
        danger: true,
      }))
    )
      return
    setBusy(true)
    try {
      const status = await aiApi.setWebSearchConfig({ apiKey: '' })
      applyStatus(status)
      toast.success(t('ai.webSearch.removed'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.webSearch.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  const saveGoogleCse = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!canWrite || busy) return
    const keyTrimmed = googleApiKey.trim()
    const cxTrimmed = googleCx.trim()
    if (!cxTrimmed || (!keyTrimmed && !hasGoogleCse)) return
    setBusy(true)
    try {
      const status = await aiApi.setWebSearchConfig({
        googleCseApiKey: keyTrimmed || undefined,
        googleCseCx: cxTrimmed,
      })
      applyStatus(status)
      setGoogleApiKey('')
      toast.success(t('ai.webSearch.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.webSearch.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  const removeGoogleCse = async () => {
    if (!canWrite || busy) return
    if (
      !(await confirm({
        message: t('searchEngineSettings.googleRemoveConfirm'),
        confirmText: t('common.delete'),
        danger: true,
      }))
    )
      return
    setBusy(true)
    try {
      const status = await aiApi.setWebSearchConfig({
        googleCseApiKey: '',
        googleCseCx: '',
      })
      applyStatus(status)
      setGoogleApiKey('')
      toast.success(t('ai.webSearch.removed'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.webSearch.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  const toggleExternalApisOnCustom = async (next: boolean) => {
    if (!canWrite || busy) return
    setBusy(true)
    try {
      const status = await aiApi.setWebSearchConfig({ externalApisOnCustom: next })
      applyStatus(status)
      toast.success(t('ai.webSearch.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.webSearch.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  if (!canRead) {
    return <div className="msm-card p-6 text-sm text-on-surface-variant">{t('aiSettings.noPermission')}</div>
  }

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner size="lg" className="text-primary" />
      </div>
    )
  }

  const apisPausedByCustomInstance = !isDefaultSearxng && !externalApisOnCustom

  return (
    <div className="space-y-6">
      {/* 1. Metasuch-Instanz (SearXNG Sidecar / Eigene Instanz) */}
      <section className="msm-card space-y-5 p-6" aria-labelledby="search-engine-instance-title">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Search className="h-5 w-5 text-primary" aria-hidden="true" />
            <h2 id="search-engine-instance-title" className="font-headline text-title-lg font-semibold text-on-surface">
              {t('searchEngineSettings.title')}
            </h2>
          </div>
          <Badge variant="info">{t('searchEngineSettings.badge')}</Badge>
        </div>

        <p className="max-w-3xl text-sm text-on-surface-variant">
          {t('searchEngineSettings.description')}
        </p>

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
          <div className="min-w-[16rem] flex-1">
            <Input
              label={t('ai.webSearch.searxngUrl')}
              type="text"
              maxLength={512}
              value={searxngUrl}
              disabled={!canWrite || busy}
              placeholder={t('ai.webSearch.searxngDefaultPlaceholder', { url: defaultSearxngUrl })}
              onChange={(event) => setSearxngUrl(event.target.value)}
              aria-label={t('ai.webSearch.searxngUrl')}
            />
          </div>
          {canWrite && (
            <>
              <Button type="submit" disabled={busy || searxngUrl.trim() === currentSearxngUrl}>
                <Save className="h-4 w-4" aria-hidden="true" />
                {t('settings.save')}
              </Button>
              {Boolean(currentSearxngUrl) && (
                <Kurzinfo text={t('ai.webSearch.resetToDefault')}>
                  <Button
                    type="button"
                    variant="destructive"
                    disabled={busy}
                    onClick={() => void removeSearxng()}
                    aria-description={t('ai.webSearch.resetToDefault')}
                  >
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                    {t('common.delete')}
                  </Button>
                </Kurzinfo>
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

        {/* Schutzschalter bei fremder SearXNG-Adresse */}
        {!isDefaultSearxng && (
          <div
            data-testid="custom-searxng-api-guard"
            className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-warning/35 bg-warning/10 p-4"
          >
            <div className="max-w-2xl space-y-1">
              <div className="text-sm font-semibold text-on-surface">
                {t('searchEngineSettings.customInstanceGuardTitle')}
              </div>
              <p className="text-xs leading-relaxed text-on-surface-variant">
                {t('searchEngineSettings.customInstanceGuardHint')}
              </p>
            </div>
            <Switch
              checked={externalApisOnCustom}
              disabled={!canWrite || busy}
              onCheckedChange={(next) => void toggleExternalApisOnCustom(next)}
              aria-label={t('searchEngineSettings.customInstanceGuardTitle')}
            />
          </div>
        )}
      </section>

      {/* 2. Offizielle Such-APIs (Brave Search & Google Programmable Search) */}
      <section className="msm-card space-y-6 p-6" aria-labelledby="search-engine-apis-title">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <KeyRound className="h-5 w-5 text-secondary" aria-hidden="true" />
            <h3 id="search-engine-apis-title" className="font-headline text-title-lg font-semibold text-on-surface">
              {t('searchEngineSettings.apisTitle')}
            </h3>
          </div>
          <div className="flex items-center gap-1.5 text-xs text-on-surface-variant">
            <ShieldCheck className="h-4 w-4 text-primary" aria-hidden="true" />
            <span>{t('searchEngineSettings.encryptedHint')}</span>
          </div>
        </div>

        <p className="max-w-3xl text-sm text-on-surface-variant">
          {t('searchEngineSettings.apisDescription')}
        </p>

        {apisPausedByCustomInstance && (
          <div className="rounded-xl border border-outline-variant/40 bg-surface-container-low p-3.5 text-xs text-on-surface-variant">
            {t('searchEngineSettings.apisPausedNotice')}
          </div>
        )}

        {/* 2a. Brave Search API */}
        <div className="space-y-3 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h4 className="text-sm font-semibold text-on-surface">{t('searchEngineSettings.braveTitle')}</h4>
              <p className="text-xs text-on-surface-variant">{t('searchEngineSettings.braveDescription')}</p>
            </div>
            <Badge variant={hasBraveKey ? 'success' : 'default'}>
              {hasBraveKey ? t('searchEngineSettings.statusConfigured') : t('searchEngineSettings.statusOptional')}
            </Badge>
          </div>

          <form className="flex flex-wrap items-end gap-3" onSubmit={saveBraveKey}>
            <div className="min-w-[16rem] flex-1">
              <PasswordInput
                label={t('ai.webSearch.apiKey')}
                autoComplete="new-password"
                maxLength={512}
                value={braveKey}
                disabled={!canWrite || busy}
                placeholder={hasBraveKey ? t('ai.webSearch.configured') : t('ai.webSearch.placeholder')}
                onChange={(event) => setBraveKey(event.target.value)}
                aria-label={t('ai.webSearch.apiKey')}
              />
            </div>
            {canWrite && (
              <>
                <Button type="submit" disabled={busy || !braveKey.trim()}>
                  <Save className="h-4 w-4" aria-hidden="true" />
                  {t('settings.save')}
                </Button>
                {hasBraveKey && (
                  <Button type="button" variant="destructive" disabled={busy} onClick={() => void removeBraveKey()}>
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                    {t('common.delete')}
                  </Button>
                )}
              </>
            )}
          </form>
        </div>

        {/* 2b. Google Programmable Search Engine (CSE JSON API) */}
        <div className="space-y-3 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h4 className="text-sm font-semibold text-on-surface">{t('searchEngineSettings.googleTitle')}</h4>
              <p className="text-xs text-on-surface-variant">{t('searchEngineSettings.googleDescription')}</p>
            </div>
            <Badge variant={hasGoogleCse ? 'success' : 'default'}>
              {hasGoogleCse ? t('searchEngineSettings.statusConfigured') : t('searchEngineSettings.statusOptional')}
            </Badge>
          </div>

          <form className="grid grid-cols-1 gap-3 md:grid-cols-12 md:items-end" onSubmit={saveGoogleCse}>
            <div className="md:col-span-5">
              <PasswordInput
                label={t('searchEngineSettings.googleApiKey')}
                autoComplete="new-password"
                maxLength={512}
                value={googleApiKey}
                disabled={!canWrite || busy}
                placeholder={hasGoogleCse ? t('ai.webSearch.configured') : t('searchEngineSettings.googleApiKeyPlaceholder')}
                onChange={(event) => setGoogleApiKey(event.target.value)}
                aria-label={t('searchEngineSettings.googleApiKey')}
              />
            </div>
            <div className="md:col-span-4">
              <Input
                label={t('searchEngineSettings.googleCx')}
                type="text"
                maxLength={256}
                value={googleCx}
                disabled={!canWrite || busy}
                placeholder={t('searchEngineSettings.googleCxPlaceholder')}
                onChange={(event) => setGoogleCx(event.target.value)}
                aria-label={t('searchEngineSettings.googleCx')}
              />
            </div>
            {canWrite && (
              <div className="flex items-center gap-2 md:col-span-3 md:justify-end">
                <Button
                  type="submit"
                  disabled={
                    busy ||
                    !googleCx.trim() ||
                    (!googleApiKey.trim() && (!hasGoogleCse || googleCx.trim() === savedGoogleCx))
                  }
                >
                  <Save className="h-4 w-4" aria-hidden="true" />
                  {t('settings.save')}
                </Button>
                {hasGoogleCse && (
                  <Button type="button" variant="destructive" disabled={busy} onClick={() => void removeGoogleCse()}>
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                    {t('common.delete')}
                  </Button>
                )}
              </div>
            )}
          </form>
        </div>

        <div className="flex items-center gap-2 text-xs text-on-surface-variant">
          <Globe className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
          <span>{t('ai.webSearch.hint')}</span>
        </div>
      </section>
    </div>
  )
}
