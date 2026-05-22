import { useToastStore } from '@/stores/toastStore'
import { X, CheckCircle, AlertCircle } from 'lucide-react'

export function ToastContainer() {
  const { toasts, removeToast } = useToastStore()

  if (toasts.length === 0) return null

  return (
    <div className="fixed top-4 right-4 z-[9999] flex flex-col gap-2 max-w-sm w-full pointer-events-none">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`pointer-events-auto flex items-start gap-3 p-4 rounded-lg shadow-lg border ${
            t.type === 'error'
              ? 'bg-error-container/95 border-status-error/40 text-on-error-container'
              : 'bg-status-success/10 border-status-success/30 text-status-success'
          }`}
        >
          {t.type === 'error' ? (
            <AlertCircle className="w-5 h-5 text-status-error shrink-0 mt-0.5" />
          ) : (
            <CheckCircle className="w-5 h-5 text-status-success shrink-0 mt-0.5" />
          )}
          <p className="text-sm flex-1">{t.message}</p>
          <button
            onClick={() => removeToast(t.id)}
            className="text-current opacity-60 hover:opacity-100 transition-opacity shrink-0"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      ))}
    </div>
  )
}