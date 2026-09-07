import { useState, useEffect } from 'react'
import {
  Button,
  Badge,
  Dialog,
  DialogContent,
} from '@/Singra/UI'
import {
  Trophy,
  Award,
  Clock,
  Sparkles,
  CheckCircle2,
  Lock,
  Flame,
} from 'lucide-react'
import {
  type AchievementsOverview,
  type UserStatsResponse,
  getAchievements,
  getStats,
} from '@/api/social'
import { renderAchievementIcon } from './achievementIcons'

interface AchievementsModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function AchievementsModal({ open, onOpenChange }: AchievementsModalProps) {
  const [overview, setOverview] = useState<AchievementsOverview | null>(null)
  const [stats, setStats] = useState<UserStatsResponse | null>(null)
  const [filter, setFilter] = useState<'all' | 'unlocked' | 'locked'>('all')

  const fetchData = async () => {
    try {
      const [ovData, stData] = await Promise.all([getAchievements(), getStats()])
      setOverview(ovData)
      setStats(stData)
    } catch {
      // Ignore network errors
    }
  }

  useEffect(() => {
    if (open) {
      fetchData()
    }
  }, [open])

  const formatHours = (seconds: number) => {
    const hrs = Math.floor(seconds / 3600)
    const mins = Math.floor((seconds % 3600) / 60)
    if (hrs === 0) return `${mins}m`
    return `${hrs}h ${mins}m`
  }

  const filteredAchievements = (overview?.achievements || []).filter((item) => {
    if (filter === 'unlocked') return item.unlocked
    if (filter === 'locked') return !item.unlocked
    return true
  })

  const progressPercent = overview
    ? Math.round((overview.total_unlocked / Math.max(overview.total_available, 1)) * 100)
    : 0

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] p-0" showCloseButton>
        {/* Steam-Style Header */}
        <div className="p-6 border-b border-outline-variant/30 bg-gradient-to-r from-surface-container-low via-surface-container to-surface-container-low relative">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pr-8">
            <div className="flex items-center gap-3">
              <div className="p-3 rounded-2xl bg-amber-500/15 border border-amber-500/30 text-amber-400 shadow-[0_0_15px_rgba(245,158,11,0.2)]">
                <Trophy className="w-8 h-8" />
              </div>
              <div>
                <h3 className="font-headline text-title-lg font-black text-primary tracking-tight">
                  Errungenschaften & Prestige
                </h3>
                <div className="font-body text-xs text-on-surface-variant flex items-center gap-2 mt-0.5">
                  <span>Zentrale Kontobindung (MSM & MSS)</span>
                  <span>•</span>
                  <span className="text-amber-400 font-semibold">{overview?.prestige_score || 0} Prestige-Punkte</span>
                </div>
              </div>
            </div>

            {/* Quick Level / Progress Badge */}
            <div className="flex flex-col items-end">
              <div className="text-xs font-bold text-primary mb-1">
                {overview?.total_unlocked || 0} / {overview?.total_available || 0} ({progressPercent}%)
              </div>
              <div className="w-32 h-2.5 bg-surface-container-high rounded-full overflow-hidden border border-outline-variant/30">
                <div
                  className="h-full bg-gradient-to-r from-amber-500 to-amber-300 transition-all duration-500 rounded-full"
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
            </div>
          </div>

          {/* Activity Time Counter Bar */}
          {stats && (
            <div className="mt-5 pt-4 border-t border-outline-variant/20 grid grid-cols-2 sm:grid-cols-4 gap-2">
              <div className="bg-surface-container-high/40 p-2 rounded-lg border border-outline-variant/20 text-center">
                <div className="flex items-center justify-center gap-1 text-[11px] text-on-surface-variant mb-0.5">
                  <Clock className="w-3 h-3 text-primary" />
                  <span>Aktive Spielzeit</span>
                </div>
                <div className="text-xs font-bold text-primary font-mono">
                  {formatHours(stats.total_activity_seconds || stats.active_time_seconds || 0)}
                </div>
              </div>
              <div className="bg-surface-container-high/40 p-2 rounded-lg border border-outline-variant/20 text-center">
                <div className="flex items-center justify-center gap-1 text-[11px] text-on-surface-variant mb-0.5">
                  <Sparkles className="w-3 h-3 text-cyan-400" />
                  <span>KI-Dialoge</span>
                </div>
                <div className="text-xs font-bold text-primary font-mono">
                  {formatHours(stats.categories?.ai_chat || stats.active_time_by_category?.ai_chat || 0)}
                </div>
              </div>
              <div className="bg-surface-container-high/40 p-2 rounded-lg border border-outline-variant/20 text-center">
                <div className="flex items-center justify-center gap-1 text-[11px] text-on-surface-variant mb-0.5">
                  <Award className="w-3 h-3 text-amber-400" />
                  <span>Server-Admin</span>
                </div>
                <div className="text-xs font-bold text-primary font-mono">
                  {formatHours(stats.categories?.server_admin || stats.active_time_by_category?.server_admin || 0)}
                </div>
              </div>
              <div className="bg-surface-container-high/40 p-2 rounded-lg border border-outline-variant/20 text-center">
                <div className="flex items-center justify-center gap-1 text-[11px] text-on-surface-variant mb-0.5">
                  <Flame className="w-3 h-3 text-rose-400" />
                  <span>Terminal / Befehle</span>
                </div>
                <div className="text-xs font-bold text-primary font-mono">
                  {formatHours(stats.categories?.command_exec || stats.active_time_by_category?.command_exec || 0)}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Filter Buttons */}
        <div className="px-6 py-3 border-b border-outline-variant/20 bg-surface-container-lowest flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5">
            <Button
              variant={filter === 'all' ? 'primary' : 'ghost'}
              size="sm"
              onClick={() => setFilter('all')}
              className="text-xs h-7 px-3"
            >
              Alle ({overview?.achievements.length || 0})
            </Button>
            <Button
              variant={filter === 'unlocked' ? 'primary' : 'ghost'}
              size="sm"
              onClick={() => setFilter('unlocked')}
              className="text-xs h-7 px-3"
            >
              Freigeschaltet ({overview?.total_unlocked || 0})
            </Button>
            <Button
              variant={filter === 'locked' ? 'primary' : 'ghost'}
              size="sm"
              onClick={() => setFilter('locked')}
              className="text-xs h-7 px-3"
            >
              Gesperrt ({(overview?.total_available || 0) - (overview?.total_unlocked || 0)})
            </Button>
          </div>
        </div>

        {/* Achievement List */}
        <div className="flex-1 overflow-y-auto p-6 space-y-3">
          {filteredAchievements.map((item) => {
            const rarity = item.rarity_percent ?? item.global_unlocked_percentage ?? 0
            const isRare = rarity > 0 && rarity <= 10
            return (
              <div
                key={item.id}
                className={`flex items-center gap-4 p-4 rounded-xl border transition-all ${
                  item.unlocked
                    ? 'bg-surface-container/60 border-outline-variant/40 shadow-sm'
                    : 'bg-surface-container-lowest/40 border-outline-variant/20 opacity-60'
                }`}
              >
                {/* Milestone Icon */}
                <div
                  className={`w-12 h-12 rounded-xl flex items-center justify-center text-xl shrink-0 border ${
                    item.unlocked
                      ? isRare
                        ? 'bg-amber-500/20 border-amber-500/40 text-amber-300 shadow-[0_0_12px_rgba(245,158,11,0.25)]'
                        : 'bg-primary/15 border-primary/30 text-primary'
                      : 'bg-surface-container-high/50 border-outline-variant/20 text-on-surface-variant/40'
                  }`}
                >
                  {item.unlocked ? renderAchievementIcon(item.icon) : <Lock className="w-5 h-5" />}
                </div>

                {/* Details */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h4 className="font-headline text-body-md font-bold text-primary truncate">
                      {item.title}
                    </h4>
                    <span className="text-[11px] font-mono text-amber-400/90 font-semibold">
                      +{item.points} Pkt
                    </span>
                    {isRare && (
                      <Badge variant="warning" className="text-[10px] uppercase tracking-wider py-0 px-1.5 font-bold">
                        💎 Selten
                      </Badge>
                    )}
                  </div>
                  <p className="font-body text-xs text-on-surface-variant mt-0.5 leading-relaxed">
                    {item.description}
                  </p>

                  <div className="flex items-center gap-3 mt-2 text-[11px] text-on-surface-variant/70 flex-wrap">
                    <span className={`font-medium ${isRare ? 'text-amber-400' : ''}`}>
                      {item.rarity_text}
                    </span>

                    {item.unlocked && item.unlocked_at && (
                      <span className="inline-flex items-center gap-1 text-emerald-400/90">
                        <CheckCircle2 className="w-3 h-3" />
                        <span>Freigeschaltet am {new Date(item.unlocked_at).toLocaleDateString()}</span>
                      </span>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </DialogContent>
    </Dialog>
  )
}
