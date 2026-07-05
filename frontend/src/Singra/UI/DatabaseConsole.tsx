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
  Play,
  Plus,
  RefreshCw,
  Search,
  Shield,
  Sparkles,
  Table2,
  Trash2,
  Users,
  Wand2,
} from 'lucide-react'
import { Dropdown } from '@/components/ui/Dropdown'
import { Checkbox } from '@/components/ui/Checkbox'
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

export interface DatabaseConsoleProps {
  title: string
  subtitle: string
  databaseLabel?: string
  databases: Array<Pick<PostgresDatabase, 'id' | 'name' | 'owner_role' | 'is_superuser'>>
  selectedDatabaseId: number | null
  stats: PostgresDatabaseStats | null
  tables: PostgresTable[]
  selectedTable: PostgresTable | null
  tableInfo: PostgresTableInfo | null
  rows: PostgresRowsResult | null
  sqlText: string
  sqlResult: PostgresSqlResult | null
  history: string[]
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
  const selectedDatabase = databases.find((db) => db.id === selectedDatabaseId) || databases[0] || null
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
                  ? 'border-secondary text-secondary'
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
      ) : (
      <div className="msm-database-console-grid">
        <aside className="msm-card p-4">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-on-surface-variant" />
            <input className="msm-input pl-9" placeholder="Tabellen suchen..." onChange={(e) => setSearch(e.target.value)} />
          </div>
          <div className="mt-4 max-h-[62vh] space-y-4 overflow-y-auto pr-1">
            {groupedTables.map((group) => {
              const visible = group.tables.filter((table) => table.name.toLowerCase().includes(search.toLowerCase()))
              if (!visible.length) return null
              return (
                <div key={group.schema}>
                  <div className="mb-2 flex items-center justify-between text-xs text-on-surface-variant">
                    <span className="font-semibold text-on-surface">{group.schema}</span>
                    <span className="rounded-full border border-outline-variant px-2 py-0.5 font-mono">{visible.length}</span>
                  </div>
                  <div className="space-y-1">
                    {visible.map((table) => (
                      <button
                        key={`${table.schema}.${table.name}`}
                        className={`flex w-full items-center justify-between gap-2 rounded-md border px-3 py-2 text-left ${
                          selectedTable?.schema === table.schema && selectedTable.name === table.name
                            ? 'border-secondary bg-secondary/10 text-secondary'
                            : 'border-transparent text-on-surface-variant hover:border-outline-variant hover:bg-surface-container-high'
                        }`}
                        onClick={() => onSelectTable(table)}
                      >
                        <span className="flex min-w-0 items-center gap-2">
                          <Table2 className="h-4 w-4 shrink-0" />
                          <span className="truncate font-mono text-sm">{table.name}</span>
                        </span>
                        <span className="shrink-0 font-mono text-xs">{formatRows(table.row_estimate)}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
          {canAdmin && onCreateTable && (
            <button className="msm-btn-secondary mt-4 w-full py-2 inline-flex items-center justify-center gap-2" onClick={onCreateTable}>
              <Plus className="h-4 w-4" />
              Neue Tabelle erstellen
            </button>
          )}
        </aside>

        <main className="space-y-4 min-w-0">
          {activeTab === 'tables' && (
            <section className="msm-card p-4">
              <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div>
                  <h3 className="font-headline text-lg font-semibold text-on-surface">
                    Tabelle: <span className="font-mono">{selectedTable?.name || '-'}</span>
                  </h3>
                  <p className="text-xs text-on-surface-variant">{formatRows(tableInfo?.row_estimate)} Zeilen · {formatBytes(tableInfo?.size_bytes)}</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {selectedRowIndices.size > 0 && (
                    <div className="inline-flex items-center gap-2 rounded-md border border-outline-variant bg-surface-container-high px-3 py-1.5 text-xs text-on-surface-variant">
                      <span>{selectedRowIndices.size} ausgewählt</span>
                      <button className="text-secondary hover:text-primary" onClick={() => setSelectedRowIndices(new Set())}>
                        Auswahl aufheben
                      </button>
                    </div>
                  )}
                  <div className="relative min-w-64">
                    <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-on-surface-variant" />
                    <input
                      className="msm-input pl-9"
                      placeholder="In dieser Tabelle suchen..."
                      onChange={(event) => setSearch(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') onSearchRows(search)
                      }}
                    />
                  </div>
                  <button className="msm-btn-secondary px-3 inline-flex items-center gap-2" onClick={() => onSearchRows(search)}>
                    <Search className="h-4 w-4" />
                    Suchen
                  </button>
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
                    <button className="msm-btn-destructive px-3 inline-flex items-center gap-2" onClick={onDropTable}>
                      <Trash2 className="h-4 w-4" />
                      Tabelle löschen
                    </button>
                  )}
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
              <RowsGrid
                result={processedRows}
                selectable
                selectedIndices={selectedRowIndices}
                onToggleRow={toggleRow}
                onToggleAll={toggleAll}
              />
            </section>
          )}

          <section className="msm-card p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h3 className="font-headline text-lg font-semibold text-on-surface">SQL-Konsole</h3>
              <div className="flex flex-wrap gap-2">
                {onImport && (
                  <label className="msm-btn-secondary cursor-pointer px-3 py-2 inline-flex items-center gap-2">
                    <FileUp className="h-4 w-4" />
                    Import
                    <input className="hidden" type="file" accept=".sql,text/sql,text/plain" onChange={(event) => {
                      const file = event.target.files?.[0]
                      if (file) onImport(file)
                      event.currentTarget.value = ''
                    }} />
                  </label>
                )}
                {onExport && (
                  <button className="msm-btn-secondary px-3 inline-flex items-center gap-2" onClick={onExport}>
                    <Download className="h-4 w-4" />
                    Export
                  </button>
                )}
              </div>
            </div>
            <textarea
              className="msm-input min-h-52 font-mono text-sm leading-relaxed"
              value={sqlText}
              onChange={(event) => onSqlTextChange(event.target.value)}
              onKeyDown={(event) => {
                if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
                  event.preventDefault()
                  onRunSql()
                }
              }}
              spellCheck={false}
            />
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2" onClick={onRunSql} disabled={!canAdmin || busy === 'sql'}>
                <Play className="h-4 w-4" />
                Ausführen
              </button>
              <button className="msm-btn-secondary px-3 py-2 inline-flex items-center gap-2" onClick={() => onSqlTextChange(formatSql(sqlText))}>
                <Wand2 className="h-4 w-4" />
                Formatieren
              </button>
              <span className="text-xs text-on-surface-variant">Ctrl+Enter · 500 Zeilen · lange Skripte erlaubt</span>
            </div>
            <SqlResult result={sqlResult} />
          </section>
        </main>

        <aside className="msm-card p-4">
          <div className="mb-4 flex items-center gap-3">
            <div className="rounded-lg border border-secondary/30 bg-secondary/10 p-2 text-secondary">
              <Layers3 className="h-5 w-5" />
            </div>
            <div>
              <h3 className="font-headline text-lg font-semibold text-on-surface">{selectedTable?.name || 'Keine Tabelle'}</h3>
              <p className="text-xs text-on-surface-variant">Schema</p>
            </div>
          </div>
          <SchemaPanel tableInfo={tableInfo} />
          {canManagePowerUser && (
            <div className="mt-5 border-t border-outline-variant pt-4">
              <h4 className="mb-2 flex items-center gap-2 text-sm font-semibold text-on-surface">
                <KeyRound className="h-4 w-4 text-status-warning" />
                Superuser
              </h4>
              <div className="space-y-2">
                <p className="text-xs text-on-surface-variant">
                  {powerUserActive ? 'Owner-Rolle hat aktuell SUPERUSER-Rechte.' : 'Superuser nur für bewusste Admin-Arbeiten aktivieren.'}
                </p>
                <div className="flex flex-wrap gap-2">
                  {powerUserActive ? (
                    <>
                      <button className="msm-btn-secondary px-3 py-2 text-xs" onClick={onRotatePowerUser}>Rotieren</button>
                      <button className="msm-btn-destructive px-3 py-2 text-xs" onClick={onDemotePowerUser}>Entziehen</button>
                    </>
                  ) : (
                    <button className="msm-btn-secondary px-3 py-2 text-xs inline-flex items-center gap-2" onClick={onEnablePowerUser}>
                      <Shield className="h-3.5 w-3.5" />
                      Aktivieren
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}
          <div className="mt-5 border-t border-outline-variant pt-4">
            <h4 className="mb-2 flex items-center gap-2 text-sm font-semibold text-on-surface">
              <History className="h-4 w-4" />
              Abfrage-Verlauf
            </h4>
            <div className="space-y-2">
              {history.slice(0, 5).map((entry, index) => (
                <button
                  key={`${entry}-${index}`}
                  className="w-full rounded-md border border-outline-variant bg-surface-container-high p-2 text-left font-mono text-xs text-on-surface-variant hover:text-on-surface"
                  onClick={() => onSqlTextChange(entry)}
                >
                  {entry.length > 90 ? `${entry.slice(0, 87)}...` : entry}
                </button>
              ))}
              {!history.length && <p className="text-xs text-on-surface-variant">Noch keine Abfragen.</p>}
            </div>
          </div>
        </aside>
      </div>
      )}
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
  if (!result) return <div className="rounded-lg border border-outline-variant p-8 text-center text-sm text-on-surface-variant">Tabelle auswählen.</div>
  if (!result.columns.length) return <p className="text-sm text-on-surface-variant">{result.status || 'Keine Daten.'}</p>
  const allSelected = selectable && result.rows.length > 0 && selectedIndices?.size === result.rows.length
  return (
    <div className="max-h-[58vh] overflow-auto rounded-lg border border-outline-variant">
      <table className="min-w-full text-sm">
        <thead className="sticky top-0 z-10 bg-surface-container-highest text-on-surface">
          <tr>
            {selectable && (
              <th className="px-3 py-2">
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
            <tr key={index} className="bg-surface-container text-on-surface-variant hover:bg-surface-container-high">
              {selectable && (
                <td className="px-3 py-2">
                  <Checkbox checked={selectedIndices?.has(index) ?? false} onCheckedChange={() => onToggleRow?.(index)} />
                </td>
              )}
              {result.columns.map((column) => (
                <td key={column} className="max-w-[420px] px-3 py-2 align-top font-mono text-xs whitespace-pre-wrap break-words">
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
  if (!tableInfo) return <p className="text-sm text-on-surface-variant">Keine Schema-Details geladen.</p>
  return (
    <div className="space-y-4">
      <div>
        <h4 className="mb-2 text-sm font-semibold text-secondary">Spalten ({tableInfo.columns.length})</h4>
        <div className="overflow-hidden rounded-lg border border-outline-variant">
          {tableInfo.columns.map((column) => (
            <div key={column.name} className="grid grid-cols-[1fr_auto] gap-2 border-b border-outline-variant px-3 py-2 last:border-b-0">
              <span className="truncate font-mono text-xs text-on-surface">{column.name}</span>
              <span className="font-mono text-xs text-on-surface-variant">{column.data_type}</span>
            </div>
          ))}
        </div>
      </div>
      <InfoList icon={Boxes} title={`Indizes (${tableInfo.indexes.length})`} items={tableInfo.indexes.map((idx) => idx.name)} empty="Keine Indizes." />
      <InfoList icon={Sparkles} title={`Fremdschlüssel (${tableInfo.foreign_keys.length})`} items={tableInfo.foreign_keys.map((fk) => `${fk.column_name} -> ${fk.foreign_table}.${fk.foreign_column}`)} empty="Keine Fremdschlüssel definiert." />
    </div>
  )
}

function InfoList({ icon: Icon, title, items, empty }: { icon: typeof Boxes; title: string; items: string[]; empty: string }) {
  return (
    <div>
      <h4 className="mb-2 flex items-center gap-2 text-sm font-semibold text-on-surface"><Icon className="h-4 w-4" />{title}</h4>
      {items.length ? (
        <div className="space-y-1">{items.map((item) => <div key={item} className="truncate rounded-md border border-outline-variant px-3 py-2 font-mono text-xs text-on-surface-variant">{item}</div>)}</div>
      ) : <p className="text-xs text-on-surface-variant">{empty}</p>}
    </div>
  )
}

function SqlResult({ result }: { result: PostgresSqlResult | null }) {
  if (!result) return null
  return (
    <div className="mt-4 space-y-3">
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
      className={`msm-btn-secondary px-3 inline-flex items-center gap-2 ${active ? 'ring-1 ring-primary' : ''} ${hasState ? 'text-secondary' : ''}`}
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
    >
      <Icon className="h-4 w-4" />
      {label}
    </button>
  )
}

function DropdownPanel({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div className="absolute left-0 top-full z-50 mt-1 w-72 rounded-lg border border-outline-variant bg-surface-container-highest p-3 shadow-lg">
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
            <label key={column} className="flex items-center gap-2 text-sm text-on-surface">
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
