import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bot } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiWorkerInfo } from '@/api/ai'
import { AI_ZUSTELLUNG_EVENT } from '@/lib/aiZustellung'

/**
 * Derselbe gemächliche Takt wie im Guardian- und Worker-Fenster: schnell genug,
 * um einen frisch gestarteten Auftrag zu sehen, ohne dass die Seite dauernd
 * fragt. Zwischendurch weckt das Zustell-Ereignis der Glocke die Leiste sofort.
 */
const NACHSEHEN_MS = 20_000

const STATUS_TEXT: Record<string, string> = {
  running: 'ai.worker.running',
  waiting_confirmation: 'ai.worker.waitingApproval',
  waiting_user: 'ai.worker.waitingUser',
  waiting_wake: 'ai.worker.waitingWake',
}

/**
 * Die Worker-Leiste des Chats: je lebendem Hintergrund-Auftrag eine Pille.
 *
 * Einsehbar, nicht beschreibbar, räumt sich auf (docs/agentic-framework.md,
 * Frontend-Zeile): Die Liste zeigt nur, was das Backend als lebend meldet —
 * Endzustände fallen beim nächsten Blick von selbst heraus, gelöscht wird
 * nichts. Ein Klick führt in die lesende Worker-Ansicht
 * (`?ansicht=worker&id=<uuid>`; nur die Kennung in der Adresse, nie der Titel).
 *
 * Ohne Aufträge rendert die Leiste **nichts** — der Chat sieht dann aus wie
 * immer. Kein Store, kein Reducer: lokaler State reicht für eine Liste, die
 * alle zwanzig Sekunden neu aus der Datenbank kommt (KISS).
 */
export function WorkerLeiste() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [workers, setWorkers] = useState<AiWorkerInfo[]>([])

  const laden = useCallback(async () => {
    try {
      setWorkers(await aiApi.listWorkers())
    } catch {
      // Die Leiste ist eine Zusatzauskunft. Scheitert der Abruf, bleibt der
      // letzte Stand stehen — ein Fehlertoast wäre lästiger als eine kurz
      // veraltete Liste.
    }
  }, [])

  useEffect(() => {
    void laden()
    const timer = window.setInterval(() => void laden(), NACHSEHEN_MS)
    window.addEventListener(AI_ZUSTELLUNG_EVENT, laden)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener(AI_ZUSTELLUNG_EVENT, laden)
    }
  }, [laden])

  if (workers.length === 0) return null

  return (
    <div
      className="flex shrink-0 flex-wrap items-center gap-2 border-b border-outline-variant/40 px-3 py-2 sm:px-4"
      aria-label={t('ai.worker.listLabel')}
    >
      <span className="inline-flex items-center gap-1.5 text-xs font-medium text-on-surface-variant">
        <Bot className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        {t('ai.worker.listLabel')}
      </span>
      {workers.map((worker) => (
        <button
          key={worker.conversation_id}
          type="button"
          onClick={() => navigate(`/ai?ansicht=worker&id=${encodeURIComponent(worker.conversation_id)}`)}
          title={worker.title}
          className={[
            'inline-flex max-w-[16rem] items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium',
            'transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60',
            worker.status === 'running'
              ? 'border-primary/40 bg-primary/10 text-primary hover:bg-primary/15'
              : worker.status === 'waiting_wake'
                ? 'border-outline-variant/60 bg-surface-container-high text-on-surface-variant hover:text-on-surface'
                : 'border-status-warning/40 bg-status-warning/10 text-status-warning hover:bg-status-warning/15',
          ].join(' ')}
        >
          <span className="truncate">{worker.title}</span>
          <span className="shrink-0 opacity-80">
            · {t(STATUS_TEXT[worker.status] ?? 'ai.worker.running')}
          </span>
        </button>
      ))}
    </div>
  )
}
