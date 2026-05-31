import { Link } from 'react-router-dom'

interface LegalFooterProps {
  version?: string
  className?: string
}

export function LegalFooter({ version, className = '' }: LegalFooterProps) {
  return (
    <footer className={`text-center font-mono-sm text-mono-sm text-on-surface-variant/70 ${className}`}>
      <span>Maunting Server Manager{version ? ` ${version}` : ''}</span>
      <span aria-hidden="true"> · </span>
      <Link to="/privacy" className="text-on-surface-variant underline-offset-4 transition-colors hover:text-primary hover:underline">
        Datenschutz
      </Link>
    </footer>
  )
}
