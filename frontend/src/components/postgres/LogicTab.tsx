import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Blocks, FlaskConical, Pencil, Plus, Search, Trash2 } from 'lucide-react'
import { Badge, Button, Checkbox, Dropdown, Input, MultiSelect, Switch, Textarea } from '@/Singra/UI'
import { FormDialog } from './FormDialog'
import { useStudio } from './StudioContext'
import { Empty, ErrorBox, Field, Loading, Section, useLoad } from './shared'
import type { StudioFunction, StudioOperation } from './studioApi'

const TEMPLATES = {
  plpgsql: {
    arguments: 'p_id bigint',
    returns: 'integer',
    body: 'DECLARE\n  result integer;\nBEGIN\n  SELECT count(*) INTO result FROM some_table WHERE id = p_id;\n  RETURN result;\nEND;',
  },
  trigger: {
    arguments: '',
    returns: 'trigger',
    body: 'BEGIN\n  NEW.updated_at := now();\n  RETURN NEW;\nEND;',
  },
}

export function LogicTab({ onOpenSql }: { onOpenSql: (sql: string, rollback: boolean) => void }) {
  const { t } = useTranslation()
  const { api, overview, revision } = useStudio()
  const [schema, setSchema] = useState(overview.schemas.some((s) => s.name === 'public') ? 'public' : overview.schemas[0]?.name || 'public')
  const objects = useLoad(() => api.objects(schema), [api, schema, revision])
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <span className="text-sm text-on-surface-variant">{t('postgresStudio.schemas.label')}</span>
        <Dropdown value={schema} onChange={setSchema} options={overview.schemas.map((s) => ({ value: s.name, label: s.name }))} className="w-56" aria-label={t('postgresStudio.schemas.label')} />
      </div>
      <ErrorBox message={objects.error} />
      {objects.loading && !objects.data && <Loading />}
      {objects.data && (
        <>
          <FunctionsSection schema={schema} functions={objects.data.functions} onOpenSql={onOpenSql} />
          <TriggersSection schema={schema} objects={objects.data} />
        </>
      )}
      <ExtensionStore />
    </div>
  )
}

function FunctionsSection({ schema, functions, onOpenSql }: { schema: string; functions: StudioFunction[]; onOpenSql: (sql: string, rollback: boolean) => void }) {
  const { t } = useTranslation()
  const { api, canAdmin, runOperation } = useStudio()
  const [selected, setSelected] = useState<StudioFunction | null>(null)
  const [definition, setDefinition] = useState<string | null>(null)
  const [editor, setEditor] = useState<'plpgsql' | 'trigger' | null>(null)

  useEffect(() => {
    setDefinition(null)
    if (!selected) return
    let active = true
    api.functionDefinition(selected.oid).then((d) => active && setDefinition(d.definition)).catch(() => active && setDefinition(null))
    return () => {
      active = false
    }
  }, [api, selected])

  const testCall = (fn: StudioFunction) => {
    const args = fn.arguments
      ? fn.arguments.split(',').map((arg) => `NULL /* ${arg.trim()} */`).join(', ')
      : ''
    onOpenSql(`SELECT "${schema}"."${fn.name}"(${args});`, true)
  }

  return (
    <Section
      title={t('postgresStudio.functions.title')}
      actions={
        canAdmin ? (
          <>
            <Button size="sm" onClick={() => setEditor('plpgsql')} data-testid="new-function">
              <Plus className="h-3.5 w-3.5" />
              {t('postgresStudio.functions.create')}
            </Button>
            <Button size="sm" variant="secondary" onClick={() => setEditor('trigger')}>
              <Plus className="h-3.5 w-3.5" />
              {t('postgresStudio.functions.createTrigger')}
            </Button>
          </>
        ) : undefined
      }
    >
      {functions.length === 0 ? (
        <Empty>{t('postgresStudio.functions.none')}</Empty>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <ul className="max-h-96 space-y-1 overflow-y-auto">
            {functions.map((fn) => (
              <li key={fn.oid}>
                <button
                  type="button"
                  onClick={() => setSelected(fn)}
                  className={`w-full rounded-md border px-3 py-2 text-left transition ${selected?.oid === fn.oid ? 'border-secondary bg-secondary/10' : 'border-outline-variant hover:bg-surface-container-high'}`}
                >
                  <span className="font-mono text-xs text-on-surface">
                    {fn.name}({fn.arguments})
                  </span>
                  <span className="ml-2 font-mono text-label-sm text-secondary">→ {fn.returns}</span>
                  <span className="mt-1 flex gap-1">
                    <Badge>{fn.language}</Badge>
                    {fn.kind === 'p' && <Badge variant="info">PROCEDURE</Badge>}
                    {fn.security_definer && <Badge variant="warning">SECURITY DEFINER</Badge>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <div className="min-w-0">
            {selected ? (
              <div className="space-y-2">
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="secondary" onClick={() => testCall(selected)}>
                    <FlaskConical className="h-3.5 w-3.5" />
                    {t('postgresStudio.functions.test')}
                  </Button>
                  {canAdmin && definition && (
                    <Button size="sm" variant="secondary" onClick={() => onOpenSql(definition, false)}>
                      <Pencil className="h-3.5 w-3.5" />
                      {t('postgresStudio.functions.editInSql')}
                    </Button>
                  )}
                  {canAdmin && (
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => void runOperation({ op: 'drop_function', schema_name: schema, name: selected.name, arguments: selected.arguments }, { title: t('postgresStudio.functions.drop') }).then((ok) => ok && setSelected(null))}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      {t('common.delete')}
                    </Button>
                  )}
                </div>
                <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-container-lowest p-3 font-mono text-xs text-on-surface">{definition ?? '…'}</pre>
              </div>
            ) : (
              <Empty>{t('postgresStudio.functions.pick')}</Empty>
            )}
          </div>
        </div>
      )}
      {editor && <FunctionEditor schema={schema} template={editor} onClose={() => setEditor(null)} />}
    </Section>
  )
}

function FunctionEditor({ schema, template, onClose }: { schema: string; template: 'plpgsql' | 'trigger'; onClose: () => void }) {
  const { t } = useTranslation()
  const { runOperation } = useStudio()
  const base = TEMPLATES[template]
  const [name, setName] = useState(template === 'trigger' ? 'set_updated_at' : '')
  const [args, setArgs] = useState(base.arguments)
  const [returns, setReturns] = useState(base.returns)
  const [language, setLanguage] = useState<'plpgsql' | 'sql'>('plpgsql')
  const [volatility, setVolatility] = useState('volatile')
  const [securityDefiner, setSecurityDefiner] = useState(false)
  const [strict, setStrict] = useState(false)
  const [body, setBody] = useState(base.body)
  const [error, setError] = useState<string | null>(null)
  const title = template === 'trigger' ? t('postgresStudio.functions.createTrigger') : t('postgresStudio.functions.create')
  const submit = async () => {
    if (!name.trim()) {
      setError(t('postgresStudio.validation.emptyName'))
      return
    }
    const operation: StudioOperation = {
      op: 'create_function',
      schema_name: schema,
      name: name.trim(),
      arguments: args,
      returns,
      language,
      body,
      volatility,
      security_definer: securityDefiner,
      strict,
      or_replace: true,
    }
    if (await runOperation(operation, { title })) onClose()
  }
  return (
    <FormDialog open wide title={title} error={error} onClose={onClose} onSubmit={() => void submit()} testId="function-editor">
      <div className="grid gap-3 md:grid-cols-2">
        <Input label={t('postgresStudio.columns.name')} value={name} onChange={(e) => setName(e.target.value)} className="font-mono text-xs" />
        <Input label={t('postgresStudio.functions.returns')} value={returns} onChange={(e) => setReturns(e.target.value)} className="font-mono text-xs" />
        <Input label={t('postgresStudio.functions.arguments')} value={args} onChange={(e) => setArgs(e.target.value)} placeholder="p_id bigint, p_name text" className="font-mono text-xs" />
        <div className="grid grid-cols-2 gap-3">
          <Field label={t('postgresStudio.functions.language')}>
            <Dropdown value={language} onChange={(v) => setLanguage(v as 'plpgsql')} options={[{ value: 'plpgsql', label: 'PL/pgSQL' }, { value: 'sql', label: 'SQL' }]} aria-label={t('postgresStudio.functions.language')} />
          </Field>
          <Field label={t('postgresStudio.functions.volatility')}>
            <Dropdown value={volatility} onChange={setVolatility} options={['volatile', 'stable', 'immutable'].map((v) => ({ value: v, label: v.toUpperCase() }))} aria-label={t('postgresStudio.functions.volatility')} />
          </Field>
        </div>
      </div>
      <Textarea mono rows={12} label={t('postgresStudio.functions.body')} value={body} onChange={(e) => setBody(e.target.value)} />
      <div className="flex flex-wrap gap-6">
        <label className="flex items-center gap-2 text-sm text-on-surface">
          <Checkbox checked={securityDefiner} onCheckedChange={setSecurityDefiner} />
          SECURITY DEFINER
        </label>
        <label className="flex items-center gap-2 text-sm text-on-surface">
          <Checkbox checked={strict} onCheckedChange={setStrict} />
          STRICT
        </label>
      </div>
      {securityDefiner && <p className="text-xs text-status-warning">{t('postgresStudio.functions.securityDefinerHint')}</p>}
    </FormDialog>
  )
}

function TriggersSection({ schema, objects }: { schema: string; objects: { relations: { name: string; kind: string }[]; functions: StudioFunction[]; triggers: { name: string; table?: string; enabled: boolean; definition: string }[] } }) {
  const { t } = useTranslation()
  const { canAdmin, runOperation } = useStudio()
  const [open, setOpen] = useState(false)
  return (
    <Section
      title={t('postgresStudio.triggers.title')}
      actions={
        canAdmin ? (
          <Button size="sm" variant="secondary" onClick={() => setOpen(true)} data-testid="new-trigger">
            <Plus className="h-3.5 w-3.5" />
            {t('postgresStudio.triggers.create')}
          </Button>
        ) : undefined
      }
    >
      {objects.triggers.length === 0 ? (
        <Empty>{t('postgresStudio.triggers.none')}</Empty>
      ) : (
        <ul className="space-y-2">
          {objects.triggers.map((trigger) => (
            <li key={`${trigger.table}.${trigger.name}`} className="flex items-start justify-between gap-3 rounded-lg border border-outline-variant p-3">
              <div className="min-w-0">
                <span className="font-mono text-xs text-on-surface">{trigger.name}</span>
                <span className="ml-2 text-xs text-on-surface-variant">{trigger.table}</span>
                <p className="mt-1 break-words font-mono text-label-sm text-on-surface-variant">{trigger.definition}</p>
              </div>
              {canAdmin && (
                <div className="flex shrink-0 items-center gap-2">
                  <Switch
                    checked={trigger.enabled}
                    aria-label={t('postgresStudio.triggers.enabled')}
                    onCheckedChange={(enabled) => void runOperation({ op: 'set_trigger_enabled', schema_name: schema, table: trigger.table, name: trigger.name, enabled }, { title: t('postgresStudio.triggers.toggle') })}
                  />
                  <Button size="icon" variant="ghost" aria-label={t('postgresStudio.triggers.drop')} onClick={() => void runOperation({ op: 'drop_trigger', schema_name: schema, table: trigger.table, name: trigger.name }, { title: t('postgresStudio.triggers.drop') })}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {open && <TriggerDialog schema={schema} tables={objects.relations.filter((r) => r.kind === 'table' || r.kind === 'partitioned_table' || r.kind === 'view').map((r) => r.name)} functions={objects.functions.filter((f) => f.returns === 'trigger')} onClose={() => setOpen(false)} />}
    </Section>
  )
}

function TriggerDialog({ schema, tables, functions, onClose }: { schema: string; tables: string[]; functions: StudioFunction[]; onClose: () => void }) {
  const { t } = useTranslation()
  const { runOperation } = useStudio()
  const [table, setTable] = useState(tables[0] || '')
  const [name, setName] = useState('')
  const [timing, setTiming] = useState('before')
  const [events, setEvents] = useState<string[]>(['insert', 'update'])
  const [forEach, setForEach] = useState('row')
  const [fn, setFn] = useState(functions[0]?.name || '')
  const [when, setWhen] = useState('')
  const [error, setError] = useState<string | null>(null)
  const title = t('postgresStudio.triggers.create')
  const submit = async () => {
    if (!name.trim() || !table || !fn || !events.length) {
      setError(t('postgresStudio.triggers.incomplete'))
      return
    }
    const operation = { op: 'create_trigger', schema_name: schema, table, name: name.trim(), timing, events, for_each: forEach, function_schema: schema, function_name: fn, when: when.trim() || null }
    if (await runOperation(operation, { title })) onClose()
  }
  return (
    <FormDialog open wide title={title} error={error} onClose={onClose} onSubmit={() => void submit()} testId="trigger-dialog">
      {functions.length === 0 && <p className="text-sm text-status-warning">{t('postgresStudio.triggers.noFunction')}</p>}
      <div className="grid gap-3 md:grid-cols-2">
        <Field label={t('postgresStudio.triggers.table')}>
          <Dropdown value={table || null} onChange={setTable} options={tables.map((v) => ({ value: v, label: v }))} searchable aria-label={t('postgresStudio.triggers.table')} />
        </Field>
        <Input label={t('postgresStudio.columns.name')} value={name} onChange={(e) => setName(e.target.value)} />
        <Field label={t('postgresStudio.triggers.timing')}>
          <Dropdown value={timing} onChange={setTiming} options={[{ value: 'before', label: 'BEFORE' }, { value: 'after', label: 'AFTER' }, { value: 'instead_of', label: 'INSTEAD OF' }]} aria-label={t('postgresStudio.triggers.timing')} />
        </Field>
        <Field label={t('postgresStudio.triggers.events')}>
          <MultiSelect options={['insert', 'update', 'delete', 'truncate'].map((v) => ({ value: v, label: v.toUpperCase() }))} values={events} onChange={setEvents} placeholder={t('postgresStudio.triggers.events')} aria-label={t('postgresStudio.triggers.events')} />
        </Field>
        <Field label={t('postgresStudio.triggers.forEach')}>
          <Dropdown value={forEach} onChange={setForEach} options={[{ value: 'row', label: 'FOR EACH ROW' }, { value: 'statement', label: 'FOR EACH STATEMENT' }]} aria-label={t('postgresStudio.triggers.forEach')} />
        </Field>
        <Field label={t('postgresStudio.triggers.function')}>
          <Dropdown value={fn || null} onChange={setFn} options={functions.map((f) => ({ value: f.name, label: f.name }))} aria-label={t('postgresStudio.triggers.function')} />
        </Field>
      </div>
      <Input label={t('postgresStudio.triggers.when')} value={when} onChange={(e) => setWhen(e.target.value)} placeholder="OLD.* IS DISTINCT FROM NEW.*" className="font-mono text-xs" />
    </FormDialog>
  )
}

function ExtensionStore() {
  const { t } = useTranslation()
  const { api, canAdmin, dedicated, revision, runOperation } = useStudio()
  const extensions = useLoad(() => api.extensions(), [api, revision])
  const [search, setSearch] = useState('')
  const [onlyInstalled, setOnlyInstalled] = useState(false)
  const list = (extensions.data || []).filter(
    (e) => (!onlyInstalled || e.installed_version) && (e.name.includes(search.toLowerCase()) || (e.comment || '').toLowerCase().includes(search.toLowerCase())),
  )
  return (
    <Section
      title={<span className="inline-flex items-center gap-2"><Blocks className="h-4 w-4" />{t('postgresStudio.extensions.title')}</span>}
      actions={
        <>
          <Input prefix={<Search className="h-3.5 w-3.5" />} value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t('postgresStudio.extensions.search')} aria-label={t('postgresStudio.extensions.search')} />
          <label className="flex items-center gap-2 whitespace-nowrap text-xs text-on-surface">
            <Switch checked={onlyInstalled} onCheckedChange={setOnlyInstalled} />
            {t('postgresStudio.extensions.onlyInstalled')}
          </label>
        </>
      }
    >
      <p className="mb-3 text-xs text-on-surface-variant">{dedicated ? t('postgresStudio.extensions.dedicatedHint') : t('postgresStudio.extensions.sharedHint')}</p>
      <ErrorBox message={extensions.error} />
      {extensions.loading && !extensions.data && <Loading />}
      <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
        {list.map((extension) => (
          <div key={extension.name} className="flex flex-col justify-between gap-2 rounded-lg border border-outline-variant p-3" data-testid={`extension-${extension.name}`}>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm text-on-surface">{extension.name}</span>
                <span className="font-mono text-label-sm text-on-surface-variant">{extension.installed_version || extension.default_version}</span>
                {extension.installed_version && <Badge variant="success">{t('postgresStudio.extensions.installed')}</Badge>}
                {extension.trusted && <Badge variant="info">trusted</Badge>}
              </div>
              <p className="mt-1 text-xs text-on-surface-variant">{extension.comment}</p>
            </div>
            {canAdmin && (
              <div className="flex gap-2">
                {!extension.installed_version && (
                  <Button size="sm" disabled={!extension.installable} onClick={() => void runOperation({ op: 'create_extension', name: extension.name }, { title: t('postgresStudio.extensions.install', { name: extension.name }) })}>
                    {extension.installable ? t('postgresStudio.extensions.installShort') : t('postgresStudio.extensions.notAllowed')}
                  </Button>
                )}
                {extension.installed_version && extension.installed_version !== extension.default_version && (
                  <Button size="sm" variant="secondary" onClick={() => void runOperation({ op: 'update_extension', name: extension.name }, { title: t('postgresStudio.extensions.update') })}>
                    {t('postgresStudio.extensions.update')}
                  </Button>
                )}
                {extension.installed_version && extension.name !== 'plpgsql' && (
                  <Button size="sm" variant="ghost" onClick={() => void runOperation({ op: 'drop_extension', name: extension.name }, { title: t('postgresStudio.extensions.remove', { name: extension.name }) })}>
                    {t('common.remove')}
                  </Button>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </Section>
  )
}
