import { useTranslation } from 'react-i18next'
import { HardDrive } from 'lucide-react'

export function Backups() {
  const { t } = useTranslation()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-headline text-headline-sm text-primary">{t('nav.backups')}</h1>
        <p className="font-body-md text-body-md text-on-surface-variant mt-1">
          Backup-Verwaltung
        </p>
      </div>

      <div className="msm-card p-12 text-center">
        <HardDrive className="w-12 h-12 text-on-surface-variant mx-auto mb-4" />
        <h3 className="font-headline text-body-lg text-on-surface mb-2">Backups</h3>
        <p className="font-body-md text-sm text-on-surface-variant">
          Backup-Funktionen werden hier verf&uuml;gbar sein.
        </p>
      </div>
    </div>
  )
}