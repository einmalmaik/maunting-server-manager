import { useTranslation } from 'react-i18next'
import { ShieldCheck } from 'lucide-react'
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/Singra/UI'

export interface SicherheitsnummerGeraet {
  id: string
  label: string
  number: string
}

interface SafetyNumberDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  contactName: string
  devices: SicherheitsnummerGeraet[]
  loading: boolean
}

export function SafetyNumberDialog({ open, onOpenChange, contactName, devices, loading }: SafetyNumberDialogProps) {
  const { t } = useTranslation()

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-secondary" />
            <span>{t('messenger.safetyNumberModalTitle')}</span>
          </DialogTitle>
          <DialogDescription>
            {t('messenger.safetyNumberModalDesc')}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3 px-6 py-4">
          {loading && (
            <p className="text-xs text-on-surface-variant">{t('common.loading')}</p>
          )}
          {!loading && devices.length === 0 && (
            <p className="text-xs text-on-surface-variant">{t('messenger.noDeviceOnline', { name: contactName })}</p>
          )}
          {!loading && devices.map((d) => (
            <div key={d.id} className="rounded-lg border border-outline-variant/30 bg-surface-container-high/40 p-3 space-y-1">
              <div className="flex items-center justify-between text-xs text-on-surface font-medium">
                <span>{d.label}</span>
                <span className="font-mono text-on-surface-variant">{d.id.slice(0, 10)}</span>
              </div>
              <div className="font-mono text-sm tracking-wider text-primary font-bold bg-surface-container-lowest/60 rounded px-2 py-1.5 text-center select-all">
                {d.number || '—'}
              </div>
            </div>
          ))}
        </div>

        <DialogFooter>
          <Button variant="secondary" size="sm" onClick={() => onOpenChange(false)}>
            {t('common.close')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
