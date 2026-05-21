import { useTranslation } from 'react-i18next'
import { Settings as SettingsIcon } from 'lucide-react'

export function Settings() {
  const { t } = useTranslation()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-headline text-headline-sm text-primary">{t('nav.settings')}</h1>
        <p className="font-body-md text-body-md text-on-surface-variant mt-1">
          Panel-Einstellungen
        </p>
      </div>

      <div className="msm-card p-12 text-center">
        <SettingsIcon className="w-12 h-12 text-on-surface-variant mx-auto mb-4" />
        <h3 className="font-headline text-body-lg text-on-surface mb-2">Einstellungen</h3>
        <p className="font-body-md text-sm text-on-surface-variant">
          SMTP, Domain, Sprache und weitere Panel-Einstellungen werden hier verf&uuml;gbar sein.
        </p>
      </div>
    </div>
  )
}