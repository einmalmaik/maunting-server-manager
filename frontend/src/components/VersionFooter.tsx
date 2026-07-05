import { useVersion } from '@/hooks/useVersion'
import { usePublicLegalSettings } from '@/hooks/usePublicLegalSettings'
import { LegalFooter } from './LegalFooter'

export function VersionFooter({ className = 'mt-6' }: { className?: string }) {
  const version = useVersion()
  const legal = usePublicLegalSettings()
  const imprintUrl = legal.imprint_enabled ? legal.imprint_url : ''

  return <LegalFooter version={version} className={className} imprintUrl={imprintUrl} />
}
