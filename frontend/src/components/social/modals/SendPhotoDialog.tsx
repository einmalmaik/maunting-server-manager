import { useTranslation } from 'react-i18next'
import { Camera, UsersRound } from 'lucide-react'
import { Avatar, Dialog, DialogContent } from '@/Singra/UI'
import { StatusDot } from '@/components/social/StatusIndicator'
import type { ChatGroupItem } from '@/api/social'
import type { ChatContact } from '@/pages/Messenger'

interface SendPhotoDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  groups: ChatGroupItem[]
  contacts: ChatContact[]
  onPickGroup: (group: ChatGroupItem) => void
  onPickContact: (contact: ChatContact) => void
}

export function SendPhotoDialog({
  open,
  onOpenChange,
  groups,
  contacts,
  onPickGroup,
  onPickContact,
}: SendPhotoDialogProps) {
  const { t } = useTranslation()

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md max-h-[80vh] flex flex-col p-4">
        <div className="flex items-center justify-between pb-3 border-b border-outline-variant/20">
          <div className="flex items-center gap-2">
            <Camera className="w-4 h-4 text-primary" />
            <span className="font-headline text-body-sm font-bold text-primary">{t('messenger.sendPhotoTo')}</span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto space-y-1 py-2">
          <div className="text-label-sm font-semibold text-on-surface-variant/70 uppercase tracking-wider px-2 py-1">
            {t('messenger.pickChat')}
          </div>
          {groups.map((g) => (
            <button
              key={`photo-g-${g.id}`}
              type="button"
              onClick={() => onPickGroup(g)}
              className="w-full flex items-center gap-2.5 p-2.5 rounded-xl hover:bg-surface-container-high transition-colors text-left"
            >
              <div className="w-8 h-8 rounded-full bg-primary/10 text-primary flex items-center justify-center text-xs font-bold shrink-0">
                {g.avatar_url ? (
                  <img src={g.avatar_url} alt="" className="w-full h-full rounded-full object-cover" />
                ) : (
                  <UsersRound className="w-4 h-4" />
                )}
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-xs font-semibold text-primary truncate">{g.name}</div>
                <div className="text-label-sm text-on-surface-variant/70">Gruppe ({g.member_count} Mitglieder)</div>
              </div>
            </button>
          ))}

          {contacts.map((c) => (
            <button
              key={c.listKey}
              type="button"
              onClick={() => onPickContact(c)}
              className="w-full flex items-center gap-2.5 p-2.5 rounded-xl hover:bg-surface-container-high transition-colors text-left"
            >
              <div className="relative shrink-0">
                <Avatar src={c.avatarUrl} name={c.username} size="sm" />
                <StatusDot status={c.status} size="sm" className="absolute bottom-0 right-0" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-xs font-semibold text-primary truncate">{c.username}</div>
                <div className="text-label-sm text-on-surface-variant/70">{c.teamName || (c.isFriend ? 'Freund' : 'Kontakt')}</div>
              </div>
            </button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  )
}
