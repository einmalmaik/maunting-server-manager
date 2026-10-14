/**
 * Meldungen (`toast(...)`) im Browser: als Leiste über der Seite statt als
 * schwebender Stapel. Ein schwebender Toast läge unter der Webview und wäre
 * unsichtbar; die Leiste schiebt die Seite stattdessen ein Stück nach unten,
 * wie die Infoleisten anderer Browser. Derselbe Store wie überall
 * (`toastStore.ts`), nur anders gezeichnet.
 *
 * Gezeichnet wird nur mit Bausteinen der Design-DNA (`Button` aus `Singra/UI`,
 * Farb- und Schrift-Token): Typ-Plakette und Tönung sprechen dieselbe
 * Banner-Sprache wie `AiBetaBanner`, Fehlermaschinentext dieselbe Schrift wie
 * im `ToastContainer`.
 */
import { AlertCircle, AlertTriangle, CheckCircle, Info, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button, Kurzinfo } from '@/Singra/UI'
import { useToastStore, type ToastTyp } from '@/stores/toastStore'

const STIL: Record<ToastTyp, { leiste: string; plakette: string; symbol: typeof Info }> = {
  error: {
    leiste: 'border-status-destructive/25 bg-status-destructive/10',
    plakette: 'border-status-destructive/30 bg-status-destructive/20 text-status-destructive',
    symbol: AlertCircle,
  },
  warning: {
    leiste: 'border-status-warning/25 bg-status-warning/10',
    plakette: 'border-status-warning/30 bg-status-warning/20 text-status-warning',
    symbol: AlertTriangle,
  },
  info: {
    leiste: 'border-primary/25 bg-primary/10',
    plakette: 'border-primary/30 bg-primary/20 text-primary',
    symbol: Info,
  },
  success: {
    leiste: 'border-status-success/25 bg-status-success/10',
    plakette: 'border-status-success/30 bg-status-success/20 text-status-success',
    symbol: CheckCircle,
  },
}

export function Meldungsleiste() {
  const { t } = useTranslation()
  const toasts = useToastStore((s) => s.toasts)
  const entfernen = useToastStore((s) => s.removeToast)
  if (toasts.length === 0) return null

  return (
    <div className="flex shrink-0 flex-col backdrop-blur-sm">
      {toasts.map((toast) => {
        const { leiste, plakette, symbol: Symbol } = STIL[toast.type]
        return (
          <div
            key={toast.id}
            role={toast.type === 'error' ? 'alert' : 'status'}
            className={`flex items-center gap-2.5 border-b px-3 py-2 ${leiste}`}
          >
            <span className={`inline-flex shrink-0 items-center rounded-md border px-1.5 py-1 ${plakette}`}>
              <Symbol className="h-3.5 w-3.5" aria-hidden="true" />
            </span>
            <p
              className={`max-h-16 min-w-0 flex-1 overflow-y-auto whitespace-pre-wrap break-words text-on-surface ${
                // Wie im ToastContainer: Fehlermaschinentext in Mono, Fließtext sonst.
                toast.type === 'error' ? 'font-mono text-xs' : 'font-body-md text-body-sm'
              }`}
            >
              {toast.message}
            </p>
            {toast.aktion && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  toast.aktion?.ausfuehren()
                  entfernen(toast.id)
                }}
                className="shrink-0"
              >
                {toast.aktion.label}
              </Button>
            )}
            <Kurzinfo text={t('common.close')} lage="oben" seite="ende">
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => entfernen(toast.id)}
                aria-label={t('common.close')}
                className="shrink-0"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </Button>
            </Kurzinfo>
          </div>
        )
      })}
    </div>
  )
}
