/**
 * Meldungen (`toast(...)`) im Browser: als Leiste über der Seite statt als
 * schwebender Stapel. Ein schwebender Toast läge unter der Webview und wäre
 * unsichtbar; die Leiste schiebt die Seite stattdessen ein Stück nach unten,
 * wie die Infoleisten anderer Browser. Derselbe Store wie überall
 * (`toastStore.ts`), nur anders gezeichnet.
 */
import { AlertCircle, AlertTriangle, CheckCircle, Info, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Kurzinfo } from '@/Singra/UI'
import { useToastStore, type ToastTyp } from '@/stores/toastStore'

const STIL: Record<ToastTyp, { farbe: string; symbol: typeof Info }> = {
  error: { farbe: 'border-status-destructive/40 text-status-destructive', symbol: AlertCircle },
  warning: { farbe: 'border-status-warning/40 text-status-warning', symbol: AlertTriangle },
  info: { farbe: 'border-primary/40 text-primary', symbol: Info },
  success: { farbe: 'border-status-success/40 text-status-success', symbol: CheckCircle },
}

export function Meldungsleiste() {
  const { t } = useTranslation()
  const toasts = useToastStore((s) => s.toasts)
  const entfernen = useToastStore((s) => s.removeToast)
  if (toasts.length === 0) return null

  return (
    <div className="flex shrink-0 flex-col border-b border-outline-variant bg-surface-container-high">
      {toasts.map((toast) => {
        const { farbe, symbol: Symbol } = STIL[toast.type]
        return (
          <div
            key={toast.id}
            role={toast.type === 'error' ? 'alert' : 'status'}
            className={`flex items-center gap-3 border-l-2 px-3 py-1.5 text-body-sm ${farbe}`}
          >
            <Symbol className="h-4 w-4 shrink-0" aria-hidden="true" />
            <p className="max-h-16 min-w-0 flex-1 overflow-y-auto whitespace-pre-wrap break-words text-on-surface">{toast.message}</p>
            {toast.aktion && (
              <button
                type="button"
                onClick={() => {
                  toast.aktion?.ausfuehren()
                  entfernen(toast.id)
                }}
                className="shrink-0 rounded px-2 py-0.5 text-label-md text-on-surface underline-offset-2 hover:underline"
              >
                {toast.aktion.label}
              </button>
            )}
            <Kurzinfo text={t('common.close')} lage="oben" seite="ende">
              <button
                type="button"
                onClick={() => entfernen(toast.id)}
                aria-label={t('common.close')}
                className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-on-surface-variant hover:bg-surface-container-highest"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </Kurzinfo>
          </div>
        )
      })}
    </div>
  )
}
