import { useVersion } from '@/hooks/useVersion'
import { LegalFooter } from './LegalFooter'

export function VersionFooter({ className = 'mt-6' }: { className?: string }) {
  const version = useVersion()

  return <LegalFooter version={version} className={className} />
}
