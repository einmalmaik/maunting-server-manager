import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/Singra/UI'
import { ErrorBox } from './shared'

/** Formular im Dialog; „Weiter" öffnet danach die SQL-Vorschau. */
export function FormDialog({ open, title, description, error, onClose, onSubmit, submitLabel, wide, children, testId }: {
  open: boolean
  title: string
  description?: string
  error?: string | null
  onClose: () => void
  onSubmit: () => void
  submitLabel?: string
  wide?: boolean
  children: ReactNode
  testId?: string
}) {
  const { t } = useTranslation()
  return (
    <Dialog open={open} onOpenChange={(value) => !value && onClose()}>
      <DialogContent className={wide ? 'max-w-3xl' : 'max-w-xl'} data-testid={testId}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <form
          className="contents"
          onSubmit={(event) => {
            event.preventDefault()
            onSubmit()
          }}
        >
          <div className="max-h-[65vh] space-y-4 overflow-y-auto p-6">
            {children}
            <ErrorBox message={error ?? null} />
          </div>
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={onClose}>{t('common.cancel')}</Button>
            <Button type="submit" data-testid={testId ? `${testId}-submit` : undefined}>{submitLabel || t('postgresStudio.preview.show')}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
