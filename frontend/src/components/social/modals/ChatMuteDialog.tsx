import { useTranslation } from 'react-i18next'
import { Bell, BellOff, Clock } from 'lucide-react'
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'

interface ChatMuteDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Das Postfach des offenen Chats. Ohne Postfach schliesst jede Wahl nur den Dialog. */
  mailboxId: string | null
  /** Wie der Chat im Satz heisst. `null` wird zu „diesen Chat". */
  chatName: string | null
}

export function ChatMuteDialog({ open, onOpenChange, mailboxId, chatName }: ChatMuteDialogProps) {
  const { t } = useTranslation()
  const muteChat = useMessengerNotificationStore((s) => s.muteChat)
  const unmuteChat = useMessengerNotificationStore((s) => s.unmuteChat)
  // `isMuted` räumt abgelaufene Fristen selbst weg. Als Selektor liefe dieses
  // Aufräumen bei jeder Store-Änderung mit, hier nur beim Zeichnen.
  const isMuted = useMessengerNotificationStore((s) => s.isMuted)
  const muted = mailboxId ? isMuted(mailboxId) : false

  // Minuten wie im Store: 0 heisst für immer.
  const stummschalten = (minuten: number, meldung: string) => {
    if (mailboxId) {
      muteChat(mailboxId, minuten)
      toast.success(meldung)
    }
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <BellOff className="w-5 h-5 text-primary" />
            <span>{t('messenger.muteNotifications')}</span>
          </DialogTitle>
          <DialogDescription>
            Wähle, wie lange Benachrichtigungen für {chatName ? `"${chatName}"` : 'diesen Chat'} stummgeschaltet werden sollen.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-2 px-6 py-4">
          <Button
            variant="secondary"
            className="w-full justify-start text-left text-xs py-2.5 h-auto"
            onClick={() => stummschalten(480, t('messenger.muted8h'))}
          >
            <Clock className="w-4 h-4 mr-2.5 text-on-surface-variant" />
            <div>
              <div className="font-semibold">{t('messenger.mute8h')}</div>
              <div className="text-label-sm text-on-surface-variant/70">{t('messenger.mute8hHint')}</div>
            </div>
          </Button>

          <Button
            variant="secondary"
            className="w-full justify-start text-left text-xs py-2.5 h-auto"
            onClick={() => stummschalten(10080, t('messenger.muted1w'))}
          >
            <Clock className="w-4 h-4 mr-2.5 text-on-surface-variant" />
            <div>
              <div className="font-semibold">{t('messenger.mute1w')}</div>
              <div className="text-label-sm text-on-surface-variant/70">{t('messenger.mute1wHint')}</div>
            </div>
          </Button>

          <Button
            variant="secondary"
            className="w-full justify-start text-left text-xs py-2.5 h-auto"
            onClick={() => stummschalten(0, t('messenger.mutedForever'))}
          >
            <BellOff className="w-4 h-4 mr-2.5 text-on-surface-variant" />
            <div>
              <div className="font-semibold">Immer</div>
              <div className="text-label-sm text-on-surface-variant/70">{t('messenger.muteForeverHint')}</div>
            </div>
          </Button>

          {mailboxId && muted && (
            <Button
              variant="ghost"
              className="w-full justify-start text-left text-xs py-2.5 h-auto text-primary hover:bg-primary/10 mt-1 border border-primary/20"
              onClick={() => {
                unmuteChat(mailboxId)
                toast.success(t('social.contacts.unmuted'))
                onOpenChange(false)
              }}
            >
              <Bell className="w-4 h-4 mr-2.5 text-primary" />
              <div>
                <div className="font-semibold">{t('messenger.unmute')}</div>
                <div className="text-label-sm text-on-surface-variant/70">{t('messenger.unmuteHint')}</div>
              </div>
            </Button>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onOpenChange(false)}
          >
            Abbrechen
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
