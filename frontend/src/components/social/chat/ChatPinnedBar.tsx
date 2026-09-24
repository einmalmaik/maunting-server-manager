import { useTranslation } from 'react-i18next'
import { Pin, X } from 'lucide-react'

interface ChatPinnedBarProps {
  text: string
  onOeffnen: () => void
  /** Fehlt, wer nicht anheften darf; dann gibt es keinen Lösen-Knopf. */
  onLoesen?: () => void
}

/**
 * Die angeheftete Nachricht der Gruppe.
 * Ob sie erscheint, entscheidet das Recht des **Anheftenden** — geprüft beim
 * Empfänger, weil der Server den Inhalt nicht lesen und die Regel deshalb
 * nicht durchsetzen kann. Diese Leiste zeigt nur, was die Seite entschieden hat.
 */
export function ChatPinnedBar({ text, onOeffnen, onLoesen }: ChatPinnedBarProps) {
  const { t } = useTranslation()

  return (
    <button
      type="button"
      onClick={onOeffnen}
      className="absolute top-[4.25rem] sm:top-14 left-3 right-3 z-20 min-h-11 px-3 py-2 flex items-center gap-2.5 rounded-xl bg-surface-container-high/90 backdrop-blur-md border border-outline-variant/30 shadow-sm text-left"
    >
      <Pin className="w-3.5 h-3.5 text-primary shrink-0" />
      <span className="min-w-0 flex-1">
        <span className="block text-label-sm font-semibold text-primary">Angeheftet</span>
        <span className="block text-label-sm text-on-surface-variant truncate">{text}</span>
      </span>
      {onLoesen && (
        // Ein <button> im <button> ist kein gültiges HTML, daher die Rolle.
        <span
          role="button"
          tabIndex={0}
          onClick={(e) => {
            e.stopPropagation()
            onLoesen()
          }}
          onKeyDown={(e) => {
            if (e.key !== 'Enter' && e.key !== ' ') return
            e.preventDefault()
            e.stopPropagation()
            onLoesen()
          }}
          className="w-11 h-11 -mr-2 rounded-full flex items-center justify-center text-on-surface-variant hover:bg-surface-container-highest transition-colors shrink-0"
          aria-label={t('messenger.unpin')}
        >
          <X className="w-4 h-4" />
        </span>
      )}
    </button>
  )
}
