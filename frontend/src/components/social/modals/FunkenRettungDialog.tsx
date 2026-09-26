import { useTranslation } from 'react-i18next'
import { Sparkles } from 'lucide-react'
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/Singra/UI'

interface FunkenRettungDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  contactName: string
  /** Der erloschene Stand, der zurückkommt. */
  anzahl: number
  /** Schickt die Wiederherstellung und schliesst den Dialog. */
  onConfirm: () => void | Promise<void>
}

/** Die Rückfrage vor einer Wiederherstellung — sie geht nur alle 3 Monate. */
export function FunkenRettungDialog({ open, onOpenChange, contactName, anzahl, onConfirm }: FunkenRettungDialogProps) {
  const { t } = useTranslation()

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-status-warning">
            <Sparkles className="w-5 h-5" />
            <span>{t('messenger.streak.restoreQuestion')}</span>
          </DialogTitle>
          <DialogDescription>
            {t('messenger.streak.restoreDesc', { count: anzahl, name: contactName })}
          </DialogDescription>
        </DialogHeader>

        <DialogFooter>
          <Button variant="secondary" size="sm" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button variant="primary" size="sm" onClick={() => void onConfirm()}>
            {t('messenger.streak.restoreConfirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
