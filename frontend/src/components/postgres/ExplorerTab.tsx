import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Eye, Hash, Layers, ListOrdered, Plus, Search, Table2, Tags } from 'lucide-react'
import { ActionMenu, Badge, Button, Dropdown, Input } from '@/Singra/UI'
import { prompt } from '@/stores/promptStore'
import { useStudio } from './StudioContext'
import { DataGrid, type GridJump } from './DataGrid'
import { EnumDialog, SequenceDialog, ViewDialog } from './StructureDialogs'
import { TableDesignerDialog } from './TableDesigner'
import { TableStructure } from './TableStructure'
import { Empty, ErrorBox, Loading, Section, formatNumber, useLoad } from './shared'
import { formatBytes, type RowFilter, type StudioRelation } from './studioApi'

type Selection = { kind: 'relation'; name: string } | { kind: 'sequence'; name: string } | { kind: 'enum'; name: string } | null

const RELATION_ICONS = {
  table: Table2,
  partitioned_table: Layers,
  view: Eye,
  materialized_view: Eye,
  foreign_table: Table2,
  sequence: ListOrdered,
}

export function ExplorerTab() {
  const { t, i18n } = useTranslation()
  const { api, overview, canAdmin, revision, runOperation } = useStudio()
  const [schema, setSchema] = useState(() => (overview.schemas.some((s) => s.name === 'public') ? 'public' : overview.schemas[0]?.name || 'public'))
  const [selection, setSelection] = useState<Selection>(null)
  const [filters, setFilters] = useState<RowFilter[] | undefined>(undefined)
  const [search, setSearch] = useState('')
  const [view, setView] = useState<'data' | 'structure'>('data')
  const [dialog, setDialog] = useState<'table' | 'view' | 'sequence' | 'enum' | null>(null)
  const objects = useLoad(() => api.objects(schema), [api, schema, revision])
  const schemas = useLoad(() => api.overview().then((o) => o.schemas), [api, revision])

  const relations = useMemo(() => (objects.data?.relations || []).filter((r) => r.kind !== 'sequence'), [objects.data])
  const enums = (objects.data?.enums || []).map((e) => e.name)

  useEffect(() => {
    if (!objects.data) return
    const exists =
      selection &&
      ((selection.kind === 'relation' && relations.some((r) => r.name === selection.name)) ||
        (selection.kind === 'sequence' && objects.data.sequences.some((s) => s.name === selection.name)) ||
        (selection.kind === 'enum' && objects.data.enums.some((e) => e.name === selection.name)))
    if (!exists) {
      const first = relations.find((r) => !r.is_partition) || relations[0]
      setSelection(first ? { kind: 'relation', name: first.name } : null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [objects.data])

  const openTable = (targetSchema: string, name: string, jumpFilters?: RowFilter[]) => {
    setFilters(jumpFilters)
    setSchema(targetSchema)
    setSelection({ kind: 'relation', name })
    if (jumpFilters) setView('data')
  }

  const createSchema = async () => {
    const name = await prompt({ title: t('postgresStudio.schemas.create'), message: t('postgresStudio.schemas.createMessage') })
    if (name && (await runOperation({ op: 'create_schema', name }, { title: t('postgresStudio.schemas.create') }))) setSchema(name)
  }
  const dropSchema = async () => {
    if (await runOperation({ op: 'drop_schema', name: schema, cascade: false }, { title: t('postgresStudio.schemas.drop', { schema }) })) setSchema('public')
  }

  const groups: { key: string; label: string; items: { name: string; hint: string; kind: string; selection: Selection }[] }[] = [
    {
      key: 'tables',
      label: t('postgresStudio.explorer.tables'),
      items: relations
        .filter((r) => r.kind === 'table' || r.kind === 'partitioned_table' || r.kind === 'foreign_table')
        .map((r) => ({ name: r.name, hint: `~${formatNumber(Math.max(0, r.estimated_rows), i18n.language)}`, kind: r.kind, selection: { kind: 'relation', name: r.name } })),
    },
    {
      key: 'views',
      label: t('postgresStudio.explorer.views'),
      items: relations
        .filter((r) => r.kind === 'view' || r.kind === 'materialized_view')
        .map((r) => ({ name: r.name, hint: r.kind === 'materialized_view' ? 'MV' : '', kind: r.kind, selection: { kind: 'relation', name: r.name } })),
    },
    {
      key: 'sequences',
      label: t('postgresStudio.explorer.sequences'),
      items: (objects.data?.sequences || []).map((s) => ({ name: s.name, hint: String(s.last_value ?? '–'), kind: 'sequence', selection: { kind: 'sequence', name: s.name } })),
    },
    {
      key: 'enums',
      label: t('postgresStudio.explorer.enums'),
      items: (objects.data?.enums || []).map((e) => ({ name: e.name, hint: String(e.values.length), kind: 'enum', selection: { kind: 'enum', name: e.name } })),
    },
  ]

  return (
    <div className="grid gap-4 xl:grid-cols-12">
      <aside className="msm-card flex flex-col gap-3 p-4 xl:col-span-3" data-testid="object-browser">
        <div className="flex items-center gap-2">
          <Dropdown
            value={schema}
            onChange={(value) => {
              setSchema(value)
              setSelection(null)
            }}
            options={(schemas.data || overview.schemas).map((s) => ({ value: s.name, label: s.name, hint: String(s.table_count) }))}
            className="min-w-0 flex-1"
            aria-label={t('postgresStudio.schemas.label')}
          />
          {canAdmin && (
            <ActionMenu
              compact
              label={t('postgresStudio.schemas.actions')}
              items={[
                { key: 'create', label: t('postgresStudio.schemas.create'), onSelect: () => void createSchema() },
                { key: 'drop', label: t('postgresStudio.schemas.drop', { schema }), destructive: true, disabled: schema === 'public', onSelect: () => void dropSchema() },
              ]}
            />
          )}
        </div>
        <Input prefix={<Search className="h-3.5 w-3.5" />} value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t('postgresStudio.explorer.search')} aria-label={t('postgresStudio.explorer.search')} />
        {canAdmin && (
          <ActionMenu
            label={t('postgresStudio.explorer.new')}
            icon={<Plus className="h-4 w-4" />}
            items={[
              { key: 'table', label: t('postgresStudio.designer.title'), onSelect: () => setDialog('table') },
              { key: 'view', label: t('postgresStudio.views.create'), onSelect: () => setDialog('view') },
              { key: 'sequence', label: t('postgresStudio.sequences.create'), onSelect: () => setDialog('sequence') },
              { key: 'enum', label: t('postgresStudio.enums.create'), onSelect: () => setDialog('enum') },
            ]}
          />
        )}
        {objects.loading && !objects.data && <Loading />}
        <ErrorBox message={objects.error} />
        <div className="max-h-[60vh] space-y-4 overflow-y-auto pr-1">
          {groups.map((group) => {
            const items = group.items.filter((item) => item.name.toLowerCase().includes(search.toLowerCase()))
            if (!items.length) return null
            return (
              <div key={group.key}>
                <div className="mb-1.5 flex items-center justify-between text-xs">
                  <span className="font-semibold text-on-surface">{group.label}</span>
                  <span className="rounded-full border border-outline-variant px-2 font-mono text-label-sm text-on-surface-variant">{items.length}</span>
                </div>
                <div className="space-y-0.5">
                  {items.map((item) => {
                    const active = selection?.kind === item.selection?.kind && selection?.name === item.name
                    const Icon = item.kind === 'enum' ? Tags : RELATION_ICONS[item.kind as keyof typeof RELATION_ICONS] || Hash
                    return (
                      <button
                        key={`${group.key}-${item.name}`}
                        type="button"
                        onClick={() => {
                          setFilters(undefined)
                          setSelection(item.selection)
                        }}
                        className={`flex w-full items-center justify-between gap-2 rounded-md border px-2.5 py-1.5 text-left transition ${
                          active ? 'border-secondary bg-secondary/10 text-secondary' : 'border-transparent text-on-surface-variant hover:border-outline-variant hover:bg-surface-container-high'
                        }`}
                      >
                        <span className="flex min-w-0 items-center gap-2">
                          <Icon className="h-3.5 w-3.5 shrink-0" />
                          <span className="truncate font-mono text-xs">{item.name}</span>
                        </span>
                        <span className="shrink-0 font-mono text-label-sm text-on-surface-variant/80">{item.hint}</span>
                      </button>
                    )
                  })}
                </div>
              </div>
            )
          })}
          {objects.data && groups.every((g) => g.items.length === 0) && <Empty>{t('postgresStudio.explorer.emptySchema')}</Empty>}
        </div>
      </aside>

      <main className="min-w-0 xl:col-span-9">
        {selection?.kind === 'relation' && (
          <RelationView
            schema={schema}
            name={selection.name}
            relation={relations.find((r) => r.name === selection.name)}
            relations={relations}
            enums={enums}
            filters={filters}
            view={view}
            onView={setView}
            onOpenTable={(s, n, f) => openTable(s, n, f)}
            onDropped={() => setSelection(null)}
          />
        )}
        {selection?.kind === 'sequence' && objects.data && (
          <SequenceView schema={schema} sequence={objects.data.sequences.find((s) => s.name === selection.name)!} />
        )}
        {selection?.kind === 'enum' && objects.data && <EnumView schema={schema} enumType={objects.data.enums.find((e) => e.name === selection.name)!} />}
        {!selection && objects.data && (
          <div className="msm-card p-8 text-center">
            <p className="text-sm text-on-surface-variant">{t('postgresStudio.explorer.nothingSelected')}</p>
            {canAdmin && (
              <Button className="mt-4" onClick={() => setDialog('table')}>
                <Plus className="h-4 w-4" />
                {t('postgresStudio.designer.title')}
              </Button>
            )}
          </div>
        )}
      </main>

      <TableDesignerDialog open={dialog === 'table'} onClose={() => setDialog(null)} schema={schema} relations={relations} enums={enums} />
      <ViewDialog open={dialog === 'view'} onClose={() => setDialog(null)} schema={schema} />
      <SequenceDialog open={dialog === 'sequence'} onClose={() => setDialog(null)} schema={schema} />
      <EnumDialog open={dialog === 'enum'} onClose={() => setDialog(null)} schema={schema} />
    </div>
  )
}

function RelationView({ schema, name, relation, relations, enums, filters, view, onView, onOpenTable, onDropped }: {
  schema: string
  name: string
  relation?: StudioRelation
  relations: StudioRelation[]
  enums: string[]
  filters?: RowFilter[]
  view: 'data' | 'structure'
  onView: (view: 'data' | 'structure') => void
  onOpenTable: (schema: string, name: string, filters?: RowFilter[]) => void
  onDropped: () => void
}) {
  const { t, i18n } = useTranslation()
  const { api, revision, canAdmin, canWrite, runOperation } = useStudio()
  const details = useLoad(() => api.table(schema, name), [api, schema, name, revision])
  const table = details.data

  const drop = async () => {
    if (!table) return
    const isView = table.kind === 'v' || table.kind === 'm'
    const operation = isView
      ? { op: 'drop_view', schema_name: schema, name, materialized: table.kind === 'm' }
      : { op: 'drop_table', schema_name: schema, name }
    const typed = await prompt({
      title: t('postgresStudio.explorer.dropTitle', { name }),
      message: t('postgresStudio.explorer.dropMessage', { name }),
      expectedValue: name,
      confirmText: t('common.delete'),
      danger: true,
    })
    if (typed && (await runOperation(operation, { title: t('postgresStudio.explorer.dropTitle', { name }) }))) onDropped()
  }

  const menu = table
    ? [
        ...(table.kind === 'm' && canWrite
          ? [{ key: 'refresh', label: t('postgresStudio.views.refresh'), onSelect: () => void runOperation({ op: 'refresh_materialized_view', schema_name: schema, name }, { title: t('postgresStudio.views.refresh') }) }]
          : []),
        ...((table.kind === 'r' || table.kind === 'm') && canWrite
          ? [
              { key: 'vacuum', label: t('postgresStudio.maintenance.vacuumAnalyze'), onSelect: () => void runOperation({ op: 'vacuum', schema_name: schema, table: name, analyze: true }, { title: t('postgresStudio.maintenance.vacuumAnalyze') }) },
              { key: 'analyze', label: t('postgresStudio.maintenance.analyze'), onSelect: () => void runOperation({ op: 'analyze', schema_name: schema, table: name }, { title: t('postgresStudio.maintenance.analyze') }) },
              { key: 'reindex', label: t('postgresStudio.maintenance.reindex'), onSelect: () => void runOperation({ op: 'reindex', target: 'table', schema_name: schema, name, concurrently: true }, { title: t('postgresStudio.maintenance.reindex') }) },
            ]
          : []),
        ...(canAdmin ? [{ key: 'drop', label: t('postgresStudio.explorer.drop'), destructive: true, separatorBefore: true, onSelect: () => void drop() }] : []),
      ]
    : []

  return (
    <div className="space-y-3">
      <div className="msm-card flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="min-w-0">
          <h3 className="font-headline text-lg font-semibold text-on-surface">
            <span className="text-on-surface-variant">{schema}.</span>
            <span className="font-mono text-secondary">{name}</span>
          </h3>
          <p className="text-xs text-on-surface-variant">
            {relation && t(`postgresStudio.kinds.${relation.kind}`)}
            {table?.size_bytes != null && ` · ${formatBytes(table.size_bytes, i18n.language)}`}
            {table && ` · ~${formatNumber(Math.max(0, table.estimated_rows), i18n.language)} ${t('postgresStudio.explorer.rowsShort')}`}
            {table?.rls_enabled && (
              <Badge variant="info" className="ml-2">
                RLS
              </Badge>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="inline-flex rounded-lg border border-outline-variant bg-surface-container-high p-0.5" role="tablist">
            {(['data', 'structure'] as const).map((key) => (
              <Button key={key} size="sm" variant={view === key ? 'primary' : 'ghost'} role="tab" aria-selected={view === key} onClick={() => onView(key)}>
                {t(`postgresStudio.explorer.${key}`)}
              </Button>
            ))}
          </div>
          {menu.length > 0 && <ActionMenu label={t('postgresStudio.explorer.actions')} items={menu} />}
        </div>
      </div>
      <ErrorBox message={details.error} />
      {!table && details.loading && <Loading />}
      {table && view === 'data' && (
        <Section title={t('postgresStudio.explorer.data')}>
          <DataGrid table={table} initialFilters={filters} onJump={(jump: GridJump) => onOpenTable(jump.schema, jump.table, jump.filters)} />
        </Section>
      )}
      {table && view === 'structure' && <TableStructure table={table} relations={relations} enums={enums} onOpenTable={(s, n) => onOpenTable(s, n)} />}
    </div>
  )
}

function SequenceView({ schema, sequence }: { schema: string; sequence: { name: string; last_value: number | null; start_value: number; increment_by: number; min_value: number; max_value: number; cycle: boolean } }) {
  const { t, i18n } = useTranslation()
  const { runOperation, canAdmin } = useStudio()
  if (!sequence) return null
  const restart = async () => {
    const value = await prompt({ title: t('postgresStudio.sequences.restart'), message: t('postgresStudio.sequences.restartMessage'), defaultValue: String(sequence.start_value) })
    if (value !== null) {
      const number = value.trim() === '' ? null : Number(value)
      if (number !== null && !Number.isInteger(number)) return
      await runOperation({ op: 'restart_sequence', schema_name: schema, name: sequence.name, value: number }, { title: t('postgresStudio.sequences.restart') })
    }
  }
  const rows: [string, string][] = [
    [t('postgresStudio.sequences.lastValue'), formatNumber(sequence.last_value, i18n.language)],
    [t('postgresStudio.sequences.start'), formatNumber(sequence.start_value, i18n.language)],
    [t('postgresStudio.sequences.increment'), formatNumber(sequence.increment_by, i18n.language)],
    [t('postgresStudio.sequences.range'), `${sequence.min_value} … ${sequence.max_value}`],
    [t('postgresStudio.sequences.cycle'), sequence.cycle ? '✓' : '–'],
  ]
  return (
    <Section
      title={<span className="font-mono">{sequence.name}</span>}
      actions={
        canAdmin ? (
          <>
            <Button size="sm" variant="secondary" onClick={() => void restart()}>{t('postgresStudio.sequences.restart')}</Button>
            <Button size="sm" variant="destructive" onClick={() => void runOperation({ op: 'drop_sequence', schema_name: schema, name: sequence.name }, { title: t('postgresStudio.sequences.drop') })}>
              {t('common.delete')}
            </Button>
          </>
        ) : undefined
      }
    >
      <dl className="grid gap-2 sm:grid-cols-2">
        {rows.map(([label, value]) => (
          <div key={label} className="rounded-lg border border-outline-variant p-3">
            <dt className="text-xs text-on-surface-variant">{label}</dt>
            <dd className="font-mono text-sm text-on-surface">{value}</dd>
          </div>
        ))}
      </dl>
    </Section>
  )
}

function EnumView({ schema, enumType }: { schema: string; enumType: { name: string; values: string[] } }) {
  const { t } = useTranslation()
  const { runOperation, canAdmin } = useStudio()
  if (!enumType) return null
  const add = async () => {
    const value = await prompt({ title: t('postgresStudio.enums.addValue'), message: t('postgresStudio.enums.addValueMessage') })
    if (value) await runOperation({ op: 'add_enum_value', schema_name: schema, name: enumType.name, value }, { title: t('postgresStudio.enums.addValue') })
  }
  const rename = async (value: string) => {
    const next = await prompt({ title: t('postgresStudio.enums.renameValue'), message: t('postgresStudio.enums.renameValueMessage', { value }), defaultValue: value })
    if (next && next !== value) await runOperation({ op: 'rename_enum_value', schema_name: schema, name: enumType.name, value, new_value: next }, { title: t('postgresStudio.enums.renameValue') })
  }
  return (
    <Section
      title={<span className="font-mono">{enumType.name}</span>}
      actions={
        canAdmin ? (
          <>
            <Button size="sm" variant="secondary" onClick={() => void add()}>
              <Plus className="h-3.5 w-3.5" />
              {t('postgresStudio.enums.addValue')}
            </Button>
            <Button size="sm" variant="destructive" onClick={() => void runOperation({ op: 'drop_type', schema_name: schema, name: enumType.name }, { title: t('postgresStudio.enums.drop') })}>
              {t('common.delete')}
            </Button>
          </>
        ) : undefined
      }
    >
      <p className="mb-3 text-xs text-on-surface-variant">{t('postgresStudio.enums.orderHint')}</p>
      <ol className="space-y-1">
        {enumType.values.map((value, index) => (
          <li key={value} className="flex items-center justify-between rounded-md border border-outline-variant px-3 py-1.5">
            <span className="font-mono text-xs text-on-surface">
              <span className="mr-2 text-on-surface-variant">{index + 1}.</span>
              {value}
            </span>
            {canAdmin && (
              <Button size="sm" variant="ghost" onClick={() => void rename(value)}>
                {t('postgresStudio.enums.renameValue')}
              </Button>
            )}
          </li>
        ))}
      </ol>
    </Section>
  )
}
