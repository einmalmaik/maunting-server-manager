import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Activity, Archive, Braces, Plug, RotateCcw, Settings2, Shield, Table2, TerminalSquare } from 'lucide-react'
import { Button } from '@/Singra/UI'
import { TabBar, type TabDef } from '@/components/ui/TabBar'
import { StudioProvider } from './StudioContext'
import { BackupTab } from './BackupTab'
import { ConnectionTab } from './ConnectionTab'
import { ExplorerTab } from './ExplorerTab'
import { InstanceTab } from './InstanceTab'
import { LogicTab } from './LogicTab'
import { MonitorTab } from './MonitorTab'
import { SecurityTab } from './SecurityTab'
import { SqlTab } from './SqlTab'
import { ErrorBox, Loading, useLoad } from './shared'
import { studioApi } from './studioApi'

type TabId = 'explorer' | 'sql' | 'logic' | 'security' | 'monitor' | 'backup' | 'instance' | 'connection'

/**
 * PostgreSQL-Studio einer Datenbank. Jeder Reiter ist ein eigenes Modul;
 * Strukturänderungen laufen über `StudioProvider.runOperation` (SQL-Vorschau).
 */
export function PostgresStudio({ serverId, databaseId, databaseName, onResourcesChanged, onBootstrap }: {
  serverId: number
  databaseId: number
  databaseName: string
  onResourcesChanged: () => void
  /** Eigene Instanz: Einrichtung nachholen, wenn die Datenbank noch fehlt. */
  onBootstrap?: () => Promise<void>
}) {
  const { t } = useTranslation()
  const api = useMemo(() => studioApi(serverId, databaseId), [serverId, databaseId])
  const overview = useLoad(() => api.overview(), [api])
  const [tab, setTab] = useState<TabId>('explorer')
  const [sqlDraft, setSqlDraft] = useState<{ sql: string; rollback: boolean; key: number } | null>(null)
  const [bootstrapping, setBootstrapping] = useState(false)

  if (overview.loading && !overview.data) return <Loading />
  if (overview.error || !overview.data) {
    return (
      <div className="msm-card space-y-3 p-6">
        <ErrorBox message={overview.error || t('common.error')} />
        {onBootstrap && (
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm text-on-surface-variant">{t('postgresStudio.connection.bootstrapPending')}</p>
            <Button
              variant="secondary"
              disabled={bootstrapping}
              onClick={async () => {
                setBootstrapping(true)
                try {
                  await onBootstrap()
                } finally {
                  setBootstrapping(false)
                }
              }}
              data-testid="studio-bootstrap"
            >
              <RotateCcw className="h-4 w-4" />
              {t('postgresStudio.connection.bootstrapRetry')}
            </Button>
          </div>
        )}
      </div>
    )
  }

  const data = overview.data
  const admin = data.permissions.admin
  const tabs: TabDef<TabId>[] = [
    { id: 'explorer', labelKey: 'postgresStudio.tabs.explorer', icon: Table2 },
    ...(admin ? [{ id: 'sql' as const, labelKey: 'postgresStudio.tabs.sql', icon: TerminalSquare }] : []),
    { id: 'logic', labelKey: 'postgresStudio.tabs.logic', icon: Braces },
    { id: 'security', labelKey: 'postgresStudio.tabs.security', icon: Shield },
    { id: 'monitor', labelKey: 'postgresStudio.tabs.monitor', icon: Activity },
    ...(admin ? [{ id: 'backup' as const, labelKey: 'postgresStudio.tabs.backup', icon: Archive }] : []),
    ...(admin && data.kind === 'dedicated' ? [{ id: 'instance' as const, labelKey: 'postgresStudio.tabs.instance', icon: Settings2 }] : []),
    { id: 'connection', labelKey: 'postgresStudio.tabs.connection', icon: Plug },
  ]
  const active = tabs.some((entry) => entry.id === tab) ? tab : 'explorer'

  const openSql = (sql: string, rollback: boolean) => {
    setSqlDraft({ sql, rollback, key: Date.now() })
    setTab('sql')
  }

  return (
    <StudioProvider serverId={serverId} databaseId={databaseId} databaseName={databaseName} api={api} overview={data}>
      <div className="space-y-4" data-testid="postgres-studio">
        <TabBar tabs={tabs} active={active} onChange={setTab} ariaLabel={t('postgresStudio.title')} />
        {active === 'explorer' && <ExplorerTab />}
        {active === 'sql' && <SqlTab key={sqlDraft?.key ?? 0} initialSql={sqlDraft?.sql} initialRollback={sqlDraft?.rollback} />}
        {active === 'logic' && <LogicTab onOpenSql={openSql} />}
        {active === 'security' && <SecurityTab />}
        {active === 'monitor' && <MonitorTab />}
        {active === 'backup' && <BackupTab />}
        {active === 'instance' && <InstanceTab />}
        {active === 'connection' && <ConnectionTab onResourcesChanged={onResourcesChanged} />}
      </div>
    </StudioProvider>
  )
}
