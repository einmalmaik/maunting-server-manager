import { useVersion } from '@/hooks/useVersion'
import { LegalFooter } from './LegalFooter'

export function VersionFooter() {
  const version = useVersion()

  return <LegalFooter version={version} className="mt-6" />
}
