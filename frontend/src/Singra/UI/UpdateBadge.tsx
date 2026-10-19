import { forwardRef, useState, useEffect, type HTMLAttributes } from 'react'
import { Download, RefreshCw, CheckCircle2, AlertCircle } from 'lucide-react'

export type UpdatePhase = 'verfuegbar' | 'laedt' | 'bereit' | 'installiert_android' | 'fehler'

export interface UpdateBadgeProps extends HTMLAttributes<HTMLButtonElement> {
  /** Die neu entdeckte Versionsnummer (z. B. "5.5.2") */
  neueVersion?: string | null
  /** Aktuelle Phase des Update-Lebenszyklus */
  phase?: UpdatePhase
  /** Fortschritt in Prozent (0 bis 100) während der Ladephase */
  prozent?: number
  /** Fehlermeldung, falls die Übertragung oder Prüfung fehlschlug */
  fehlerText?: string | null
  /** Callback, wenn der Nutzer die Aktualisierung anstoßen oder abschließen möchte */
  onInstallieren?: () => void
  /** Ob das Badge kompakt (nur Symbol + Kurztext) oder vollflächig dargestellt wird */
  kompakt?: boolean
}

/**
 * Zentrales UpdateBadge der Maunting Design-DNA.
 *
 * Einheitlicher, barrierefreier Indikator für App-Aktualisierungen (Pontis Browser & MSS).
 * Zeigt den Status unaufdringlich an und führt erst bei bewusster Nutzerinteraktion
 * zur Installation oder zum Neustart. Keine nativen Dialoge oder Popups.
 */
export const UpdateBadge = forwardRef<HTMLButtonElement, UpdateBadgeProps>(
  (
    {
      className = '',
      neueVersion,
      phase = 'verfuegbar',
      prozent = 0,
      fehlerText,
      onInstallieren,
      kompakt = false,
      onClick,
      ...props
    },
    ref,
  ) => {
    const [animationAktiv, setAnimationAktiv] = useState(false)

    useEffect(() => {
      if (phase === 'bereit' || phase === 'verfuegbar') {
        setAnimationAktiv(true)
        const timer = setTimeout(() => setAnimationAktiv(false), 2400)
        return () => clearTimeout(timer)
      }
    }, [phase])

    // Styling abgestimmt auf Design-Tokens
    const statusFarben: Record<UpdatePhase, string> = {
      verfuegbar: 'border-primary/40 bg-primary/10 text-primary hover:bg-primary/20',
      laedt: 'border-status-warning/40 bg-status-warning/10 text-status-warning cursor-wait',
      bereit: 'border-status-success/40 bg-status-success/15 text-status-success hover:bg-status-success/25',
      installiert_android: 'border-status-success/40 bg-status-success/15 text-status-success',
      fehler: 'border-status-destructive/40 bg-status-destructive/15 text-status-destructive hover:bg-status-destructive/25',
    }

    const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => {
      if (onClick) onClick(e)
      if (phase !== 'laedt' && onInstallieren) {
        onInstallieren()
      }
    }

    const titelText = fehlerText
      ? fehlerText
      : phase === 'bereit'
        ? `Version ${neueVersion ?? ''} ist einsatzbereit. Jetzt neu starten.`
        : phase === 'laedt'
          ? `Aktualisierung lädt: ${prozent}%`
          : `Neue Version ${neueVersion ?? ''} verfügbar. Zum Laden tippen.`

    return (
      <button
        ref={ref}
        type="button"
        title={titelText}
        aria-label={titelText}
        onClick={handleClick}
        className={`group relative inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-all select-none ${statusFarben[phase]} ${
          animationAktiv ? 'ring-2 ring-primary/40 animate-pulse' : ''
        } ${className}`}
        {...props}
      >
        {/* Symbolanzeige passend zum Status */}
        {phase === 'laedt' && (
          <RefreshCw className="h-3.5 w-3.5 animate-spin text-status-warning" aria-hidden="true" />
        )}
        {phase === 'verfuegbar' && (
          <Download className="h-3.5 w-3.5 text-primary transition-transform group-hover:-translate-y-0.5" aria-hidden="true" />
        )}
        {(phase === 'bereit' || phase === 'installiert_android') && (
          <CheckCircle2 className="h-3.5 w-3.5 text-status-success" aria-hidden="true" />
        )}
        {phase === 'fehler' && (
          <AlertCircle className="h-3.5 w-3.5 text-status-destructive" aria-hidden="true" />
        )}

        {/* Textanzeige */}
        {!kompakt && (
          <span className="truncate max-w-[160px]">
            {phase === 'laedt' && `Lädt ${prozent}%`}
            {phase === 'verfuegbar' && (neueVersion ? `Update v${neueVersion}` : 'Update verfügbar')}
            {phase === 'bereit' && 'Neu starten'}
            {phase === 'installiert_android' && 'Wird installiert'}
            {phase === 'fehler' && 'Update wiederholen'}
          </span>
        )}

        {/* Fortschritts-Füllung im Hintergrund beim Laden */}
        {phase === 'laedt' && prozent > 0 && (
          <span
            className="absolute inset-y-0 left-0 rounded-full bg-status-warning/20 transition-all pointer-events-none"
            style={{ width: `${Math.min(100, Math.max(0, prozent))}%` }}
            aria-hidden="true"
          />
        )}
      </button>
    )
  },
)

UpdateBadge.displayName = 'UpdateBadge'
