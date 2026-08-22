/**
 * Slider — Singra/UI Regler für einen Zahlenwert in festen Grenzen.
 *
 * Design DNA: dieselbe Kopfzeile wie ProgressBar (Label links, Wert rechts
 * in Mono), darunter ein nativer Range-Input mit Primärfarbe. Bewusst der
 * native Input statt eines nachgebauten: Tastatur, Touch und Screenreader
 * funktionieren dort ohne eigenen Code, und `accent-color` trägt das Token.
 */
import { cx } from '@/utils/classNames'

interface SliderProps {
  value: number
  min: number
  max: number
  step: number
  onValueChange: (wert: number) => void
  disabled?: boolean
  label?: string
  /** Anzeige des aktuellen Werts rechts über dem Regler (z. B. „150 %"). */
  hint?: string
  /** Zugänglicher Name, wenn kein sichtbares `label` gerendert wird. */
  ariaLabel?: string
  className?: string
}

export function Slider({
  value,
  min,
  max,
  step,
  onValueChange,
  disabled = false,
  label,
  hint,
  ariaLabel,
  className = '',
}: SliderProps) {
  return (
    <div className={cx('w-full', className)}>
      {(label || hint) && (
        <div className="mb-1 flex items-center justify-between gap-2">
          {label && (
            <span className="font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
              {label}
            </span>
          )}
          {hint && (
            <span className="font-mono-sm text-mono-sm text-on-surface-variant">{hint}</span>
          )}
        </div>
      )}
      <input
        type="range"
        value={value}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onChange={(e) => onValueChange(Number(e.target.value))}
        aria-label={ariaLabel ?? label}
        className="h-2 w-full cursor-pointer appearance-auto accent-primary disabled:cursor-not-allowed disabled:opacity-50"
      />
    </div>
  )
}
