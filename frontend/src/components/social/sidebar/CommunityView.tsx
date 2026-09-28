import { useTranslation } from 'react-i18next'
import { Plus, Share2, UsersRound } from 'lucide-react'
import { Button } from '@/Singra/UI'
import type { ChatGroupItem } from '@/api/social'

interface CommunityViewProps {
  gruppen: ChatGroupItem[]
  onGruppe: (gruppe: ChatGroupItem) => void
  onNeueGruppe: () => void
  onEinladungKopieren: (gruppe: ChatGroupItem) => void
}

/** Der Reiter „Community": alle Gruppen samt Einladungslink. */
export function CommunityView({ gruppen, onGruppe, onNeueGruppe, onEinladungKopieren }: CommunityViewProps) {
  const { t } = useTranslation()

  return (
    <div className="space-y-3 p-1">
      <div className="flex items-center justify-between px-1 pt-1">
        <div>
          <div className="text-xs font-headline font-bold text-primary flex items-center gap-1.5">
            <UsersRound className="w-3.5 h-3.5" />
            <span>{t('messenger.communitiesTitle')}</span>
            <span className="text-label-sm text-on-surface-variant/70">({gruppen.length})</span>
          </div>
          <p className="text-label-sm text-on-surface-variant/80">
            {t('messenger.communitySubtitle')}
          </p>
        </div>
        <Button
          type="button"
          variant="primary"
          size="sm"
          onClick={onNeueGruppe}
          className="h-7 text-xs gap-1 px-2.5 rounded-full"
          aria-label={t('messenger.newGroup')}
          title={t('messenger.newGroup')}
        >
          <Plus className="w-3.5 h-3.5" />
          <span>{t('messenger.createGroup')}</span>
        </Button>
      </div>

      <div className="space-y-1.5 pt-1">
        {gruppen.length === 0 ? (
          <p className="py-6 text-center text-xs text-on-surface-variant/70">
            {t('messenger.noGroupsJoined')}
          </p>
        ) : (
          gruppen.map((g) => (
            <div
              key={`comm-g-${g.id}`}
              className="flex items-center justify-between p-2.5 rounded-xl border border-outline-variant/20 bg-surface-container-lowest/60"
            >
              <div
                className="flex items-center gap-3 min-w-0 flex-1 cursor-pointer"
                onClick={() => onGruppe(g)}
              >
                <div className="w-9 h-9 rounded-full bg-primary/10 text-primary flex items-center justify-center font-bold text-xs shrink-0">
                  {g.avatar_url ? (
                    <img src={g.avatar_url} alt="" className="w-full h-full rounded-full object-cover" />
                  ) : (
                    <UsersRound className="w-4 h-4" />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-semibold text-primary truncate">{g.name}</div>
                  <div className="text-label-sm text-on-surface-variant/70">{g.member_count} Mitglieder</div>
                </div>
              </div>

              {g.invite_code && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => onEinladungKopieren(g)}
                  className="h-7 px-2 text-xs gap-1 text-primary"
                  title={t('messenger.copyInvite')}
                >
                  <Share2 className="w-3.5 h-3.5" />
                  <span>Link</span>
                </Button>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
