/**
 * ProgressBar — reusable Singra/UI meter for CPU/RAM/resource visualization.
 * Design DNA: quiet cyan track, no aggressive glow, compact for cards/tables.
 * When value is null/undefined, the track stays visible (empty) — never fake 0%.
 */
import { cx } from '@/utils/classNames'

interface ProgressBarProps {
  /** 0–100, or null when usage is unknown */
  value: number | null | undefined
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
  const known = value != null && Number.isFinite(value)
  const clamped = known ? Math.max(0, Math.min(100, value as number)) : 0
  let barColor = 'bg-secondary'
  if (heat && known) {
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
        className={cx(
          'h-2 w-full overflow-hidden rounded-full border border-outline-variant/40',
          known ? 'bg-surface-container-highest' : 'bg-surface-container-highest/70',
        )}
        role="progressbar"
        aria-valuenow={known ? Math.round(clamped) : undefined}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
        aria-valuetext={known ? undefined : 'unknown'}
      >
        {known ? (
          <div
            className={cx('h-full rounded-full transition-all duration-300', barColor)}
            style={{ width: `${clamped}%` }}
          />
        ) : (
          <div
            className="h-full w-full rounded-full bg-[repeating-linear-gradient(90deg,transparent,transparent_4px,rgba(255,255,255,0.04)_4px,rgba(255,255,255,0.04)_8px)]"
            aria-hidden
          />
        )}
      </div>
    </div>
  )
}
