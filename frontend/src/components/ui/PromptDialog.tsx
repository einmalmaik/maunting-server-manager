import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle } from 'lucide-react'
import { usePromptStore } from '@/stores/promptStore'
import { Button } from './Button'

/** Globaler Prompt-Dialog. Genau einmal in der App montieren (siehe App.tsx).
 *
 * Styling spiegelt das vorhandene Modal-Pattern (msm-card auf dunklem Overlay),
 * analog zu ConfirmDialog — bewusst keine eigene UI-Library (KISS).
 *
 * Tastatur: Enter bestaetigt (sofern freigegeben), Escape bricht ab. Das
 * Eingabefeld hat autofocus, damit sofort getippt werden kann.
 */
export function PromptDialog() {
  const { t } = useTranslation()
  const pending = usePromptStore((s) => s.pending)
  const resolve = usePromptStore((s) => s.resolve)
  const [value, setValue] = useState('')

  // Eingabefeld beim Oeffnen vorbelegen.
  useEffect(() => {
    if (pending) {
      setValue(pending.defaultValue ?? '')
    }
  }, [pending])

  // Escape bricht ab, Enter bestaetigt (wenn freigegeben und nicht leer).
  useEffect(() => {
    if (!pending) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        resolve(null)
      } else if (e.key === 'Enter') {
        const trimmed = value.trim()
        const canConfirm = pending.expectedValue
          ? value === pending.expectedValue
          : trimmed.length > 0
        if (canConfirm) {
          e.preventDefault()
          resolve(pending.expectedValue ? value : trimmed)
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [pending, resolve, value])

  if (!pending) return null

  const isDanger = !!pending.danger
  const confirmText = pending.confirmText ?? t('common.confirm')
  const cancelText = pending.cancelText ?? t('common.cancel')
  const canConfirm = pending.expectedValue
    ? value === pending.expectedValue
    : value.trim().length > 0

  return (
    <div
      className="msm-modal-overlay"
      onClick={() => resolve(null)}
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
            <p className="font-body-md text-sm text-on-surface mb-4">
              {pending.message}
            </p>
            <input
              className="msm-input"
              placeholder={pending.placeholder ?? ''}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              autoFocus
            />
            {pending.expectedValue && (
              <p className="text-xs text-on-surface-variant mt-2">
                Geben Sie „{pending.expectedValue}“ ein zum Bestätigen.
              </p>
            )}
          </div>
        </div>

        <div className="flex justify-end gap-2 mt-6">
          <Button
            type="button"
            onClick={() => resolve(null)}
            variant="ghost"
          >
            {cancelText}
          </Button>
          <Button
            type="button"
            onClick={() => resolve(pending.expectedValue ? value : (value.trim() || null))}
            disabled={!canConfirm}
            variant={isDanger ? 'destructive' : 'primary'}
          >
            {confirmText}
          </Button>
        </div>
      </div>
    </div>
  )
}
