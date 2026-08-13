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
 *
 * **Er steht ab dem ersten Augenblick da, auch ohne Inhalt.** Das ist der
 * eigentliche Punkt und kam aus einer Beobachtung im Betrieb: "der
 * Nachdenken-Block kam erst am Ende". Gemessen stimmte das auch — nur nicht aus
 * dem vermuteten Grund. Der Block wurde frueher erst gerendert, wenn schon
 * Denktext da war, und **manche Modelle liefern in der ersten Runde gar keinen**
 * (gemessen: erste Denkzeichen nach 6,5 s, in einem Fall erst nach 29 s, lange
 * nach dem ersten Antworttext). Bis dahin sah man nur eine nackte Zeile, und
 * dann sprang oben ein Kasten hinein, den es vorher nicht gab.
 *
 * Jetzt ist er von Anfang an der Ort, an dem "es passiert etwas" steht. Er
 * erscheint nicht mehr — er fuellt sich.
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
        {/*
          Eigener Text, nicht `ai.chat.thinking`. Der Block trug frueher
          waehrend des Denkens exakt denselben Satz wie der globale
          Ladezustand ("Antwort wird erstellt …"). Damit stand ueber der
          einzigen Stelle, an der sichtbar etwas geschah, wortwoertlich das,
          was auch dasteht, wenn noch nichts geschieht — und "Nachgedacht"
          erschien erst am Schluss. Der Block sagt jetzt selbst, was er tut.
        */}
        <span>{streaming ? t('ai.chat.thinkingNow') : t('ai.chat.thought')}</span>
        <ChevronDown
          className={`ml-auto h-3.5 w-3.5 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
          aria-hidden="true"
        />
      </button>
      {open && (content ? (
        <p className="whitespace-pre-wrap break-words px-3 pb-3 text-xs leading-5 text-on-surface-variant">
          {content}
        </p>
      ) : streaming ? (
        // Noch kein Denktext, aber der Block steht schon. Drei pulsende Punkte
        // statt eines leeren Kastens: er soll arbeiten aussehen, nicht kaputt.
        <p className="px-3 pb-3 text-xs leading-5 text-on-surface-variant" aria-hidden="true">
          <span className="inline-flex gap-1">
            <span className="h-1 w-1 animate-pulse rounded-full bg-current" />
            <span className="h-1 w-1 animate-pulse rounded-full bg-current [animation-delay:150ms]" />
            <span className="h-1 w-1 animate-pulse rounded-full bg-current [animation-delay:300ms]" />
          </span>
        </p>
      ) : null)}
    </div>
  )
}
