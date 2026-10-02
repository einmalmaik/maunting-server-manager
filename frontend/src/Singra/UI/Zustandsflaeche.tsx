/**
 * Was statt des Inhalts steht, wenn keiner da ist: leer, gescheitert oder ohne
 * Netz nicht verfügbar.
 *
 * - `leer`: ruhig, ohne Rolle. Nach einer Suche mit `ansagen` als Status, damit
 *   ein Screenreader „keine Treffer“ hört.
 * - `fehler`: `role="alert"`, mit „Erneut laden“, sobald `erneutLabel` und
 *   `onErneut` gesetzt sind.
 * - `offline`: `role="status"` und ohne Knopf; wer sie zeigt, lädt von selbst
 *   neu, wenn das Netz zurückkommt.
 *
 * Texte trägt der Baustein keine. Eine weitere Handlung (etwa „Hochladen“)
 * kommt als `children`. Auf dunklem Grund (Lichtbox) setzt `aufDunkel` die
 * Farben auf Weiß.
 */
import type { ReactNode } from 'react'
import { RotateCw, TriangleAlert, WifiOff } from 'lucide-react'
import { cx } from '@/utils/classNames'
import { Button } from '@/components/ui/Button'

export interface ZustandsflaecheProps {
  art: 'leer' | 'fehler' | 'offline'
  titel?: string
  text?: ReactNode
  /** Ersetzt das Symbol; `leer` hat ohne Angabe keins. */
  icon?: ReactNode
  erneutLabel?: string
  onErneut?: () => void
  /** Nur für `leer`: als Statusmeldung ansagen. */
  ansagen?: boolean
  aufDunkel?: boolean
  children?: ReactNode
  className?: string
}

const SYMBOL = 'h-10 w-10'

export function Zustandsflaeche({ art, titel, text, icon, erneutLabel, onErneut, ansagen, aufDunkel, children, className }: ZustandsflaecheProps) {
  const leise = aufDunkel ? 'text-white/60' : 'text-on-surface-variant/60'
  const symbol =
    icon ??
    (art === 'offline' ? (
      <WifiOff className={SYMBOL} />
    ) : art === 'fehler' ? (
      <TriangleAlert className={SYMBOL} />
    ) : null)
  const rolle = art === 'fehler' ? 'alert' : art === 'offline' || ansagen ? 'status' : undefined

  return (
    <div role={rolle} className={cx('flex flex-col items-center justify-center gap-3 px-6 py-8 text-center', className)}>
      {symbol && (
        <span className={cx('flex shrink-0 items-center justify-center', leise)} aria-hidden>
          {symbol}
        </span>
      )}
      {(titel || text) && (
        <div className="max-w-sm">
          {titel && <p className={cx('text-sm font-semibold', aufDunkel ? 'text-white' : 'text-on-surface')}>{titel}</p>}
          {text && <p className={cx('text-sm', titel && 'mt-1', aufDunkel ? 'text-white/70' : 'text-on-surface-variant')}>{text}</p>}
        </div>
      )}
      {art === 'fehler' && erneutLabel && onErneut && (
        <Button type="button" variant="secondary" className="max-sm:min-h-11" onClick={onErneut}>
          <RotateCw className="h-4 w-4" aria-hidden />
          {erneutLabel}
        </Button>
      )}
      {children}
    </div>
  )
}
