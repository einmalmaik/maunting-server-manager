import { CheckCircle2, CircleDashed, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

export type ProcessStatus = 'fertig' | 'laeuft' | 'wartet'

export interface ActiveProcessItem {
  id: string
  label: string
  status: ProcessStatus
  detail?: string
}

interface ActiveProcessesCardProps {
  processes?: ActiveProcessItem[]
  className?: string
}

export function ActiveProcessesCard({ processes, className = '' }: ActiveProcessesCardProps) {
  const { t } = useTranslation()

  // Standard-Prozessliste für die regionale Analyse, falls keine dynamische übergeben wird
  const items: ActiveProcessItem[] = processes ?? [
    {
      id: 'satellite',
      label: t('ai.geo.processes.satellite', 'Satellitendaten (Sentinel-2)'),
      status: 'fertig',
    },
    {
      id: 'weather',
      label: t('ai.geo.processes.weather', 'Wetterdaten'),
      status: 'fertig',
    },
    {
      id: 'news',
      label: t('ai.geo.processes.news', 'Nachrichten & Websuche'),
      status: 'fertig',
    },
    {
      id: 'social',
      label: t('ai.geo.processes.social', 'Soziale Medien Analyse'),
      status: 'fertig',
    },
    {
      id: 'traffic',
      label: t('ai.geo.processes.traffic', 'Verkehr & Bewegung'),
      status: 'fertig',
    },
  ]

  return (
    <div
      className={`rounded-xl border border-outline-variant/30 bg-surface-container-lowest/80 p-3.5 backdrop-blur-md space-y-2.5 ${className}`}
      aria-label={t('ai.geo.processes.title', 'Aktive Prozesse')}
    >
      <div className="flex items-center justify-between border-b border-outline-variant/20 pb-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
          {t('ai.geo.processes.title', 'Aktive Prozesse')}
        </h3>
        <span className="flex h-2 w-2 relative">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75" />
          <span className="relative inline-flex rounded-full h-2 w-2 bg-primary" />
        </span>
      </div>

      <div className="space-y-2">
        {items.map((item) => (
          <div
            key={item.id}
            className="flex items-center justify-between text-xs transition-colors"
          >
            <div className="flex items-center gap-2 text-on-surface">
              {item.status === 'fertig' ? (
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 shrink-0" aria-hidden="true" />
              ) : item.status === 'laeuft' ? (
                <Loader2 className="h-3.5 w-3.5 text-primary animate-spin shrink-0" aria-hidden="true" />
              ) : (
                <CircleDashed className="h-3.5 w-3.5 text-on-surface-variant/50 shrink-0" aria-hidden="true" />
              )}
              <span className={item.status === 'wartet' ? 'text-on-surface-variant/70' : 'text-on-surface'}>
                {item.label}
              </span>
            </div>

            <span
              className={`text-[11px] font-medium px-2 py-0.5 rounded-full border ${
                item.status === 'fertig'
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                  : item.status === 'laeuft'
                  ? 'bg-primary/10 text-primary border-primary/20 animate-pulse'
                  : 'bg-surface-container-highest text-on-surface-variant/60 border-outline-variant/20'
              }`}
            >
              {item.status === 'fertig'
                ? t('ai.geo.processes.done', 'Fertig')
                : item.status === 'laeuft'
                ? t('ai.geo.processes.running', 'Läuft...')
                : t('ai.geo.processes.waiting', 'Wartet...')}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
