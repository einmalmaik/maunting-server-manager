/**
 * ProgressBar — reusable Singra/UI meter for CPU/RAM/resource visualization.
 * Design DNA: quiet cyan track, no aggressive glow, compact for cards/tables.
 */
import { cx } from '@/utils/classNames'

interface ProgressBarProps {
  /** 0–100 */
  value: number
  label?: string
  hint?: string
  className?: string
  /** When true, high values use warning/error tint */
  heat?: boolean
  'data-testid'?: string
}

export function ProgressBar({
  value,
  label,
  hint,
  className = '',
  heat = false,
  'data-testid': testId,
}: ProgressBarProps) {
  const clamped = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0))
  let barColor = 'bg-secondary'
  if (heat) {
    if (clamped >= 90) barColor = 'bg-status-error'
    else if (clamped >= 70) barColor = 'bg-status-warning'
  }

  return (
    <div className={cx('w-full', className)} data-testid={testId}>
      {(label || hint) && (
        <div className="mb-1 flex items-center justify-between gap-2">
          {label && (
            <span className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider">
              {label}
            </span>
          )}
          {hint && (
            <span className="font-mono-sm text-mono-sm text-on-surface-variant">
              {hint}
            </span>
          )}
        </div>
      )}
      <div
        className="h-2 w-full overflow-hidden rounded-full bg-surface-container-highest border border-outline-variant/40"
        role="progressbar"
        aria-valuenow={Math.round(clamped)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
      >
        <div
          className={cx('h-full rounded-full transition-all duration-300', barColor)}
          style={{ width: `${clamped}%` }}
        />
      </div>
    </div>
  )
}
