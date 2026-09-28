import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowDown, ArrowUp, Copy, Filter, Link2, Plus, RefreshCw, Trash2, Upload, X } from 'lucide-react'
import { ActionMenu, Badge, Button, Checkbox, Dropdown, Input, Pagination } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'
import { useStudio } from './StudioContext'
import { ImportWizard } from './ImportWizard'
import { JsonEditorDialog, RowDialog } from './RowDialogs'
import { ErrorBox, Loading } from './shared'
import {
  CTID_KEY,
  cellText,
  saveDownload,
  type FilterOperator,
  type Json,
  type RowFilter,
  type RowSort,
  type StudioRowKey,
  type StudioRows,
  type StudioTableDetails,
} from './studioApi'

const PAGE_SIZES = [25, 50, 100, 500] as const
type PageSize = (typeof PAGE_SIZES)[number]
const OPERATORS: FilterOperator[] = ['eq', 'neq', 'lt', 'lte', 'gt', 'gte', 'ilike', 'like', 'not_like', 'is_null', 'not_null', 'in', 'contains']
const NO_VALUE: FilterOperator[] = ['is_null', 'not_null']

export interface GridJump {
  schema: string
  table: string
  filters: RowFilter[]
}

export function DataGrid({ table, initialFilters, onJump }: {
  table: StudioTableDetails
  initialFilters?: RowFilter[]
  onJump: (jump: GridJump) => void
}) {
  const { t, i18n } = useTranslation()
  const { api, canWrite, revision } = useStudio()
  const [filters, setFilters] = useState<RowFilter[]>(initialFilters || [])
  const [draft, setDraft] = useState<RowFilter>({ column: table.columns[0]?.name || '', operator: 'eq', value: '' })
  const [sort, setSort] = useState<RowSort[]>([])
  const [pageSize, setPageSize] = useState<PageSize>(50)
  const [page, setPage] = useState(1)
  const [data, setData] = useState<StudioRows | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [editing, setEditing] = useState<{ row: number; column: string; value: string } | null>(null)
  const [jsonEdit, setJsonEdit] = useState<{ row: number; column: string; value: Json } | null>(null)
  const [rowDialog, setRowDialog] = useState<{ mode: 'insert' | 'duplicate'; values: Record<string, Json> } | null>(null)
  const [importOpen, setImportOpen] = useState(false)

  useEffect(() => {
    setFilters(initialFilters || [])
    setSort([])
    setPage(1)
    setDraft({ column: table.columns[0]?.name || '', operator: 'eq', value: '' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [table.schema, table.table, initialFilters])

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await api.rows({ schema_name: table.schema, table: table.table, filters, sort, limit: pageSize, offset: (page - 1) * pageSize })
      setData(result)
      setSelected(new Set())
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, table.schema, table.table, filters, sort, pageSize, page, revision])

  const fkByColumn = useMemo(() => {
    const map = new Map<string, { schema: string; table: string; column: string }>()
    for (const c of table.constraints) {
      if (c.type === 'foreign_key' && c.columns.length === 1 && c.ref_table) {
        map.set(c.columns[0], { schema: c.ref_schema || table.schema, table: c.ref_table, column: c.ref_columns[0] })
      }
    }
    return map
  }, [table])

  const columnMeta = useMemo(() => new Map(table.columns.map((c) => [c.name, c])), [table.columns])
  const editable = Boolean(data?.editable && canWrite)
  const visibleColumns = (data?.columns || []).filter((c) => c !== CTID_KEY)
  const pageCount = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1

  const keyOf = (row: Json[]): StudioRowKey => {
    const values: Record<string, Json> = {}
    for (const name of data!.key) values[name] = row[data!.columns.indexOf(name)]
    return { values }
  }
  const valueOf = (row: Json[], column: string) => row[data!.columns.indexOf(column)]
  const isJson = (column: string) => /^jsonb?$/.test(columnMeta.get(column)?.type || '')

  const toggleSort = (column: string) => {
    setSort((current) => {
      const existing = current.find((s) => s.column === column)
      if (!existing) return [{ column, descending: false }]
      if (!existing.descending) return [{ column, descending: true }]
      return []
    })
  }

  const addFilter = () => {
    if (!draft.column) return
    const value = NO_VALUE.includes(draft.operator)
      ? undefined
      : draft.operator === 'in'
        ? String(draft.value ?? '').split(',').map((v) => v.trim())
        : draft.operator === 'contains'
          ? safeJson(String(draft.value ?? ''))
          : draft.value
    setFilters((list) => [...list, { ...draft, value }])
    setPage(1)
    setDraft((d) => ({ ...d, value: '' }))
  }

  const saveCell = async (rowIndex: number, column: string, value: Json) => {
    if (!data) return
    try {
      const result = await api.updateRow(table.schema, table.table, keyOf(data.rows[rowIndex]), { [column]: value })
      if (result.row) {
        const updated = data.columns.map((name) => result.row![result.columns.indexOf(name)] ?? null)
        setData({ ...data, rows: data.rows.map((r, i) => (i === rowIndex ? updated : r)) })
      }
      toast.success(t('postgresStudio.data.saved'))
      setEditing(null)
      setJsonEdit(null)
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  const deleteSelected = async () => {
    if (!data || !selected.size) return
    const ok = await confirm({
      title: t('postgresStudio.data.deleteTitle'),
      message: t('postgresStudio.data.deleteMessage', { count: selected.size, table: table.table }),
      confirmText: t('common.delete'),
      danger: true,
    })
    if (!ok) return
    try {
      const result = await api.deleteRows(table.schema, table.table, [...selected].map((i) => keyOf(data.rows[i])))
      toast.success(t('postgresStudio.data.deleted', { count: result.deleted }))
      await load()
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  const duplicate = () => {
    if (!data || selected.size !== 1) return
    const row = data.rows[[...selected][0]]
    const values: Record<string, Json> = {}
    for (const column of table.columns) {
      // Primärschlüssel, Identity und generierte Spalten vergibt die Datenbank neu.
      if (column.identity || column.generated || table.primary_key.includes(column.name)) continue
      values[column.name] = valueOf(row, column.name)
    }
    setRowDialog({ mode: 'duplicate', values })
  }

  const exportAs = async (format: 'csv' | 'json' | 'sql') => {
    try {
      const file = await api.exportRows({ schema_name: table.schema, table: table.table, format, filters, sort })
      saveDownload(file)
      if (file.headers.get('X-MSM-Export-Truncated') === '1') toast.warning(t('postgresStudio.data.exportTruncated'))
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-end gap-2">
        <Dropdown
          value={draft.column || null}
          onChange={(column) => setDraft((d) => ({ ...d, column }))}
          options={table.columns.map((c) => ({ value: c.name, label: c.name, hint: c.type }))}
          searchable
          className="w-44"
          aria-label={t('postgresStudio.data.filterColumn')}
        />
        <Dropdown
          value={draft.operator}
          onChange={(operator) => setDraft((d) => ({ ...d, operator: operator as FilterOperator }))}
          options={OPERATORS.map((op) => ({ value: op, label: t(`postgresStudio.data.operators.${op}`) }))}
          className="w-40"
          aria-label={t('postgresStudio.data.filterOperator')}
        />
        {!NO_VALUE.includes(draft.operator) && (
          <div className="w-52">
            <Input
              value={String(draft.value ?? '')}
              onChange={(e) => setDraft((d) => ({ ...d, value: e.target.value }))}
              onKeyDown={(e) => e.key === 'Enter' && addFilter()}
              placeholder={draft.operator === 'in' ? 'a, b, c' : draft.operator === 'contains' ? '{"key": "value"}' : t('postgresStudio.data.filterValue')}
              aria-label={t('postgresStudio.data.filterValue')}
              className="h-10 text-xs"
            />
          </div>
        )}
        <Button variant="secondary" onClick={addFilter} data-testid="add-filter">
          <Filter className="h-4 w-4" />
          {t('postgresStudio.data.addFilter')}
        </Button>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Button variant="ghost" size="icon" onClick={() => void load()} aria-label={t('common.refresh')}>
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>
          {editable && selected.size === 1 && (
            <Button variant="secondary" onClick={duplicate}>
              <Copy className="h-4 w-4" />
              {t('postgresStudio.data.duplicate')}
            </Button>
          )}
          {editable && selected.size > 0 && (
            <Button variant="destructive" onClick={() => void deleteSelected()} data-testid="delete-rows">
              <Trash2 className="h-4 w-4" />
              {t('postgresStudio.data.deleteSelected', { count: selected.size })}
            </Button>
          )}
          {canWrite && table.kind !== 'v' && table.kind !== 'm' && (
            <>
              <Button onClick={() => setRowDialog({ mode: 'insert', values: {} })} data-testid="insert-row">
                <Plus className="h-4 w-4" />
                {t('postgresStudio.data.insert')}
              </Button>
              <Button variant="secondary" onClick={() => setImportOpen(true)}>
                <Upload className="h-4 w-4" />
                {t('postgresStudio.data.import')}
              </Button>
            </>
          )}
          <ActionMenu
            label={t('postgresStudio.data.export')}
            items={(['csv', 'json', 'sql'] as const).map((format) => ({ key: format, label: format.toUpperCase(), onSelect: () => void exportAs(format) }))}
          />
        </div>
      </div>

      {filters.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {filters.map((filter, index) => (
            <span key={`${filter.column}-${index}`} className="inline-flex items-center gap-1 rounded-full border border-secondary/40 bg-secondary/10 px-3 py-1 font-mono text-xs text-secondary">
              {filter.column} {t(`postgresStudio.data.operators.${filter.operator}`)} {NO_VALUE.includes(filter.operator) ? '' : cellText(filter.value as Json)}
              <button type="button" aria-label={t('common.remove')} onClick={() => setFilters((list) => list.filter((_, i) => i !== index))} className="ml-1 hover:text-on-surface">
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
          <Button size="sm" variant="ghost" onClick={() => setFilters([])}>{t('common.clear')}</Button>
        </div>
      )}

      {!data?.editable && data && canWrite && table.kind !== 'v' && table.kind !== 'm' && (
        <p className="text-xs text-status-warning">{t('postgresStudio.data.readOnlyNoKey')}</p>
      )}
      {data?.key[0] === CTID_KEY && editable && <p className="text-xs text-on-surface-variant">{t('postgresStudio.data.ctidHint')}</p>}
      <ErrorBox message={error} />
      {!data && loading && <Loading />}

      {data && (
        <div className="min-h-0 overflow-auto rounded-lg border border-outline-variant">
          <table className="w-full text-left text-xs" data-testid="data-grid">
            <thead className="sticky top-0 bg-surface-container-high text-on-surface-variant">
              <tr>
                {editable && (
                  <th className="w-8 px-2 py-2">
                    <Checkbox
                      checked={data.rows.length > 0 && selected.size === data.rows.length}
                      onCheckedChange={(all) => setSelected(all ? new Set(data.rows.map((_, i) => i)) : new Set())}
                      aria-label={t('postgresStudio.data.selectAll')}
                    />
                  </th>
                )}
                {visibleColumns.map((column) => {
                  const current = sort.find((s) => s.column === column)
                  return (
                    <th key={column} className="whitespace-nowrap px-3 py-2 font-medium">
                      <button type="button" className="inline-flex items-center gap-1 hover:text-on-surface" onClick={() => toggleSort(column)}>
                        <span className="font-mono text-on-surface">{column}</span>
                        <span className="text-label-sm">{columnMeta.get(column)?.type}</span>
                        {current && (current.descending ? <ArrowDown className="h-3 w-3" /> : <ArrowUp className="h-3 w-3" />)}
                      </button>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, rowIndex) => (
                <tr key={rowIndex} className={`border-t border-outline-variant/50 ${selected.has(rowIndex) ? 'bg-secondary/10' : 'hover:bg-surface-container-high/50'}`}>
                  {editable && (
                    <td className="px-2 py-1.5">
                      <Checkbox
                        checked={selected.has(rowIndex)}
                        onCheckedChange={(checked) =>
                          setSelected((set) => {
                            const next = new Set(set)
                            if (checked) next.add(rowIndex)
                            else next.delete(rowIndex)
                            return next
                          })
                        }
                        aria-label={t('postgresStudio.data.selectRow', { row: rowIndex + 1 })}
                      />
                    </td>
                  )}
                  {visibleColumns.map((column) => {
                    const value = valueOf(row, column)
                    const fk = fkByColumn.get(column)
                    const isEditing = editing?.row === rowIndex && editing.column === column
                    return (
                      <td
                        key={column}
                        className="max-w-72 px-3 py-1.5 align-top font-mono"
                        onDoubleClick={() => {
                          if (!editable || columnMeta.get(column)?.generated) return
                          if (isJson(column)) setJsonEdit({ row: rowIndex, column, value })
                          else setEditing({ row: rowIndex, column, value: value === null ? '' : cellText(value) })
                        }}
                      >
                        {isEditing ? (
                          <div className="flex items-center gap-1">
                            <Input
                              autoFocus
                              value={editing.value}
                              onChange={(e) => setEditing({ ...editing, value: e.target.value })}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') void saveCell(rowIndex, column, editing.value)
                                if (e.key === 'Escape') setEditing(null)
                              }}
                              aria-label={column}
                              className="h-8 min-w-40 text-xs"
                            />
                            <Button size="sm" variant="ghost" onClick={() => void saveCell(rowIndex, column, null)}>NULL</Button>
                          </div>
                        ) : (
                          <span className="inline-flex max-w-full items-center gap-1">
                            <span className={`truncate ${value === null ? 'italic text-on-surface-variant/60' : 'text-on-surface'}`}>{cellText(value)}</span>
                            {fk && value !== null && (
                              <button
                                type="button"
                                className="shrink-0 text-secondary hover:text-on-surface"
                                aria-label={t('postgresStudio.data.jump', { table: fk.table })}
                                onClick={() => onJump({ schema: fk.schema, table: fk.table, filters: [{ column: fk.column, operator: 'eq', value }] })}
                              >
                                <Link2 className="h-3 w-3" />
                              </button>
                            )}
                          </span>
                        )}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          {data.rows.length === 0 && <p className="p-6 text-center text-sm italic text-on-surface-variant">{t('postgresStudio.data.empty')}</p>}
        </div>
      )}

      {data && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-xs text-on-surface-variant">
            <Dropdown
              value={String(pageSize)}
              onChange={(value) => {
                setPageSize(Number(value) as PageSize)
                setPage(1)
              }}
              options={PAGE_SIZES.map((size) => ({ value: String(size), label: t('postgresStudio.data.perPage', { count: size }) }))}
              className="w-40"
              aria-label={t('postgresStudio.data.pageSize')}
            />
            {data.total_is_estimate && <Badge variant="warning">{t('postgresStudio.data.estimate')}</Badge>}
            {editable && <span>{t('postgresStudio.data.editHint')}</span>}
          </div>
          <Pagination
            page={page}
            pageCount={pageCount}
            label={t('postgresStudio.data.rows', { count: data.total, formatted: data.total.toLocaleString(i18n.language) })}
            disabled={loading}
            onChange={setPage}
          />
        </div>
      )}

      {jsonEdit && (
        <JsonEditorDialog
          column={jsonEdit.column}
          value={jsonEdit.value}
          onClose={() => setJsonEdit(null)}
          onSave={(value) => void saveCell(jsonEdit.row, jsonEdit.column, value)}
        />
      )}
      {rowDialog && (
        <RowDialog
          table={table}
          mode={rowDialog.mode}
          initial={rowDialog.values}
          onClose={() => setRowDialog(null)}
          onSaved={() => {
            setRowDialog(null)
            void load()
          }}
        />
      )}
      <ImportWizard open={importOpen} table={table} onClose={() => setImportOpen(false)} onImported={() => void load()} />
    </div>
  )
}

function safeJson(text: string): Json {
  try {
    return JSON.parse(text) as Json
  } catch {
    return text
  }
}
