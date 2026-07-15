import { useTranslation } from 'react-i18next'
import { normalizePanelLanguage, panelLanguageCodes, type PanelLanguageCode } from '@/config/panelLocales'

interface LanguageSwitcherProps {
  className?: string
  onLanguageChange?: (code: PanelLanguageCode) => void
}

export function LanguageSwitcher({ className = '', onLanguageChange }: LanguageSwitcherProps) {
  const { i18n, t } = useTranslation()
  const active = normalizePanelLanguage(i18n.language)

  const setLang = (code: PanelLanguageCode) => {
    void i18n.changeLanguage(code)
    onLanguageChange?.(code)
  }

  return (
    <div
      className={`inline-flex items-center rounded-lg border border-outline-variant/50 bg-surface-container-high p-0.5 ${className}`}
      role="group"
      aria-label={t('language.label')}
    >
      {panelLanguageCodes.map((code) => {
        const selected = active === code
        return (
          <button
            key={code}
            type="button"
            onClick={() => setLang(code)}
            aria-pressed={selected}
            className={`min-w-[2.75rem] rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
              selected
                ? 'bg-primary text-on-primary shadow-sm'
                : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest'
            }`}
          >
            {t(`language.${code}`)}
          </button>
        )
      })}
    </div>
  )
}