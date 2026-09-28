/**
 * Suchen im offenen Chat.
 *
 * **Die ganze Leiste sitzt unten**, unmittelbar über der Nachrichteneingabe.
 * Mit offener Tastatur ist der obere Bildschirmrand eines Telefons außer
 * Reichweite; wer tippt und Treffer durchblättert, tut beides mit dem Daumen.
 * Die schwebende Kopfzeile des Chats weicht solange.
 *
 * Die Eingabe ist entprellt: bei gesetztem PIN kostet das Entsiegeln des
 * Verlaufs Rechenzeit, und ein ruckelndes Tippfeld fällt sofort auf.
 */

import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronUp, Lock, Search, X } from 'lucide-react'

export const ENTPRELLUNG_MS = 250

export interface VerlaufSuchleisteProps {
  onSchliessen: () => void
  /** Wird entprellt mit der Frage gerufen. */
  onSuchen: (frage: string) => void
  /** Wie viele Treffer es gibt und bei welchem wir gerade stehen (0-basiert). */
  trefferAnzahl: number
  aktuellerTreffer: number
  onVor: () => void
  onZurueck: () => void
  /** Der Messenger ist zu — ohne Schlüssel gibt die Ablage nichts heraus. */
  gesperrt?: boolean
}

export function VerlaufSuchleiste({
  onSchliessen,
  onSuchen,
  trefferAnzahl,
  aktuellerTreffer,
  onVor,
  onZurueck,
  gesperrt,
}: VerlaufSuchleisteProps) {
  const { t } = useTranslation()
  const [frage, setFrage] = useState('')
  const feld = useRef<HTMLInputElement>(null)

  useEffect(() => {
    feld.current?.focus()
  }, [])

  useEffect(() => {
    const uhr = window.setTimeout(() => onSuchen(frage), ENTPRELLUNG_MS)
    return () => window.clearTimeout(uhr)
  }, [frage, onSuchen])

  return (
    <>
      <div className="shrink-0 px-2 py-2 border-b border-outline-variant/20 bg-surface-container-lowest flex items-center gap-1">
        <span className="w-9 h-9 flex items-center justify-center text-on-surface-variant shrink-0" aria-hidden="true">
          <Search className="w-4 h-4" />
        </span>
        <input
          ref={feld}
          value={frage}
          onChange={(e) => setFrage(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              e.shiftKey ? onZurueck() : onVor()
            } else if (e.key === 'Escape') {
              e.preventDefault()
              onSchliessen()
            }
          }}
          placeholder={t('messenger.searchInChatPlaceholder')}
          aria-label={t('messenger.searchInChat')}
          className="flex-1 min-w-0 bg-transparent text-sm text-on-surface placeholder-on-surface-variant outline-none py-2"
        />
        <button
          type="button"
          onClick={onSchliessen}
          className="w-11 h-11 rounded-full flex items-center justify-center text-on-surface-variant hover:bg-surface-container-high transition-colors shrink-0"
          aria-label={t('messenger.closeSearch')}
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Die Navigation. Liegt als eigene Leiste unmittelbar über der Eingabe
          und damit über der Tastatur — oben käme man mit dem Daumen nicht hin. */}
      {(frage.trim() || gesperrt) && (
        <div className="shrink-0 px-3 py-1.5 border-t border-outline-variant/20 bg-surface-container-low flex items-center justify-between gap-2 text-xs">
          {gesperrt ? (
            <span className="flex items-center gap-1.5 text-on-surface-variant">
              <Lock className="w-3.5 h-3.5" />
              <span>{t('messenger.searchLockedShort')}</span>
            </span>
          ) : (
            <span className="text-on-surface-variant tabular-nums">
              {trefferAnzahl === 0
                ? t('messenger.noMatches')
                : t('messenger.matchPosition', {
                    aktuell: aktuellerTreffer + 1,
                    gesamt: trefferAnzahl,
                  })}
            </span>
          )}
          <span className="flex items-center gap-1 shrink-0">
            <button
              type="button"
              onClick={onZurueck}
              disabled={trefferAnzahl === 0}
              className="w-11 h-11 rounded-full flex items-center justify-center text-on-surface-variant hover:bg-surface-container-high disabled:opacity-30 transition-colors"
              aria-label={t('messenger.previousMatch')}
            >
              <ChevronUp className="w-4 h-4" />
            </button>
            <button
              type="button"
              onClick={onVor}
              disabled={trefferAnzahl === 0}
              className="w-11 h-11 rounded-full flex items-center justify-center text-on-surface-variant hover:bg-surface-container-high disabled:opacity-30 transition-colors"
              aria-label={t('messenger.nextMatch')}
            >
              <ChevronDown className="w-4 h-4" />
            </button>
          </span>
        </div>
      )}
    </>
  )
}
