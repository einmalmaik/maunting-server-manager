import { useTranslation } from 'react-i18next'
import { AlertCircle } from 'lucide-react'

/**
 * Schalter für den Beta-Status des KI-Bereichs.
 *
 * Wenn die Beta-Phase abgeschlossen ist, kann diese Konstante auf `false` gesetzt
 * oder die Einbindung in `Ai.tsx` entfernt werden.
 */
export const AI_IS_BETA = true

export interface AiBetaBannerProps {
  className?: string
}

export function AiBetaBanner({ className = '' }: AiBetaBannerProps) {
  const { t } = useTranslation()

  if (!AI_IS_BETA) {
    return null
  }

  return (
    <div
      role="status"
      aria-label={t('ai.betaNotice', t('messenger.betaNotice', 'Beta-Version: Es können noch Fehler auftreten.'))}
      className={`shrink-0 border-b border-status-warning/25 bg-status-warning/10 px-3 py-1.5 text-xs text-on-surface backdrop-blur-sm z-20 flex items-center justify-between gap-2 overflow-hidden ${className}`}
    >
      <div className="flex items-center gap-2 min-w-0">
        <span className="inline-flex items-center gap-1 rounded bg-status-warning/20 px-1.5 py-0.5 text-label-sm font-bold uppercase tracking-wider text-status-warning border border-status-warning/30 shrink-0">
          <AlertCircle className="w-3 h-3" aria-hidden="true" />
          <span>{t('ai.betaBadge', t('messenger.betaBadge', 'Beta'))}</span>
        </span>
        <span className="text-label-sm text-on-surface-variant truncate sm:hidden">
          {t('ai.betaNoticeMobile', t('messenger.betaNoticeMobile', 'Beta: Fehler möglich'))}
        </span>
        <span className="text-xs text-on-surface-variant hidden sm:inline truncate">
          {t('ai.betaNotice', t('messenger.betaNotice', 'Beta-Version: Es können noch Fehler auftreten.'))}
        </span>
      </div>
    </div>
  )
}
