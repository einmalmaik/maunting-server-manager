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

interface ChatEntfernenDialogProps {
  open: boolean
  /** Der Name des Gegenübers, damit die Meldung sagt, was verschwindet. */
  name: string
  onCancel: () => void
  onConfirm: () => void
}

/**
 * Ein Gespräch aus der örtlichen Liste nehmen.
 *
 * Die Gegenprobe zum Öffnen: der Verlauf liegt auf diesem Gerät, und was hier
 * verschwindet, kommt nicht zurück. Deshalb fragt der Dialog, statt den Klick
 * aus dem Menü sofort auszuführen.
 */
export function ChatEntfernenDialog({ open, name, onCancel, onConfirm }: ChatEntfernenDialogProps) {
  const { t } = useTranslation()

  return (
    <Dialog open={open} onOpenChange={(offen) => !offen && onCancel()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="text-error flex items-center gap-2">
            <Trash2 className="w-5 h-5 text-error" />
            <span>{t('messenger.removeChatTitle')}</span>
          </DialogTitle>
          <DialogDescription>
            {t('messenger.removeChatMessage', { name })}
          </DialogDescription>
        </DialogHeader>

        <DialogFooter>
          <Button variant="secondary" size="sm" onClick={onCancel}>
            {t('common.cancel')}
          </Button>
          <Button variant="destructive" size="sm" onClick={onConfirm} className="gap-1.5">
            <Trash2 className="w-4 h-4" />
            <span>{t('messenger.removeChatConfirm')}</span>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
