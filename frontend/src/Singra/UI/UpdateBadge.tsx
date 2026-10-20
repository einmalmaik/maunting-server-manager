import { forwardRef, useState, useEffect, type HTMLAttributes } from 'react'
import { useTranslation } from 'react-i18next'
import { Download, RefreshCw, CheckCircle2, AlertCircle } from 'lucide-react'

import { Kurzinfo } from './Kurzinfo'

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
 * Einheitlicher, barrierefreier Indikator für App-Aktualisierungen (Pontis
 * Browser & MSS). Unter Windows bereitet die App Aktualisierungen still im
 * Hintergrund vor; das Badge erscheint deshalb erst als `bereit` und fordert
 * zum Neustart auf. Unter Android meldet es die Verfügbarkeit und startet auf
 * Klick die APK-Installation. Keine nativen Dialoge, kein `title`.
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
    const { t } = useTranslation()
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

    const version = neueVersion ?? ''
    const titelText =
      phase === 'fehler'
        ? (fehlerText ?? t('common.updateBadge.fehlerTitel'))
        : phase === 'bereit'
          ? t('common.updateBadge.bereit', { version })
          : phase === 'laedt'
            ? t('common.updateBadge.laedt', { prozent })
            : phase === 'installiert_android'
              ? t('common.updateBadge.installiertAndroid')
              : t('common.updateBadge.verfuegbar', { version })

    const kurzText =
      phase === 'laedt'
        ? t('common.updateBadge.kurzLaedt', { prozent })
        : phase === 'bereit'
          ? t('common.updateBadge.kurzBereit')
          : phase === 'installiert_android'
            ? t('common.updateBadge.kurzAndroid')
            : phase === 'fehler'
              ? t('common.updateBadge.kurzFehler')
              : neueVersion
                ? t('common.updateBadge.kurzVerfuegbar', { version })
                : t('common.updateBadge.kurzOhneVersion')

    const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => {
      if (onClick) onClick(e)
      if (phase !== 'laedt' && onInstallieren) {
        onInstallieren()
      }
    }

    const knopf = (
      <button
        ref={ref}
        type="button"
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
        {!kompakt && <span className="max-w-[160px] truncate">{kurzText}</span>}

        {/* Fortschritts-Füllung im Hintergrund beim Laden */}
        {phase === 'laedt' && prozent > 0 && (
          <span
            className="pointer-events-none absolute inset-y-0 left-0 rounded-full bg-status-warning/20 transition-all"
            style={{ width: `${Math.min(100, Math.max(0, prozent))}%` }}
            aria-hidden="true"
          />
        )}
      </button>
    )

    // Nur das reine Symbol braucht den Namen zusätzlich in der Kurzinfo; mit
    // sichtbarem Text steht er da und `aria-label` trägt den vollen Hinweis.
    return kompakt ? <Kurzinfo text={titelText}>{knopf}</Kurzinfo> : knopf
  },
)

UpdateBadge.displayName = 'UpdateBadge'
