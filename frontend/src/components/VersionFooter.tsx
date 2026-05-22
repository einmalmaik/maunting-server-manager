import { useVersion } from '@/hooks/useVersion'

export function VersionFooter() {
  const version = useVersion()

  return (
    <p className="text-center font-mono-sm text-mono-sm text-on-surface-variant mt-6 opacity-60">
      Maunting Server Manager {version}
    </p>
  )
}
