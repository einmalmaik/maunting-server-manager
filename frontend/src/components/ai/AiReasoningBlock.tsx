import { useState } from 'react'
import { Brain, ChevronDown } from 'lucide-react'
import { useTranslation } from 'react-i18next'

/**
 * Einklappbarer Denkschritt-Block.
 *
 * Standardmaessig zu: die Denkschritte sind eine Nebenausgabe, keine Antwort.
 * Waehrend das Modell noch denkt, ist der Block aber offen und laeuft mit —
 * genau dann ist er naemlich das Einzige, was passiert, und ein leerer
 * Bildschirm sieht aus wie ein Fehler.
 */
export function AiReasoningBlock({ content, streaming }: { content: string; streaming: boolean }) {
  const { t } = useTranslation()
  const [manuallyToggled, setManuallyToggled] = useState<boolean | null>(null)
  const open = manuallyToggled ?? streaming

  return (
    <div className="mb-2 rounded-lg border border-outline-variant/40 bg-surface-container-low/40">
      <button
        type="button"
        onClick={() => setManuallyToggled(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-xs font-medium text-on-surface-variant transition-colors hover:text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Brain className={`h-3.5 w-3.5 shrink-0 ${streaming ? 'animate-pulse text-primary' : ''}`} aria-hidden="true" />
        <span>{streaming ? t('ai.chat.thinking') : t('ai.chat.thought')}</span>
        <ChevronDown
          className={`ml-auto h-3.5 w-3.5 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
          aria-hidden="true"
        />
      </button>
      {open && (
        <p className="whitespace-pre-wrap break-words px-3 pb-3 text-xs leading-5 text-on-surface-variant">
          {content}
        </p>
      )}
    </div>
  )
}
