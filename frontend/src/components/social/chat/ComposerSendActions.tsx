import { useTranslation } from 'react-i18next'
import { Mic, Send, Video } from 'lucide-react'

interface ComposerSendActionsProps {
  /** Text, Foto oder Datei liegen bereit: dann steht hier Senden. */
  hatInhalt: boolean
  sendet: boolean
  onVideonotiz: () => void
  onSprachnachricht: () => void
}

/** Rechts in der Eingabeleiste: Senden, oder ohne Inhalt die beiden Aufnahmen. */
export function ComposerSendActions({ hatInhalt, sendet, onVideonotiz, onSprachnachricht }: ComposerSendActionsProps) {
  const { t } = useTranslation()

  if (hatInhalt) {
    /*
     * Ein einfacher Knopf, kein `<Button>` — wie seine Nachbarn in dieser
     * Leiste. `<Button size="sm">` bringt `h-8` mit, und das gewinnt gegen ein
     * `h-11` aus `className`: über die Höhe entscheidet die Reihenfolge im
     * Stylesheet, nicht die im Attribut. Gemessen war der Knopf am Telefon
     * deshalb 44 × 32 statt 44 × 44 — zu flach für einen Daumen, und das bei
     * der einen Handlung, für die es keinen zweiten Weg gibt.
     * `msm-btn-primary` bringt nur die Farben mit und kollidiert mit nichts.
     */
    return (
      <button
        type="submit"
        disabled={sendet}
        className="msm-btn-primary w-11 h-11 sm:w-8 sm:h-8 shrink-0 rounded-full flex items-center justify-center"
        title="Senden"
        aria-label="Senden"
      >
        <Send className="w-3.5 h-3.5" />
      </button>
    )
  }

  return (
    <div className="flex items-center gap-1">
      <button
        type="button"
        onClick={onVideonotiz}
        className="w-11 h-11 sm:w-8 sm:h-8 shrink-0 flex items-center justify-center text-on-surface-variant hover:text-status-success hover:bg-status-success/10 rounded-full transition-colors"
        title={t('messenger.recordVideoNoteHint')}
        aria-label={t('messenger.recordVideoNote')}
      >
        <Video className="w-4 h-4" />
      </button>
      <button
        type="button"
        onClick={onSprachnachricht}
        className="w-11 h-11 sm:w-8 sm:h-8 shrink-0 flex items-center justify-center text-on-surface-variant hover:text-primary hover:bg-primary/10 rounded-full transition-colors"
        title={t('messenger.recordVoice')}
        aria-label={t('messenger.recordVoice')}
      >
        <Mic className="w-4 h-4" />
      </button>
    </div>
  )
}
