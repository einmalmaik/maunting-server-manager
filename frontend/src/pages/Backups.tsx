import { useTranslation } from 'react-i18next'
import { HardDrive } from 'lucide-react'

export function Backups() {
  const { t } = useTranslation()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-headline text-headline-sm text-primary">{t('nav.backups')}</h1>
        <p className="font-body-md text-body-md text-on-surface-variant mt-1">
          {t('backups.subtitle')}
        </p>
      </div>

      <div className="msm-card p-12 text-center">
        <HardDrive className="w-12 h-12 text-on-surface-variant mx-auto mb-4" />
        <h3 className="font-headline text-body-lg text-on-surface mb-2">{t('nav.backups')}</h3>
        <p className="font-body-md text-sm text-on-surface-variant">
          {t('backups.comingSoon')}
        </p>
      </div>
    </div>
  )
}