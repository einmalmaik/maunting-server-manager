import { useEffect, useRef } from 'react'
import { Sparkles, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/Singra/UI'
import { AiSkillDirectory } from './AiSkillDirectory'

interface AiSkillModalProps {
  open: boolean
  onClose: () => void
}

export function AiSkillModal({ open, onClose }: AiSkillModalProps) {
  const { t } = useTranslation()
  const modalRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="skills-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6 bg-black/60 backdrop-blur-sm animate-fade-in"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        ref={modalRef}
        className="relative flex flex-col w-full max-w-3xl max-h-[85vh] rounded-2xl border border-outline-variant/40 bg-surface shadow-2xl overflow-hidden animate-content-show"
      >
        <header className="flex items-center justify-between px-5 py-3.5 border-b border-outline-variant/30 bg-surface-container-high/40">
          <div className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-primary" aria-hidden="true" />
            <h2 id="skills-modal-title" className="font-headline text-base font-semibold text-on-surface">
              {t('ai.skills.directoryTitle')}
            </h2>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onClose}
            aria-label={t('common.close', 'Schließen')}
            className="h-9 w-9 rounded-full p-0 text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/80 transition-colors"
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </Button>
        </header>

        <div className="flex-1 overflow-y-auto p-4 sm:p-6">
          <AiSkillDirectory />
        </div>
      </div>
    </div>
  )
}
