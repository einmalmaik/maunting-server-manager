/**
 * Leiste für eine Mehrfachauswahl: wie viele gewählt sind, was man mit ihnen
 * tun kann, und Abbrechen.
 *
 * Es gibt sie in zwei Lagen, die der Aufrufer selbst platziert:
 * - `kopf` ersetzt die Kopfleiste. Ab `md` stehen dort auch die Aktionen,
 *   darunter nur Anzahl und Abbrechen.
 * - `fuss` gehört ans untere Ende der Fläche und ist nur unter `md` sichtbar:
 *   am Telefon liegen die Aktionen so in Daumenreichweite. Den Abstand zur
 *   Gestenleiste hält `env(safe-area-inset-bottom)`.
 */
import type { ReactNode } from 'react'
import { CheckCheck, X } from 'lucide-react'
import { cx } from '@/utils/classNames'
import { Button } from '@/components/ui/Button'

export interface AuswahlAktion {
  key: string
  label: string
  /** Kürzer für die Fußleiste, wo fünf Knöpfe nebeneinander stehen; `label` bleibt der Name. */
  kurz?: string
  icon: ReactNode
  onSelect: () => void
  destructive?: boolean
  disabled?: boolean
}

export interface AuswahlleisteProps {
  variante: 'kopf' | 'fuss'
  /** Etwa „3 ausgewählt“. */
  anzahlLabel: string
  aktionen: AuswahlAktion[]
  abbrechenLabel: string
  onAbbrechen: () => void
  /** Mit Text erscheint in der Kopfleiste „Alle auswählen“. */
  alleLabel?: string
  onAlle?: () => void
  className?: string
}

export function Auswahlleiste({ variante, anzahlLabel, aktionen, abbrechenLabel, onAbbrechen, alleLabel, onAlle, className }: AuswahlleisteProps) {
  if (variante === 'fuss') {
    return (
      <div
        role="toolbar"
        aria-label={anzahlLabel}
        className={cx(
          'flex shrink-0 items-stretch justify-around gap-1 border-t border-outline-variant/40 bg-surface-container-low px-1 pt-1.5 pb-[calc(0.375rem+env(safe-area-inset-bottom))] md:hidden',
          className,
        )}
      >
        {aktionen.map((a) => (
          <button
            key={a.key}
            type="button"
            disabled={a.disabled}
            onClick={a.onSelect}
            aria-label={a.kurz ? a.label : undefined}
            className={cx(
              'flex min-h-12 min-w-14 flex-1 flex-col items-center justify-center gap-0.5 rounded-xl text-label-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary disabled:opacity-40',
              a.destructive ? 'text-status-destructive hover:bg-status-destructive/10' : 'text-on-surface-variant hover:bg-surface-container-high',
            )}
          >
            {a.icon}
            <span className="max-w-full truncate" aria-hidden={a.kurz ? true : undefined}>
              {a.kurz ?? a.label}
            </span>
          </button>
        ))}
      </div>
    )
  }
  return (
    <div role="toolbar" aria-label={anzahlLabel} className={cx('flex min-w-0 flex-1 items-center gap-1', className)}>
      <Button type="button" variant="ghost" size="icon" className="-ml-1 shrink-0 max-md:h-11 max-md:w-11" aria-label={abbrechenLabel} onClick={onAbbrechen}>
        <X className="h-4 w-4" />
      </Button>
      <span className="mr-auto truncate text-sm font-semibold tabular-nums text-on-surface" aria-live="polite">
        {anzahlLabel}
      </span>
      {alleLabel && onAlle && (
        <Button type="button" variant="ghost" size="sm" className="shrink-0 max-md:h-11" onClick={onAlle}>
          <CheckCheck className="h-4 w-4" />
          {alleLabel}
        </Button>
      )}
      {aktionen.map((a) => (
        <Button
          key={a.key}
          type="button"
          variant={a.destructive ? 'destructive' : 'ghost'}
          size="sm"
          disabled={a.disabled}
          onClick={a.onSelect}
          aria-label={a.label}
          className="hidden shrink-0 md:inline-flex"
        >
          {a.icon}
          <span className="hidden lg:inline">{a.label}</span>
        </Button>
      ))}
    </div>
  )
}
