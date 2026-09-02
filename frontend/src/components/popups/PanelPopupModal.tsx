import { useEffect, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Megaphone, X, ExternalLink, Check, EyeOff } from 'lucide-react'
import { getActivePopup, dismissPopup, type PanelPopup } from '@/api/popups'
import { AiMarkdown } from '@/components/ai/AiMarkdown'
import { Button } from '@/components/ui/Button'
import { useAuthStore } from '@/stores/authStore'

interface PanelPopupModalProps {
  popup?: PanelPopup | null
  isPreview?: boolean
  onClose?: () => void
}

export function PanelPopupModal({ popup: initialPopup, isPreview = false, onClose }: PanelPopupModalProps) {
  const { t } = useTranslation()
  const { isAuthenticated } = useAuthStore()
  const [popup, setPopup] = useState<PanelPopup | null>(initialPopup ?? null)
  const [dismissing, setDismissing] = useState(false)
  const previousFocus = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (initialPopup !== undefined) {
      setPopup(initialPopup)
      return
    }

    if (!isAuthenticated || isPreview) return

    let active = true
    getActivePopup()
      .then((data) => {
        if (active) setPopup(data)
      })
      .catch(() => {
        // Fehler beim Popup-Abruf stören den normalen Betrieb nicht
      })

    return () => {
      active = false
    }
  }, [initialPopup, isAuthenticated, isPreview])

  // Escape-Taste schließt mit Snooze
  useEffect(() => {
    if (!popup) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        handleDismiss('snooze')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [popup])

  // Fokus merken und wiederherstellen
  if (popup && !previousFocus.current) {
    previousFocus.current = document.activeElement as HTMLElement | null
  }

  useEffect(() => {
    if (popup) return
    const target = previousFocus.current
    previousFocus.current = null
    if (target?.isConnected) target.focus()
  }, [popup])

  if (!popup) return null

  const handleDismiss = async (mode: 'snooze' | 'permanent') => {
    if (isPreview) {
      onClose?.()
      return
    }

    setDismissing(true)
    try {
      await dismissPopup(popup.id, mode)
      setPopup(null)
      onClose?.()
    } catch {
      // Bei Fehler Dialog trotzdem lokal schließen
      setPopup(null)
      onClose?.()
    } finally {
      setDismissing(false)
    }
  }

  return (
    <div
      className="msm-modal-overlay z-50 flex items-center justify-center p-4"
      onClick={() => handleDismiss('snooze')}
      role="dialog"
      aria-modal="true"
      aria-labelledby="popup-title"
    >
      <div
        className="msm-card max-w-2xl w-full max-h-[85vh] flex flex-col relative border border-primary/30 shadow-2xl shadow-primary/10 overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-outline-variant/30 bg-surface-container-high/40">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-9 h-9 rounded-lg bg-primary/15 text-primary flex items-center justify-center shrink-0">
              <Megaphone className="w-5 h-5" />
            </div>
            <h2 id="popup-title" className="font-headline text-title-lg text-on-surface truncate">
              {popup.title}
            </h2>
          </div>
          <button
            type="button"
            onClick={() => handleDismiss('snooze')}
            disabled={dismissing}
            className="p-1.5 rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest transition-colors"
            aria-label={t('common.close', 'Schließen')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body Content */}
        <div className="p-6 overflow-y-auto space-y-4 flex-1">
          <AiMarkdown content={popup.content_markdown} />

          {/* Optionaler Aktions-Button */}
          {popup.button_text && popup.button_url && (
            <div className="pt-3 pb-1">
              <a
                href={popup.button_url}
                target="_blank"
                rel="noreferrer noopener"
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-on-primary font-medium text-sm hover:bg-primary/90 transition-colors shadow-sm"
              >
                <span>{popup.button_text}</span>
                <ExternalLink className="w-4 h-4" />
              </a>
            </div>
          )}
        </div>

        {/* Footer Actions */}
        <div className="flex flex-wrap items-center justify-between gap-3 p-4 border-t border-outline-variant/30 bg-surface-container-low">
          <div>
            <Button
              variant="ghost"
              size="sm"
              disabled={dismissing}
              onClick={() => handleDismiss('permanent')}
              className="text-on-surface-variant hover:text-error"
            >
              <EyeOff className="w-4 h-4 mr-1.5" />
              {t('popups.neverShowAgain', 'Nicht mehr anzeigen')}
            </Button>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              disabled={dismissing}
              onClick={() => handleDismiss('snooze')}
            >
              {t('common.close', 'Schließen')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={dismissing}
              onClick={() => handleDismiss('snooze')}
            >
              <Check className="w-4 h-4 mr-1.5" />
              {t('popups.understand', 'Verstanden')}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
