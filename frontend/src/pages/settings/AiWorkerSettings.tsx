import { useEffect, useState } from 'react'
import { Bot } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiWorkerPolicy } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button, NumberStepper } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

/**
 * Die Betreiber-Deckel der Hintergrund-Aufträge: wie viele Worker je Benutzer
 * gleichzeitig, wie viele Anbieter-Runden je Auftrag.
 *
 * Beide Deckel liegen beim Betreiber, weil er zahlt (docs/agentic-framework.md,
 * §5): ohne sie wird „schau die Server nach, mach den Kalender, und noch drei
 * Sachen" zum unsichtbaren Dauerverbraucher. Panelweit und nicht je Rolle —
 * die Deckel müssen ohne jede Konfiguration gelten, und der Kunde stellt
 * Worker ohnehin nicht ein. Welche **Modelle** die Worker benutzen, steht
 * daneben am jeweiligen Zugang (Karte „Worker" im Anbieterformular).
 */
export function AiWorkerSettings({ canWrite }: { canWrite: boolean }) {
  const { t } = useTranslation()
  const [state, setState] = useState<AiWorkerPolicy | null>(null)
  const [entwurf, setEntwurf] = useState<{ workers: number; rounds: number } | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    aiApi.getWorkerPolicy()
      .then((policy) => {
        if (!active) return
        setState(policy)
        setEntwurf({
          workers: policy.max_parallel_workers,
          rounds: policy.rounds_per_worker,
        })
      })
      .catch(() => { if (active) toast.error(t('aiSettings.worker.errors.load')) })
    return () => { active = false }
  }, [t])

  const speichern = async () => {
    if (!canWrite || busy || !entwurf) return
    setBusy(true)
    try {
      const policy = await aiApi.setWorkerPolicy(entwurf.workers, entwurf.rounds)
      setState(policy)
      setEntwurf({
        workers: policy.max_parallel_workers,
        rounds: policy.rounds_per_worker,
      })
      toast.success(t('aiSettings.worker.saved'))
    } catch (error: unknown) {
      toast.error(
        error instanceof SanitizedApiError ? error.message : t('aiSettings.worker.errors.save'),
      )
    } finally {
      setBusy(false)
    }
  }

  if (!state || !entwurf) return null

  const veraendert = entwurf.workers !== state.max_parallel_workers
    || entwurf.rounds !== state.rounds_per_worker

  return (
    <section className="msm-card space-y-4 p-6" aria-labelledby="ai-worker-title">
      <div className="flex items-center gap-2">
        <Bot className="h-5 w-5 text-tertiary" aria-hidden="true" />
        <h3 id="ai-worker-title" className="font-headline text-lg font-semibold text-on-surface">
          {t('aiSettings.worker.title')}
        </h3>
      </div>
      <p className="max-w-3xl text-sm text-on-surface-variant">
        {t('aiSettings.worker.description')}
      </p>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 sm:max-w-2xl">
        <label className="space-y-1.5">
          <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
            {t('aiSettings.worker.maxParallel')}
          </span>
          <NumberStepper
            value={String(entwurf.workers)}
            min={state.min_workers}
            max={state.max_workers}
            onValueChange={(wert) => {
              const workers = Number(wert)
              if (Number.isFinite(workers)) {
                setEntwurf((jetzt) => (jetzt ? { ...jetzt, workers } : jetzt))
              }
            }}
            disabled={!canWrite || busy}
            aria-label={t('aiSettings.worker.maxParallel')}
          />
          <span className="msm-field-help block">{t('aiSettings.worker.maxParallelHint')}</span>
        </label>
        <label className="space-y-1.5">
          <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
            {t('aiSettings.worker.roundsPerWorker')}
          </span>
          <NumberStepper
            value={String(entwurf.rounds)}
            min={state.min_rounds}
            max={state.max_rounds}
            onValueChange={(wert) => {
              const rounds = Number(wert)
              if (Number.isFinite(rounds)) {
                setEntwurf((jetzt) => (jetzt ? { ...jetzt, rounds } : jetzt))
              }
            }}
            disabled={!canWrite || busy}
            aria-label={t('aiSettings.worker.roundsPerWorker')}
          />
          <span className="msm-field-help block">{t('aiSettings.worker.roundsHint')}</span>
        </label>
      </div>

      {canWrite && (
        <Button type="button" disabled={busy || !veraendert} onClick={() => void speichern()}>
          {busy ? t('common.loading') : t('settings.save')}
        </Button>
      )}
    </section>
  )
}
