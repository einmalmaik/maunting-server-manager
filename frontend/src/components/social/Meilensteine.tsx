import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckCircle2, Lock, Trophy } from 'lucide-react'

import type { AchievementItem } from '@/api/social'
import { renderAchievementIcon } from '@/components/social/achievementIcons'
import { Badge, Button } from '@/Singra/UI'

type Filter = 'all' | 'unlocked' | 'locked'

const SCHRITT = 12

/**
 * Meilensteine mit Fortschritt, Filter und Nachladen.
 *
 * Dieselbe Karte für die eigenen Erfolge (Profil → Freunde) und für ein fremdes
 * Profil. Auf dem fremden stehen nur die Abzeichen, die der Server Fremden
 * zeigt; Zähler und Punkte kommen von dort mit, damit sie zur Liste passen.
 */
export function Meilensteine({
  achievements,
  freigeschaltet,
  gesamt,
  punkte,
  titel,
  beschreibung,
  startFilter = 'all',
}: {
  achievements: AchievementItem[]
  freigeschaltet: number
  gesamt: number
  punkte: number
  titel: string
  beschreibung?: string
  startFilter?: Filter
}) {
  const { t } = useTranslation()
  const [filter, setFilter] = useState<Filter>(startFilter)
  const [sichtbar, setSichtbar] = useState(SCHRITT)

  const gefiltert = useMemo(() => {
    if (filter === 'unlocked') return achievements.filter((m) => m.unlocked)
    if (filter === 'locked') return achievements.filter((m) => !m.unlocked)
    return achievements
  }, [achievements, filter])

  const prozent = Math.round((freigeschaltet / Math.max(gesamt, 1)) * 100)

  const waehle = (neu: Filter) => {
    setFilter(neu)
    setSichtbar(SCHRITT)
  }

  return (
    <section className="msm-card p-6" aria-labelledby="milestones-title">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-4">
        <div className="flex items-center gap-2">
          <Trophy className="h-5 w-5 text-secondary" aria-hidden="true" />
          <div>
            <h2 id="milestones-title" className="font-headline text-title-lg font-semibold text-on-surface">
              {titel}
            </h2>
            {beschreibung && <p className="font-body-md text-xs text-on-surface-variant mt-0.5">{beschreibung}</p>}
          </div>
        </div>

        <div className="flex items-center gap-3 self-start sm:self-auto">
          <div className="text-right">
            <span className="text-xs font-bold text-primary font-mono block">
              {freigeschaltet} / {gesamt}
            </span>
            <span className="text-label-sm text-on-surface-variant font-mono">
              {t('social.milestones.points', { count: punkte })}
            </span>
          </div>
          <div className="w-24 h-2 bg-surface-container-high rounded-full overflow-hidden border border-outline-variant/30">
            <div
              className="h-full bg-primary transition-all duration-500 rounded-full"
              style={{ width: `${prozent}%` }}
            />
          </div>
        </div>
      </div>

      <div className="flex items-center gap-1.5 mb-4 pt-1 border-t border-outline-variant/20 flex-wrap">
        <Button
          variant={filter === 'all' ? 'primary' : 'ghost'}
          size="sm"
          onClick={() => waehle('all')}
          className="text-xs h-7 px-3"
        >
          {t('social.milestones.filterAll', { count: achievements.length })}
        </Button>
        <Button
          variant={filter === 'unlocked' ? 'primary' : 'ghost'}
          size="sm"
          onClick={() => waehle('unlocked')}
          className="text-xs h-7 px-3"
        >
          {t('social.milestones.filterUnlocked', { count: freigeschaltet })}
        </Button>
        <Button
          variant={filter === 'locked' ? 'primary' : 'ghost'}
          size="sm"
          onClick={() => waehle('locked')}
          className="text-xs h-7 px-3"
        >
          {t('social.milestones.filterLocked', { count: Math.max(gesamt - freigeschaltet, 0) })}
        </Button>
      </div>

      {gefiltert.length === 0 ? (
        <p className="text-xs text-on-surface-variant/70 py-6">{t('social.milestones.none')}</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {gefiltert.slice(0, sichtbar).map((m) => (
            <Meilenstein key={m.id} m={m} />
          ))}
        </div>
      )}

      {gefiltert.length > sichtbar && (
        <div className="pt-3 flex justify-center">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setSichtbar((bisher) => bisher + SCHRITT)}
            className="text-xs gap-1.5 px-4"
          >
            <span>{t('social.milestones.loadMore', { count: gefiltert.length - sichtbar })}</span>
          </Button>
        </div>
      )}
    </section>
  )
}

function Meilenstein({ m }: { m: AchievementItem }) {
  const { t } = useTranslation()
  const seltenheit = m.rarity_percent ?? m.global_unlocked_percentage ?? 0
  const selten = seltenheit > 0 && seltenheit <= 10

  return (
    <div
      className={`flex items-start gap-3.5 p-3.5 rounded-xl border transition-all ${
        m.unlocked
          ? 'bg-surface-container-low border-outline-variant/40 shadow-sm'
          : 'bg-surface-container-lowest/40 border-outline-variant/20 opacity-55'
      }`}
    >
      <div
        className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 border ${
          m.unlocked
            ? selten
              ? 'bg-status-warning/20 border-status-warning/40 text-status-warning'
              : 'bg-primary/15 border-primary/30 text-primary'
            : 'bg-surface-container-high/50 border-outline-variant/20 text-on-surface-variant/40'
        }`}
      >
        {m.unlocked ? renderAchievementIcon(m.icon, 'w-5 h-5') : <Lock className="w-4 h-4" />}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-headline text-xs font-bold text-primary truncate">{m.title}</span>
          <span className="text-label-sm font-mono text-status-warning/90 font-semibold">
            {t('social.milestones.pointsShort', { count: m.points })}
          </span>
          {selten && (
            <Badge variant="warning" className="text-label-sm px-1 py-0 uppercase font-bold">
              {t('social.milestones.rare')}
            </Badge>
          )}
        </div>
        <p className="font-body text-xs text-on-surface-variant mt-0.5 leading-relaxed">{m.description}</p>
        <div className="flex items-center gap-3 mt-1.5 text-label-sm text-on-surface-variant/70 flex-wrap">
          {m.rarity_text && <span>{m.rarity_text}</span>}
          {m.unlocked && m.unlocked_at && (
            <span className="inline-flex items-center gap-1 text-status-success">
              <CheckCircle2 className="w-3 h-3" />
              <span>{t('social.milestones.unlockedOn', { date: new Date(m.unlocked_at).toLocaleDateString() })}</span>
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
