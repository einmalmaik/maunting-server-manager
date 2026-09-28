import { Logo } from '@/components/Logo'
import { Spinner, type SpinnerSize } from '@/components/ui/Spinner'

interface LoaderProps {
  label?: string
  size?: 'sm' | 'md' | 'lg'
  fullScreen?: boolean
}

// Der Ring selbst steht in `Spinner` — hier nur die Zuordnung der drei
// Stufen, die der Lader kennt.
const RING: Record<NonNullable<LoaderProps['size']>, SpinnerSize> = {
  sm: 'sm',
  md: 'lg',
  lg: 'xl',
}

export function Loader({ label, size = 'md', fullScreen = false }: LoaderProps) {
  const content = (
    <div className="flex flex-col items-center justify-center gap-4 text-on-surface-variant" role="status" aria-live="polite">
      {fullScreen && <Logo size="md" />}
      <Spinner size={RING[size]} className="text-primary" />
      {label && <span className="font-body-md text-sm">{label}</span>}
    </div>
  )

  if (fullScreen) {
    return <div className="min-h-screen bg-background flex items-center justify-center">{content}</div>
  }

  return content
}
