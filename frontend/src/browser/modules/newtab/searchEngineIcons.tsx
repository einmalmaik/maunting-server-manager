import type { ReactNode } from 'react'

interface IconProps {
  className?: string
}

export function GoogleIcon({ className = 'w-4 h-4' }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z" />
      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z" />
    </svg>
  )
}

export function EcosiaIcon({ className = 'w-4 h-4' }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d="M12 2C7.58 2 4 5.58 4 10c0 2.38 1.04 4.52 2.7 6l-.7 6 6-2 6 2-.7-6C18.96 14.52 20 12.38 20 10c0-4.42-3.58-8-8-8zm-1 14.92l-3.23 1.08.38-3.25A7.95 7.95 0 0 1 6 10c0-3.31 2.69-6 6-6s6 2.69 6 6c0 1.9-.66 3.64-1.77 5.02l.39 3.25L13.4 17.2l-.7-.23-.7.23-.01-.28zM12 6a4 4 0 0 0-4 4c0 1.5.8 2.8 2 3.5V15h4v-1.5c1.2-.7 2-2 2-3.5a4 4 0 0 0-4-4z" />
    </svg>
  )
}

export function DuckDuckGoIcon({ className = 'w-4 h-4' }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1.2 4.2c1.7 0 3.1 1.2 3.5 2.8.2.8.1 1.6-.3 2.3 1.2.3 2.1 1.2 2.1 2.4 0 1.6-1.6 2.8-3.5 2.8-1.5 0-2.8-.8-3.3-2-.7.2-1.4.2-2.1 0-.9 1.3-2.5 1.7-4 1-.2-.1-.4-.3-.5-.5 1.5-.3 2.6-1.5 2.6-3 0-1.2-.7-2.3-1.8-2.7.5-.9 1.4-1.5 2.5-1.5.8 0 1.5.3 2 .8.8-1.4 2.4-2.4 4.8-2.4zm-1.8 4.8a.9.9 0 1 0 0-1.8.9.9 0 0 0 0 1.8z" />
    </svg>
  )
}

export function BraveIcon({ className = 'w-4 h-4' }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d="M12 2L4 5.5v5.8c0 5.4 3.4 10.5 8 11.7 4.6-1.2 8-6.3 8-11.7V5.5L12 2zm0 3.1l5.5 2.4v4c0 3.8-2.3 7.4-5.5 8.4-3.2-1-5.5-4.6-5.5-8.4v-4L12 5.1zm-1.8 3.9l-2.2 4.5h2.5l-1.3 4 4.6-5.5h-2.5l2.2-3H10.2z" />
    </svg>
  )
}

export function BingIcon({ className = 'w-4 h-4' }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d="M4.5 2.5v19l6.5-3.7 5.5 3.7V9.7l-4.5 2.6v-5.2l-7.5-4.6zm3 4.2l3 1.8v3.4l-3-1.7V6.7z" />
    </svg>
  )
}

export function SearxngIcon({ className = 'w-4 h-4' }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d="M12 2L3 6v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V6l-9-4zm-1 6a4 4 0 1 1 0 8 4 4 0 0 1 0-8zm0 2a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm5.5 5.5l-1.4 1.4-2.1-2.1 1.4-1.4 2.1 2.1z" />
    </svg>
  )
}

/**
 * Gibt das passende Vektor-Icon für eine Suchmaschinen-ID zurück.
 */
export function getSearchEngineIcon(engineId: string, className = 'w-4 h-4'): ReactNode {
  switch (engineId) {
    case 'google':
      return <GoogleIcon className={className} />
    case 'ecosia':
      return <EcosiaIcon className={className} />
    case 'duckduckgo':
      return <DuckDuckGoIcon className={className} />
    case 'brave':
      return <BraveIcon className={className} />
    case 'bing':
      return <BingIcon className={className} />
    case 'searxng':
      return <SearxngIcon className={className} />
    default:
      return <GoogleIcon className={className} />
  }
}
