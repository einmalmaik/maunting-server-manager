import { useTranslation } from 'react-i18next'
import { Plus } from 'lucide-react'
import { Avatar } from '@/Singra/UI'
import type { ChatStoryItem } from '@/api/social'

/** Die Stories eines Kontakts, so wie Leiste und Statusansicht sie zeigen. */
export interface StoryGruppe {
  userId: number
  username: string
  avatarUrl?: string | null
  stories: ChatStoryItem[]
  latestStory?: ChatStoryItem
  hasUnseen: boolean
}

export interface StoryIch {
  avatarUrl?: string | null
  username?: string | null
}

interface StoriesCarouselBarProps {
  ich: StoryIch
  eigeneStories: ChatStoryItem[]
  freunde: StoryGruppe[]
  onOeffnen: (stories: ChatStoryItem[]) => void
  onErstellen: () => void
}

/** Die Story-Leiste über der Chatliste. */
export function StoriesCarouselBar({ ich, eigeneStories, freunde, onOeffnen, onErstellen }: StoriesCarouselBarProps) {
  const { t } = useTranslation()

  return (
    <div className="px-2.5 py-2 border-b border-outline-variant/15 bg-surface-container/20">
      <div className="flex items-center gap-3 overflow-x-auto no-scrollbar py-1">
        {/* Der eigene Kreis: vorhandene Stories ansehen oder eine anlegen */}
        <div className="flex flex-col items-center gap-1 shrink-0 w-14">
          <div
            className="relative cursor-pointer group"
            onClick={() => (eigeneStories.length > 0 ? onOeffnen(eigeneStories) : onErstellen())}
          >
            <div
              className={`w-12 h-12 rounded-full p-0.5 shrink-0 transition-transform group-hover:scale-105 flex items-center justify-center ${
                eigeneStories.length > 0
                  ? 'bg-gradient-to-tr from-cyan-400 via-sky-500 to-indigo-500 ring-2 ring-primary/30 ring-offset-2 ring-offset-surface'
                  : 'border-2 border-dashed border-outline-variant/70'
              }`}
            >
              <Avatar src={ich.avatarUrl} name={ich.username || 'Ich'} size="md" />
            </div>
            {eigeneStories.length === 0 ? (
              <div className="absolute -bottom-0.5 -right-0.5 w-4 h-4 rounded-full bg-primary text-on-primary flex items-center justify-center text-label-sm shadow-sm border-2 border-surface">
                <Plus className="w-2.5 h-2.5" />
              </div>
            ) : (
              <span className="absolute -top-0.5 -right-0.5 w-4 h-4 rounded-full bg-status-success text-white text-label-sm font-bold flex items-center justify-center border-2 border-surface">
                {eigeneStories.length}
              </span>
            )}
          </div>
          <span className="text-label-sm text-on-surface-variant truncate w-full text-center">
            {eigeneStories.length > 0 ? t('messenger.yourStatus') : 'Neu'}
          </span>
        </div>

        {freunde.map((gruppe) => (
          <div
            key={`tray-user-${gruppe.userId}`}
            className="flex flex-col items-center gap-1 shrink-0 w-14 cursor-pointer group"
            onClick={() => onOeffnen(gruppe.stories)}
          >
            <div className="relative shrink-0">
              <div
                className={`w-12 h-12 rounded-full p-0.5 shrink-0 transition-all duration-300 group-hover:scale-105 flex items-center justify-center ${
                  gruppe.hasUnseen
                    ? 'bg-gradient-to-tr from-cyan-400 via-indigo-500 to-fuchsia-500 ring-2 ring-primary ring-offset-2 ring-offset-surface shadow-[0_0_14px_rgba(99,102,241,0.65)] animate-pulse'
                    : 'bg-surface-container-highest ring-1 ring-outline-variant/50 opacity-85'
                }`}
              >
                <Avatar src={gruppe.avatarUrl} name={gruppe.username} size="md" />
              </div>
              {gruppe.stories.length > 1 && (
                <span className="absolute -top-0.5 -right-0.5 w-4 h-4 rounded-full bg-primary text-on-primary text-label-sm font-bold flex items-center justify-center border-2 border-surface">
                  {gruppe.stories.length}
                </span>
              )}
            </div>
            <span
              className={`text-label-sm truncate w-full text-center ${
                gruppe.hasUnseen ? 'text-primary font-bold' : 'text-on-surface-variant font-normal'
              }`}
            >
              {gruppe.username}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
