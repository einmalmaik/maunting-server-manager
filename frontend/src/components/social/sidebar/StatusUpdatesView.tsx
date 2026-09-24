import { useTranslation } from 'react-i18next'
import { Clock, Plus, Sparkles } from 'lucide-react'
import { Avatar, Button } from '@/Singra/UI'
import type { ChatStoryItem } from '@/api/social'
import { DeviceBadge } from '@/components/social/DeviceBadge'
import { StatusDot } from '@/components/social/StatusIndicator'
import type { ChatContact } from '@/components/social/sidebar/ConversationListItem'
import type { StoryGruppe, StoryIch } from './StoriesCarouselBar'

interface StatusUpdatesViewProps {
  ich: StoryIch
  eigeneStories: ChatStoryItem[]
  freunde: StoryGruppe[]
  kontakte: ChatContact[]
  onOeffnen: (stories: ChatStoryItem[]) => void
  onErstellen: () => void
  onChat: (kontakt: ChatContact) => void
}

/** Der Reiter „Aktuelles": eigener Status, Stories der Freunde, wer gerade da ist. */
export function StatusUpdatesView({
  ich,
  eigeneStories,
  freunde,
  kontakte,
  onOeffnen,
  onErstellen,
  onChat,
}: StatusUpdatesViewProps) {
  const { t } = useTranslation()
  const eigeneOeffnen = () => (eigeneStories.length > 0 ? onOeffnen(eigeneStories) : onErstellen())

  return (
    <div className="space-y-4 p-1">
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-primary" />
          <span className="text-sm font-headline font-bold text-on-surface">Status</span>
        </div>
        <Button
          type="button"
          variant="primary"
          size="sm"
          onClick={onErstellen}
          className="h-8 text-xs gap-1.5 px-3 rounded-xl font-medium"
          title={t('messenger.addStatus')}
        >
          <Plus className="w-3.5 h-3.5" />
          <span>{t('common.add')}</span>
        </Button>
      </div>

      {/* Eigener Status */}
      <div className="p-3.5 rounded-2xl bg-surface-container/70 border border-outline-variant/35 shadow-sm transition-colors hover:bg-surface-container/90">
        <div className="flex items-center justify-between mb-3">
          <span className="text-xs font-headline font-bold text-on-surface">{t('messenger.myStatus')}</span>
          <span className="text-label-sm text-on-surface-variant font-medium">{t('social.story.badge24h')}</span>
        </div>

        <div className="flex items-center gap-3">
          <div className="relative cursor-pointer shrink-0" onClick={eigeneOeffnen}>
            <div className={`w-12 h-12 rounded-full p-0.5 shrink-0 flex items-center justify-center ${
              eigeneStories.length > 0
                ? 'bg-gradient-to-tr from-cyan-400 via-sky-500 to-indigo-500 ring-2 ring-primary/40 ring-offset-2 ring-offset-surface'
                : 'border-2 border-dashed border-outline-variant/80'
            }`}>
              <Avatar src={ich.avatarUrl} name={ich.username || 'Ich'} size="md" />
            </div>
            <div className="absolute -bottom-1 -right-1 w-5 h-5 rounded-full bg-primary text-on-primary flex items-center justify-center text-xs shadow-md border-2 border-surface">
              <Plus className="w-3 h-3" />
            </div>
          </div>
          <div className="min-w-0 flex-1 cursor-pointer" onClick={eigeneOeffnen}>
            <div className="text-xs font-semibold text-on-surface truncate">
              {eigeneStories.length > 0 ? t('messenger.viewStatus') : t('social.story.share')}
            </div>
            <p className="text-label-sm text-on-surface-variant truncate">
              {eigeneStories.length > 0
                ? t('messenger.activeStories', { count: eigeneStories.length })
                : t('messenger.statusHint')}
            </p>
          </div>
        </div>
      </div>

      {/* Stories der Freunde */}
      <div className="space-y-2">
        <div className="px-1 text-label-sm font-semibold text-on-surface-variant/80 uppercase tracking-wider flex items-center justify-between">
          <span>{t('messenger.recentUpdates')}</span>
          <span className="text-label-sm px-1.5 py-0.5 rounded-full bg-surface-container font-mono text-on-surface-variant">
            {freunde.length}
          </span>
        </div>

        {freunde.length === 0 ? (
          <div className="p-4 rounded-xl bg-surface-container/40 border border-outline-variant/25 text-center text-xs text-on-surface-variant">
            {t('messenger.noStatusUpdates')}
          </div>
        ) : (
          <div className="space-y-1.5">
            {freunde.map((grp) => (
              <div
                key={`story-grp-${grp.userId}`}
                onClick={() => onOeffnen(grp.stories)}
                className="flex items-center gap-3 p-2.5 rounded-xl border border-outline-variant/30 bg-surface-container/60 hover:bg-surface-container-high/80 cursor-pointer transition-colors"
              >
                <div className="relative shrink-0">
                  <div
                    className={`w-12 h-12 rounded-full p-0.5 shrink-0 transition-all duration-300 flex items-center justify-center ${
                      grp.hasUnseen
                        ? 'bg-gradient-to-tr from-cyan-400 via-indigo-500 to-fuchsia-500 ring-2 ring-primary ring-offset-2 ring-offset-surface shadow-[0_0_14px_rgba(99,102,241,0.65)] animate-pulse'
                        : 'bg-surface-container-highest ring-1 ring-outline-variant/50 opacity-85'
                    }`}
                  >
                    <Avatar src={grp.avatarUrl} name={grp.username} size="md" />
                  </div>
                  {grp.stories.length > 1 && (
                    <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-primary text-on-primary text-label-sm font-bold flex items-center justify-center border border-surface">
                      {grp.stories.length}
                    </span>
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-semibold text-on-surface truncate">
                    {grp.username}
                  </div>
                  <div className="text-label-sm text-on-surface-variant flex items-center gap-1">
                    <Clock className="w-3 h-3" />
                    <span>
                      {grp.latestStory
                        ? new Date(grp.latestStory.created_at).toLocaleTimeString([], {
                            hour: '2-digit',
                            minute: '2-digit',
                          })
                        : ''}
                    </span>
                    {grp.stories.length > 1 && (
                      <span className="text-on-surface-variant/70">• {grp.stories.length} Updates</span>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Wer gerade da ist */}
      <div className="space-y-2 pt-2 border-t border-outline-variant/20">
        <div className="px-1 text-label-sm font-semibold text-on-surface-variant/70 uppercase tracking-wider">
          {t('messenger.contactActivity')}
        </div>
        <div className="space-y-1">
          {kontakte.map((c) => (
            <div
              key={c.listKey}
              className="flex items-center justify-between p-2.5 rounded-xl border border-outline-variant/20 bg-surface-container-lowest/60"
            >
              <div className="flex items-center gap-3 min-w-0">
                <div className="relative shrink-0">
                  <Avatar src={c.avatarUrl} name={c.username} size="sm" />
                  <StatusDot status={c.status} size="sm" className="absolute bottom-0 right-0" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-semibold text-primary truncate flex items-center gap-1.5">
                    <span>{c.username}</span>
                    <DeviceBadge deviceType={c.deviceType} />
                  </div>
                  <div className="text-label-sm text-on-surface-variant/80 truncate">
                    {c.activityLabel || (c.status === 'online' ? 'Online' : c.status === 'away' ? 'Abwesend' : 'Offline')}
                  </div>
                </div>
              </div>

              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => onChat(c)}
                className="text-xs h-7 px-2 text-primary"
              >
                Chat
              </Button>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
