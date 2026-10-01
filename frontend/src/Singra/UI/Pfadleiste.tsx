/**
 * Pfadleiste (Breadcrumb) für Dateibereiche: Stamm, dann ein Knopf je Ebene.
 *
 * Optional ist jeder Teil ein Ablageziel wie im Explorer: zieht man etwas
 * darüber und `kannAblegen` sagt ja, wird der Teil markiert, und `onAblegen`
 * bekommt das `DataTransfer`. Während des Ziehens gibt der Browser nur die
 * Typen heraus, nicht den Inhalt; `kannAblegen` entscheidet also über die
 * Typen und über eigenen Zustand des Aufrufers.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ChevronRight, CornerLeftUp } from 'lucide-react'
import { cx } from '@/utils/classNames'
import { Button } from '@/components/ui/Button'

export interface Pfadteil {
  key: string
  label: string
}

export interface PfadleisteProps {
  /** Name der Navigation für Screenreader. */
  label: string
  stamm: { key: string; label: string; icon?: ReactNode }
  /** Ebenen unter dem Stamm, die letzte ist die geöffnete. */
  teile: Pfadteil[]
  onWaehlen: (key: string) => void
  kannAblegen?: (key: string, daten: DataTransfer) => boolean
  onAblegen?: (key: string, daten: DataTransfer) => void
  /** Mit Text erscheint auf schmalen Bildschirmen ein Knopf „eine Ebene hoch“. */
  hochLabel?: string
  className?: string
}

export function Pfadleiste({ label, stamm, teile, onWaehlen, kannAblegen, onAblegen, hochLabel, className }: PfadleisteProps) {
  const leiste = useRef<HTMLElement>(null)
  const [ziel, setZiel] = useState<string | null>(null)
  const aktuell = teile.length ? teile[teile.length - 1].key : stamm.key
  const pfadSchluessel = teile.map((teil) => teil.key).join('/')

  // Bei tiefen Pfaden bleibt die geöffnete Ebene sichtbar.
  useEffect(() => {
    const el = leiste.current
    if (el) el.scrollLeft = el.scrollWidth
  }, [pfadSchluessel])

  const ablage = (key: string) =>
    onAblegen
      ? {
          onDragOver: (event: React.DragEvent) => {
            if (kannAblegen && !kannAblegen(key, event.dataTransfer)) return
            event.preventDefault()
            setZiel(key)
          },
          onDragLeave: () => setZiel((z) => (z === key ? null : z)),
          onDrop: (event: React.DragEvent) => {
            event.preventDefault()
            event.stopPropagation()
            setZiel(null)
            onAblegen(key, event.dataTransfer)
          },
        }
      : {}

  // Ein Teil ist entweder Ablageziel, offene Ebene oder normal, nie zwei Farben zugleich.
  const knopf = (key: string, normal: string, offen = normal) =>
    cx('rounded px-1 transition-colors', ziel === key ? 'bg-primary/15 text-primary ring-1 ring-primary/50' : key === aktuell ? offen : normal)

  return (
    <nav ref={leiste} aria-label={label} className={cx('flex min-w-0 flex-1 items-center gap-1 overflow-x-auto text-on-surface-variant', className)}>
      {hochLabel && teile.length > 0 && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="-ml-1 h-9 w-9 shrink-0 p-0 text-on-surface md:hidden"
          aria-label={hochLabel}
          onClick={() => onWaehlen(teile.length > 1 ? teile[teile.length - 2].key : stamm.key)}
        >
          <CornerLeftUp className="h-4 w-4" />
        </Button>
      )}
      <button
        type="button"
        onClick={() => onWaehlen(stamm.key)}
        aria-current={aktuell === stamm.key ? 'page' : undefined}
        className={cx('inline-flex items-center gap-1 whitespace-nowrap', knopf(stamm.key, 'text-secondary hover:text-primary'))}
        {...ablage(stamm.key)}
      >
        {stamm.icon}
        {stamm.label}
      </button>
      {teile.map((teil) => (
        <span key={teil.key} className="flex items-center gap-1 whitespace-nowrap">
          <ChevronRight className="h-3 w-3 shrink-0" aria-hidden />
          <button
            type="button"
            onClick={() => onWaehlen(teil.key)}
            aria-current={aktuell === teil.key ? 'page' : undefined}
            className={knopf(teil.key, 'hover:text-on-surface', 'text-on-surface')}
            {...ablage(teil.key)}
          >
            {teil.label}
          </button>
        </span>
      ))}
    </nav>
  )
}
