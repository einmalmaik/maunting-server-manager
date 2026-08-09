import { useState } from 'react'
import { Brain, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

/**
 * Hinweis vor der ersten Nachricht: die KI kann sich Dinge merken.
 *
 * Er steht hier und nicht in den Einstellungen, weil er genau dort auftauchen
 * muss, wo die Entscheidung Folgen hat — vor dem Tippen, nicht in einem Menue,
 * das niemand oeffnet.
 *
 * Drei Antworten, zwei Zustaende: "Ja" schaltet ein, "Spaeter" verschiebt um
 * 24 Stunden, "Nicht mehr fragen" beendet den Hinweis. Das Gedaechtnis bleibt
 * danach unter Profil > KI erreichbar — abgestellt wird die Frage, nicht die
 * Moeglichkeit.
 */
export function AiMemoryNotice({ onAnswered }: { onAnswered: (enabled: boolean) => void }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)

  const answer = async (enable: boolean, hideFuture: boolean) => {
    if (busy) return
    setBusy(true)
    try {
      const state = await aiApi.answerMemoryNotice(enable, hideFuture)
      if (enable) toast.success(t('ai.memoryNotice.enabled'))
      onAnswered(state.enabled)
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.memoryNotice.failed'))
      setBusy(false)
    }
  }

  return (
    <div
      role="region"
      aria-label={t('ai.memoryNotice.title')}
      className="mx-auto mb-3 w-full max-w-3xl rounded-xl border border-outline-variant/40 bg-surface-container-low/60 p-4"
    >
      <div className="flex items-start gap-3">
        <Brain className="mt-0.5 h-5 w-5 shrink-0 text-primary" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h3 className="font-headline text-sm font-semibold text-on-surface">
            {t('ai.memoryNotice.title')}
          </h3>
          <p className="mt-1 text-sm leading-6 text-on-surface-variant">
            {t('ai.memoryNotice.body')}
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button type="button" disabled={busy} onClick={() => void answer(true, false)}>
              {t('ai.memoryNotice.enable')}
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={busy}
              onClick={() => void answer(false, false)}
            >
              {t('ai.memoryNotice.later')}
            </Button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void answer(false, true)}
              className="inline-flex min-h-11 items-center gap-1.5 rounded-lg px-2 text-xs text-on-surface-variant transition-colors hover:text-on-surface disabled:opacity-50"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
              {t('ai.memoryNotice.never')}
            </button>
          </div>
          <p className="mt-2 text-xs text-on-surface-variant">{t('ai.memoryNotice.hint')}</p>
        </div>
      </div>
    </div>
  )
}
