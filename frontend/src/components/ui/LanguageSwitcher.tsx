import { Globe } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { supportedLocales } from '@/config/locales'
import { Dropdown } from './Dropdown'

interface LanguageSwitcherProps {
  className?: string
}

export function LanguageSwitcher({ className = '' }: LanguageSwitcherProps) {
  const { i18n } = useTranslation()

  return (
    <Dropdown
      className={className}
      value={i18n.language}
      onChange={(value) => void i18n.changeLanguage(value)}
      aria-label="Sprache auswählen"
      options={supportedLocales.map((locale) => ({
        value: locale.code,
        label: locale.nativeLabel,
        icon: <Globe className="h-3.5 w-3.5" aria-hidden="true" />,
      }))}
      buttonClassName="h-8 min-w-[9rem] bg-transparent py-1.5 text-xs"
    />
  )
}
