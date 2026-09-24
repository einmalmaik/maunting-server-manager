import { useTranslation } from 'react-i18next'
import { Briefcase, Globe, LayoutGrid, UserCheck, UsersRound, type LucideIcon } from 'lucide-react'
import type { ChatContact } from '@/components/social/sidebar/ConversationListItem'

export type KontaktFilter = 'all' | 'groups' | 'friends' | 'teams' | 'public'

interface ContactFilterTabsProps {
  filter: KontaktFilter
  onFilter: (filter: KontaktFilter) => void
  gruppenAnzahl: number
  kontakte: ChatContact[]
}

interface Reiter {
  wert: KontaktFilter
  Icon: LucideIcon
  titel: string
  /** Kurzer Text neben dem Symbol, erst ab `xs` sichtbar. */
  label?: string
  /** Ohne Angabe steht keine Zahl am Reiter. */
  anzahl?: number
}

/** Die Filterreiter über der Chatliste. */
export function ContactFilterTabs({ filter, onFilter, gruppenAnzahl, kontakte }: ContactFilterTabsProps) {
  const { t } = useTranslation()
  const freunde = kontakte.filter((c) => c.isFriend).length
  const team = kontakte.filter((c) => c.teamName).length
  const oeffentlich = kontakte.filter((c) => c.isPublicUser).length

  const reiter: Reiter[] = [
    { wert: 'all', Icon: LayoutGrid, titel: t('messenger.filterAll'), label: 'Alle' },
    { wert: 'groups', Icon: UsersRound, titel: t('messenger.filterGroups', { count: gruppenAnzahl }), anzahl: gruppenAnzahl },
    { wert: 'friends', Icon: UserCheck, titel: t('messenger.filterFriends', { count: freunde }), anzahl: freunde },
    { wert: 'teams', Icon: Briefcase, titel: t('messenger.filterTeams', { count: team }), anzahl: team },
    { wert: 'public', Icon: Globe, titel: t('messenger.filterPublic', { count: oeffentlich }), label: 'Entdecken', anzahl: oeffentlich },
  ]

  return (
    <div className="grid grid-cols-5 gap-0.5 sm:gap-1 p-1 rounded-xl bg-surface-container-high/50 border border-outline-variant/15 w-full">
      {reiter.map(({ wert, Icon, titel, label, anzahl }) => {
        const aktiv = filter === wert
        return (
          <button
            key={wert}
            type="button"
            onClick={() => onFilter(wert)}
            className={`h-7 rounded-lg flex items-center justify-center ${wert === 'all' ? 'gap-1' : 'gap-0.5 sm:gap-1'} transition-all text-xs font-semibold ${
              aktiv
                ? 'bg-primary text-on-primary shadow-sm'
                : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/70'
            }`}
            title={titel}
            aria-label={titel}
          >
            <Icon className="w-3.5 h-3.5 shrink-0" />
            {label && <span className="text-label-sm leading-none hidden xs:inline">{label}</span>}
            {anzahl !== undefined && anzahl > 0 && (
              <span
                className={`text-label-sm px-1 py-0.2 rounded-full font-bold leading-none ${
                  aktiv ? 'bg-white/20 text-white' : 'bg-surface-container-highest text-on-surface-variant'
                }`}
              >
                {anzahl}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
