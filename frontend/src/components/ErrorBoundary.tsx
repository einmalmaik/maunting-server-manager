import { Component, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle } from 'lucide-react'

/**
 * Auffangschale für Renderfehler. Ohne sie hängt React 18 bei einem
 * unbehandelten Fehler den kompletten Root aus — Sidebar, Topbar und Toasts
 * inklusive, sichtbar als völlig weiße Seite. Häufigster Auslöser: nach einem
 * Panel-Update fehlt ein alter Vite-Chunk, der dynamische Import einer
 * lazy-Route schlägt fehl und wirft beim Rendern.
 *
 * Die Meldung bleibt bewusst allgemein: `error.message` kann interne Pfade
 * tragen und gehört nicht auf den Bildschirm.
 */
function ErrorCard() {
  const { t } = useTranslation()
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="msm-card w-full max-w-md p-8 text-center">
        <AlertTriangle className="mx-auto mb-4 h-10 w-10 text-status-error" />
        <h1 className="font-headline text-body-lg text-on-surface mb-2">{t('errorBoundary.title')}</h1>
        <p className="font-body-md mb-6 text-sm text-on-surface-variant">{t('errorBoundary.hint')}</p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="msm-btn-primary min-h-11 px-4 py-2"
        >
          {t('errorBoundary.reload')}
        </button>
      </div>
    </div>
  )
}

interface ErrorBoundaryState {
  hasError: boolean
}

export class ErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true }
  }

  render() {
    return this.state.hasError ? <ErrorCard /> : this.props.children
  }
}
