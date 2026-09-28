import { useTranslation } from 'react-i18next'

import type { UserStatsResponse } from '@/api/social'
import { formatActivityCategory } from '@/hooks/usePresenceAndActivity'

/**
 * Die Nutzungszeit eines Kontos: gesamt und je Bereich (Panel, KI, Server …).
 *
 * Eine Fassung für die eigene Ansicht im Browser, die in der App und das
 * fremde Profil. Vorher stand sie nur in der App, mit eigener Rechnung und
 * deutschen Einheiten im Quelltext.
 */
export function Nutzungszeit({ stats }: { stats?: UserStatsResponse | null }) {
  const { t } = useTranslation()
  const gesamt = stats?.active_time_seconds ?? stats?.total_activity_seconds ?? 0
  const bereiche = Object.entries(stats?.active_time_by_category ?? stats?.categories ?? {})
    .filter(([, sekunden]) => sekunden > 0)
    .sort((a, b) => b[1] - a[1])

  const dauer = (sekunden: number) => {
    const stunden = Math.floor(sekunden / 3600)
    const minuten = Math.floor((sekunden % 3600) / 60)
    return stunden === 0
      ? t('social.milestones.durationMinutes', { minutes: minuten })
      : t('social.milestones.durationHours', { hours: stunden, minutes: minuten })
  }

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
      <Kachel label={t('social.milestones.statActivity')} wert={dauer(gesamt)} hervorgehoben />
      {bereiche.map(([bereich, sekunden]) => (
        <Kachel
          key={bereich}
          label={t(`social.activityCategory.${bereich}`, { defaultValue: formatActivityCategory(bereich) })}
          wert={dauer(sekunden)}
        />
      ))}
    </div>
  )
}

function Kachel({ label, wert, hervorgehoben = false }: { label: string; wert: string; hervorgehoben?: boolean }) {
  return (
    <div
      className={`p-2.5 rounded-xl border ${
        hervorgehoben
          ? 'bg-primary/10 border-primary/30'
          : 'bg-surface-container-low border-outline-variant/30'
      }`}
    >
      <span className="text-label-sm font-bold text-on-surface-variant tracking-wider block truncate">{label}</span>
      <span className={`text-xs font-semibold font-mono ${hervorgehoben ? 'text-primary' : 'text-on-surface'}`}>
        {wert}
      </span>
    </div>
  )
}
