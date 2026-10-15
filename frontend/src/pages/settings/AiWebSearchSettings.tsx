import { useEffect, useState } from 'react'
import { ExternalLink, Globe, Search } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Badge, Button, Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

/**
 * Websuche-Schalter für Singra (KI-Einstellungen -> Funktionen).
 *
 * Die Suchmaschine selbst (SearXNG-Sidecar, eigene Instanz, Brave-API,
 * Google-CSE-API) wird zentral im Einstellungen-Tab „Suchmaschine“ (Maunting
 * Search) eingerichtet. Hier legt der Betreiber nur fest, ob Singra das
 * Werkzeug `web_search` nutzen darf — ohne doppelte Schlüssel- oder
 * Adressfelder.
 */
export function AiWebSearchSettings({
  canWrite,
  onOpenSearchEngineTab,
}: {
  canWrite: boolean
  onOpenSearchEngineTab?: () => void
}) {
  const { t } = useTranslation()
  const [aiEnabled, setAiEnabled] = useState(true)
  const [searchEngineConfigured, setSearchEngineConfigured] = useState(false)
  const [sidecarRunning, setSidecarRunning] = useState(false)
  const [hasBrave, setHasBrave] = useState(false)
  const [hasGoogle, setHasGoogle] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    aiApi
      .getWebSearchStatus()
      .then((status) => {
        if (active) {
          setAiEnabled(status.ai_enabled ?? true)
          setSearchEngineConfigured(Boolean(status.search_engine_configured ?? status.configured))
          setSidecarRunning(Boolean(status.sidecar_running))
          setHasBrave(Boolean(status.has_api_key))
          setHasGoogle(Boolean(status.has_google_cse))
        }
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
  }, [t])

  const toggleAiWebSearch = async (next: boolean) => {
    if (!canWrite || busy) return
    setBusy(true)
    try {
      const status = await aiApi.setWebSearchConfig({ aiEnabled: next })
      setAiEnabled(status.ai_enabled ?? next)
      setSearchEngineConfigured(Boolean(status.search_engine_configured ?? status.configured))
      toast.success(t('ai.webSearch.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.webSearch.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  const openSearchTab = () => {
    if (onOpenSearchEngineTab) {
      onOpenSearchEngineTab()
      return
    }
    window.dispatchEvent(new CustomEvent('msm:open-settings-tab', { detail: { tab: 'searchEngine' } }))
  }

  if (loading) return null

  return (
    <section className="msm-card space-y-4 p-6" aria-labelledby="ai-web-search-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Globe className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h3 id="ai-web-search-title" className="font-headline text-title-lg font-semibold text-on-surface">
            {t('ai.webSearch.title')}
          </h3>
        </div>
        <Badge variant={aiEnabled && searchEngineConfigured ? 'success' : 'default'}>
          {aiEnabled ? t('ai.webSearch.aiAccessActive') : t('ai.webSearch.aiAccessDisabled')}
        </Badge>
      </div>

      <p className="max-w-3xl text-sm text-on-surface-variant">{t('ai.webSearch.aiToggleDescription')}</p>

      <div className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4">
        <div className="space-y-1">
          <div className="text-sm font-semibold text-on-surface">{t('ai.webSearch.aiToggleLabel')}</div>
          <p className="text-xs text-on-surface-variant">{t('ai.webSearch.aiToggleHint')}</p>
        </div>
        <Switch
          checked={aiEnabled}
          disabled={!canWrite || busy}
          onCheckedChange={(next) => void toggleAiWebSearch(next)}
          aria-label={t('ai.webSearch.aiToggleLabel')}
        />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
        <div className="flex flex-wrap items-center gap-2 text-xs text-on-surface-variant">
          <Search className="h-4 w-4 text-primary" aria-hidden="true" />
          <span>
            {t('ai.webSearch.centralConfigInfo', {
              sources: [
                sidecarRunning ? 'SearXNG' : null,
                hasBrave ? 'Brave API' : null,
                hasGoogle ? 'Google CSE' : null,
              ]
                .filter(Boolean)
                .join(' · ') || 'Maunting Search',
            })}
          </span>
        </div>

        <Button type="button" variant="secondary" size="sm" onClick={openSearchTab}>
          <span>{t('ai.webSearch.openSearchEngineSettings')}</span>
          <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
        </Button>
      </div>
    </section>
  )
}
