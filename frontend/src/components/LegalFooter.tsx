import { Link } from 'react-router-dom'

interface LegalFooterProps {
  version?: string
  className?: string
  imprintUrl?: string
}

export function LegalFooter({ version, className = '', imprintUrl }: LegalFooterProps) {
  return (
    <footer className={`text-center font-mono-sm text-mono-sm text-on-surface-variant/70 ${className}`}>
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
    </footer>
  )
}
