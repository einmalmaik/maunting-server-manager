import { AlertCircle } from 'lucide-react'

interface ErrorMessageProps {
  message: string
  className?: string
}

export function ErrorMessage({ message, className = '' }: ErrorMessageProps) {
  if (!message) return null

  return (
    <div className={`msm-alert-error flex items-start gap-2 ${className}`} role="alert">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <span className="break-words">{message}</span>
    </div>
  )
}
