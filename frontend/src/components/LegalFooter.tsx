import { Link } from 'react-router-dom'
import { DisBadge } from './DisBadge'
import { usePrivacyNoticeVisible } from './ui/PrivacyNoticeVisibility'

interface LegalFooterProps {
  version?: string
  className?: string
  imprintUrl?: string
}

export function LegalFooter({ version, className = '', imprintUrl }: LegalFooterProps) {
  const privacyNoticeVisible = usePrivacyNoticeVisible()

  return (
    <footer className={`flex flex-wrap items-center justify-center gap-x-3 gap-y-2 border-t border-outline-variant/20 pt-4 text-center font-mono-sm text-mono-sm text-on-surface-variant/70 ${className}`}>
      <div className="flex flex-wrap items-center justify-center gap-x-1">
        <span>Maunting Server Manager{version ? ` ${version}` : ''}</span>
        <span aria-hidden="true"> · </span>
        <Link to="/privacy" className="text-on-surface-variant underline-offset-4 transition-colors hover:text-primary hover:underline">
          Datenschutz
        </Link>
        {imprintUrl && (
          <>
            <span aria-hidden="true"> · </span>
            <a
              href={imprintUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-on-surface-variant underline-offset-4 transition-colors hover:text-primary hover:underline"
            >
              Impressum
            </a>
          </>
        )}
      </div>
      {!privacyNoticeVisible && <DisBadge />}
    </footer>
  )
}
