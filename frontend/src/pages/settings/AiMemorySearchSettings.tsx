import { useEffect, useState } from 'react'
import { Languages } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiMemorySearchFallback, type AiMemorySearchPolicy } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Dropdown } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

const WAHLEN: AiMemorySearchFallback[] = ['off', 'google', 'openai']

/**
 * Bei wem darf die Bedeutungssuche rechnen, wenn das lokale Modell fehlt?
 *
 * Eine Datenschutzentscheidung des Betreibers, deshalb ab Werk „Aus“: der
 * Rückfall schickt Gedächtnistexte, Skillbeschreibungen und Chatfragen im
 * Klartext an den gewählten Anbieter, auch wenn der Chat über einen anderen
 * läuft. Der Zugang selbst wurde für den Chat eingetragen, nicht dafür.
 *
 * Das lokale Modell bleibt immer der erste Weg. Die Statuszeile sagt, ob die
 * Wahl gerade überhaupt wirkt und ob der gewählte Anbieter einen Zugang hat.
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

  const change = async (value: string) => {
    if (!canWrite || busy) return
    setBusy(true)
    try {
      setState(await aiApi.setMemorySearchPolicy(value as AiMemorySearchFallback))
      toast.success(t('ai.memorySearch.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.memorySearch.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  if (!state) return null

  const fehltZugang = state.fallback !== 'off' && !state.available.includes(state.fallback)
  const status = state.local_ready
    ? t('ai.memorySearch.localReady')
    : [
        t('ai.memorySearch.localMissing'),
        fehltZugang
          ? t('ai.memorySearch.noAccess', { provider: t(`ai.memorySearch.options.${state.fallback}`) })
          : state.ready
            ? t('ai.memorySearch.fallbackActive', { provider: t(`ai.memorySearch.options.${state.fallback}`) })
            : t('ai.memorySearch.searchOff'),
      ].join(' ')

  return (
    <section className="msm-card space-y-4 p-6" aria-labelledby="ai-memory-search-title">
      <div className="flex items-center gap-2">
        <Languages className="h-5 w-5 text-tertiary" aria-hidden="true" />
        <h3 id="ai-memory-search-title" className="font-headline text-title-lg font-semibold text-on-surface">
          {t('ai.memorySearch.title')}
        </h3>
      </div>
      <p className="max-w-3xl text-sm leading-relaxed text-on-surface-variant">
        {t('ai.memorySearch.description')}
      </p>

      <label className="block w-full max-w-md space-y-1.5">
        <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
          {t('ai.memorySearch.choice')}
        </span>
        <Dropdown
          value={state.fallback}
          onChange={(value) => void change(value)}
          options={WAHLEN.map((wahl) => ({
            value: wahl,
            label: t(`ai.memorySearch.options.${wahl}`),
            hint: wahl !== 'off' && !state.available.includes(wahl)
              ? t('ai.memorySearch.noAccessHint')
              : undefined,
          }))}
          disabled={!canWrite || busy}
          aria-label={t('ai.memorySearch.choice')}
        />
      </label>

      <p className="max-w-3xl text-sm leading-relaxed text-on-surface-variant">
        {t('ai.memorySearch.warning')}
      </p>
      <p className="max-w-3xl text-xs leading-5 text-on-surface-variant">{status}</p>
    </section>
  )
}
