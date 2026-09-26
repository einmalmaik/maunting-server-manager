import { useTranslation } from 'react-i18next'
import { PostgresStudio } from '@/components/postgres/PostgresStudio'
import type { StudioZiel } from '@/components/postgres/studioApi'
import { PageHeader } from '@/Singra/UI/PageHeader'
import { useHasPermission } from '@/hooks/useHasPermission'

const PANEL: StudioZiel = { art: 'panel' }

/**
 * Die eigene Datenbank des Panels im selben Studio wie jede Server-Datenbank.
 * Lesen mit `panel.database.read`; Daten, SQL und Wartung mit
 * `panel.database.admin`. Die Struktur legen die Migrationen fest.
 */
export function PanelDatabase() {
  const { t } = useTranslation()
  const canAdmin = useHasPermission('panel.database.admin')

  return (
    <div className="msm-page">
      <PageHeader
        eyebrow={t('pageContext.data')}
        title={t('panelDatabase.title')}
        description={t('panelDatabase.subtitle')}
        status={<span className={canAdmin ? 'msm-badge-warning' : 'msm-badge-info'}>{canAdmin ? t('panelDatabase.admin') : t('panelDatabase.readOnly')}</span>}
      />
      <PostgresStudio ziel={PANEL} />
    </div>
  )
}
