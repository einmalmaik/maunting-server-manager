import type { ReactNode } from 'react'

interface PageHeaderProps {
  eyebrow: string
  title: string
  description: string
  status?: ReactNode
  actions?: ReactNode
}

export function PageHeader({ eyebrow, title, description, status, actions }: PageHeaderProps) {
  return (
    <header className="msm-page-header">
      <div className="relative flex min-w-0 flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
        <div className="msm-status-rail min-w-0 max-w-3xl">
          <p className="mb-2 font-label-md text-xs font-semibold uppercase tracking-[0.16em] text-primary/75">
            {eyebrow}
          </p>
          <h1 className="font-headline text-2xl font-bold text-on-surface md:text-[2rem]">{title}</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-on-surface-variant md:text-base">{description}</p>
        </div>
        {(status || actions) && (
          <div className="flex min-w-0 flex-col items-start gap-3 sm:flex-row sm:items-center lg:shrink-0 lg:justify-end">
            {status}
            {actions && <div className="max-w-full overflow-x-auto">{actions}</div>}
          </div>
        )}
      </div>
    </header>
  )
}
