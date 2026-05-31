import { Logo } from '@/components/Logo'

interface LoaderProps {
  label?: string
  size?: 'sm' | 'md' | 'lg'
  fullScreen?: boolean
}

const spinnerSizes = {
  sm: 'h-4 w-4 border',
  md: 'h-8 w-8 border-2',
  lg: 'h-10 w-10 border-2',
} as const

export function Loader({ label, size = 'md', fullScreen = false }: LoaderProps) {
  const content = (
    <div className="flex flex-col items-center justify-center gap-4 text-on-surface-variant" role="status" aria-live="polite">
      {fullScreen && <Logo size="md" />}
      <span className={`${spinnerSizes[size]} rounded-full border-primary border-t-transparent animate-spin`} aria-hidden="true" />
      {label && <span className="font-body-md text-sm">{label}</span>}
    </div>
  )

  if (fullScreen) {
    return <div className="min-h-screen bg-background flex items-center justify-center">{content}</div>
  }

  return content
}
