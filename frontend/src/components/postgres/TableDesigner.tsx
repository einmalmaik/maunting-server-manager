import { useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2 } from 'lucide-react'
import {
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Dropdown,
  Input,
  MultiSelect,
} from '@/Singra/UI'
import { useStudio } from './StudioContext'
import { checkNames, ErrorBox, Field, TypePicker, useFkActionOptions } from './shared'
import type { ColumnSpec, FkAction, ForeignKeySpec, StudioRelation } from './studioApi'

interface DesignColumn extends ColumnSpec {
  key: number
  primary: boolean
}

interface DesignFk extends ForeignKeySpec {
  key: number
}

let nextKey = 1
const newColumn = (partial: Partial<DesignColumn> = {}): DesignColumn => ({
  key: nextKey++,
  name: '',
  type: 'text',
  nullable: true,
  default: '',
  identity: null,
  primary: false,
  ...partial,
})

export function TableDesignerDialog({ open, onClose, schema, relations, enums }: {
  open: boolean
  onClose: () => void
  schema: string
  relations: StudioRelation[]
  enums: string[]
}) {
  const { t } = useTranslation()
  const { runOperation } = useStudio()
  const [name, setName] = useState('')
  const [comment, setComment] = useState('')
  const [columns, setColumns] = useState<DesignColumn[]>([])
  const [uniques, setUniques] = useState<{ key: number; columns: string[] }[]>([])
  const [checks, setChecks] = useState<{ key: number; name: string; expression: string }[]>([])
  const [fks, setFks] = useState<DesignFk[]>([])
  const [partition, setPartition] = useState<{ strategy: '' | 'range' | 'list' | 'hash'; columns: string[] }>({ strategy: '', columns: [] })
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setName('')
    setComment('')
    setColumns([
      newColumn({ name: 'id', type: 'bigint', nullable: false, identity: 'always', primary: true }),
      newColumn({ name: 'created_at', type: 'timestamp with time zone', nullable: false, default: 'now()' }),
    ])
    setUniques([])
    setChecks([])
    setFks([])
    setPartition({ strategy: '', columns: [] })
    setError(null)
  }, [open])

  const columnNames = columns.map((c) => c.name).filter(Boolean)
  const columnOptions = columnNames.map((value) => ({ value, label: value }))
  const updateColumn = (key: number, patch: Partial<DesignColumn>) =>
    setColumns((list) => list.map((c) => (c.key === key ? { ...c, ...patch } : c)))

  const submit = async () => {
    const problem = (!name.trim() && t('postgresStudio.validation.emptyName')) || checkNames(columns.map((c) => c.name), t)
    if (problem) {
      setError(problem)
      return
    }
    setError(null)
    const primary = columns.filter((c) => c.primary).map((c) => c.name)
    const operation = {
      op: 'create_table',
      schema_name: schema,
      name: name.trim(),
      comment: comment.trim() || null,
      columns: columns.map(({ key: _k, primary: isPrimary, ...c }) => ({
        ...c,
        default: c.identity ? null : c.default?.trim() || null,
        nullable: isPrimary ? false : c.nullable,
      })),
      primary_key: primary.length ? { columns: primary } : null,
      uniques: uniques.filter((u) => u.columns.length).map((u) => ({ columns: u.columns })),
      checks: checks.filter((c) => c.expression.trim()).map((c) => ({ name: c.name.trim() || null, expression: c.expression })),
      foreign_keys: fks.map(({ key: _k, ...fk }) => fk),
      partition_by: partition.strategy && partition.columns.length ? partition : null,
    }
    if (await runOperation(operation, { title: t('postgresStudio.designer.title'), success: t('postgresStudio.designer.created', { name }) })) {
      onClose()
    }
  }

  return (
    <Dialog open={open} onOpenChange={(value) => !value && onClose()}>
      <DialogContent className="max-w-5xl" data-testid="table-designer">
        <DialogHeader>
          <DialogTitle>{t('postgresStudio.designer.title')}</DialogTitle>
          <DialogDescription>{t('postgresStudio.designer.description', { schema })}</DialogDescription>
        </DialogHeader>
        <div className="max-h-[65vh] space-y-5 overflow-y-auto p-6">
          <div className="grid gap-4 md:grid-cols-2">
            <Input label={t('postgresStudio.designer.name')} value={name} onChange={(e) => setName(e.target.value)} placeholder="orders" autoFocus />
            <Input label={t('postgresStudio.designer.comment')} value={comment} onChange={(e) => setComment(e.target.value)} />
          </div>

          <div>
            <div className="mb-2 flex items-center justify-between">
              <h4 className="text-sm font-semibold text-on-surface">{t('postgresStudio.designer.columns')}</h4>
              <Button size="sm" variant="secondary" onClick={() => setColumns((list) => [...list, newColumn()])}>
                <Plus className="h-3.5 w-3.5" />
                {t('postgresStudio.designer.addColumn')}
              </Button>
            </div>
            <div className="space-y-2">
              <div className="hidden grid-cols-[1.2fr_1.6fr_1.2fr_1fr_auto_auto_auto] gap-2 px-1 text-xs text-on-surface-variant lg:grid">
                <span>{t('postgresStudio.columns.name')}</span>
                <span>{t('postgresStudio.columns.type')}</span>
                <span>{t('postgresStudio.columns.default')}</span>
                <span>{t('postgresStudio.columns.identity')}</span>
                <span>{t('postgresStudio.columns.primary')}</span>
                <span>{t('postgresStudio.columns.notNull')}</span>
                <span />
              </div>
              {columns.map((column) => (
                <div key={column.key} className="grid items-center gap-2 rounded-lg border border-outline-variant p-2 lg:grid-cols-[1.2fr_1.6fr_1.2fr_1fr_auto_auto_auto] lg:border-0 lg:p-0">
                  <Input value={column.name} onChange={(e) => updateColumn(column.key, { name: e.target.value })} aria-label={t('postgresStudio.columns.name')} className="font-mono text-xs" />
                  <TypePicker value={column.type} onChange={(type) => updateColumn(column.key, { type })} enums={enums} />
                  <Input
                    value={column.default || ''}
                    onChange={(e) => updateColumn(column.key, { default: e.target.value })}
                    disabled={Boolean(column.identity)}
                    placeholder="now()"
                    aria-label={t('postgresStudio.columns.default')}
                    className="font-mono text-xs"
                  />
                  <Dropdown
                    value={column.identity || 'none'}
                    onChange={(value) => updateColumn(column.key, { identity: value === 'none' ? null : (value as 'always' | 'by_default') })}
                    options={[
                      { value: 'none', label: t('postgresStudio.columns.identityNone') },
                      { value: 'always', label: t('postgresStudio.columns.identityAlways') },
                      { value: 'by_default', label: t('postgresStudio.columns.identityByDefault') },
                    ]}
                    aria-label={t('postgresStudio.columns.identity')}
                  />
                  <Checkbox checked={column.primary} onCheckedChange={(primary) => updateColumn(column.key, { primary })} aria-label={t('postgresStudio.columns.primary')} />
                  <Checkbox
                    checked={column.primary || !column.nullable}
                    disabled={column.primary}
                    onCheckedChange={(checked) => updateColumn(column.key, { nullable: !checked })}
                    aria-label={t('postgresStudio.columns.notNull')}
                  />
                  <Button size="icon" variant="ghost" onClick={() => setColumns((list) => list.filter((c) => c.key !== column.key))} aria-label={t('common.remove')} disabled={columns.length === 1}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
          </div>

          <ListBlock title={t('postgresStudio.designer.uniques')} onAdd={() => setUniques((list) => [...list, { key: nextKey++, columns: [] }])}>
            {uniques.map((unique) => (
              <div key={unique.key} className="flex items-center gap-2">
                <MultiSelect
                  options={columnOptions}
                  values={unique.columns}
                  onChange={(values) => setUniques((list) => list.map((u) => (u.key === unique.key ? { ...u, columns: values } : u)))}
                  placeholder={t('postgresStudio.designer.pickColumns')}
                  aria-label={t('postgresStudio.designer.uniques')}
                  className="flex-1"
                />
                <RemoveButton onClick={() => setUniques((list) => list.filter((u) => u.key !== unique.key))} />
              </div>
            ))}
          </ListBlock>

          <ListBlock title={t('postgresStudio.designer.checks')} onAdd={() => setChecks((list) => [...list, { key: nextKey++, name: '', expression: '' }])}>
            {checks.map((check) => (
              <div key={check.key} className="grid gap-2 md:grid-cols-[1fr_2fr_auto]">
                <Input value={check.name} placeholder={t('postgresStudio.designer.constraintName')} onChange={(e) => setChecks((list) => list.map((c) => (c.key === check.key ? { ...c, name: e.target.value } : c)))} aria-label={t('postgresStudio.designer.constraintName')} />
                <Input value={check.expression} placeholder="price >= 0" className="font-mono text-xs" onChange={(e) => setChecks((list) => list.map((c) => (c.key === check.key ? { ...c, expression: e.target.value } : c)))} aria-label={t('postgresStudio.designer.expression')} />
                <RemoveButton onClick={() => setChecks((list) => list.filter((c) => c.key !== check.key))} />
              </div>
            ))}
          </ListBlock>

          <ListBlock
            title={t('postgresStudio.designer.foreignKeys')}
            onAdd={() => setFks((list) => [...list, { key: nextKey++, columns: [], ref_schema: schema, ref_table: '', ref_columns: [], on_delete: 'no_action', on_update: 'no_action' }])}
          >
            {fks.map((fk) => (
              <ForeignKeyEditor
                key={fk.key}
                value={fk}
                columns={columnNames}
                relations={relations}
                onChange={(next) => setFks((list) => list.map((f) => (f.key === fk.key ? { ...next, key: fk.key } : f)))}
                onRemove={() => setFks((list) => list.filter((f) => f.key !== fk.key))}
              />
            ))}
          </ListBlock>

          <div className="grid gap-3 md:grid-cols-2">
            <Field label={t('postgresStudio.designer.partitionBy')} hint={t('postgresStudio.designer.partitionHint')}>
              <Dropdown
                value={partition.strategy || 'none'}
                onChange={(value) => setPartition((p) => ({ ...p, strategy: value === 'none' ? '' : (value as 'range') }))}
                options={[
                  { value: 'none', label: t('postgresStudio.designer.noPartitioning') },
                  { value: 'range', label: 'RANGE' },
                  { value: 'list', label: 'LIST' },
                  { value: 'hash', label: 'HASH' },
                ]}
                aria-label={t('postgresStudio.designer.partitionBy')}
              />
            </Field>
            {partition.strategy && (
              <Field label={t('postgresStudio.designer.partitionColumns')}>
                <MultiSelect options={columnOptions} values={partition.columns} onChange={(values) => setPartition((p) => ({ ...p, columns: values }))} placeholder={t('postgresStudio.designer.pickColumns')} aria-label={t('postgresStudio.designer.partitionColumns')} />
              </Field>
            )}
          </div>
          <ErrorBox message={error} />
        </div>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>{t('common.cancel')}</Button>
          <Button onClick={() => void submit()} data-testid="table-designer-submit">{t('postgresStudio.preview.show')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ListBlock({ title, onAdd, children }: { title: string; onAdd: () => void; children: ReactNode }) {
  const { t } = useTranslation()
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <h4 className="text-sm font-semibold text-on-surface">{title}</h4>
        <Button size="sm" variant="ghost" onClick={onAdd}>
          <Plus className="h-3.5 w-3.5" />
          {t('common.add')}
        </Button>
      </div>
      <div className="space-y-2">{children}</div>
    </div>
  )
}

function RemoveButton({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation()
  return (
    <Button size="icon" variant="ghost" onClick={onClick} aria-label={t('common.remove')}>
      <Trash2 className="h-4 w-4" />
    </Button>
  )
}

/** Fremdschlüssel: eigene Spalten → Zieltabelle/-spalten, ON DELETE/UPDATE. */
export function ForeignKeyEditor({ value, columns, relations, onChange, onRemove }: {
  value: ForeignKeySpec
  columns: string[]
  relations: StudioRelation[]
  onChange: (value: ForeignKeySpec) => void
  onRemove?: () => void
}) {
  const { t } = useTranslation()
  const { api } = useStudio()
  const actions = useFkActionOptions()
  const [refColumns, setRefColumns] = useState<string[]>([])

  useEffect(() => {
    if (!value.ref_table) {
      setRefColumns([])
      return
    }
    let active = true
    api
      .table(value.ref_schema, value.ref_table)
      .then((details) => active && setRefColumns(details.columns.map((c) => c.name)))
      .catch(() => active && setRefColumns([]))
    return () => {
      active = false
    }
  }, [api, value.ref_schema, value.ref_table])

  const tables = relations.filter((r) => r.kind === 'table' || r.kind === 'partitioned_table')
  return (
    <div className="grid gap-2 rounded-lg border border-outline-variant p-3 md:grid-cols-2">
      <Field label={t('postgresStudio.fk.columns')}>
        <MultiSelect options={columns.map((c) => ({ value: c, label: c }))} values={value.columns} onChange={(next) => onChange({ ...value, columns: next })} placeholder={t('postgresStudio.designer.pickColumns')} aria-label={t('postgresStudio.fk.columns')} />
      </Field>
      <Field label={t('postgresStudio.fk.refTable')}>
        <Dropdown
          value={value.ref_table || null}
          onChange={(ref_table) => onChange({ ...value, ref_table, ref_columns: [] })}
          options={tables.map((r) => ({ value: r.name, label: r.name }))}
          searchable
          placeholder={t('postgresStudio.fk.pickTable')}
          aria-label={t('postgresStudio.fk.refTable')}
        />
      </Field>
      <Field label={t('postgresStudio.fk.refColumns')}>
        <MultiSelect options={refColumns.map((c) => ({ value: c, label: c }))} values={value.ref_columns} onChange={(next) => onChange({ ...value, ref_columns: next })} placeholder={t('postgresStudio.designer.pickColumns')} aria-label={t('postgresStudio.fk.refColumns')} disabled={!value.ref_table} />
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label={t('postgresStudio.fk.onDelete')}>
          <Dropdown value={value.on_delete} onChange={(v) => onChange({ ...value, on_delete: v as FkAction })} options={actions} aria-label={t('postgresStudio.fk.onDelete')} />
        </Field>
        <Field label={t('postgresStudio.fk.onUpdate')}>
          <Dropdown value={value.on_update} onChange={(v) => onChange({ ...value, on_update: v as FkAction })} options={actions} aria-label={t('postgresStudio.fk.onUpdate')} />
        </Field>
      </div>
      {onRemove && (
        <div className="md:col-span-2 flex justify-end">
          <Button size="sm" variant="ghost" onClick={onRemove}>
            <Trash2 className="h-3.5 w-3.5" />
            {t('common.remove')}
          </Button>
        </div>
      )}
    </div>
  )
}
