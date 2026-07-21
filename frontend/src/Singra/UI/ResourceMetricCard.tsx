import type { ReactNode } from 'react'
import { ProgressBar } from './ProgressBar'

interface ResourceMetricCardProps {
  label: string
  value: ReactNode
  hint: string
  icon: ReactNode
  percent?: number | null
  heat?: boolean
}

/** Dense, data-only server metric. Unknown percentages stay visibly unknown. */
export function ResourceMetricCard({
  label,
  value,
  hint,
  icon,
  percent,
  heat = false,
}: ResourceMetricCardProps) {
  return (
    <section className="group relative min-h-[112px] overflow-hidden rounded-xl border border-outline-variant/75 bg-surface-container-low/75 px-4 py-3.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.025)]">
      <div className="absolute inset-x-6 top-0 h-px bg-gradient-to-r from-transparent via-primary/20 to-transparent" aria-hidden />
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-outline-variant bg-surface-container text-secondary">
          {icon}
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-label-md text-[11px] font-semibold uppercase tracking-[0.09em] text-on-surface-variant">
            {label}
          </p>
          <p className="mt-0.5 truncate font-headline text-lg font-semibold text-on-surface">{value}</p>
        </div>
      </div>
      {percent !== undefined ? (
        <ProgressBar value={percent} hint={hint} heat={heat} className="mt-2.5" />
      ) : (
        <p className="mt-2.5 truncate text-xs text-on-surface-variant">{hint}</p>
      )}
    </section>
  )
}
