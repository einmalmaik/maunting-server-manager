import { useTranslation } from 'react-i18next'
import { MessageSquare, Sparkles, UsersRound } from 'lucide-react'

export type MessengerModus = 'chats' | 'updates' | 'community'

interface MessengerModeNavProps {
  modus: MessengerModus
  onModus: (modus: MessengerModus) => void
}

/** Die Moduswahl oben in der Seitenleiste, nur auf breiten Bildschirmen. */
export function MessengerModeNav({
  modus,
  onModus,
  hatStories,
}: MessengerModeNavProps & { hatStories: boolean }) {
  const { t } = useTranslation()
  const klasse = (aktiv: boolean) =>
    `flex-1 py-1.5 px-2 rounded-lg text-xs font-medium flex items-center justify-center gap-1.5 transition-colors ${
      aktiv
        ? 'bg-primary text-on-primary shadow-sm font-semibold'
        : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/60'
    }`

  return (
    <div className="hidden md:flex px-2.5 pt-2 pb-1 border-b border-outline-variant/15 items-center gap-1 bg-surface-container/60">
      <button
        type="button"
        onClick={() => onModus('chats')}
        className={klasse(modus === 'chats')}
        aria-label={t('messenger.nav.chats')}
      >
        <MessageSquare className="w-3.5 h-3.5" />
        <span>{t('messenger.nav.chats')}</span>
      </button>
      <button
        type="button"
        onClick={() => onModus('updates')}
        className={`${klasse(modus === 'updates')} relative`}
        aria-label={t('messenger.nav.updates')}
      >
        <Sparkles className="w-3.5 h-3.5" />
        <span>{t('messenger.nav.updates')}</span>
        {hatStories && (
          <span className={`w-2 h-2 rounded-full ${modus === 'updates' ? 'bg-white' : 'bg-status-success animate-pulse'}`} />
        )}
      </button>
      <button
        type="button"
        onClick={() => onModus('community')}
        className={klasse(modus === 'community')}
        aria-label={t('messenger.nav.community')}
      >
        <UsersRound className="w-3.5 h-3.5" />
        <span>{t('messenger.nav.community')}</span>
      </button>
    </div>
  )
}

/** Dieselbe Wahl als Leiste am unteren Rand, nur auf schmalen Bildschirmen. */
export function MessengerBottomNav({ modus, onModus }: MessengerModeNavProps) {
  const { t } = useTranslation()
  const eintraege = [
    { wert: 'chats', label: t('messenger.nav.chats'), Icon: MessageSquare },
    { wert: 'updates', label: t('messenger.nav.updates'), Icon: Sparkles },
    { wert: 'community', label: t('messenger.nav.community'), Icon: UsersRound },
  ] as const

  // Höhe plus sichere Fläche, siehe die Eingabeleiste im Chat.
  return (
    <nav className="md:hidden shrink-0 h-14 box-content pb-[env(safe-area-inset-bottom)] border-t border-outline-variant/20 bg-surface-container/95 backdrop-blur flex items-center justify-around px-2 z-10">
      {eintraege.map(({ wert, label, Icon }) => (
        <button
          key={wert}
          type="button"
          onClick={() => onModus(wert)}
          className={`flex flex-col items-center justify-center flex-1 py-1 transition-colors ${
            modus === wert ? 'text-primary font-semibold' : 'text-on-surface-variant/70 hover:text-on-surface'
          }`}
          aria-label={label}
        >
          <div className={`p-1 rounded-full ${modus === wert ? 'bg-primary/15' : ''}`}>
            <Icon className="w-4 h-4" />
          </div>
          <span className="text-label-sm mt-0.5">{label}</span>
        </button>
      ))}
    </nav>
  )
}
