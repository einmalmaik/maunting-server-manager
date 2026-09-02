import type { RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { Logo } from '@/components/Logo'
import { Menu } from 'lucide-react'

interface TopbarProps {
  onOpenNavigation?: () => void
  menuButtonRef?: RefObject<HTMLButtonElement>
}

export function Topbar({ onOpenNavigation, menuButtonRef }: TopbarProps) {
  const { t } = useTranslation()

  return (
    <header className="lg:hidden h-12 flex items-center justify-between px-3 border-b border-outline-variant/30 bg-surface-container-low/95 backdrop-blur-md sticky top-0 z-30 shrink-0">
      <div className="flex items-center gap-2.5">
        <button
          ref={menuButtonRef}
          type="button"
          onClick={onOpenNavigation}
          className="grid min-h-10 min-w-10 place-items-center rounded-lg text-on-surface-variant transition-colors hover:bg-surface-container-high hover:text-primary"
          aria-label={t('shell.openNavigation', 'Open navigation')}
          aria-haspopup="dialog"
        >
          <Menu className="w-5 h-5" />
        </button>
        <div className="flex items-center gap-2">
          <Logo size="sm" />
          <span className="font-headline text-sm font-bold text-primary">MSM</span>
        </div>
      </div>
    </header>
  )
}
