import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle } from 'lucide-react'
import { useConfirmStore } from '@/stores/confirmStore'
import { Button } from './Button'

/** Globaler Confirm-Dialog. Genau einmal in der App montieren (siehe App.tsx).
 *
 * Styling spiegelt das vorhandene Modal-Pattern (msm-card auf dunklem Overlay).
 * Bewusst keine eigene UI-Library — KISS, gleiche Optik wie die anderen
 * Dialoge im Panel.
 */
export function ConfirmDialog() {
  const { t } = useTranslation()
  const pending = useConfirmStore((s) => s.pending)
  const resolve = useConfirmStore((s) => s.resolve)

  // Escape zum Abbrechen — Standard-Verhalten fuer Modals. Enter triggert den
  // Confirm-Button (er hat autofocus), das deckt die OK-via-Tastatur ab.
  useEffect(() => {
    if (!pending) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        resolve(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [pending, resolve])

  if (!pending) return null

  const isDanger = !!pending.danger
  const confirmText = pending.confirmText ?? t('common.confirm')
  const cancelText = pending.cancelText ?? t('common.cancel')

  return (
    <div
      className="msm-modal-overlay"
      onClick={() => resolve(false)}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="msm-card w-full max-w-md p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-3">
          {isDanger && (
            <AlertTriangle className="w-6 h-6 text-status-destructive shrink-0 mt-0.5" />
          )}
          <div className="flex-1">
            {pending.title && (
              <h2 className="font-headline text-headline-md text-primary mb-2">
                {pending.title}
              </h2>
            )}
            <p className="font-body-md text-sm text-on-surface">
              {pending.message}
            </p>
          </div>
        </div>

        <div className="flex justify-end gap-2 mt-6">
          <Button
            type="button"
            onClick={() => resolve(false)}
            variant="ghost"
          >
            {cancelText}
          </Button>
          <Button
            type="button"
            autoFocus
            onClick={() => resolve(true)}
            variant={isDanger ? 'destructive' : 'primary'}
          >
            {confirmText}
          </Button>
        </div>
      </div>
    </div>
  )
}
