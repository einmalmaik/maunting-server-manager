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
          className={`pointer-events-auto flex items-start gap-3 p-4 rounded-lg shadow-lg border font-body-md text-sm ${
            t.type === 'error'
              ? 'bg-status-destructive/10 border-status-destructive/30 text-status-destructive'
              : 'bg-status-success/10 border-status-success/30 text-status-success'
          }`}
        >
          {t.type === 'error' ? (
            <AlertCircle className="w-5 h-5 text-status-destructive shrink-0 mt-0.5" />
          ) : (
            <CheckCircle className="w-5 h-5 text-status-success shrink-0 mt-0.5" />
          )}
          <p className="flex-1">{t.message}</p>
          <button
            onClick={() => removeToast(t.id)}
            className="opacity-60 hover:opacity-100 transition-opacity shrink-0"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      ))}
    </div>
  )
}
