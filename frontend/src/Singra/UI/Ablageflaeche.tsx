/**
 * „Hier ablegen“: wie eine Fläche aussieht, über die gerade etwas gezogen wird.
 *
 * Zwei Größen, ein Aussehen:
 * - `Ablageflaeche` liegt über einer ganzen Fläche (Dateiliste, Galerie,
 *   Chat), die Dateien vom Rechner annimmt. Der Aufrufer zeigt sie, solange
 *   gezogen wird, und gibt seiner Fläche `relative`. Der Text steht unten,
 *   damit Zeilen darunter, die selbst Ziel sind, sichtbar bleiben. Klicks gehen
 *   durch sie hindurch.
 * - `ABLAGEZIEL` ist die Klasse für ein einzelnes Ziel in einer Liste
 *   (Ordnerzeile, Teil der Pfadleiste, Ordner im Baum).
 *
 * Ob und was angenommen wird, entscheidet der Aufrufer; Texte trägt der
 * Baustein keine.
 */
import type { ReactNode } from 'react'
import { Upload } from 'lucide-react'
import { cx } from '@/utils/classNames'

export const ABLAGEZIEL = 'bg-primary/15 text-primary ring-1 ring-inset ring-primary/50'

export interface AblageflaecheProps {
  /** Was beim Loslassen geschieht, etwa „In „Fotos“ hochladen“. */
  text: string
  /** Eine zweite, leisere Zeile. */
  hinweis?: string
  /** Ersetzt das Hochladen-Symbol. */
  icon?: ReactNode
  className?: string
}

export function Ablageflaeche({ text, hinweis, icon, className }: AblageflaecheProps) {
  return (
    <div
      className={cx(
        'pointer-events-none absolute inset-2 z-20 flex flex-col items-center justify-end gap-1.5 rounded-2xl border-2 border-dashed border-primary/60 bg-primary/5 px-4 pb-8',
        className,
      )}
    >
      <p className="flex items-center gap-2 rounded-full bg-surface-container-highest px-4 py-2 text-center text-sm text-on-surface shadow-panel">
        <span className="shrink-0 text-primary" aria-hidden>
          {icon ?? <Upload className="h-4 w-4" />}
        </span>
        {text}
      </p>
      {hinweis && <p className="rounded-full bg-surface-container-highest/90 px-3 py-1 text-xs text-on-surface-variant">{hinweis}</p>}
    </div>
  )
}
