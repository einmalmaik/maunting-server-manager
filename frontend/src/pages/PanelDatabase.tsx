import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { DatabaseConsole } from '@/Singra/UI/DatabaseConsole'
import { PageHeader } from '@/Singra/UI/PageHeader'
import { useHasPermission } from '@/hooks/useHasPermission'
import { toast } from '@/stores/toastStore'
import type {
  PostgresDatabaseStats,
  PostgresRowsResult,
  PostgresSqlResult,
  PostgresTable,
  PostgresTableInfo,
} from '@/types'

const PANEL_DB_ID = 0
const PANEL_DATABASE = {
  id: PANEL_DB_ID,
  name: 'panel_database',
  owner_role: 'msm_panel',
  is_superuser: false,
}
const DEFAULT_SQL = 'SELECT table_schema, table_name\nFROM information_schema.tables\nWHERE table_schema NOT IN (\'pg_catalog\', \'information_schema\')\nORDER BY table_schema, table_name\nLIMIT 50;'

export function PanelDatabase() {
  const { t } = useTranslation()
  const canAdmin = useHasPermission('panel.database.admin')
  const [stats, setStats] = useState<PostgresDatabaseStats | null>(null)
  const [tables, setTables] = useState<PostgresTable[]>([])
  const [selectedTable, setSelectedTable] = useState<PostgresTable | null>(null)
  const [tableInfo, setTableInfo] = useState<PostgresTableInfo | null>(null)
  const [rows, setRows] = useState<PostgresRowsResult | null>(null)
  const [sqlText, setSqlText] = useState(DEFAULT_SQL)
  const [sqlResult, setSqlResult] = useState<PostgresSqlResult | null>(null)
  const [history, setHistory] = useState<string[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key)
    setError(null)
    try {
      await action()
    } catch (err: any) {
      const message = err.message || 'Panel-Datenbank konnte nicht geladen werden'
      setError(message)
      toast.error(message)
    } finally {
      setBusy(null)
    }
  }

  const load = async () => {
    const [statsData, tableData] = await Promise.all([
      api<PostgresDatabaseStats>('/panel/database/stats'),
      api<{ tables: PostgresTable[] }>('/panel/database/tables/list'),
    ])
    setStats(statsData)
    setTables(tableData.tables)
    const nextTable = tableData.tables[0] || null
    setSelectedTable(nextTable)
    if (nextTable) {
      await selectTable(nextTable)
    }
  }

  const selectTable = async (table: PostgresTable, search?: string) => {
    setSelectedTable(table)
    const [infoData, rowsData] = await Promise.all([
      api<PostgresTableInfo>('/panel/database/tables/info', {
        method: 'POST',
        body: JSON.stringify({ database_id: PANEL_DB_ID, schema_name: table.schema, table_name: table.name }),
      }),
      api<PostgresRowsResult>('/panel/database/rows', {
        method: 'POST',
        body: JSON.stringify({
          database_id: PANEL_DB_ID,
          schema_name: table.schema,
          table_name: table.name,
          search: search || null,
          limit: 500,
          offset: 0,
        }),
      }),
    ])
    setTableInfo(infoData)
    setRows(rowsData)
  }

  useEffect(() => {
    void run('load', load)
  }, [])

  const runSql = () =>
    run('sql', async () => {
      const result = await api<PostgresSqlResult>('/panel/database/sql', {
        method: 'POST',
        body: JSON.stringify({ database_id: PANEL_DB_ID, sql: sqlText, limit: 500 }),
      })
      setSqlResult(result)
      setHistory((current) => [sqlText, ...current.filter((entry) => entry !== sqlText)].slice(0, 20))
      await load()
    })

  const importSql = (file: File) =>
    run('import', async () => {
      const sql = await file.text()
      const result = await api<PostgresSqlResult>('/panel/database/sql', {
        method: 'POST',
        body: JSON.stringify({ database_id: PANEL_DB_ID, sql, limit: 500 }),
      })
      setSqlResult(result)
      await load()
      toast.success('Panel-DB-Import ausgeführt')
    })

  return (
    <div className="msm-page">
      <PageHeader
        eyebrow={t('pageContext.data', 'Data')}
        title={t('panelDatabase.title', 'Panel database')}
        description={t('panelDatabase.subtitle', 'Manage the panel PostgreSQL database without terminal access.')}
        status={<span className={canAdmin ? 'msm-badge-warning' : 'msm-badge-info'}>{canAdmin ? t('panelDatabase.admin', 'Admin') : t('panelDatabase.readOnly', 'Read only')}</span>}
      />
      <DatabaseConsole
      title={t('panelDatabase.workspace', 'Database explorer')}
      subtitle={t('panelDatabase.workspaceSubtitle', 'Inspect tables, rows and database statistics.')}
      databaseLabel="Panel"
      databases={[PANEL_DATABASE]}
      selectedDatabaseId={PANEL_DB_ID}
      stats={stats}
      tables={tables}
      selectedTable={selectedTable}
      tableInfo={tableInfo}
      rows={rows}
      sqlText={sqlText}
      sqlResult={sqlResult}
      history={history}
      canAdmin={canAdmin}
      busy={busy}
      error={error}
      onSelectDatabase={() => undefined}
      onSelectTable={(table) => void run('table', () => selectTable(table))}
      onSearchRows={(search) => selectedTable && void run('rows', () => selectTable(selectedTable, search))}
      onSqlTextChange={setSqlText}
      onRunSql={runSql}
      onImport={canAdmin ? importSql : undefined}
      onRefresh={() => void run('refresh', load)}
      />
    </div>
  )
}
