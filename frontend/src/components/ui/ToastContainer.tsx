import { useToastStore } from '@/stores/toastStore'
import { X, CheckCircle, AlertCircle, Copy, Check } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

export function ToastContainer() {
  const { t } = useTranslation()
  const { toasts, removeToast } = useToastStore()
  const [copiedId, setCopiedId] = useState<number | null>(null)

  if (toasts.length === 0) return null

  const copyToast = async (id: number, message: string) => {
    try {
      await navigator.clipboard.writeText(message)
      setCopiedId(id)
      window.setTimeout(() => setCopiedId((current) => (current === id ? null : current)), 1500)
    } catch {
      // Clipboard is convenience only; the error text remains visible.
    }
  }

  return (
    <div className="fixed top-4 right-4 z-[9999] flex max-h-[calc(100vh-2rem)] w-[min(calc(100vw-2rem),42rem)] flex-col gap-2 overflow-y-auto pointer-events-none">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`pointer-events-auto flex items-start gap-3 p-4 rounded-lg shadow-lg border font-body-md text-sm backdrop-blur ${
            toast.type === 'error'
              ? 'bg-status-destructive/10 border-status-destructive/30 text-status-destructive'
              : 'bg-status-success/10 border-status-success/30 text-status-success'
          }`}
          role={toast.type === 'error' ? 'alert' : 'status'}
        >
          {toast.type === 'error' ? (
            <AlertCircle className="w-5 h-5 text-status-destructive shrink-0 mt-0.5" />
          ) : (
            <CheckCircle className="w-5 h-5 text-status-success shrink-0 mt-0.5" />
          )}
          <p className="flex-1 max-h-44 overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">
            {toast.message}
          </p>
          {toast.type === 'error' && (
            <button
              type="button"
              onClick={() => void copyToast(toast.id, toast.message)}
              className="opacity-70 hover:opacity-100 transition-opacity shrink-0"
              title={copiedId === toast.id ? t('common.copied') : t('common.copy')}
              aria-label={copiedId === toast.id ? t('common.copied') : t('common.copy')}
            >
              {copiedId === toast.id ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
            </button>
          )}
          <button
            type="button"
            onClick={() => removeToast(toast.id)}
            className="opacity-60 hover:opacity-100 transition-opacity shrink-0"
            title={t('common.close')}
            aria-label={t('common.close')}
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      ))}
    </div>
  )
}
