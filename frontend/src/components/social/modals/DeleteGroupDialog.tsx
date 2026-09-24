import { useTranslation } from 'react-i18next'
import { Trash2 } from 'lucide-react'
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/Singra/UI'
import type { ChatGroupItem } from '@/api/social'

interface DeleteGroupDialogProps {
  /** Die Gruppe, die gelöscht werden soll. `null` hält den Dialog zu. */
  group: ChatGroupItem | null
  deleting: boolean
  onCancel: () => void
  onConfirm: () => void
}

export function DeleteGroupDialog({ group, deleting, onCancel, onConfirm }: DeleteGroupDialogProps) {
  const { t } = useTranslation()

  return (
    <Dialog open={Boolean(group)} onOpenChange={(open) => !open && onCancel()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="text-error flex items-center gap-2">
            <Trash2 className="w-5 h-5 text-error" />
            <span>{t('messenger.deleteGroupTitle')}</span>
          </DialogTitle>
          <DialogDescription>
            {t('messenger.deleteGroupMessage', { name: group?.name ?? '' })}
          </DialogDescription>
        </DialogHeader>

        <DialogFooter>
          <Button
            variant="secondary"
            size="sm"
            onClick={onCancel}
            disabled={deleting}
          >
            Abbrechen
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={onConfirm}
            disabled={deleting}
            className="gap-1.5"
          >
            <Trash2 className="w-4 h-4" />
            <span>{deleting ? t('messenger.deleting') : t('messenger.deleteForGood')}</span>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
