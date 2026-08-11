import { ReactNode, useEffect, useMemo, useState } from 'react'
import {
  ArrowUpDown,
  Boxes,
  CheckCircle2,
  Clock3,
  Columns3,
  Database,
  Download,
  FileUp,
  Filter,
  HardDrive,
  History,
  KeyRound,
  Layers3,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Search,
  Shield,
  Sparkles,
  Star,
  Table2,
  Trash2,
  Users,
  Wand2,
  X,
} from 'lucide-react'
import { Dropdown } from '@/components/ui/Dropdown'
import { Checkbox } from '@/components/ui/Checkbox'
import {
  readSqlConsoleEntries,
  sqlConsoleStorageKeys,
  writeSqlConsoleEntries,
} from '@/lib/sqlConsoleStorage'
import { useAuthStore } from '@/stores/authStore'
import type {
  PostgresDatabase,
  PostgresDatabaseStats,
  PostgresRowsResult,
  PostgresSqlResult,
  PostgresTable,
  PostgresTableInfo,
  PostgresUser,
} from '@/types'

type TabKey = 'tables' | 'sql' | 'users'

export interface SqlFavorite {
  id: string
  title: string
  sql: string
  createdAt: string
}

export interface DatabaseConsoleProps {
  title: string
  subtitle: string
  databaseLabel?: string
  databases: Array<Pick<PostgresDatabase, 'id' | 'name' | 'owner_role' | 'is_power_user'>>
  selectedDatabaseId: number | null
  stats: PostgresDatabaseStats | null
  tables: PostgresTable[]
  selectedTable: PostgresTable | null
  tableInfo: PostgresTableInfo | null
  rows: PostgresRowsResult | null
  sqlText: string
  sqlResult: PostgresSqlResult | null
  history: string[]
  /**
   * Benennt die Konsole, zu der Verlauf und Favoriten gehören — 'panel' oder
   * 'server-7'. Zusammen mit der Benutzerkennung ergibt das den Speicher-
   * schlüssel (siehe lib/sqlConsoleStorage.ts). Pflicht und nicht optional,
   * damit eine neue Einbindung die Trennung nicht stillschweigend aufhebt.
   */
  storageScope: string
  canAdmin: boolean
  canManagePowerUser?: boolean
  powerUserActive?: boolean
  busy?: string | null
  error?: string | null
  onSelectDatabase: (id: number) => void
  onSelectTable: (table: PostgresTable) => void
  onSearchRows: (search: string) => void
  onSqlTextChange: (value: string) => void
  onRunSql: () => void
  onCreateDatabase?: () => void
  onDeleteDatabase?: () => void
  onCreateTable?: () => void
  onDropTable?: () => void
  onImport?: (file: File) => void
  onExport?: () => void
  onEnablePowerUser?: () => void
  onRotatePowerUser?: () => void
  onDemotePowerUser?: () => void
  onRefresh?: () => void
  dbUsers?: PostgresUser[]
  onCreateUser?: () => void
  onRotateUser?: (userId: number) => void
  onDeleteUser?: (userId: number) => void
  onUpdateRow?: (
    schema: string,
    table: string,
    keyConditions: Record<string, any>,
    updates: Record<string, any>
  ) => Promise<void> | void
  onDeleteRows?: (
    schema: string,
    table: string,
    rowConditions: Array<Record<string, any>>
  ) => Promise<void> | void
  onInsertRow?: (
    schema: string,
    table: string,
    rowData: Record<string, any>
  ) => Promise<void> | void
}

export function DatabaseConsole({
  title,
  subtitle,
  databaseLabel = 'Datenbank',
  databases,
  selectedDatabaseId,
  stats,
  tables,
  selectedTable,
  tableInfo,
  rows,
  sqlText,
  sqlResult,
  history,
  storageScope,
  canAdmin,
  canManagePowerUser = false,
  powerUserActive = false,
  busy,
  error,
  onSelectDatabase,
  onSelectTable,
  onSearchRows,
  onSqlTextChange,
  onRunSql,
  onCreateDatabase,
  onDeleteDatabase,
  onCreateTable,
  onDropTable,
  onImport,
  onExport,
  onEnablePowerUser,
  onRotatePowerUser,
  onDemotePowerUser,
  onRefresh,
  dbUsers,
  onCreateUser,
  onRotateUser,
  onDeleteUser,
  onUpdateRow,
  onDeleteRows,
  onInsertRow,
}: DatabaseConsoleProps) {
  const [activeTab, setActiveTab] = useState<TabKey>('tables')
  const [search, setSearch] = useState('')
  const [openDropdown, setOpenDropdown] = useState<'filter' | 'sort' | 'columns' | null>(null)
  const [filterColumn, setFilterColumn] = useState('')
  const [filterValue, setFilterValue] = useState('')
  const [sortColumn, setSortColumn] = useState('')
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc')
  const [hiddenColumns, setHiddenColumns] = useState<Set<string>>(() => new Set())
  const [selectedRowIndices, setSelectedRowIndices] = useState<Set<number>>(new Set())

  const [editingRowIndex, setEditingRowIndex] = useState<number | null>(null)
  const [showInsertModal, setShowInsertModal] = useState(false)
  const [showDeleteModal, setShowDeleteModal] = useState(false)
  const [showSaveFavoriteModal, setShowSaveFavoriteModal] = useState(false)

  // Verlauf und Favoriten gehören dem angemeldeten Benutzer und dieser einen
  // Konsole. Die Benutzerkennung holt sich die Komponente selbst aus dem
  // Auth-Store, statt sie sich reichen zu lassen: eine Prop kann jede neue
  // Einbindung vergessen — und genau daran hing der Fehler. Der Schlüssel war
  // global, localStorage überlebt das Abmelden, und der nächste Benutzer am
  // selben Rechner las die Abfragen des vorigen im Verlaufsmenü mit.
  const userId = useAuthStore((state) => state.user?.id ?? 'anonym')
  const storageKeys = useMemo(() => sqlConsoleStorageKeys(userId, storageScope), [userId, storageScope])

  // Persistent History
  const [localHistory, setLocalHistory] = useState<string[]>(() =>
    readSqlConsoleEntries<string>(storageKeys.history),
  )

  // Persistent Favorites
  const [favorites, setFavorites] = useState<SqlFavorite[]>(() =>
    readSqlConsoleEntries<SqlFavorite>(storageKeys.favorites),
  )

  // Wechselt der Schlüssel zur Laufzeit — anderer Benutzer, andere Konsole —,
  // muss der angezeigte Zustand mitwechseln. Sonst stünde weiter der Inhalt des
  // alten Schlüssels auf dem Schirm und die nächste Abfrage schriebe ihn unter
  // dem neuen fest; die Trennung wäre wieder aufgehoben.
  useEffect(() => {
    setLocalHistory(readSqlConsoleEntries<string>(storageKeys.history))
    setFavorites(readSqlConsoleEntries<SqlFavorite>(storageKeys.favorites))
  }, [storageKeys])

  useEffect(() => {
    if (history.length > 0) {
      setLocalHistory((prev) => {
        const merged = Array.from(new Set([...history, ...prev])).slice(0, 30)
        writeSqlConsoleEntries(storageKeys.history, merged)
        return merged
      })
    }
  }, [history, storageKeys])

  const saveFavorite = (favTitle: string) => {
    if (!sqlText.trim()) return
    const newFav: SqlFavorite = {
      id: String(Date.now()),
      title: favTitle.trim() || 'Abfrage',
      sql: sqlText.trim(),
      createdAt: new Date().toISOString(),
    }
    setFavorites((prev) => {
      const next = [newFav, ...prev.filter((f) => f.sql !== sqlText.trim())]
      writeSqlConsoleEntries(storageKeys.favorites, next)
      return next
    })
    setShowSaveFavoriteModal(false)
  }

  const deleteFavorite = (id: string) => {
    setFavorites((prev) => {
      const next = prev.filter((f) => f.id !== id)
      writeSqlConsoleEntries(storageKeys.favorites, next)
      return next
    })
  }

  const handleRunSqlWithHistory = () => {
    if (sqlText.trim()) {
      setLocalHistory((prev) => {
        const next = [sqlText.trim(), ...prev.filter((h) => h !== sqlText.trim())].slice(0, 30)
        writeSqlConsoleEntries(storageKeys.history, next)
        return next
      })
    }
    onRunSql()
  }

  const safeDatabases = Array.isArray(databases) ? databases : []
  const selectedDatabase = safeDatabases.find((db) => db.id === selectedDatabaseId) || safeDatabases[0] || null
  const groupedTables = useMemo(() => groupTables(tables), [tables])

  const tabs: Array<{ key: TabKey; label: string; icon: typeof Table2 }> = [
    { key: 'tables', label: 'Tabellen', icon: Table2 },
    { key: 'sql', label: 'SQL-Konsole', icon: Play },
    ...(onCreateUser ? [{ key: 'users' as TabKey, label: 'Benutzer', icon: Users }] : []),
  ]

  const resultColumns = rows?.columns ?? []
  const processedRows = useMemo<PostgresRowsResult | null>(() => {
    if (!rows) return null
    let next = rows.rows
    if (filterColumn && filterValue) {
      const needle = filterValue.toLowerCase()
      next = next.filter((row) => String(row[filterColumn] ?? '').toLowerCase().includes(needle))
    }
    if (sortColumn) {
      const dir = sortDirection
      next = [...next].sort((a, b) => compareValues(a[sortColumn], b[sortColumn], dir))
    }
    const visibleColumns = hiddenColumns.size ? rows.columns.filter((col) => !hiddenColumns.has(col)) : rows.columns
    return { ...rows, columns: visibleColumns, rows: next }
  }, [rows, filterColumn, filterValue, sortColumn, sortDirection, hiddenColumns])

  useEffect(() => {
    setSelectedRowIndices(new Set())
    setEditingRowIndex(null)
    setShowInsertModal(false)
    setShowDeleteModal(false)
  }, [selectedTable?.name, processedRows])

  const toggleRow = (index: number) => {
    setSelectedRowIndices((current) => {
      const next = new Set(current)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  const toggleAll = () => {
    const rowCount = processedRows?.rows.length ?? 0
    setSelectedRowIndices((current) => {
      if (current.size === rowCount) return new Set()
      return new Set(Array.from({ length: rowCount }, (_, i) => i))
    })
  }

  const toggleColumn = (column: string) => {
    setHiddenColumns((current) => {
      const next = new Set(current)
      if (next.has(column)) next.delete(column)
      else next.add(column)
      return next
    })
  }

  const selectedSingleIndex = selectedRowIndices.size === 1 ? Array.from(selectedRowIndices)[0] : null
  const selectedRowForEdit = selectedSingleIndex !== null && processedRows ? processedRows.rows[selectedSingleIndex] : null

  const handleConfirmDeleteSelectedRows = async () => {
    if (!selectedTable || !onDeleteRows || !processedRows) return
    const indices = Array.from(selectedRowIndices)
    const rowConditions = indices.map((idx) => {
      const row = processedRows.rows[idx]
      return buildRowKeyConditions(row, tableInfo, resultColumns)
    })
    await onDeleteRows(selectedTable.schema, selectedTable.name, rowConditions)
    setSelectedRowIndices(new Set())
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
        <div>
          <div className="flex items-center gap-2 text-xs text-on-surface-variant">
            <span>Server</span>
            <span>/</span>
            <span>{databaseLabel}</span>
            {selectedDatabase && (
              <>
                <span>/</span>
                <span className="font-mono text-on-surface">{selectedDatabase.name}</span>
              </>
            )}
          </div>
          <h2 className="mt-3 font-headline text-2xl font-bold text-on-surface">{title}</h2>
          <p className="mt-1 text-sm text-on-surface-variant">{subtitle}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {selectedDatabase && (
            <Dropdown
              options={[
                ...databases.map((db) => ({ value: String(db.id), label: db.name })),
                ...(onDeleteDatabase
                  ? [{
                      value: '__delete__',
                      label: 'Datenbank löschen',
                      icon: <Trash2 className="h-4 w-4 text-status-error" />,
                      disabled: !selectedDatabaseId,
                    }]
                  : []),
              ]}
              value={selectedDatabaseId != null ? String(selectedDatabaseId) : null}
              placeholder="Datenbank auswählen"
              onChange={(value) => {
                if (value === '__delete__') {
                  onDeleteDatabase?.()
                } else {
                  onSelectDatabase(Number(value))
                }
              }}
              className="h-11 min-w-56"
              buttonClassName="h-11"
            />
          )}
          {onRefresh && (
            <button className="msm-btn-secondary h-11 px-3 inline-flex items-center gap-2" onClick={onRefresh}>
              <RefreshCw className="h-4 w-4" />
              Aktualisieren
            </button>
          )}
          {canAdmin && onCreateDatabase && (
            <button className="msm-btn-primary h-11 px-4 inline-flex items-center gap-2" onClick={onCreateDatabase}>
              <Plus className="h-4 w-4" />
              Datenbank verbinden
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-status-warning/35 bg-status-warning/10 p-3 text-sm text-status-warning">
          {error}
        </div>
      )}

      <div className="grid gap-3 xl:grid-cols-6">
        <MetricCard icon={Database} label="Datenbank" value={selectedDatabase?.name || '-'} hint={stats?.engine || 'PostgreSQL'} />
        <MetricCard icon={CheckCircle2} label="Status" value={stats?.status === 'healthy' ? 'Gesund' : 'Unklar'} hint="Backend-geprüft" tone="success" />
        <MetricCard icon={Table2} label="Tabellen" value={formatNumber(stats?.table_count ?? tables.length)} hint="In dieser Datenbank" tone="violet" />
        <MetricCard icon={HardDrive} label="Speicher" value={formatBytes(stats?.size_bytes)} hint="Gesamte Datengröße" tone="mint" />
        <MetricCard icon={Users} label="Verbindungen" value={formatConnections(stats)} hint="Aktive / maximale" tone="blue" />
        <MetricCard icon={Clock3} label="Latenz" value={formatLatency(stats?.latency_ms)} hint="Backend-Verbindung" tone="green" />
      </div>

      <div className="flex flex-wrap items-center gap-1 border-b border-outline-variant">
        {tabs.map((tab) => {
          const Icon = tab.icon
          return (
            <button
              key={tab.key}
              className={`inline-flex h-10 items-center gap-2 border-b px-3 text-sm transition ${
                activeTab === tab.key
                  ? 'border-secondary text-secondary font-semibold'
                  : 'border-transparent text-on-surface-variant hover:text-on-surface'
              }`}
              onClick={() => setActiveTab(tab.key)}
            >
              <Icon className="h-4 w-4" />
              {tab.label}
            </button>
          )
        })}
      </div>

      {activeTab === 'users' && onCreateUser ? (
        <UsersPanel
          users={dbUsers ?? []}
          canAdmin={canAdmin}
          busy={busy}
          onCreateUser={onCreateUser}
          onRotateUser={onRotateUser}
          onDeleteUser={onDeleteUser}
        />
      ) : activeTab === 'sql' ? (
        <div className="grid gap-4 xl:grid-cols-12 h-[calc(100vh-270px)] min-h-[540px] items-stretch">
          <section className="msm-card p-4 xl:col-span-9 flex flex-col h-full overflow-hidden">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 shrink-0">
              <div>
                <h3 className="font-headline text-lg font-semibold text-on-surface">SQL-Konsole</h3>
                <p className="text-xs text-on-surface-variant">Interaktive SQL-Befehle ausführen, formatieren und als Favorit speichern.</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  className="msm-btn-secondary px-3 py-1.5 text-xs inline-flex items-center gap-1.5 text-status-warning"
                  onClick={() => setShowSaveFavoriteModal(true)}
                  disabled={!sqlText.trim()}
                  title="Als Favorit speichern"
                >
                  <Star className="h-3.5 w-3.5 fill-status-warning/20" />
                  Favorit speichern
                </button>
                {onImport && (
                  <label className="msm-btn-secondary cursor-pointer px-3 py-1.5 text-xs inline-flex items-center gap-1.5">
                    <FileUp className="h-3.5 w-3.5" />
                    Import
                    <input className="hidden" type="file" accept=".sql,text/sql,text/plain" onChange={(event) => {
                      const file = event.target.files?.[0]
                      if (file) onImport(file)
                      event.currentTarget.value = ''
                    }} />
                  </label>
                )}
                {onExport && (
                  <button className="msm-btn-secondary px-3 py-1.5 text-xs inline-flex items-center gap-1.5" onClick={onExport}>
                    <Download className="h-3.5 w-3.5" />
                    Export
                  </button>
                )}
              </div>
            </div>
            <textarea
              className="msm-input min-h-48 font-mono text-xs leading-relaxed shrink-0"
              value={sqlText}
              onChange={(event) => onSqlTextChange(event.target.value)}
              onKeyDown={(event) => {
                if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
                  event.preventDefault()
                  handleRunSqlWithHistory()
                }
              }}
              placeholder="SELECT * FROM users WHERE active = true;"
              spellCheck={false}
            />
            <div className="mt-2.5 mb-2 flex flex-wrap items-center gap-2 shrink-0">
              <button className="msm-btn-primary px-4 py-1.5 text-xs inline-flex items-center gap-2" onClick={handleRunSqlWithHistory} disabled={!canAdmin || busy === 'sql'}>
                <Play className="h-3.5 w-3.5" />
                Ausführen
              </button>
              <button className="msm-btn-secondary px-3 py-1.5 text-xs inline-flex items-center gap-2" onClick={() => onSqlTextChange(formatSql(sqlText))}>
                <Wand2 className="h-3.5 w-3.5" />
                Formatieren
              </button>
              <span className="text-[11px] text-on-surface-variant">Ctrl+Enter · Max. 500 Zeilen im Ergebnis</span>
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto pr-1">
              <SqlResult result={sqlResult} />
            </div>
          </section>

          <aside className="msm-card p-4 xl:col-span-3 flex flex-col h-full overflow-hidden space-y-4">
            <div className="flex-1 min-h-0 flex flex-col overflow-hidden space-y-4">
              {/* Favorites Section */}
              <div className="flex flex-col h-1/2 min-h-0">
                <h4 className="mb-2 flex items-center gap-2 text-xs font-semibold text-on-surface shrink-0">
                  <Star className="h-3.5 w-3.5 text-status-warning fill-status-warning/20" />
                  SQL-Favoriten ({favorites.length})
                </h4>
                <div className="flex-1 min-h-0 overflow-y-auto space-y-1.5 pr-1">
                  {favorites.map((fav) => (
                    <div
                      key={fav.id}
                      className="group flex items-start justify-between gap-2 rounded-md border border-outline-variant bg-surface-container-high p-2 hover:border-secondary/50 transition"
                    >
                      <button
                        className="min-w-0 flex-1 text-left"
                        onClick={() => onSqlTextChange(fav.sql)}
                      >
                        <div className="font-semibold text-xs text-on-surface truncate">{fav.title}</div>
                        <div className="font-mono text-[10px] text-on-surface-variant truncate mt-0.5">{fav.sql}</div>
                      </button>
                      <button
                        className="text-on-surface-variant/50 hover:text-status-error opacity-0 group-hover:opacity-100 transition p-0.5 shrink-0"
                        onClick={() => deleteFavorite(fav.id)}
                        title="Favorit entfernen"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                  {!favorites.length && (
                    <p className="text-[11px] text-on-surface-variant/70 italic py-2">Keine Favoriten gespeichert.</p>
                  )}
                </div>
              </div>

              {/* History Section */}
              <div className="flex flex-col h-1/2 min-h-0 border-t border-outline-variant pt-3">
                <h4 className="mb-2 flex items-center justify-between text-xs font-semibold text-on-surface shrink-0">
                  <span className="flex items-center gap-2">
                    <History className="h-3.5 w-3.5 text-secondary" />
                    Abfrageverlauf
                  </span>
                  {localHistory.length > 0 && (
                    <button
                      className="text-[10px] text-on-surface-variant hover:text-on-surface"
                      onClick={() => {
                        setLocalHistory([])
                        // Leeren heißt leeren: der Eintrag verschwindet aus dem
                        // Browserspeicher, nicht nur aus der Anzeige.
                        try { localStorage.removeItem(storageKeys.history) } catch {}
                      }}
                    >
                      Leeren
                    </button>
                  )}
                </h4>
                <div className="flex-1 min-h-0 overflow-y-auto space-y-1.5 pr-1">
                  {localHistory.map((entry, index) => (
                    <button
                      key={`${entry}-${index}`}
                      className="w-full rounded-md border border-outline-variant bg-surface-container-high p-2 text-left font-mono text-[11px] text-on-surface-variant hover:text-on-surface hover:border-secondary/40 transition truncate"
                      onClick={() => onSqlTextChange(entry)}
                      title={entry}
                    >
                      {entry.length > 80 ? `${entry.slice(0, 77)}...` : entry}
                    </button>
                  ))}
                  {!localHistory.length && (
                    <p className="text-[11px] text-on-surface-variant/70 italic py-2">Noch keine Abfragen im Verlauf.</p>
                  )}
                </div>
              </div>
            </div>
          </aside>
        </div>
      ) : (
        <div className="msm-database-console-grid grid gap-4 xl:grid-cols-12 h-[calc(100vh-270px)] min-h-[540px] items-stretch">
          {/* Left Sidebar: Tables List */}
          <aside className="msm-card p-4 xl:col-span-3 flex flex-col h-full overflow-hidden">
            <div className="relative shrink-0">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-on-surface-variant" />
              <input className="msm-input pl-9 text-xs h-9" placeholder="Tabellen suchen..." onChange={(e) => setSearch(e.target.value)} />
            </div>
            <div className="mt-3 flex-1 min-h-0 space-y-4 overflow-y-auto pr-1">
              {groupedTables.map((group) => {
                const visible = group.tables.filter((table) => table.name.toLowerCase().includes(search.toLowerCase()))
                if (!visible.length) return null
                return (
                  <div key={group.schema}>
                    <div className="mb-2 flex items-center justify-between text-xs text-on-surface-variant">
                      <span className="font-semibold text-on-surface">{group.schema}</span>
                      <span className="rounded-full border border-outline-variant px-2 py-0.5 font-mono text-[10px]">{visible.length}</span>
                    </div>
                    <div className="space-y-1">
                      {visible.map((table) => (
                        <button
                          key={`${table.schema}.${table.name}`}
                          className={`flex w-full items-center justify-between gap-2 rounded-md border px-3 py-2 text-left transition ${
                            selectedTable?.schema === table.schema && selectedTable.name === table.name
                              ? 'border-secondary bg-secondary/10 text-secondary font-medium'
                              : 'border-transparent text-on-surface-variant hover:border-outline-variant hover:bg-surface-container-high'
                          }`}
                          onClick={() => onSelectTable(table)}
                        >
                          <span className="flex min-w-0 items-center gap-2">
                            <Table2 className="h-4 w-4 shrink-0" />
                            <span className="truncate font-mono text-xs">{table.name}</span>
                          </span>
                          <span className="shrink-0 font-mono text-[11px] text-on-surface-variant/80">{formatRows(table.row_estimate)}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
            {canAdmin && onCreateTable && (
              <button className="msm-btn-secondary mt-3 w-full py-2 inline-flex items-center justify-center gap-2 text-xs shrink-0" onClick={onCreateTable}>
                <Plus className="h-3.5 w-3.5" />
                Neue Tabelle erstellen
              </button>
            )}
          </aside>

          {/* Center Main: Table View & Toolbar */}
          <main className="xl:col-span-6 flex flex-col h-full overflow-hidden min-w-0">
            <section className="msm-card p-4 flex flex-col h-full overflow-hidden">
              <div className="mb-3 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between shrink-0 border-b border-outline-variant pb-3">
                <div>
                  <h3 className="font-headline text-base font-semibold text-on-surface">
                    Tabelle: <span className="font-mono text-secondary">{selectedTable?.name || '-'}</span>
                  </h3>
                  <p className="text-xs text-on-surface-variant">{formatRows(tableInfo?.row_estimate)} · {formatBytes(tableInfo?.size_bytes)}</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {selectedRowIndices.size > 0 && (
                    <span className="text-xs font-mono text-secondary font-medium">
                      {selectedRowIndices.size} gewählt
                    </span>
                  )}

                  {/* Group 1: CRUD Actions */}
                  <div className="inline-flex rounded-lg border border-outline-variant bg-surface-container-high p-0.5 gap-0.5">
                    {selectedTable && onInsertRow && (
                      <button
                        className="msm-btn-primary px-2.5 h-8 inline-flex items-center gap-1 text-xs"
                        onClick={() => setShowInsertModal(true)}
                        title="Neue Zeile einfügen"
                      >
                        <Plus className="h-3.5 w-3.5" />
                        + Zeile
                      </button>
                    )}
                    {selectedSingleIndex !== null && onUpdateRow && (
                      <button
                        className="msm-btn-secondary px-2.5 h-8 inline-flex items-center gap-1 text-xs"
                        onClick={() => setEditingRowIndex(selectedSingleIndex)}
                        title="Zeile bearbeiten"
                      >
                        <Pencil className="h-3.5 w-3.5 text-secondary" />
                        Bearbeiten
                      </button>
                    )}
                    {selectedRowIndices.size > 0 && onDeleteRows && (
                      <button
                        className="msm-btn-destructive px-2.5 h-8 inline-flex items-center gap-1 text-xs"
                        onClick={() => setShowDeleteModal(true)}
                        title="Ausgewählte Zeilen löschen"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        Löschen
                      </button>
                    )}
                  </div>

                  {/* Group 2: Search & View Controls */}
                  <div className="flex items-center gap-1.5">
                    <div className="relative w-36">
                      <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-on-surface-variant" />
                      <input
                        className="msm-input pl-8 h-8 text-xs"
                        placeholder="Suchen..."
                        onChange={(event) => setSearch(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') onSearchRows(search)
                        }}
                      />
                    </div>
                    <ToolbarToggleButton
                      icon={Filter}
                      label="Filter"
                      active={openDropdown === 'filter'}
                      hasState={Boolean(filterColumn && filterValue)}
                      disabled={!resultColumns.length}
                      onClick={() => setOpenDropdown(openDropdown === 'filter' ? null : 'filter')}
                    />
                    <ToolbarToggleButton
                      icon={ArrowUpDown}
                      label="Sortieren"
                      active={openDropdown === 'sort'}
                      hasState={Boolean(sortColumn)}
                      disabled={!resultColumns.length}
                      onClick={() => setOpenDropdown(openDropdown === 'sort' ? null : 'sort')}
                    />
                    <ToolbarToggleButton
                      icon={Columns3}
                      label="Spalten"
                      active={openDropdown === 'columns'}
                      hasState={hiddenColumns.size > 0}
                      disabled={!resultColumns.length}
                      onClick={() => setOpenDropdown(openDropdown === 'columns' ? null : 'columns')}
                    />
                    {canAdmin && onDropTable && (
                      <button className="msm-btn-destructive px-2 h-8 inline-flex items-center gap-1 text-xs" onClick={onDropTable} title="Tabelle löschen">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                </div>
              </div>

              <div className="relative">
                {openDropdown === 'filter' && (
                  <FilterDropdown
                    columns={resultColumns}
                    filterColumn={filterColumn}
                    filterValue={filterValue}
                    onFilterColumn={setFilterColumn}
                    onFilterValue={setFilterValue}
                    onClose={() => setOpenDropdown(null)}
                  />
                )}
                {openDropdown === 'sort' && (
                  <SortDropdown
                    columns={resultColumns}
                    sortColumn={sortColumn}
                    sortDirection={sortDirection}
                    onSortColumn={setSortColumn}
                    onSortDirection={setSortDirection}
                    onClose={() => setOpenDropdown(null)}
                  />
                )}
                {openDropdown === 'columns' && (
                  <ColumnsDropdown
                    columns={resultColumns}
                    hiddenColumns={hiddenColumns}
                    onToggle={toggleColumn}
                    onReset={() => setHiddenColumns(new Set())}
                    onClose={() => setOpenDropdown(null)}
                  />
                )}
              </div>

              <div className="flex-1 min-h-0 overflow-auto">
                <RowsGrid
                  result={processedRows}
                  selectable
                  selectedIndices={selectedRowIndices}
                  onToggleRow={toggleRow}
                  onToggleAll={toggleAll}
                />
              </div>
            </section>
          </main>

          {/* Right Sidebar: Schema & Details */}
          <aside className="msm-card p-4 xl:col-span-3 flex flex-col h-full overflow-hidden">
            <div className="mb-3 flex items-center gap-3 shrink-0 border-b border-outline-variant pb-3">
              <div className="rounded-lg border border-secondary/30 bg-secondary/10 p-2 text-secondary">
                <Layers3 className="h-4 w-4" />
              </div>
              <div>
                <h3 className="font-headline text-sm font-semibold text-on-surface truncate">{selectedTable?.name || 'Keine Tabelle'}</h3>
                <p className="text-[11px] text-on-surface-variant">Schema & Indizes</p>
              </div>
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto pr-1">
              <SchemaPanel tableInfo={tableInfo} />
            </div>
            {canManagePowerUser && (
              <div className="mt-3 border-t border-outline-variant pt-3 shrink-0">
                <h4 className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-on-surface">
                  <KeyRound className="h-3.5 w-3.5 text-status-warning" />
                  Power-User
                </h4>
                <div className="space-y-1.5 text-[11px]">
                  <p className="text-on-surface-variant">
                    {powerUserActive
                      ? 'Owner-Zugang aktiv (kein Cluster-SUPERUSER).'
                      : 'Power-User nur für bewusste Admin-Arbeiten aktivieren.'}
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {powerUserActive ? (
                      <>
                        <button className="msm-btn-secondary px-2.5 py-1 text-[11px]" onClick={onRotatePowerUser}>Rotieren</button>
                        <button className="msm-btn-destructive px-2.5 py-1 text-[11px]" onClick={onDemotePowerUser}>Entziehen</button>
                      </>
                    ) : (
                      <button className="msm-btn-secondary px-2.5 py-1 text-[11px] inline-flex items-center gap-1" onClick={onEnablePowerUser}>
                        <Shield className="h-3 w-3" />
                        Aktivieren
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )}
          </aside>
        </div>
      )}

      {/* Row Edit Modal */}
      {editingRowIndex !== null && selectedRowForEdit && selectedTable && onUpdateRow && (
        <EditRowModal
          columns={resultColumns}
          initialRow={selectedRowForEdit}
          tableInfo={tableInfo}
          onClose={() => setEditingRowIndex(null)}
          onSave={async (keyConditions, updates) => {
            await onUpdateRow(selectedTable.schema, selectedTable.name, keyConditions, updates)
            setEditingRowIndex(null)
          }}
        />
      )}

      {/* Row Insert Modal */}
      {showInsertModal && selectedTable && onInsertRow && (
        <InsertRowModal
          columns={resultColumns}
          onClose={() => setShowInsertModal(false)}
          onSave={async (rowData) => {
            await onInsertRow(selectedTable.schema, selectedTable.name, rowData)
            setShowInsertModal(false)
          }}
        />
      )}

      {/* Delete Confirmation Modal */}
      {showDeleteModal && selectedTable && onDeleteRows && (
        <DeleteConfirmModal
          count={selectedRowIndices.size}
          onClose={() => setShowDeleteModal(false)}
          onConfirm={async () => {
            await handleConfirmDeleteSelectedRows()
            setShowDeleteModal(false)
          }}
        />
      )}

      {/* Save Favorite Query Modal */}
      {showSaveFavoriteModal && (
        <SaveFavoriteModal
          sql={sqlText}
          onClose={() => setShowSaveFavoriteModal(false)}
          onSave={saveFavorite}
        />
      )}
    </div>
  )
}

function SaveFavoriteModal({
  sql,
  onClose,
  onSave,
}: {
  sql: string
  onClose: () => void
  onSave: (title: string) => void
}) {
  const [title, setTitle] = useState('')
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 p-4 backdrop-blur-sm">
      <div className="msm-card max-w-md w-full p-6 shadow-2xl space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-headline text-base font-bold text-on-surface flex items-center gap-2">
            <Star className="h-4 w-4 text-status-warning fill-status-warning/20" />
            Abfrage als Favorit speichern
          </h3>
          <button className="text-on-surface-variant hover:text-on-surface" onClick={onClose}><X className="h-4 w-4" /></button>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-on-surface-variant font-medium">Titel / Bezeichnung</label>
          <input
            className="msm-input text-xs"
            placeholder="z. B. Aktive Benutzer suchen"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            autoFocus
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs text-on-surface-variant font-medium">SQL-Befehl</label>
          <div className="rounded border border-outline-variant bg-surface-container p-2 font-mono text-[11px] text-on-surface-variant max-h-32 overflow-y-auto">
            {sql}
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-outline-variant pt-3">
          <button className="msm-btn-secondary px-3 py-1.5 text-xs" onClick={onClose}>
            Abbrechen
          </button>
          <button
            className="msm-btn-primary px-4 py-1.5 text-xs"
            onClick={() => onSave(title)}
            disabled={!title.trim()}
          >
            Speichern
          </button>
        </div>
      </div>
    </div>
  )
}

function UsersPanel({ users, canAdmin, busy, onCreateUser, onRotateUser, onDeleteUser }: {
  users: PostgresUser[]
  canAdmin: boolean
  busy?: string | null
  onCreateUser?: () => void
  onRotateUser?: (userId: number) => void
  onDeleteUser?: (userId: number) => void
}) {
  return (
    <div className="msm-card p-4">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h3 className="font-headline text-lg font-semibold text-on-surface">Datenbank-Benutzer</h3>
          <p className="text-xs text-on-surface-variant">Zusätzliche Benutzer mit Zugriff auf die Datenbanken dieses Servers.</p>
        </div>
        {canAdmin && onCreateUser && (
          <button className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2" onClick={onCreateUser} disabled={busy === 'create-user'}>
            <Plus className="h-4 w-4" />
            Benutzer erstellen
          </button>
        )}
      </div>
      {!users.length ? (
        <div className="rounded-lg border border-outline-variant p-8 text-center text-sm text-on-surface-variant">
          Keine Datenbank-Benutzer vorhanden.
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-outline-variant">
          <table className="min-w-full text-sm">
            <thead className="bg-surface-container-highest text-on-surface">
              <tr>
                <th className="px-3 py-2 text-left font-mono font-medium">Benutzername</th>
                <th className="px-3 py-2 text-left font-mono font-medium">Passwort</th>
                <th className="px-3 py-2 text-left font-mono font-medium">Erstellt</th>
                <th className="px-3 py-2 text-left font-mono font-medium">Zuletzt rotiert</th>
                {canAdmin && <th className="px-3 py-2 text-right font-mono font-medium">Aktionen</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-outline-variant">
              {users.map((user) => (
                <tr key={user.id} className="bg-surface-container text-on-surface-variant hover:bg-surface-container-high">
                  <td className="px-3 py-2 font-mono text-xs text-on-surface">{user.username}</td>
                  <td className="px-3 py-2 font-mono text-xs">{user.password_mask}</td>
                  <td className="px-3 py-2 font-mono text-xs">{formatDate(user.created_at)}</td>
                  <td className="px-3 py-2 font-mono text-xs">{user.last_rotated_at ? formatDate(user.last_rotated_at) : '-'}</td>
                  {canAdmin && (
                    <td className="px-3 py-2 text-right">
                      <div className="inline-flex gap-2">
                        {onRotateUser && (
                          <button className="msm-btn-secondary px-2 py-1 text-xs" onClick={() => onRotateUser(user.id)} disabled={busy === `rotate-user-${user.id}`}>
                            Rotieren
                          </button>
                        )}
                        {onDeleteUser && (
                          <button className="msm-btn-destructive px-2 py-1 text-xs" onClick={() => onDeleteUser(user.id)} disabled={busy === `delete-user-${user.id}`}>
                            <Trash2 className="h-3 w-3" />
                          </button>
                        )}
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function MetricCard({ icon: Icon, label, value, hint, tone = 'default' }: {
  icon: typeof Database
  label: string
  value: string
  hint: string
  tone?: 'default' | 'success' | 'violet' | 'mint' | 'blue' | 'green'
}) {
  const toneClass = {
    default: 'text-primary bg-primary/10 border-primary/20',
    success: 'text-status-success bg-status-success/10 border-status-success/20',
    violet: 'text-violet-300 bg-violet-400/10 border-violet-400/20',
    mint: 'text-secondary bg-secondary/10 border-secondary/20',
    blue: 'text-sky-300 bg-sky-400/10 border-sky-400/20',
    green: 'text-mint-accent bg-mint-accent/10 border-mint-accent/20',
  }[tone]
  return (
    <div className="msm-card p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs text-on-surface-variant">{label}</p>
          <p className="mt-2 truncate font-headline text-xl font-bold text-on-surface" title={value}>{value}</p>
          <p className="mt-1 truncate text-xs text-on-surface-variant">{hint}</p>
        </div>
        <div className={`rounded-xl border p-3 ${toneClass}`}>
          <Icon className="h-5 w-5" />
        </div>
      </div>
    </div>
  )
}

function RowsGrid({ result, selectable, selectedIndices, onToggleRow, onToggleAll }: {
  result: PostgresRowsResult | null
  selectable?: boolean
  selectedIndices?: Set<number>
  onToggleRow?: (index: number) => void
  onToggleAll?: () => void
}) {
  if (!result) return <div className="rounded-lg border border-outline-variant p-8 text-center text-xs text-on-surface-variant">Tabelle auswählen.</div>
  if (!result.columns.length) return <p className="text-xs text-on-surface-variant p-4">{result.status || 'Keine Daten.'}</p>
  const allSelected = selectable && result.rows.length > 0 && selectedIndices?.size === result.rows.length
  return (
    <div className="w-full">
      <table className="min-w-full text-xs">
        <thead className="sticky top-0 z-10 bg-surface-container-highest text-on-surface border-b border-outline-variant">
          <tr>
            {selectable && (
              <th className="px-2.5 py-2 w-8 text-center">
                <Checkbox checked={allSelected ?? false} onCheckedChange={() => onToggleAll?.()} />
              </th>
            )}
            {result.columns.map((column) => (
              <th key={column} className="px-3 py-2 text-left font-mono font-medium whitespace-nowrap">{column}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-outline-variant">
          {result.rows.map((row, index) => (
            <tr key={index} className={`text-on-surface-variant transition ${selectedIndices?.has(index) ? 'bg-secondary/15' : 'bg-surface-container hover:bg-surface-container-high'}`}>
              {selectable && (
                <td className="px-2.5 py-2 text-center">
                  <Checkbox checked={selectedIndices?.has(index) ?? false} onCheckedChange={() => onToggleRow?.(index)} />
                </td>
              )}
              {result.columns.map((column) => (
                <td key={column} className="max-w-[380px] px-3 py-2 align-top font-mono text-[11px] whitespace-pre-wrap break-words">
                  {formatValue(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SchemaPanel({ tableInfo }: { tableInfo: PostgresTableInfo | null }) {
  if (!tableInfo) return <p className="text-xs text-on-surface-variant text-center py-6">Keine Schema-Details geladen.</p>

  const pkNames = new Set(
    tableInfo.columns.filter((c) => (c as any).primary_key || c.name === 'id').map((c) => c.name)
  )

  return (
    <div className="space-y-4 text-xs">
      <div className="grid grid-cols-2 gap-2 rounded-lg border border-outline-variant bg-surface-container-high p-2.5">
        <div>
          <span className="text-[11px] text-on-surface-variant block">Geschätzte Zeilen</span>
          <span className="font-mono font-bold text-xs text-on-surface">{formatRows(tableInfo.row_estimate)}</span>
        </div>
        <div>
          <span className="text-[11px] text-on-surface-variant block">Datengröße</span>
          <span className="font-mono font-bold text-xs text-on-surface">{formatBytes(tableInfo.size_bytes)}</span>
        </div>
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <h4 className="font-semibold text-on-surface flex items-center gap-1.5">
            <Columns3 className="h-3.5 w-3.5 text-secondary" />
            Spalten ({tableInfo.columns.length})
          </h4>
        </div>
        <div className="overflow-hidden rounded-lg border border-outline-variant divide-y divide-outline-variant bg-surface-container">
          {tableInfo.columns.map((column) => {
            const isPk = pkNames.has(column.name)
            return (
              <div key={column.name} className="flex items-center justify-between gap-2 px-2.5 py-1.5 text-xs hover:bg-surface-container-high transition">
                <span className="truncate font-mono font-medium text-on-surface flex items-center gap-1">
                  {isPk && <span className="text-[9px] font-bold text-status-warning bg-status-warning/15 px-1 py-0.2 rounded" title="Primärschlüssel">PK</span>}
                  {column.name}
                </span>
                <div className="flex items-center gap-1 shrink-0">
                  <span className="font-mono text-[10px] text-on-surface-variant bg-surface-container-highest px-1.5 py-0.5 rounded border border-outline-variant/60">
                    {column.data_type}
                  </span>
                  {column.nullable && (
                    <span className="text-[9px] text-on-surface-variant/70 italic">NULL</span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {tableInfo.indexes.length > 0 && (
        <div>
          <h4 className="mb-2 font-semibold text-on-surface flex items-center gap-1.5">
            <Boxes className="h-3.5 w-3.5 text-sky-400" />
            Indizes ({tableInfo.indexes.length})
          </h4>
          <div className="space-y-1 max-h-36 overflow-y-auto pr-1">
            {tableInfo.indexes.map((idx) => (
              <div key={idx.name} className="truncate rounded border border-outline-variant bg-surface-container px-2.5 py-1.5 font-mono text-[10px] text-on-surface-variant">
                {idx.name}
              </div>
            ))}
          </div>
        </div>
      )}

      {tableInfo.foreign_keys.length > 0 && (
        <div>
          <h4 className="mb-2 font-semibold text-on-surface flex items-center gap-1.5">
            <Sparkles className="h-3.5 w-3.5 text-violet-400" />
            Fremdschlüssel ({tableInfo.foreign_keys.length})
          </h4>
          <div className="space-y-1 max-h-36 overflow-y-auto pr-1">
            {tableInfo.foreign_keys.map((fk) => (
              <div key={fk.name || `${fk.column_name}-${fk.foreign_table}`} className="rounded border border-outline-variant bg-surface-container p-2 font-mono text-[10px] text-on-surface-variant space-y-0.5">
                <div className="font-bold text-on-surface">{fk.column_name}</div>
                <div className="text-[10px] text-secondary">➔ {fk.foreign_table}.{fk.foreign_column}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function SqlResult({ result }: { result: PostgresSqlResult | null }) {
  if (!result) return null
  return (
    <div className="mt-3 space-y-3">
      {result.statements.map((entry, index) => (
        <div key={index} className={`rounded-lg border p-3 ${entry.error ? 'border-status-error/40 bg-status-error/10' : 'border-outline-variant bg-surface-container-high'}`}>
          <div className="mb-2 flex items-center justify-between gap-2">
            <code className="truncate text-xs text-on-surface-variant">{entry.statement}</code>
            <span className="font-mono text-xs text-on-surface-variant">{entry.duration_ms ?? 0} ms</span>
          </div>
          {entry.error ? (
            <pre className="whitespace-pre-wrap break-words font-mono text-xs text-status-error">{entry.error}</pre>
          ) : entry.columns.length ? (
            <RowsGrid result={{ columns: entry.columns, rows: entry.rows }} />
          ) : (
            <p className="font-mono text-xs text-on-surface-variant">{entry.status || 'OK'}</p>
          )}
        </div>
      ))}
    </div>
  )
}

function ToolbarToggleButton({ icon: Icon, label, active, hasState, disabled, onClick }: {
  icon: typeof Filter
  label: string
  active: boolean
  hasState: boolean
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <button
      className={`msm-btn-secondary px-2.5 h-8 inline-flex items-center gap-1.5 text-xs ${active ? 'ring-1 ring-primary' : ''} ${hasState ? 'text-secondary' : ''}`}
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
    >
      <Icon className="h-3.5 w-3.5" />
      {label}
    </button>
  )
}

function DropdownPanel({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div className="absolute right-0 top-full z-50 mt-1 w-72 rounded-lg border border-outline-variant bg-surface-container-highest p-3 shadow-lg">
        {children}
      </div>
    </>
  )
}

function FilterDropdown({ columns, filterColumn, filterValue, onFilterColumn, onFilterValue, onClose }: {
  columns: string[]
  filterColumn: string
  filterValue: string
  onFilterColumn: (value: string) => void
  onFilterValue: (value: string) => void
  onClose: () => void
}) {
  const active = Boolean(filterColumn && filterValue)
  return (
    <DropdownPanel onClose={onClose}>
      <div className="space-y-3">
        <div className="space-y-1">
          <label className="text-xs text-on-surface-variant">Spalte</label>
          <Dropdown
            options={columns.map((col) => ({ value: col, label: col }))}
            value={filterColumn || null}
            placeholder="-- Spalte wählen --"
            onChange={(value) => onFilterColumn(value)}
            buttonClassName="h-9 text-sm"
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs text-on-surface-variant">Wert enthält</label>
          <input
            className="msm-input h-9 text-sm"
            value={filterValue}
            placeholder="Text..."
            onChange={(event) => onFilterValue(event.target.value)}
          />
        </div>
        {active && (
          <button
            className="msm-btn-secondary w-full py-1.5 text-xs"
            onClick={() => { onFilterColumn(''); onFilterValue('') }}
          >
            Zurücksetzen
          </button>
        )}
      </div>
    </DropdownPanel>
  )
}

function SortDropdown({ columns, sortColumn, sortDirection, onSortColumn, onSortDirection, onClose }: {
  columns: string[]
  sortColumn: string
  sortDirection: 'asc' | 'desc'
  onSortColumn: (value: string) => void
  onSortDirection: (value: 'asc' | 'desc') => void
  onClose: () => void
}) {
  return (
    <DropdownPanel onClose={onClose}>
      <div className="space-y-3">
        <div className="space-y-1">
          <label className="text-xs text-on-surface-variant">Spalte</label>
          <Dropdown
            options={columns.map((col) => ({ value: col, label: col }))}
            value={sortColumn || null}
            placeholder="-- Spalte wählen --"
            onChange={(value) => onSortColumn(value)}
            buttonClassName="h-9 text-sm"
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs text-on-surface-variant">Richtung</label>
          <div className="flex gap-2">
            <button
              className={`msm-btn-secondary flex-1 py-1.5 text-xs ${sortDirection === 'asc' ? 'ring-1 ring-primary' : ''}`}
              onClick={() => onSortDirection('asc')}
            >
              Aufsteigend
            </button>
            <button
              className={`msm-btn-secondary flex-1 py-1.5 text-xs ${sortDirection === 'desc' ? 'ring-1 ring-primary' : ''}`}
              onClick={() => onSortDirection('desc')}
            >
              Absteigend
            </button>
          </div>
        </div>
        {sortColumn && (
          <button
            className="msm-btn-secondary w-full py-1.5 text-xs"
            onClick={() => { onSortColumn(''); onSortDirection('asc') }}
          >
            Zurücksetzen
          </button>
        )}
      </div>
    </DropdownPanel>
  )
}

function ColumnsDropdown({ columns, hiddenColumns, onToggle, onReset, onClose }: {
  columns: string[]
  hiddenColumns: Set<string>
  onToggle: (column: string) => void
  onReset: () => void
  onClose: () => void
}) {
  return (
    <DropdownPanel onClose={onClose}>
      <div className="space-y-2">
        <div className="max-h-60 space-y-1 overflow-y-auto pr-1">
          {columns.map((column) => (
            <label key={column} className="flex items-center gap-2 text-sm text-on-surface cursor-pointer">
              <input
                type="checkbox"
                checked={!hiddenColumns.has(column)}
                onChange={() => onToggle(column)}
              />
              <span className="truncate font-mono text-xs">{column}</span>
            </label>
          ))}
        </div>
        {hiddenColumns.size > 0 && (
          <button className="msm-btn-secondary w-full py-1.5 text-xs" onClick={onReset}>
            Zurücksetzen
          </button>
        )}
      </div>
    </DropdownPanel>
  )
}

function EditRowModal({
  columns,
  initialRow,
  tableInfo,
  onClose,
  onSave,
}: {
  columns: string[]
  initialRow: Record<string, any>
  tableInfo: PostgresTableInfo | null
  onClose: () => void
  onSave: (keyConditions: Record<string, any>, updates: Record<string, any>) => Promise<void>
}) {
  const [formData, setFormData] = useState<Record<string, any>>(() => ({ ...initialRow }))
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsSubmitting(true)
    setError(null)
    try {
      const keyConditions = buildRowKeyConditions(initialRow, tableInfo, columns)
      const updates: Record<string, any> = {}
      for (const col of columns) {
        if (formData[col] !== initialRow[col]) {
          updates[col] = formData[col]
        }
      }
      if (Object.keys(updates).length === 0) {
        onClose()
        return
      }
      await onSave(keyConditions, updates)
    } catch (err: any) {
      setError(err?.message || 'Fehler beim Speichern der Zeile')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 p-4 backdrop-blur-sm">
      <div className="msm-card max-h-[85vh] w-full max-w-xl flex flex-col overflow-hidden p-6 shadow-2xl">
        <div className="flex items-center justify-between">
          <h3 className="font-headline text-lg font-bold text-on-surface">Zeile bearbeiten</h3>
          <button className="text-on-surface-variant hover:text-on-surface" onClick={onClose}><X className="h-5 w-5" /></button>
        </div>
        <p className="mt-1 text-xs text-on-surface-variant">Werte für die ausgewählte Tabellenzeile anpassen.</p>
        {error && <div className="mt-3 rounded border border-status-error/30 bg-status-error/10 p-2 text-xs text-status-error">{error}</div>}
        <form onSubmit={handleSubmit} className="mt-4 flex-1 space-y-3 overflow-y-auto pr-1">
          {columns.map((col) => (
            <div key={col} className="space-y-1">
              <label className="text-xs font-mono font-medium text-on-surface-variant">{col}</label>
              <input
                className="msm-input font-mono text-xs"
                value={formData[col] === null || formData[col] === undefined ? '' : String(formData[col])}
                onChange={(e) => setFormData({ ...formData, [col]: e.target.value })}
              />
            </div>
          ))}
          <div className="mt-6 flex items-center justify-end gap-2 border-t border-outline-variant pt-4">
            <button type="button" className="msm-btn-secondary px-4 py-2 text-sm" onClick={onClose} disabled={isSubmitting}>
              Abbrechen
            </button>
            <button type="submit" className="msm-btn-primary px-4 py-2 text-sm" disabled={isSubmitting}>
              {isSubmitting ? 'Speichert...' : 'Änderungen speichern'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function InsertRowModal({
  columns,
  onClose,
  onSave,
}: {
  columns: string[]
  onClose: () => void
  onSave: (rowData: Record<string, any>) => Promise<void>
}) {
  const [formData, setFormData] = useState<Record<string, any>>({})
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsSubmitting(true)
    setError(null)
    try {
      const rowData: Record<string, any> = {}
      for (const col of columns) {
        if (formData[col] !== undefined && formData[col] !== '') {
          rowData[col] = formData[col]
        }
      }
      if (Object.keys(rowData).length === 0) {
        setError('Mindestens ein Feld muss ausgefüllt werden.')
        setIsSubmitting(false)
        return
      }
      await onSave(rowData)
    } catch (err: any) {
      setError(err?.message || 'Fehler beim Einfügen der Zeile')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 p-4 backdrop-blur-sm">
      <div className="msm-card max-h-[85vh] w-full max-w-xl flex flex-col overflow-hidden p-6 shadow-2xl">
        <div className="flex items-center justify-between">
          <h3 className="font-headline text-lg font-bold text-on-surface">Neue Zeile hinzufügen</h3>
          <button className="text-on-surface-variant hover:text-on-surface" onClick={onClose}><X className="h-5 w-5" /></button>
        </div>
        <p className="mt-1 text-xs text-on-surface-variant">Werte für eine neue Tabellenzeile eingeben.</p>
        {error && <div className="mt-3 rounded border border-status-error/30 bg-status-error/10 p-2 text-xs text-status-error">{error}</div>}
        <form onSubmit={handleSubmit} className="mt-4 flex-1 space-y-3 overflow-y-auto pr-1">
          {columns.map((col) => (
            <div key={col} className="space-y-1">
              <label className="text-xs font-mono font-medium text-on-surface-variant">{col}</label>
              <input
                className="msm-input font-mono text-xs"
                placeholder="Wert eingeben..."
                value={formData[col] || ''}
                onChange={(e) => setFormData({ ...formData, [col]: e.target.value })}
              />
            </div>
          ))}
          <div className="mt-6 flex items-center justify-end gap-2 border-t border-outline-variant pt-4">
            <button type="button" className="msm-btn-secondary px-4 py-2 text-sm" onClick={onClose} disabled={isSubmitting}>
              Abbrechen
            </button>
            <button type="submit" className="msm-btn-primary px-4 py-2 text-sm" disabled={isSubmitting}>
              {isSubmitting ? 'Einfügen...' : 'Zeile einfügen'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function DeleteConfirmModal({
  count,
  onClose,
  onConfirm,
}: {
  count: number
  onClose: () => void
  onConfirm: () => Promise<void>
}) {
  const [isDeleting, setIsDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleConfirm = async () => {
    setIsDeleting(true)
    setError(null)
    try {
      await onConfirm()
    } catch (err: any) {
      setError(err?.message || 'Fehler beim Löschen der Zeilen')
    } finally {
      setIsDeleting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 p-4 backdrop-blur-sm">
      <div className="msm-card w-full max-w-md p-6 shadow-2xl space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-headline text-lg font-bold text-status-error">Zeile(n) löschen</h3>
          <button className="text-on-surface-variant hover:text-on-surface" onClick={onClose}><X className="h-5 w-5" /></button>
        </div>
        <p className="text-sm text-on-surface-variant">
          Möchtest du die <strong className="text-on-surface font-mono">{count}</strong> ausgewählte(n) Zeile(n) wirklich unwiderruflich aus der Tabelle löschen?
        </p>
        {error && <div className="rounded border border-status-error/30 bg-status-error/10 p-2 text-xs text-status-error">{error}</div>}
        <div className="flex items-center justify-end gap-2 border-t border-outline-variant pt-4">
          <button className="msm-btn-secondary px-4 py-2 text-sm" onClick={onClose} disabled={isDeleting}>
            Abbrechen
          </button>
          <button className="msm-btn-destructive px-4 py-2 text-sm" onClick={handleConfirm} disabled={isDeleting}>
            {isDeleting ? 'Löscht...' : 'Unwiderruflich löschen'}
          </button>
        </div>
      </div>
    </div>
  )
}

function buildRowKeyConditions(row: Record<string, any>, tableInfo: PostgresTableInfo | null, columns: string[]): Record<string, any> {
  const pkCols = tableInfo?.columns.filter((c) => c.primary_key || c.name === 'id').map((c) => c.name) || []
  if (pkCols.length > 0 && pkCols.every((col) => col in row && row[col] !== undefined && row[col] !== null)) {
    const keys: Record<string, any> = {}
    for (const col of pkCols) {
      keys[col] = row[col]
    }
    return keys
  }
  const keys: Record<string, any> = {}
  for (const col of columns) {
    if (col in row && row[col] !== undefined && row[col] !== null && typeof row[col] !== 'object') {
      keys[col] = row[col]
    }
  }
  return keys
}

function compareValues(a: unknown, b: unknown, direction: 'asc' | 'desc'): number {
  const aNull = a == null
  const bNull = b == null
  if (aNull && bNull) return 0
  if (aNull) return 1
  if (bNull) return -1
  let cmp: number
  if (typeof a === 'number' && typeof b === 'number') {
    cmp = a - b
  } else {
    cmp = String(a).localeCompare(String(b))
  }
  return direction === 'asc' ? cmp : -cmp
}

function groupTables(tables: PostgresTable[]) {
  const map = new Map<string, PostgresTable[]>()
  for (const table of tables) {
    map.set(table.schema, [...(map.get(table.schema) || []), table])
  }
  return Array.from(map.entries()).map(([schema, grouped]) => ({ schema, tables: grouped }))
}

function formatDate(value: string): string {
  try {
    return new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
  } catch {
    return value
  }
}

function formatBytes(value?: number | null) {
  if (value == null) return '-'
  if (value < 1024) return `${value} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let size = value / 1024
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[index]}`
}

function formatRows(value?: number | null) {
  if (value == null) return '-'
  return `${formatNumber(value)} Zeilen`
}

function formatNumber(value: number) {
  return new Intl.NumberFormat('de-DE').format(value)
}

function formatConnections(stats: PostgresDatabaseStats | null) {
  if (!stats || stats.active_connections == null) return '-'
  return `${stats.active_connections} / ${stats.max_connections ?? '?'}`
}

function formatLatency(value?: number | null) {
  return value == null ? '-' : `${value} ms`
}

function formatValue(value: unknown) {
  if (value === null || value === undefined) return <span className="italic text-on-surface-variant/60">NULL</span>
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function formatSql(value: string) {
  return value
    .replace(/\s+(FROM|WHERE|ORDER BY|GROUP BY|LIMIT|JOIN|LEFT JOIN|RIGHT JOIN|INNER JOIN)\s+/gi, '\n$1 ')
    .replace(/\s+(AND|OR)\s+/gi, '\n  $1 ')
}
