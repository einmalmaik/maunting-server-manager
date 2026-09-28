import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2 } from 'lucide-react'
import { Button, Checkbox, Dropdown, Input, MultiSelect, NumberStepper, Switch, Textarea } from '@/Singra/UI'
import { FormDialog } from './FormDialog'
import { useStudio } from './StudioContext'
import { ForeignKeyEditor } from './TableDesigner'
import { Field, TypePicker } from './shared'
import type { ForeignKeySpec, StudioOperation, StudioRelation, StudioTableDetails } from './studioApi'

/** Gemeinsamer Ablauf: Formular prüfen → Vorschau → bei Erfolg schließen. */
function useSubmit(onClose: () => void) {
  const { runOperation } = useStudio()
  const [error, setError] = useState<string | null>(null)
  const submit = async (build: () => StudioOperation | string, title: string, success?: string) => {
    const result = build()
    if (typeof result === 'string') {
      setError(result)
      return
    }
    setError(null)
    if (await runOperation(result, { title, success })) onClose()
  }
  return { error, setError, submit }
}

const alterTable = (table: StudioTableDetails, actions: unknown[]) => ({
  op: 'alter_table',
  schema_name: table.schema,
  name: table.table,
  actions,
})

export function AddColumnDialog({ open, onClose, table, enums }: {
  open: boolean
  onClose: () => void
  table: StudioTableDetails
  enums: string[]
}) {
  const { t } = useTranslation()
  const { error, setError, submit } = useSubmit(onClose)
  const [name, setName] = useState('')
  const [type, setType] = useState('text')
  const [nullable, setNullable] = useState(true)
  const [defaultValue, setDefault] = useState('')
  const [comment, setComment] = useState('')
  useEffect(() => {
    if (open) {
      setName('')
      setType('text')
      setNullable(true)
      setDefault('')
      setComment('')
      setError(null)
    }
  }, [open, setError])
  return (
    <FormDialog
      open={open}
      onClose={onClose}
      title={t('postgresStudio.structure.addColumn')}
      error={error}
      testId="add-column"
      onSubmit={() =>
        void submit(
          () =>
            !name.trim()
              ? t('postgresStudio.validation.emptyName')
              : alterTable(table, [{ action: 'add_column', column: { name: name.trim(), type, nullable, default: defaultValue.trim() || null, comment: comment.trim() || null } }]),
          t('postgresStudio.structure.addColumn'),
        )
      }
    >
      <Input label={t('postgresStudio.columns.name')} value={name} onChange={(e) => setName(e.target.value)} autoFocus />
      <Field label={t('postgresStudio.columns.type')}>
        <TypePicker value={type} onChange={setType} enums={enums} />
      </Field>
      <Input label={t('postgresStudio.columns.default')} value={defaultValue} onChange={(e) => setDefault(e.target.value)} placeholder="now()" className="font-mono text-xs" />
      <label className="flex items-center gap-2 text-sm text-on-surface">
        <Checkbox checked={!nullable} onCheckedChange={(checked) => setNullable(!checked)} />
        {t('postgresStudio.columns.notNull')}
      </label>
      {!nullable && !defaultValue.trim() && (table.stats?.live ?? 0) > 0 && (
        <p className="text-xs text-status-warning">{t('postgresStudio.structure.notNullNeedsDefault')}</p>
      )}
      <Input label={t('postgresStudio.designer.comment')} value={comment} onChange={(e) => setComment(e.target.value)} />
    </FormDialog>
  )
}

export function ColumnTypeDialog({ open, onClose, table, column, enums }: {
  open: boolean
  onClose: () => void
  table: StudioTableDetails
  column: string
  enums: string[]
}) {
  const { t } = useTranslation()
  const { error, setError, submit } = useSubmit(onClose)
  const current = table.columns.find((c) => c.name === column)
  const [type, setType] = useState(current?.type || 'text')
  const [using, setUsing] = useState('')
  useEffect(() => {
    if (open) {
      setType(current?.type || 'text')
      setUsing('')
      setError(null)
    }
  }, [open, current?.type, setError])
  return (
    <FormDialog
      open={open}
      onClose={onClose}
      title={t('postgresStudio.structure.changeType', { column })}
      error={error}
      onSubmit={() =>
        void submit(
          () => alterTable(table, [{ action: 'alter_column_type', column, type, using: using.trim() || null }]),
          t('postgresStudio.structure.changeType', { column }),
        )
      }
    >
      <Field label={t('postgresStudio.columns.type')}>
        <TypePicker value={type} onChange={setType} enums={enums} />
      </Field>
      <Input label={t('postgresStudio.structure.using')} value={using} onChange={(e) => setUsing(e.target.value)} placeholder={`${column}::${type}`} className="font-mono text-xs" />
      <p className="text-xs text-on-surface-variant">{t('postgresStudio.structure.usingHint')}</p>
    </FormDialog>
  )
}

export type ConstraintKind = 'primary_key' | 'unique' | 'check' | 'foreign_key'

export function ConstraintDialog({ open, onClose, table, kind, relations }: {
  open: boolean
  onClose: () => void
  table: StudioTableDetails
  kind: ConstraintKind
  relations: StudioRelation[]
}) {
  const { t } = useTranslation()
  const { error, setError, submit } = useSubmit(onClose)
  const columns = table.columns.map((c) => c.name)
  const [name, setName] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [expression, setExpression] = useState('')
  const [nullsNotDistinct, setNullsNotDistinct] = useState(false)
  const [fk, setFk] = useState<ForeignKeySpec>({ columns: [], ref_schema: table.schema, ref_table: '', ref_columns: [], on_delete: 'no_action', on_update: 'no_action' })
  useEffect(() => {
    if (!open) return
    setName('')
    setSelected([])
    setExpression('')
    setNullsNotDistinct(false)
    setFk({ columns: [], ref_schema: table.schema, ref_table: '', ref_columns: [], on_delete: 'no_action', on_update: 'no_action' })
    setError(null)
  }, [open, table.schema, setError])
  const title = t(`postgresStudio.structure.add.${kind}`)

  const build = (): StudioOperation | string => {
    const constraintName = name.trim() || null
    if (kind === 'check') {
      if (!expression.trim()) return t('postgresStudio.validation.emptyExpression')
      return alterTable(table, [{ action: 'add_check', constraint: { name: constraintName, expression } }])
    }
    if (kind === 'foreign_key') {
      if (!fk.columns.length || !fk.ref_table || fk.columns.length !== fk.ref_columns.length) return t('postgresStudio.fk.incomplete')
      return alterTable(table, [{ action: 'add_foreign_key', constraint: { ...fk, name: constraintName } }])
    }
    if (!selected.length) return t('postgresStudio.designer.pickColumns')
    if (kind === 'primary_key') return alterTable(table, [{ action: 'add_primary_key', constraint: { name: constraintName, columns: selected } }])
    return alterTable(table, [{ action: 'add_unique', constraint: { name: constraintName, columns: selected, nulls_not_distinct: nullsNotDistinct } }])
  }

  return (
    <FormDialog open={open} onClose={onClose} title={title} error={error} wide={kind === 'foreign_key'} onSubmit={() => void submit(build, title)} testId={`constraint-${kind}`}>
      <Input label={t('postgresStudio.designer.constraintName')} value={name} onChange={(e) => setName(e.target.value)} placeholder={t('postgresStudio.structure.autoName')} />
      {(kind === 'primary_key' || kind === 'unique') && (
        <Field label={t('postgresStudio.designer.pickColumns')}>
          <MultiSelect options={columns.map((c) => ({ value: c, label: c }))} values={selected} onChange={setSelected} placeholder={t('postgresStudio.designer.pickColumns')} aria-label={t('postgresStudio.designer.pickColumns')} />
        </Field>
      )}
      {kind === 'unique' && (
        <label className="flex items-center gap-2 text-sm text-on-surface">
          <Checkbox checked={nullsNotDistinct} onCheckedChange={setNullsNotDistinct} />
          {t('postgresStudio.structure.nullsNotDistinct')}
        </label>
      )}
      {kind === 'check' && (
        <Input label={t('postgresStudio.designer.expression')} value={expression} onChange={(e) => setExpression(e.target.value)} placeholder="price >= 0" className="font-mono text-xs" />
      )}
      {kind === 'foreign_key' && <ForeignKeyEditor value={fk} columns={columns} relations={relations} onChange={setFk} />}
    </FormDialog>
  )
}

interface IndexColumnForm {
  key: number
  mode: 'column' | 'expression'
  value: string
  descending: boolean
}

let indexKey = 1

export function IndexDialog({ open, onClose, table }: { open: boolean; onClose: () => void; table: StudioTableDetails }) {
  const { t } = useTranslation()
  const { error, setError, submit } = useSubmit(onClose)
  const columns = table.columns.map((c) => c.name)
  const [name, setName] = useState('')
  const [items, setItems] = useState<IndexColumnForm[]>([])
  const [method, setMethod] = useState('btree')
  const [unique, setUnique] = useState(false)
  const [include, setInclude] = useState<string[]>([])
  const [where, setWhere] = useState('')
  const [concurrently, setConcurrently] = useState(true)
  useEffect(() => {
    if (!open) return
    setName(`${table.table}_idx`)
    setItems([{ key: indexKey++, mode: 'column', value: columns[0] || '', descending: false }])
    setMethod('btree')
    setUnique(false)
    setInclude([])
    setWhere('')
    setConcurrently(true)
    setError(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, table.table, setError])
  const update = (key: number, patch: Partial<IndexColumnForm>) => setItems((list) => list.map((i) => (i.key === key ? { ...i, ...patch } : i)))

  const title = t('postgresStudio.indexes.create')
  const build = (): StudioOperation | string => {
    if (!name.trim()) return t('postgresStudio.validation.emptyName')
    if (items.some((i) => !i.value.trim())) return t('postgresStudio.indexes.emptyColumn')
    return {
      op: 'create_index',
      schema_name: table.schema,
      table: table.table,
      name: name.trim(),
      method,
      unique,
      include,
      where: where.trim() || null,
      concurrently,
      columns: items.map((i) => (i.mode === 'column' ? { column: i.value, descending: i.descending } : { expression: i.value, descending: i.descending })),
    }
  }

  return (
    <FormDialog open={open} onClose={onClose} title={title} error={error} wide onSubmit={() => void submit(build, title)} testId="index-dialog">
      <div className="grid gap-4 md:grid-cols-2">
        <Input label={t('postgresStudio.columns.name')} value={name} onChange={(e) => setName(e.target.value)} />
        <Field label={t('postgresStudio.indexes.method')}>
          <Dropdown value={method} onChange={setMethod} options={['btree', 'hash', 'gin', 'gist', 'brin', 'spgist'].map((m) => ({ value: m, label: m }))} aria-label={t('postgresStudio.indexes.method')} />
        </Field>
      </div>
      <div className="space-y-2">
        {items.map((item) => (
          <div key={item.key} className="grid items-center gap-2 md:grid-cols-[auto_1fr_auto_auto]">
            <Dropdown
              value={item.mode}
              onChange={(mode) => update(item.key, { mode: mode as 'column', value: mode === 'column' ? columns[0] || '' : '' })}
              options={[
                { value: 'column', label: t('postgresStudio.indexes.column') },
                { value: 'expression', label: t('postgresStudio.indexes.expression') },
              ]}
              aria-label={t('postgresStudio.indexes.column')}
            />
            {item.mode === 'column' ? (
              <Dropdown value={item.value || null} onChange={(value) => update(item.key, { value })} options={columns.map((c) => ({ value: c, label: c }))} aria-label={t('postgresStudio.indexes.column')} />
            ) : (
              <Input value={item.value} onChange={(e) => update(item.key, { value: e.target.value })} placeholder="lower(email)" className="font-mono text-xs" aria-label={t('postgresStudio.indexes.expression')} />
            )}
            <label className="flex items-center gap-2 text-xs text-on-surface-variant">
              <Checkbox checked={item.descending} onCheckedChange={(descending) => update(item.key, { descending })} />
              DESC
            </label>
            <Button size="icon" variant="ghost" onClick={() => setItems((list) => list.filter((i) => i.key !== item.key))} disabled={items.length === 1} aria-label={t('common.remove')}>
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))}
        <Button type="button" size="sm" variant="ghost" onClick={() => setItems((list) => [...list, { key: indexKey++, mode: 'column', value: columns[0] || '', descending: false }])}>
          <Plus className="h-3.5 w-3.5" />
          {t('postgresStudio.indexes.addColumn')}
        </Button>
      </div>
      <Field label={t('postgresStudio.indexes.include')} hint={t('postgresStudio.indexes.includeHint')}>
        <MultiSelect options={columns.map((c) => ({ value: c, label: c }))} values={include} onChange={setInclude} placeholder={t('postgresStudio.designer.pickColumns')} aria-label={t('postgresStudio.indexes.include')} />
      </Field>
      <Input label={t('postgresStudio.indexes.where')} value={where} onChange={(e) => setWhere(e.target.value)} placeholder="deleted_at IS NULL" className="font-mono text-xs" />
      <div className="flex flex-wrap gap-6">
        <label className="flex items-center gap-2 text-sm text-on-surface">
          <Switch checked={unique} onCheckedChange={setUnique} />
          {t('postgresStudio.indexes.unique')}
        </label>
        <label className="flex items-center gap-2 text-sm text-on-surface">
          <Switch checked={concurrently} onCheckedChange={setConcurrently} />
          {t('postgresStudio.indexes.concurrently')}
        </label>
      </div>
      <p className="text-xs text-on-surface-variant">{t('postgresStudio.indexes.concurrentlyHint')}</p>
    </FormDialog>
  )
}

export function PartitionDialog({ open, onClose, table }: { open: boolean; onClose: () => void; table: StudioTableDetails }) {
  const { t } = useTranslation()
  const { error, setError, submit } = useSubmit(onClose)
  const strategy = (table.partition_key || '').split(' ')[0].toLowerCase() as 'range' | 'list' | 'hash'
  const keyCount = Math.max(1, (table.partition_key || '').split(',').length)
  const [name, setName] = useState('')
  const [isDefault, setIsDefault] = useState(false)
  const [from, setFrom] = useState<string[]>([])
  const [to, setTo] = useState<string[]>([])
  const [values, setValues] = useState('')
  const [modulus, setModulus] = useState(4)
  const [remainder, setRemainder] = useState(0)
  useEffect(() => {
    if (!open) return
    setName(`${table.table}_p${table.partitions.length + 1}`)
    setIsDefault(false)
    setFrom(Array(keyCount).fill(''))
    setTo(Array(keyCount).fill(''))
    setValues('')
    setModulus(4)
    setRemainder(table.partitions.length % 4)
    setError(null)
  }, [open, table.table, table.partitions.length, keyCount, setError])

  const title = t('postgresStudio.partitions.create')
  const bound = (value: string) => (value.trim().toUpperCase() === 'MINVALUE' || value.trim().toUpperCase() === 'MAXVALUE' ? null : value)
  const build = (): StudioOperation | string => {
    if (!name.trim()) return t('postgresStudio.validation.emptyName')
    let bounds: Record<string, unknown>
    if (isDefault) bounds = { kind: 'default' }
    else if (strategy === 'range') bounds = { kind: 'range', from_values: from.map(bound), to_values: to.map(bound) }
    else if (strategy === 'list') bounds = { kind: 'list', in_values: values.split(',').map((v) => v.trim()).filter(Boolean) }
    else bounds = { kind: 'hash', modulus, remainder }
    return { op: 'create_partition', schema_name: table.schema, parent: table.table, name: name.trim(), bounds }
  }

  return (
    <FormDialog open={open} onClose={onClose} title={title} description={table.partition_key || ''} error={error} onSubmit={() => void submit(build, title)} testId="partition-dialog">
      <Input label={t('postgresStudio.columns.name')} value={name} onChange={(e) => setName(e.target.value)} />
      {strategy !== 'hash' && (
        <label className="flex items-center gap-2 text-sm text-on-surface">
          <Switch checked={isDefault} onCheckedChange={setIsDefault} />
          {t('postgresStudio.partitions.default')}
        </label>
      )}
      {!isDefault && strategy === 'range' && (
        <div className="grid gap-3 md:grid-cols-2">
          {from.map((value, index) => (
            <Input key={`from-${index}`} label={t('postgresStudio.partitions.from')} value={value} onChange={(e) => setFrom((list) => list.map((v, i) => (i === index ? e.target.value : v)))} placeholder="2026-01-01 / MINVALUE" />
          ))}
          {to.map((value, index) => (
            <Input key={`to-${index}`} label={t('postgresStudio.partitions.to')} value={value} onChange={(e) => setTo((list) => list.map((v, i) => (i === index ? e.target.value : v)))} placeholder="2027-01-01 / MAXVALUE" />
          ))}
        </div>
      )}
      {!isDefault && strategy === 'list' && (
        <Input label={t('postgresStudio.partitions.values')} value={values} onChange={(e) => setValues(e.target.value)} placeholder="de, at, ch" />
      )}
      {!isDefault && strategy === 'hash' && (
        <div className="grid grid-cols-2 gap-3">
          <Field label={t('postgresStudio.partitions.modulus')}>
            <NumberStepper value={modulus} onValueChange={(v) => setModulus(Number(v))} min={1} max={1024} />
          </Field>
          <Field label={t('postgresStudio.partitions.remainder')}>
            <NumberStepper value={remainder} onValueChange={(v) => setRemainder(Number(v))} min={0} max={Math.max(0, modulus - 1)} />
          </Field>
        </div>
      )}
      <p className="text-xs text-on-surface-variant">{t('postgresStudio.partitions.literalHint')}</p>
    </FormDialog>
  )
}

export function ViewDialog({ open, onClose, schema }: { open: boolean; onClose: () => void; schema: string }) {
  const { t } = useTranslation()
  const { error, setError, submit } = useSubmit(onClose)
  const [name, setName] = useState('')
  const [query, setQuery] = useState('SELECT ')
  const [materialized, setMaterialized] = useState(false)
  useEffect(() => {
    if (open) {
      setName('')
      setQuery('SELECT ')
      setMaterialized(false)
      setError(null)
    }
  }, [open, setError])
  const title = t('postgresStudio.views.create')
  return (
    <FormDialog
      open={open}
      onClose={onClose}
      title={title}
      error={error}
      wide
      testId="view-dialog"
      onSubmit={() =>
        void submit(
          () => (!name.trim() ? t('postgresStudio.validation.emptyName') : { op: 'create_view', schema_name: schema, name: name.trim(), query, materialized, or_replace: false }),
          title,
        )
      }
    >
      <Input label={t('postgresStudio.columns.name')} value={name} onChange={(e) => setName(e.target.value)} />
      <Textarea mono label={t('postgresStudio.views.query')} value={query} onChange={(e) => setQuery(e.target.value)} rows={8} />
      <label className="flex items-center gap-2 text-sm text-on-surface">
        <Switch checked={materialized} onCheckedChange={setMaterialized} />
        {t('postgresStudio.views.materialized')}
      </label>
    </FormDialog>
  )
}

export function SequenceDialog({ open, onClose, schema }: { open: boolean; onClose: () => void; schema: string }) {
  const { t } = useTranslation()
  const { error, setError, submit } = useSubmit(onClose)
  const [name, setName] = useState('')
  const [start, setStart] = useState(1)
  const [increment, setIncrement] = useState(1)
  const [cycle, setCycle] = useState(false)
  useEffect(() => {
    if (open) {
      setName('')
      setStart(1)
      setIncrement(1)
      setCycle(false)
      setError(null)
    }
  }, [open, setError])
  const title = t('postgresStudio.sequences.create')
  return (
    <FormDialog
      open={open}
      onClose={onClose}
      title={title}
      error={error}
      onSubmit={() => void submit(() => (!name.trim() ? t('postgresStudio.validation.emptyName') : { op: 'create_sequence', schema_name: schema, name: name.trim(), start, increment, cycle }), title)}
    >
      <Input label={t('postgresStudio.columns.name')} value={name} onChange={(e) => setName(e.target.value)} />
      <div className="grid grid-cols-2 gap-3">
        <Field label={t('postgresStudio.sequences.start')}>
          <NumberStepper value={start} onValueChange={(v) => setStart(Number(v))} min={-2147483648} max={2147483647} />
        </Field>
        <Field label={t('postgresStudio.sequences.increment')}>
          <NumberStepper value={increment} onValueChange={(v) => setIncrement(Number(v))} min={-1000000} max={1000000} />
        </Field>
      </div>
      <label className="flex items-center gap-2 text-sm text-on-surface">
        <Switch checked={cycle} onCheckedChange={setCycle} />
        {t('postgresStudio.sequences.cycle')}
      </label>
    </FormDialog>
  )
}

export function EnumDialog({ open, onClose, schema }: { open: boolean; onClose: () => void; schema: string }) {
  const { t } = useTranslation()
  const { error, setError, submit } = useSubmit(onClose)
  const [name, setName] = useState('')
  const [values, setValues] = useState('')
  useEffect(() => {
    if (open) {
      setName('')
      setValues('')
      setError(null)
    }
  }, [open, setError])
  const title = t('postgresStudio.enums.create')
  const list = values.split('\n').map((v) => v.trim()).filter(Boolean)
  return (
    <FormDialog
      open={open}
      onClose={onClose}
      title={title}
      error={error}
      onSubmit={() =>
        void submit(
          () => (!name.trim() ? t('postgresStudio.validation.emptyName') : !list.length ? t('postgresStudio.enums.noValues') : { op: 'create_enum', schema_name: schema, name: name.trim(), values: list }),
          title,
        )
      }
    >
      <Input label={t('postgresStudio.columns.name')} value={name} onChange={(e) => setName(e.target.value)} />
      <Textarea mono label={t('postgresStudio.enums.values')} value={values} onChange={(e) => setValues(e.target.value)} rows={5} placeholder={'aktiv\npausiert\ngelöscht'} />
    </FormDialog>
  )
}
