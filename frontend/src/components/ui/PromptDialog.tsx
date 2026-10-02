import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { AlertTriangle } from 'lucide-react'
import { usePromptStore } from '@/stores/promptStore'
import { useZurueckSchliesst } from '@/Singra/UI/useZurueckSchliesst'
import { Button } from './Button'
import { Input } from './Input'

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
  useZurueckSchliesst(!!pending, () => resolve(null))
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

  // Wer den Dialog geöffnet hat, steht nur während des Renderns fest: das
  // `autoFocus` des Eingabefeldes zieht den Fokus schon vor jedem Effekt in den
  // Dialog. Deshalb hier merken und beim Schließen zurückgeben — sonst fällt
  // die Tastaturbedienung auf `document.body` und beginnt von vorn.
  const previousFocus = useRef<HTMLElement | null>(null)
  if (pending && !previousFocus.current) {
    previousFocus.current = document.activeElement as HTMLElement | null
  }
  const titelId = useId()
  const textId = useId()
  useEffect(() => {
    if (pending) return
    const target = previousFocus.current
    previousFocus.current = null
    if (target?.isConnected) target.focus()
  }, [pending])

  if (!pending) return null

  const isDanger = !!pending.danger
  const confirmText = pending.confirmText ?? t('common.confirm')
  const cancelText = pending.cancelText ?? t('common.cancel')
  const canConfirm = pending.expectedValue
    ? value === pending.expectedValue
    : value.trim().length > 0

  // Per Portal an body, nach allem, was schon offen ist: im App-Baum lag die
  // Rückfrage hinter dem Vollbild-Editor, der selbst per Portal an body hängt.
  return createPortal(
    <div
      className="msm-modal-overlay"
      onClick={() => resolve(null)}
      role="dialog"
      aria-modal="true"
      aria-labelledby={pending.title ? titelId : textId}
      aria-describedby={pending.title ? textId : undefined}
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
              <h2 id={titelId} className="font-headline text-headline-md text-primary mb-2">
                {pending.title}
              </h2>
            )}
            <p id={textId} className="font-body-md text-sm text-on-surface mb-4">
              {pending.message}
            </p>
            <Input
              aria-labelledby={textId}
              placeholder={pending.placeholder ?? ''}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              autoFocus
            />
            {pending.expectedValue && (
              <p className="text-xs text-on-surface-variant mt-2">
                {t('common.eintippenZumBestaetigen', { wert: pending.expectedValue })}
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
    </div>,
    document.body,
  )
}
