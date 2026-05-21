import { useTranslation } from 'react-i18next'
import { Users as UsersIcon } from 'lucide-react'

export function Users() {
  const { t } = useTranslation()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-headline text-headline-sm text-primary">{t('nav.users')}</h1>
        <p className="font-body-md text-body-md text-on-surface-variant mt-1">
          Benutzer und Berechtigungen verwalten
        </p>
      </div>

      <div className="msm-card p-12 text-center">
        <UsersIcon className="w-12 h-12 text-on-surface-variant mx-auto mb-4" />
        <h3 className="font-headline text-body-lg text-on-surface mb-2">Benutzerverwaltung</h3>
        <p className="font-body-md text-sm text-on-surface-variant">
          User- und Permission-Management wird hier verf&uuml;gbar sein.
        </p>
      </div>
    </div>
  )
}