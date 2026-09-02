import { useEffect, useState } from 'react'
import { Gauge } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiContextPolicy } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Dropdown } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

/**
 * Ab wie viel Prozent des Kontextfensters zusammengefasst wird.
 *
 * Wie groß das Fenster ist, sagt der Modellkatalog des Anbieters — dafür gibt
 * es hier bewusst nichts einzustellen. Einstellbar ist nur die Abwägung, die
 * MSM nicht für den Betreiber treffen kann: früh falten heißt kleinere und
 * billigere Anfragen, spät falten heißt, dass die KI mehr vom Gespräch wörtlich
 * vor sich hat. Bei einem Hoster fällt das anders aus als bei einer
 * Privatinstallation.
 *
 * Panelweit und nicht je Rolle: sonst wäre dieselbe Unterhaltung je nachdem,
 * wer sie zuletzt fortgesetzt hat, verschieden stark gefaltet.
 */
export function AiContextSettings({ canWrite }: { canWrite: boolean }) {
  const { t } = useTranslation()
  const [state, setState] = useState<AiContextPolicy | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    aiApi.getContextPolicy()
      .then((policy) => { if (active) setState(policy) })
      .catch(() => { if (active) toast.error(t('ai.context.errors.load')) })
    return () => { active = false }
  }, [t])

  const change = async (value: string) => {
    if (!canWrite || busy) return
    setBusy(true)
    try {
      setState(await aiApi.setContextPolicy(Number(value)))
      toast.success(t('ai.context.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.context.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  if (!state) return null

  // Die Stufen kommen aus dem Programm, ihre Grenzen vom Server. Ein Wert
  // außerhalb [min, max] wird dort mit 422 abgelehnt — hier steht er dann gar
  // nicht erst zur Wahl.
  //
  // Der aktuelle Wert kommt immer mit, auch wenn er keiner der angebotenen ist:
  // die API nimmt jede Zahl im erlaubten Bereich, und ein Dropdown, dessen
  // `value` in keiner Option vorkommt, zeigt leer an — es sähe aus, als wäre
  // nichts eingestellt.
  const stufen = Array.from(new Set([60, 70, 75, 80, 90, state.compaction_percent]))
    .filter((wert) => wert >= state.min_percent && wert <= state.max_percent)
    .sort((links, rechts) => links - rechts)

  return (
    <section className="msm-card space-y-4 p-6" aria-labelledby="ai-context-title">
      <div className="flex items-center gap-2">
        <Gauge className="h-5 w-5 text-tertiary" aria-hidden="true" />
        <h3 id="ai-context-title" className="font-headline text-lg font-semibold text-on-surface">
          {t('ai.context.title')}
        </h3>
      </div>
      <p className="max-w-3xl text-sm text-on-surface-variant">{t('ai.context.description')}</p>

      <label className="block w-full max-w-md space-y-1.5">
        <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
          {t('ai.context.threshold')}
        </span>
        <Dropdown
          value={String(state.compaction_percent)}
          onChange={(value) => void change(value)}
          options={stufen.map((wert) => ({
            value: String(wert),
            label: t('ai.context.percent', { percent: wert }),
            hint: t(`ai.context.hints.${wert}`, { defaultValue: '' }) || undefined,
          }))}
          disabled={!canWrite || busy}
          aria-label={t('ai.context.threshold')}
        />
      </label>

      <p className="max-w-3xl text-xs leading-5 text-on-surface-variant">
        {t('ai.context.costHint')}
      </p>
    </section>
  )
}
