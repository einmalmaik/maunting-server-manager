import { useTranslation } from 'react-i18next'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'

export function Servers() {
  const { t } = useTranslation()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">{t('nav.servers')}</h1>
        <p className="text-muted-foreground mt-1">Verwalte deine Game-Server</p>
      </div>

      <Card className="border-dashed border-2">
        <CardContent className="py-12 text-center">
          <CardTitle className="text-lg font-medium text-foreground mb-2">Server-Verwaltung</CardTitle>
          <CardDescription>
            Hier kannst du bald Server erstellen, starten, stoppen und konfigurieren.
          </CardDescription>
        </CardContent>
      </Card>
    </div>
  )
}
