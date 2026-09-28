import { useTranslation } from 'react-i18next'
import { Copy, Forward, Star, Trash2, X } from 'lucide-react'

interface ChatSelectionBarProps {
  anzahl: number
  /** Löschen geht nur, wenn unter den gewählten eine eigene, noch nicht gelöschte ist. */
  loeschenMoeglich: boolean
  onBeenden: () => void
  onWeiterleiten: () => void
  onKopieren: () => void
  onMarkieren: () => void
  onLoeschen: () => void
}

/**
 * Die Auswahlleiste ersetzt die schwebenden Bedienelemente.
 * Zähler und Abbrechen oben, die Aktionen unten in Daumenreichweite — am
 * Telefon ist der obere Rand außer Reichweite, sobald man einhändig hält.
 */
export function ChatSelectionBar({
  anzahl,
  loeschenMoeglich,
  onBeenden,
  onWeiterleiten,
  onKopieren,
  onMarkieren,
  onLoeschen,
}: ChatSelectionBarProps) {
  const { t } = useTranslation()
  const knopf =
    'min-w-16 min-h-12 flex flex-col items-center justify-center gap-0.5 rounded-xl text-label-sm disabled:opacity-40 transition-colors'

  return (
    <>
      <div className="absolute top-2.5 left-3 right-3 z-40 flex items-center justify-between gap-2 px-3 py-2 rounded-full bg-surface-container-high/95 backdrop-blur-md border border-outline-variant/30 shadow-sm">
        <span className="text-xs font-semibold text-on-surface tabular-nums">
          {anzahl} ausgewählt
        </span>
        <button
          type="button"
          onClick={onBeenden}
          className="w-9 h-9 -mr-1.5 rounded-full flex items-center justify-center text-on-surface-variant hover:bg-surface-container-highest transition-colors"
          aria-label={t('messenger.endSelection')}
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="absolute bottom-0 left-0 right-0 z-40 px-3 py-2 pb-[calc(0.5rem+env(safe-area-inset-bottom))] border-t border-outline-variant/30 bg-surface-container-low flex items-center justify-around">
        <button
          type="button"
          disabled={anzahl === 0}
          onClick={onWeiterleiten}
          className={`${knopf} text-on-surface-variant hover:bg-surface-container-high`}
        >
          <Forward className="w-5 h-5" />
          <span>Weiterleiten</span>
        </button>
        <button
          type="button"
          disabled={anzahl === 0}
          onClick={onKopieren}
          className={`${knopf} text-on-surface-variant hover:bg-surface-container-high`}
        >
          <Copy className="w-5 h-5" />
          <span>Kopieren</span>
        </button>
        <button
          type="button"
          disabled={anzahl === 0}
          onClick={onMarkieren}
          className={`${knopf} text-on-surface-variant hover:bg-surface-container-high`}
        >
          <Star className="w-5 h-5" />
          <span>Markieren</span>
        </button>
        <button
          type="button"
          disabled={!loeschenMoeglich}
          onClick={onLoeschen}
          className={`${knopf} text-status-destructive hover:bg-status-destructive/10`}
        >
          <Trash2 className="w-5 h-5" />
          <span>{t('common.delete')}</span>
        </button>
      </div>
    </>
  )
}
