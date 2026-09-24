import { useEffect, useState } from 'react'
import { Languages } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiMemorySearchPolicy } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

/**
 * Darf die Bedeutungssuche ohne lokales Modell bei Google rechnen?
 *
 * Eine Datenschutzentscheidung des Betreibers, deshalb ab Werk aus: der
 * Rückfall schickt Gedächtnistexte, Skillbeschreibungen und Chatfragen im
 * Klartext an Google, auch wenn der Chat über einen anderen Anbieter läuft.
 * Der Google-Zugang selbst wurde für den Chat eingetragen, nicht dafür.
 *
 * Die Statuszeile sagt, ob der Schalter gerade überhaupt wirkt. Läuft das
 * lokale Modell, rechnet die Suche ohnehin im Haus.
 */
export function AiMemorySearchSettings({ canWrite }: { canWrite: boolean }) {
  const { t } = useTranslation()
  const [state, setState] = useState<AiMemorySearchPolicy | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    aiApi.getMemorySearchPolicy()
      .then((policy) => { if (active) setState(policy) })
      .catch(() => { if (active) toast.error(t('ai.memorySearch.errors.load')) })
    return () => { active = false }
  }, [t])

  const toggle = async (next: boolean) => {
    if (!canWrite || busy) return
    setBusy(true)
    try {
      setState(await aiApi.setMemorySearchPolicy(next))
      toast.success(t('ai.memorySearch.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.memorySearch.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  if (!state) return null

  const status = state.local_ready
    ? t('ai.memorySearch.localReady')
    : [
        t('ai.memorySearch.localMissing'),
        state.ready ? t('ai.memorySearch.fallbackActive') : t('ai.memorySearch.searchOff'),
      ].join(' ')

  return (
    <section className="msm-card space-y-4 p-6" aria-labelledby="ai-memory-search-title">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <Languages className="h-5 w-5 text-tertiary" aria-hidden="true" />
          <h3 id="ai-memory-search-title" className="font-headline text-title-lg font-semibold text-on-surface">
            {t('ai.memorySearch.title')}
          </h3>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs font-medium text-on-surface-variant">
            {state.google_fallback
              ? t('ai.memorySearch.statusEnabled')
              : t('ai.memorySearch.statusDisabled')}
          </span>
          <Switch
            checked={state.google_fallback}
            disabled={!canWrite || busy}
            onCheckedChange={toggle}
            aria-label={t('ai.memorySearch.title')}
          />
        </div>
      </div>
      <p className="max-w-3xl text-sm leading-relaxed text-on-surface-variant">
        {t('ai.memorySearch.description')}
      </p>
      <p className="max-w-3xl text-sm leading-relaxed text-on-surface-variant">
        {t('ai.memorySearch.warning')}
      </p>
      <p className="max-w-3xl text-xs leading-5 text-on-surface-variant">{status}</p>
    </section>
  )
}
