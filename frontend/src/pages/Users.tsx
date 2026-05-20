import { useTranslation } from 'react-i18next'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card'

export function Users() {
  const { t } = useTranslation()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">{t('nav.users')}</h1>
        <p className="text-muted-foreground mt-1">Benutzer und Berechtigungen verwalten</p>
      </div>

      <Card>
        <CardContent className="py-12 text-center">
          <CardTitle className="text-lg font-medium text-foreground mb-2">Benutzerverwaltung</CardTitle>
          <p className="text-sm text-muted-foreground">User- und Permission-Management wird hier verfügbar sein.</p>
        </CardContent>
      </Card>
    </div>
  )
}
