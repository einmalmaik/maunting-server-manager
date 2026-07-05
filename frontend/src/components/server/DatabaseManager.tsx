import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Shield } from 'lucide-react'
import { api } from '@/api/client'
import { DatabaseConsole } from '@/Singra/UI/DatabaseConsole'
import { PostgresCredentialsDialog } from '@/components/server/PostgresCredentialsDialog'
import { useHasPermission } from '@/hooks/useHasPermission'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'
import type {
  PostgresCredential,
  PostgresDatabase,
  PostgresDatabaseStats,
  PostgresPowerUserCredential,
  PostgresResources,
  PostgresRowsResult,
  PostgresSqlResult,
  PostgresTable,
  PostgresTableInfo,
} from '@/types'

interface Props {
  serverId: number
}

const DEFAULT_SQL = 'SELECT *\nFROM public.users\nLIMIT 50;'

export function DatabaseManager({ serverId }: Props) {
  const { t } = useTranslation()
  const canWrite = useHasPermission('server.databases.write', serverId)
  const canAdmin = useHasPermission('server.databases.admin', serverId)
  const [resources, setResources] = useState<PostgresResources>({ databases: [], users: [] })
  const [selectedDbId, setSelectedDbId] = useState<number | null>(null)
  const [stats, setStats] = useState<PostgresDatabaseStats | null>(null)
  const [tables, setTables] = useState<PostgresTable[]>([])
  const [selectedTable, setSelectedTable] = useState<PostgresTable | null>(null)
  const [tableInfo, setTableInfo] = useState<PostgresTableInfo | null>(null)
  const [rows, setRows] = useState<PostgresRowsResult | null>(null)
  const [sqlText, setSqlText] = useState(DEFAULT_SQL)
  const [sqlResult, setSqlResult] = useState<PostgresSqlResult | null>(null)
  const [history, setHistory] = useState<string[]>([])
  const [credentials, setCredentials] = useState<PostgresCredential[]>([])
  const [powerDialog, setPowerDialog] = useState<{ db: PostgresDatabase; password: string } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const selectedDatabase = useMemo(
    () => resources.databases.find((database) => database.id === selectedDbId) || null,
    [resources.databases, selectedDbId],
  )

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key)
    setError(null)
    try {
      await action()
    } catch (err: any) {
      const message = err.message || t('common.error')
      setError(message)
      toast.error(message)
    } finally {
      setBusy(null)
    }
  }

  const fetchResources = async () => {
    const data = await api<PostgresResources>(`/servers/${serverId}/databases`)
    setResources(data)
    setSelectedDbId((current) => current ?? data.databases[0]?.id ?? null)
  }

  const fetchDatabaseData = async (databaseId: number) => {
    const [statsData, tableData] = await Promise.all([
      api<PostgresDatabaseStats>(`/servers/${serverId}/databases/stats`, {
        method: 'POST',
        body: JSON.stringify({ database_id: databaseId }),
      }),
      api<{ tables: PostgresTable[] }>(`/servers/${serverId}/databases/tables/list`, {
        method: 'POST',
        body: JSON.stringify({ database_id: databaseId }),
      }),
    ])
    setStats(statsData)
    setTables(tableData.tables)
    const nextTable = tableData.tables[0] || null
    setSelectedTable(nextTable)
    if (nextTable) {
      await selectTable(nextTable, databaseId)
    } else {
      setRows(null)
      setTableInfo(null)
    }
  }

  const selectTable = async (table: PostgresTable, databaseId = selectedDbId, search?: string) => {
    if (!databaseId) return
    setSelectedTable(table)
    const [infoData, rowsData] = await Promise.all([
      api<PostgresTableInfo>(`/servers/${serverId}/databases/tables/info`, {
        method: 'POST',
        body: JSON.stringify({ database_id: databaseId, schema_name: table.schema, table_name: table.name }),
      }),
      api<PostgresRowsResult>(`/servers/${serverId}/databases/rows`, {
        method: 'POST',
        body: JSON.stringify({
          database_id: databaseId,
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
    void run('load', fetchResources)
  }, [serverId])

  useEffect(() => {
    if (selectedDbId) {
      void run('database', () => fetchDatabaseData(selectedDbId))
    }
  }, [selectedDbId])

  const bootstrap = () =>
    run('bootstrap', async () => {
      const result = await api<{ credentials: PostgresCredential[] }>(`/servers/${serverId}/databases/bootstrap`, {
        method: 'POST',
        body: JSON.stringify({ database_count: 1 }),
      })
      setCredentials(result.credentials)
      await fetchResources()
    })

  const createTable = () =>
    run('create-table', async () => {
      if (!selectedDbId) return
      const name = window.prompt('Tabellenname')?.trim()
      if (!name) return
      await api(`/servers/${serverId}/databases/tables`, {
        method: 'POST',
        body: JSON.stringify({
          database_id: selectedDbId,
          schema_name: 'public',
          table_name: name,
          columns: [
            { name: 'id', type: 'bigint', primary_key: true, not_null: true },
            { name: 'created_at', type: 'timestamp', primary_key: false, not_null: true },
          ],
        }),
      })
      await fetchDatabaseData(selectedDbId)
    })

  const dropTable = () =>
    run('drop-table', async () => {
      if (!selectedDbId || !selectedTable) return
      const ok = await confirm({
        title: 'Tabelle löschen',
        message: `Tabelle ${selectedTable.schema}.${selectedTable.name} wirklich löschen?`,
        confirmText: t('common.delete'),
        danger: true,
      })
      if (!ok) return
      const typed = window.prompt(`Zum Bestätigen "${selectedTable.name}" eingeben`) || ''
      if (typed !== selectedTable.name) return
      await api(`/servers/${serverId}/databases/tables/drop`, {
        method: 'POST',
        body: JSON.stringify({
          database_id: selectedDbId,
          schema_name: selectedTable.schema,
          table_name: selectedTable.name,
          confirm_name: typed,
        }),
      })
      await fetchDatabaseData(selectedDbId)
    })

  const deleteDatabase = () =>
    run('delete-db', async () => {
      if (!selectedDbId || !selectedDatabase) return
      const ok = await confirm({
        title: 'Datenbank löschen',
        message: `Datenbank "${selectedDatabase.name}" wirklich löschen? Alle Daten gehen unwiderruflich verloren.`,
        confirmText: t('common.delete'),
        danger: true,
      })
      if (!ok) return
      const typed = window.prompt(`Zum Bestätigen "${selectedDatabase.name}" eingeben`) || ''
      if (typed !== selectedDatabase.name) return
      await api(`/servers/${serverId}/databases/${selectedDbId}`, {
        method: 'DELETE',
        body: JSON.stringify({ confirm_name: typed }),
      })
      await fetchResources()
      toast.success('Datenbank gelöscht')
    })

  const runSql = () =>
    run('sql', async () => {
      if (!selectedDbId) return
      const result = await api<PostgresSqlResult>(`/servers/${serverId}/databases/sql`, {
        method: 'POST',
        body: JSON.stringify({ database_id: selectedDbId, sql: sqlText, limit: 500 }),
      })
      setSqlResult(result)
      setHistory((current) => [sqlText, ...current.filter((entry) => entry !== sqlText)].slice(0, 20))
      await fetchDatabaseData(selectedDbId)
    })

  const importSql = (file: File) =>
    run('import', async () => {
      const sql = await file.text()
      await api(`/servers/${serverId}/databases/import`, {
        method: 'POST',
        body: JSON.stringify({ sql, confirm_text: selectedDatabase?.name || null }),
      })
      if (selectedDbId) await fetchDatabaseData(selectedDbId)
      toast.success('Import abgeschlossen')
    })

  const exportSql = () =>
    run('export', async () => {
      const res = await fetch(`/api/servers/${serverId}/databases/export`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          ...csrfHeader(),
        } as Record<string, string>,
        body: JSON.stringify({ confirm_text: selectedDatabase?.name || null }),
      })
      if (!res.ok) throw new Error(await res.text())
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `msm-server-${serverId}-postgres.sql`
      link.click()
      URL.revokeObjectURL(url)
    })

  const powerAction = (kind: 'enable' | 'rotate' | 'demote') =>
    run(`power-${kind}`, async () => {
      if (!selectedDbId || !selectedDatabase) return
      if (kind === 'demote') {
        const typed = window.prompt(`Zum Bestätigen "${selectedDatabase.owner_role}" eingeben`) || ''
        if (typed !== selectedDatabase.owner_role) return
        await api(`/servers/${serverId}/databases/power-user/demote`, {
          method: 'DELETE',
          body: JSON.stringify({ database_id: selectedDbId, username: selectedDatabase.owner_role, confirm_name: typed }),
        })
        await fetchResources()
        return
      }
      const endpoint = kind === 'enable' ? 'power-user' : 'power-user/rotate'
      const credential = await api<PostgresPowerUserCredential>(`/servers/${serverId}/databases/${endpoint}`, {
        method: 'POST',
        body: JSON.stringify({ database_id: selectedDbId }),
      })
      setPowerDialog({ db: selectedDatabase, password: credential.password })
      await fetchResources()
    })

  const createUser = () =>
    run('create-user', async () => {
      if (!selectedDbId) return
      const username = window.prompt('Benutzername für den neuen Datenbank-User')?.trim()
      if (!username) return
      const result = await api<{ credential: PostgresCredential }>(`/servers/${serverId}/databases/users`, {
        method: 'POST',
        body: JSON.stringify({ database_id: selectedDbId, username }),
      })
      setCredentials((prev) => [...prev, result.credential])
      await fetchResources()
      toast.success('Datenbank-User erstellt')
    })

  const rotateUser = (userId: number) =>
    run(`rotate-user-${userId}`, async () => {
      const result = await api<{ username: string; password: string; host: string; port: number }>(
        `/servers/${serverId}/databases/users/${userId}/rotate`,
        { method: 'POST' },
      )
      setCredentials((prev) => [...prev, {
        database_name: selectedDatabase?.name || '',
        username: result.username,
        password: result.password,
        host: result.host,
        port: result.port,
      }])
      await fetchResources()
      toast.success('Passwort rotiert')
    })

  const deleteUser = (userId: number) =>
    run(`delete-user-${userId}`, async () => {
      const user = resources.users.find((u) => u.id === userId)
      if (!user) return
      const ok = await confirm({
        title: 'Datenbank-User löschen',
        message: `User "${user.username}" wirklich löschen?`,
        confirmText: t('common.delete'),
        danger: true,
      })
      if (!ok) return
      const typed = window.prompt(`Zum Bestätigen "${user.username}" eingeben`) || ''
      if (typed !== user.username) return
      await api(`/servers/${serverId}/databases/users/${userId}`, {
        method: 'DELETE',
        body: JSON.stringify({ confirm_name: typed }),
      })
      await fetchResources()
      toast.success('Datenbank-User gelöscht')
    })

  if (resources.databases.length === 0) {
    return (
      <div className="msm-card p-8 text-center">
        <h3 className="font-headline text-xl text-on-surface">Keine PostgreSQL-Datenbank</h3>
        <p className="mt-2 text-sm text-on-surface-variant">Erstelle eine servergebundene Datenbank, bevor du Tabellen verwaltest.</p>
        <button className="msm-btn-primary mt-5 inline-flex items-center gap-2 px-4 py-2" onClick={bootstrap} disabled={busy === 'bootstrap'}>
          Datenbank erstellen
        </button>
        <PostgresCredentialsDialog credentials={credentials} onClose={() => setCredentials([])} />
      </div>
    )
  }

  return (
    <>
      <DatabaseConsole
        title="Datenbanken"
        subtitle="Verwalte und bearbeite die PostgreSQL-Datenbanken dieses Servers."
        databases={resources.databases}
        selectedDatabaseId={selectedDbId}
        stats={stats}
        tables={tables}
        selectedTable={selectedTable}
        tableInfo={tableInfo}
        rows={rows}
        sqlText={sqlText}
        sqlResult={sqlResult}
        history={history}
        canAdmin={canAdmin}
        canManagePowerUser={canAdmin}
        powerUserActive={Boolean(selectedDatabase?.is_superuser)}
        busy={busy}
        error={error}
        onSelectDatabase={(id) => {
          setSelectedDbId(id)
          setRows(null)
          setTableInfo(null)
        }}
        onSelectTable={(table) => void run('table', () => selectTable(table))}
        onSearchRows={(search) => selectedTable && void run('rows', () => selectTable(selectedTable, selectedDbId, search))}
        onSqlTextChange={setSqlText}
        onRunSql={runSql}
        onCreateDatabase={canAdmin ? bootstrap : undefined}
        onDeleteDatabase={canAdmin ? deleteDatabase : undefined}
        onCreateTable={canWrite ? createTable : undefined}
        onDropTable={canWrite ? dropTable : undefined}
        onImport={canAdmin ? importSql : undefined}
        onExport={canAdmin ? exportSql : undefined}
        onEnablePowerUser={canAdmin ? () => powerAction('enable') : undefined}
        onRotatePowerUser={canAdmin ? () => powerAction('rotate') : undefined}
        onDemotePowerUser={canAdmin ? () => powerAction('demote') : undefined}
        onRefresh={() => selectedDbId && void run('refresh', () => fetchDatabaseData(selectedDbId))}
        dbUsers={resources.users}
        onCreateUser={canAdmin ? createUser : undefined}
        onRotateUser={canAdmin ? rotateUser : undefined}
        onDeleteUser={canAdmin ? deleteUser : undefined}
      />
      <PostgresCredentialsDialog credentials={credentials} onClose={() => setCredentials([])} />
      <PowerUserDialog state={powerDialog} onClose={() => setPowerDialog(null)} />
    </>
  )
}

function csrfHeader(): Record<string, string> {
  const match = document.cookie.match(new RegExp('(^| )__Secure-csrf_token=([^;]+)'))
  return match ? { 'X-CSRF-Token': decodeURIComponent(match[2]) } : {}
}

function PowerUserDialog({ state, onClose }: { state: { db: PostgresDatabase; password: string } | null; onClose: () => void }) {
  if (!state) return null
  const connectionUrl = `postgresql://${state.db.owner_role}:${state.password}@msm-postgres:5432/${state.db.name}`
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="msm-card w-full max-w-2xl p-6" onClick={(event) => event.stopPropagation()}>
        <div className="mb-3 flex items-center gap-2">
          <Shield className="h-5 w-5 text-status-warning" />
          <h3 className="font-headline text-xl font-semibold text-on-surface">Superuser-Zugang</h3>
        </div>
        <p className="mb-4 rounded-lg border border-status-warning/40 bg-status-warning/10 p-3 text-sm text-status-warning">
          Passwort nur jetzt anzeigen. Nicht in Tickets, Logs oder URLs teilen.
        </p>
        <div className="space-y-2 font-mono text-sm">
          <div>database: {state.db.name}</div>
          <div>username: {state.db.owner_role}</div>
          <div className="break-all rounded bg-status-error/10 p-2 text-status-error">password: {state.password}</div>
          <div className="break-all rounded border border-outline-variant bg-surface-container-high p-2 text-on-surface-variant">psql "{connectionUrl}"</div>
        </div>
        <button className="msm-btn-primary mt-5 w-full py-2" onClick={onClose}>Schließen</button>
      </div>
    </div>
  )
}
