import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, ShieldCheck, Trash2 } from 'lucide-react'
import { Badge, Button, Checkbox, Dropdown, Input, MultiSelect, NumberStepper, Switch, Textarea } from '@/Singra/UI'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { FormDialog } from './FormDialog'
import { useStudio } from './StudioContext'
import { Empty, ErrorBox, Field, Loading, Section, useLoad } from './shared'
import type { StudioOperation, StudioRole } from './studioApi'

const ALLOWED_SYSTEM_ROLES = ['pg_read_all_data', 'pg_write_all_data', 'pg_monitor', 'pg_read_all_stats', 'pg_signal_backend', 'pg_maintain']
const TABLE_PRIVILEGES = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER']
const SEQUENCE_PRIVILEGES = ['USAGE', 'SELECT', 'UPDATE']
const SCHEMA_PRIVILEGES = ['USAGE', 'CREATE']
const FLAGS = ['login', 'inherit', 'createdb', 'createrole', 'bypassrls'] as const

export function SecurityTab() {
  const { t } = useTranslation()
  const { api, overview, revision } = useStudio()
  const [schema, setSchema] = useState(overview.schemas.some((s) => s.name === 'public') ? 'public' : overview.schemas[0]?.name || 'public')
  const roles = useLoad(() => api.roles(), [api, revision])
  return (
    <div className="space-y-4">
      <RolesSection roles={roles.data} error={roles.error} loading={roles.loading} />
      <div className="flex items-center gap-2">
        <span className="text-sm text-on-surface-variant">{t('postgresStudio.schemas.label')}</span>
        <Dropdown value={schema} onChange={setSchema} options={overview.schemas.map((s) => ({ value: s.name, label: s.name }))} className="w-56" aria-label={t('postgresStudio.schemas.label')} />
      </div>
      <GrantsMatrix schema={schema} roles={roles.data || []} />
      <RlsStudio schema={schema} roles={roles.data || []} />
    </div>
  )
}

function RolesSection({ roles, error, loading }: { roles: StudioRole[] | null; error: string | null; loading: boolean }) {
  const { t } = useTranslation()
  const { dedicated, canAdmin, runOperation } = useStudio()
  const [dialog, setDialog] = useState<{ mode: 'create' } | { mode: 'edit'; role: StudioRole } | null>(null)
  const editable = dedicated && canAdmin
  return (
    <Section
      title={t('postgresStudio.roles.title')}
      actions={
        editable ? (
          <Button size="sm" onClick={() => setDialog({ mode: 'create' })} data-testid="new-role">
            <Plus className="h-3.5 w-3.5" />
            {t('postgresStudio.roles.create')}
          </Button>
        ) : undefined
      }
    >
      {!dedicated && <p className="mb-3 text-xs text-on-surface-variant">{t('postgresStudio.roles.sharedHint')}</p>}
      <ErrorBox message={error} />
      {loading && !roles && <Loading />}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-on-surface-variant">
            <tr className="border-b border-outline-variant">
              <th className="py-2 pr-3 font-medium">{t('postgresStudio.roles.name')}</th>
              <th className="py-2 pr-3 font-medium">{t('postgresStudio.roles.flags')}</th>
              <th className="py-2 pr-3 font-medium">{t('postgresStudio.roles.memberOf')}</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody>
            {(roles || []).map((role) => (
              <tr key={role.name} className="border-b border-outline-variant/50">
                <td className="py-2 pr-3 font-mono text-on-surface">
                  {role.name}
                  {role.system && <Badge variant="warning" className="ml-2">{t('postgresStudio.roles.system')}</Badge>}
                  {role.managed && !role.system && <Badge variant="info" className="ml-2">{t('postgresStudio.roles.managed')}</Badge>}
                </td>
                <td className="py-2 pr-3">
                  <span className="flex flex-wrap gap-1">
                    {role.superuser && <Badge variant="destructive">SUPERUSER</Badge>}
                    {FLAGS.filter((flag) => role[flag]).map((flag) => (
                      <Badge key={flag}>{flag.toUpperCase()}</Badge>
                    ))}
                    {role.connection_limit >= 0 && <Badge>{t('postgresStudio.roles.limit', { count: role.connection_limit })}</Badge>}
                  </span>
                </td>
                <td className="py-2 pr-3 font-mono text-on-surface-variant">{role.member_of.join(', ') || '–'}</td>
                <td className="py-2 text-right">
                  {editable && !role.managed && !role.system && (
                    <span className="inline-flex gap-1">
                      <Button size="sm" variant="ghost" onClick={() => setDialog({ mode: 'edit', role })}>{t('common.edit')}</Button>
                      <Button size="icon" variant="ghost" aria-label={t('postgresStudio.roles.drop')} onClick={() => void runOperation({ op: 'drop_role', name: role.name }, { title: t('postgresStudio.roles.drop') })}>
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {dialog && <RoleDialog roles={roles || []} role={dialog.mode === 'edit' ? dialog.role : null} onClose={() => setDialog(null)} />}
    </Section>
  )
}

function RoleDialog({ roles, role, onClose }: { roles: StudioRole[]; role: StudioRole | null; onClose: () => void }) {
  const { t } = useTranslation()
  const { runOperation } = useStudio()
  const [name, setName] = useState(role?.name || '')
  const [flags, setFlags] = useState<Record<(typeof FLAGS)[number], boolean>>({
    login: role?.login ?? true,
    inherit: role?.inherit ?? true,
    createdb: role?.createdb ?? false,
    createrole: role?.createrole ?? false,
    bypassrls: role?.bypassrls ?? false,
  })
  const [password, setPassword] = useState('')
  const [limit, setLimit] = useState(role?.connection_limit ?? -1)
  const [memberOf, setMemberOf] = useState<string[]>(role?.member_of || [])
  const [error, setError] = useState<string | null>(null)
  const memberOptions = [...roles.filter((r) => !r.system && r.name !== name).map((r) => r.name), ...ALLOWED_SYSTEM_ROLES].map((value) => ({ value, label: value }))
  const title = role ? t('postgresStudio.roles.edit', { name: role.name }) : t('postgresStudio.roles.create')

  const submit = async () => {
    if (!name.trim()) {
      setError(t('postgresStudio.validation.emptyName'))
      return
    }
    const attributes = { ...flags, connection_limit: limit, password: password || null }
    let operation: StudioOperation
    if (role) {
      const changed = Object.fromEntries(
        Object.entries(attributes).filter(([key, value]) => (key === 'password' ? Boolean(value) : role[key as keyof StudioRole] !== value)),
      )
      operation = {
        op: 'alter_role',
        name: role.name,
        attributes: changed,
        grant_membership: memberOf.filter((m) => !role.member_of.includes(m)),
        revoke_membership: role.member_of.filter((m) => !memberOf.includes(m)),
      }
    } else {
      operation = { op: 'create_role', name: name.trim(), attributes, member_of: memberOf }
    }
    if (await runOperation(operation, { title })) onClose()
  }

  return (
    <FormDialog open wide title={title} description={t('postgresStudio.roles.dialogHint')} error={error} onClose={onClose} onSubmit={() => void submit()} testId="role-dialog">
      <div className="grid gap-3 md:grid-cols-2">
        <Input label={t('postgresStudio.roles.name')} value={name} onChange={(e) => setName(e.target.value)} disabled={Boolean(role)} className="font-mono text-xs" />
        <Field label={t('postgresStudio.roles.password')} hint={role ? t('postgresStudio.roles.passwordKeep') : undefined}>
          <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" aria-label={t('postgresStudio.roles.password')} />
        </Field>
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        {FLAGS.map((flag) => (
          <label key={flag} className="flex items-center gap-2 text-sm text-on-surface">
            <Checkbox checked={flags[flag]} onCheckedChange={(value) => setFlags((f) => ({ ...f, [flag]: value }))} />
            {flag.toUpperCase()}
          </label>
        ))}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label={t('postgresStudio.roles.connectionLimit')} hint={t('postgresStudio.roles.connectionLimitHint')}>
          <NumberStepper value={limit} onValueChange={(v) => setLimit(Number(v))} min={-1} max={10000} />
        </Field>
        <Field label={t('postgresStudio.roles.memberOf')}>
          <MultiSelect options={memberOptions} values={memberOf} onChange={setMemberOf} placeholder={t('postgresStudio.roles.pickRoles')} aria-label={t('postgresStudio.roles.memberOf')} />
        </Field>
      </div>
    </FormDialog>
  )
}

function GrantsMatrix({ schema, roles }: { schema: string; roles: StudioRole[] }) {
  const { t } = useTranslation()
  const { api, canAdmin, revision, runOperation } = useStudio()
  const grants = useLoad(() => api.grants(schema), [api, schema, revision])
  const [edit, setEdit] = useState<{ object: string | null; kind: string; role: string; held: string[] } | null>(null)
  const roleNames = useMemo(() => ['PUBLIC', ...roles.filter((r) => !r.system && !r.superuser).map((r) => r.name)], [roles])
  const objects = useMemo(() => {
    const map = new Map<string, string>()
    for (const row of grants.data?.objects || []) map.set(row.object, row.kind)
    return [...map.entries()]
  }, [grants.data])
  const held = (object: string | null, role: string) =>
    object === null
      ? (grants.data?.schema_grants || []).filter((g) => g.grantee === role).map((g) => g.privilege)
      : (grants.data?.objects || []).filter((g) => g.object === object && g.grantee === role).map((g) => g.privilege)

  const save = async (next: string[]) => {
    if (!edit) return
    const objectType = edit.object === null ? 'schema' : edit.kind === 'S' ? 'sequence' : 'table'
    const add = next.filter((p) => !edit.held.includes(p))
    const remove = edit.held.filter((p) => !next.includes(p))
    const base = { object_type: objectType, schema_name: schema, objects: edit.object ? [edit.object] : [], roles: [edit.role] }
    if (add.length && !(await runOperation({ op: 'grant', ...base, privileges: add }, { title: t('postgresStudio.grants.grant') }))) return
    if (remove.length && !(await runOperation({ op: 'revoke', ...base, privileges: remove }, { title: t('postgresStudio.grants.revoke') }))) return
    setEdit(null)
  }

  const cell = (object: string | null, kind: string, role: string) => {
    const list = held(object, role)
    return (
      <td key={role} className="px-2 py-1.5">
        <button
          type="button"
          disabled={!canAdmin}
          onClick={() => setEdit({ object, kind, role, held: list })}
          className="min-h-7 w-full rounded-md border border-outline-variant px-2 py-1 text-left font-mono text-label-sm text-on-surface hover:border-secondary/50 disabled:cursor-default"
          aria-label={t('postgresStudio.grants.editCell', { object: object || schema, role })}
        >
          {list.length ? list.map((p) => p.slice(0, 3)).join(' ') : '–'}
        </button>
      </td>
    )
  }

  return (
    <Section title={t('postgresStudio.grants.title')}>
      <p className="mb-3 text-xs text-on-surface-variant">{t('postgresStudio.grants.hint')}</p>
      <ErrorBox message={grants.error} />
      {grants.loading && !grants.data && <Loading />}
      {grants.data && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs" data-testid="grants-matrix">
            <thead className="text-on-surface-variant">
              <tr className="border-b border-outline-variant">
                <th className="py-2 pr-3 font-medium">{t('postgresStudio.grants.object')}</th>
                {roleNames.map((role) => (
                  <th key={role} className="whitespace-nowrap px-2 py-2 font-mono font-medium">{role}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr className="border-b border-outline-variant/50">
                <td className="py-1.5 pr-3 font-mono text-on-surface">
                  {schema} <Badge className="ml-1">SCHEMA</Badge>
                </td>
                {roleNames.map((role) => cell(null, 'n', role))}
              </tr>
              {objects.map(([object, kind]) => (
                <tr key={object} className="border-b border-outline-variant/50">
                  <td className="py-1.5 pr-3 font-mono text-on-surface">
                    {object}
                    {kind === 'S' && <Badge className="ml-1">SEQ</Badge>}
                  </td>
                  {roleNames.map((role) => cell(object, kind, role))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {edit && <GrantDialog edit={edit} schema={schema} onClose={() => setEdit(null)} onSave={(next) => void save(next)} />}
    </Section>
  )
}

function GrantDialog({ edit, schema, onClose, onSave }: {
  edit: { object: string | null; kind: string; role: string; held: string[] }
  schema: string
  onClose: () => void
  onSave: (privileges: string[]) => void
}) {
  const { t } = useTranslation()
  const base = edit.object === null ? SCHEMA_PRIVILEGES : edit.kind === 'S' ? SEQUENCE_PRIVILEGES : TABLE_PRIVILEGES
  // Was die Rolle schon hat, bleibt sichtbar (z. B. MAINTAIN aus PG 17) —
  // sonst würde Speichern es stillschweigend entziehen.
  const options = [...base, ...edit.held.filter((p) => !base.includes(p))]
  const [selected, setSelected] = useState<string[]>(edit.held)
  return (
    <FormDialog open title={t('postgresStudio.grants.dialogTitle', { object: edit.object || schema, role: edit.role })} onClose={onClose} onSubmit={() => onSave(selected)} testId="grant-dialog">
      <div className="grid gap-2 sm:grid-cols-2">
        {options.map((privilege) => (
          <label key={privilege} className="flex items-center gap-2 text-sm text-on-surface">
            <Checkbox checked={selected.includes(privilege)} onCheckedChange={(on) => setSelected((list) => (on ? [...list, privilege] : list.filter((p) => p !== privilege)))} />
            {privilege}
          </label>
        ))}
      </div>
    </FormDialog>
  )
}

const POLICY_TEMPLATES = {
  owner: { command: 'all', using: 'owner_name = current_user', with_check: 'owner_name = current_user' },
  tenant: { command: 'all', using: "tenant_id = current_setting('app.tenant_id')::bigint", with_check: "tenant_id = current_setting('app.tenant_id')::bigint" },
  readonly: { command: 'select', using: 'true', with_check: '' },
} as const

function RlsStudio({ schema, roles }: { schema: string; roles: StudioRole[] }) {
  const { t } = useTranslation()
  const { api, canAdmin, revision, runOperation } = useStudio()
  const objects = useLoad(() => api.objects(schema), [api, schema, revision])
  const tables = (objects.data?.relations || []).filter((r) => r.kind === 'table' || r.kind === 'partitioned_table')
  const [table, setTable] = useState<string | null>(null)
  const [dialog, setDialog] = useState(false)
  useEffect(() => {
    if (!table || !tables.some((r) => r.name === table)) setTable(tables[0]?.name || null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [objects.data])
  const details = useLoad(() => (table ? api.table(schema, table) : Promise.resolve(null)), [api, schema, table, revision])
  const current = details.data

  return (
    <Section
      title={<span className="inline-flex items-center gap-2"><ShieldCheck className="h-4 w-4" />{t('postgresStudio.rls.title')}</span>}
      actions={
        <Dropdown value={table} onChange={setTable} options={tables.map((r) => ({ value: r.name, label: r.name, hint: r.rls_enabled ? 'RLS' : undefined }))} searchable className="w-56" aria-label={t('postgresStudio.rls.table')} />
      }
    >
      <ErrorBox message={details.error || objects.error} />
      {!tables.length && objects.data && <Empty>{t('postgresStudio.rls.noTables')}</Empty>}
      {current && (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-6">
            <label className="flex items-center gap-2 text-sm text-on-surface">
              <Switch
                checked={current.rls_enabled}
                disabled={!canAdmin}
                onCheckedChange={(enabled) => void runOperation({ op: 'set_rls', schema_name: schema, table: current.table, enabled, forced: enabled && current.rls_forced }, { title: t('postgresStudio.rls.toggle') })}
                data-testid="rls-enabled"
              />
              {t('postgresStudio.rls.enabled')}
            </label>
            <label className="flex items-center gap-2 text-sm text-on-surface">
              <Switch
                checked={current.rls_forced}
                disabled={!canAdmin || !current.rls_enabled}
                onCheckedChange={(forced) => void runOperation({ op: 'set_rls', schema_name: schema, table: current.table, enabled: true, forced }, { title: t('postgresStudio.rls.toggle') })}
              />
              {t('postgresStudio.rls.forced')}
            </label>
          </div>
          <p className="text-xs text-on-surface-variant">{t('postgresStudio.rls.hint')}</p>
          {current.policies.length === 0 ? (
            <Empty>{t('postgresStudio.rls.noPolicies')}</Empty>
          ) : (
            <ul className="space-y-2">
              {current.policies.map((policy) => (
                <li key={policy.name} className="flex items-start justify-between gap-3 rounded-lg border border-outline-variant p-3">
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs text-on-surface">{policy.name}</span>
                      <Badge>{policy.command.toUpperCase()}</Badge>
                      <Badge variant={policy.permissive ? 'info' : 'warning'}>{policy.permissive ? 'PERMISSIVE' : 'RESTRICTIVE'}</Badge>
                      <span className="font-mono text-label-sm text-on-surface-variant">→ {policy.roles.join(', ')}</span>
                    </div>
                    {policy.using && <p className="break-words font-mono text-label-sm text-on-surface-variant">USING {policy.using}</p>}
                    {policy.with_check && <p className="break-words font-mono text-label-sm text-on-surface-variant">WITH CHECK {policy.with_check}</p>}
                  </div>
                  {canAdmin && (
                    <Button size="icon" variant="ghost" aria-label={t('postgresStudio.rls.drop')} onClick={() => void runOperation({ op: 'drop_policy', schema_name: schema, table: current.table, name: policy.name }, { title: t('postgresStudio.rls.drop') })}>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {canAdmin && (
            <Button size="sm" variant="secondary" onClick={() => setDialog(true)} data-testid="new-policy">
              <Plus className="h-3.5 w-3.5" />
              {t('postgresStudio.rls.create')}
            </Button>
          )}
          {dialog && <PolicyDialog schema={schema} table={current.table} roles={roles} onClose={() => setDialog(false)} />}
        </div>
      )}
    </Section>
  )
}

function PolicyDialog({ schema, table, roles, onClose }: { schema: string; table: string; roles: StudioRole[]; onClose: () => void }) {
  const { t } = useTranslation()
  const { runOperation } = useStudio()
  const [name, setName] = useState('')
  const [command, setCommand] = useState('all')
  const [permissive, setPermissive] = useState(true)
  const [policyRoles, setPolicyRoles] = useState<string[]>([])
  const [using, setUsing] = useState('')
  const [withCheck, setWithCheck] = useState('')
  const [error, setError] = useState<string | null>(null)
  const applyTemplate = (key: keyof typeof POLICY_TEMPLATES) => {
    const template = POLICY_TEMPLATES[key]
    setCommand(template.command)
    setUsing(template.using)
    setWithCheck(template.with_check)
    if (!name) setName(`${table}_${key}`)
  }
  const title = t('postgresStudio.rls.create')
  const submit = async () => {
    if (!name.trim()) {
      setError(t('postgresStudio.validation.emptyName'))
      return
    }
    const operation = {
      op: 'create_policy',
      schema_name: schema,
      table,
      name: name.trim(),
      command,
      permissive,
      roles: policyRoles,
      using: command === 'insert' ? null : using.trim() || null,
      with_check: command === 'select' || command === 'delete' ? null : withCheck.trim() || null,
    }
    if (await runOperation(operation, { title })) onClose()
  }
  return (
    <FormDialog open wide title={title} description={t('postgresStudio.rls.dialogHint', { table })} error={error} onClose={onClose} onSubmit={() => void submit()} testId="policy-dialog">
      <div className="flex flex-wrap gap-2">
        <span className="text-xs text-on-surface-variant">{t('postgresStudio.rls.templates')}:</span>
        {(Object.keys(POLICY_TEMPLATES) as (keyof typeof POLICY_TEMPLATES)[]).map((key) => (
          <Button key={key} type="button" size="sm" variant="ghost" onClick={() => applyTemplate(key)}>
            {t(`postgresStudio.rls.template.${key}`)}
          </Button>
        ))}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <Input label={t('postgresStudio.columns.name')} value={name} onChange={(e) => setName(e.target.value)} />
        <Field label={t('postgresStudio.rls.command')}>
          <Dropdown value={command} onChange={setCommand} options={['all', 'select', 'insert', 'update', 'delete'].map((v) => ({ value: v, label: v.toUpperCase() }))} aria-label={t('postgresStudio.rls.command')} />
        </Field>
        <Field label={t('postgresStudio.rls.roles')} hint={t('postgresStudio.rls.rolesHint')}>
          <MultiSelect options={roles.filter((r) => !r.system).map((r) => ({ value: r.name, label: r.name }))} values={policyRoles} onChange={setPolicyRoles} placeholder="PUBLIC" aria-label={t('postgresStudio.rls.roles')} />
        </Field>
        <label className="flex items-center gap-2 self-end text-sm text-on-surface">
          <Switch checked={permissive} onCheckedChange={setPermissive} />
          {permissive ? 'PERMISSIVE' : 'RESTRICTIVE'}
        </label>
      </div>
      {command !== 'insert' && <Textarea mono rows={3} label="USING" value={using} onChange={(e) => setUsing(e.target.value)} placeholder="owner_name = current_user" />}
      {command !== 'select' && command !== 'delete' && <Textarea mono rows={3} label="WITH CHECK" value={withCheck} onChange={(e) => setWithCheck(e.target.value)} />}
    </FormDialog>
  )
}
