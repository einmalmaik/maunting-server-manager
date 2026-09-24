import { useTranslation } from 'react-i18next'
import { Ban } from 'lucide-react'
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/Singra/UI'

interface BlockConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  contactName: string
  /** Ist der Kontakt schon blockiert, fragt der Dialog nach dem Aufheben. */
  blocked: boolean
  /** Blockiert oder hebt auf, je nach `blocked`, und schliesst den Dialog. */
  onConfirm: () => void | Promise<void>
}

export function BlockConfirmDialog({ open, onOpenChange, contactName, blocked, onConfirm }: BlockConfirmDialogProps) {
  const { t } = useTranslation()

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className={`flex items-center gap-2 ${blocked ? 'text-primary' : 'text-status-destructive'}`}>
            <Ban className="w-5 h-5" />
            <span>{blocked ? t('messenger.unblockTitle') : t('messenger.blockTitle')}</span>
          </DialogTitle>
          <DialogDescription>
            {blocked
              ? t('messenger.unblockMessage', { name: contactName })
              : t('messenger.blockMessage', { name: contactName })}
          </DialogDescription>
        </DialogHeader>

        <DialogFooter>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onOpenChange(false)}
          >
            Abbrechen
          </Button>
          <Button
            variant={blocked ? 'primary' : 'destructive'}
            size="sm"
            onClick={() => void onConfirm()}
          >
            {blocked ? t('messenger.unblock') : t('messenger.block')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
