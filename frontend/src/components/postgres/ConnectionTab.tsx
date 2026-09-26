import { useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Download, Eye, Globe, KeyRound, Network, Plus, RefreshCw, RotateCw, Server, Trash2 } from 'lucide-react'
import { api } from '@/api/client'
import { Badge, Button, Dropdown, Switch, Textarea } from '@/Singra/UI'
import { CodeBlock } from '@/components/docs/CodeBlock'
import { prompt } from '@/stores/promptStore'
import { toast } from '@/stores/toastStore'
import { useStudio } from './StudioContext'
import { buildSnippets, type ConnectionTarget } from './connectionSnippets'
import { ErrorBox, Field, Loading, Section, useLoad } from './shared'

interface HubUser {
  id: number
  username: string
  password_stored: boolean
}

interface ConnectionInfo {
  kind: 'shared' | 'dedicated'
  internal: { host: string; port: number }
  external: { host: string; port: number; reachable: boolean; blocked_by?: 'loopback' | 'no_networks' | null; ssl_required: boolean; allowed_cidrs: string[] } | null
  ssl_certificate: string | null
  bootstrap_pending: boolean
  databases: { id: number; name: string; owner_role: string; owner_revealable: boolean; users: HubUser[] }[]
}

type Where = 'internal' | 'external'

/**
 * Verbindungs-Hub: Endpunkte, Zugangsdaten (abrufbar mit Admin-Recht),
 * Snippets für gängige Treiber, Netzfreigaben der eigenen Instanz.
 */
export function ConnectionTab({ onResourcesChanged }: { onResourcesChanged: () => void }) {
  const { t } = useTranslation()
  const { serverId, databaseId, canAdmin } = useStudio()
  const info = useLoad(() => api<ConnectionInfo>(`/servers/${serverId}/databases/connection`), [serverId])
  const [where, setWhere] = useState<Where>('internal')
  const [who, setWho] = useState<string>('owner')
  const [revealed, setRevealed] = useState<Record<string, string>>({})

  const database = info.data?.databases.find((d) => d.id === databaseId) || info.data?.databases[0]
  const external = info.data?.external
  const userKey = (userId: number | null) => `${database?.id}:${userId ?? 'owner'}`
  const selectedUser = who === 'owner' ? null : database?.users.find((u) => String(u.id) === who) || null
  const username = selectedUser ? selectedUser.username : database?.owner_role || ''

  const target: ConnectionTarget | null = useMemo(() => {
    if (!info.data || !database) return null
    const endpoint = where === 'external' && external ? external : info.data.internal
    return {
      host: endpoint.host,
      port: endpoint.port,
      database: database.name,
      user: username,
      password: revealed[userKey(selectedUser?.id ?? null)] || '',
      sslmode: where === 'external' && external ? (external.ssl_required ? 'require' : 'prefer') : 'prefer',
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info.data, database, where, external, username, revealed, selectedUser])

  const reveal = async (userId: number | null) => {
    if (!database) return
    try {
      const result = await api<{ username: string; password: string }>(`/servers/${serverId}/databases/credentials/reveal`, {
        method: 'POST',
        body: JSON.stringify({ database_id: database.id, user_id: userId }),
      })
      setRevealed((r) => ({ ...r, [userKey(userId)]: result.password }))
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  const rotate = async (user: HubUser) => {
    try {
      const result = await api<{ password: string }>(`/servers/${serverId}/databases/users/${user.id}/rotate`, { method: 'POST' })
      setRevealed((r) => ({ ...r, [userKey(user.id)]: result.password }))
      toast.success(t('postgresStudio.connection.rotated', { user: user.username }))
      await info.reload()
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  const createUser = async () => {
    if (!database) return
    const name = await prompt({ title: t('postgresStudio.connection.newUser'), message: t('postgresStudio.connection.newUserMessage'), placeholder: 'app_reader' })
    if (!name) return
    try {
      const result = await api<{ credential: { username: string; password: string } }>(`/servers/${serverId}/databases/users`, {
        method: 'POST',
        body: JSON.stringify({ database_id: database.id, username: name }),
      })
      await info.reload()
      toast.success(t('postgresStudio.connection.userCreated', { user: result.credential.username }))
      onResourcesChanged()
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  const deleteUser = async (user: HubUser) => {
    const typed = await prompt({
      title: t('postgresStudio.connection.deleteUser'),
      message: t('postgresStudio.connection.deleteUserMessage', { user: user.username }),
      expectedValue: user.username,
      confirmText: t('common.delete'),
      danger: true,
    })
    if (!typed) return
    try {
      await api(`/servers/${serverId}/databases/users/${user.id}`, { method: 'DELETE', body: JSON.stringify({ confirm_name: typed }) })
      if (who === String(user.id)) setWho('owner')
      await info.reload()
      onResourcesChanged()
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  const retryBootstrap = async () => {
    try {
      const result = await api<{ created?: string[] }>(`/servers/${serverId}/databases/instance/bootstrap`, { method: 'POST' })
      toast.success(t('postgresStudio.connection.bootstrapDone', { count: result.created?.length ?? 0 }))
      await info.reload()
      onResourcesChanged()
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  const downloadCert = () => {
    if (!info.data?.ssl_certificate) return
    const blob = new Blob([info.data.ssl_certificate], { type: 'application/x-pem-file' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `msm-server-${serverId}-postgres.crt`
    link.click()
    URL.revokeObjectURL(url)
  }

  if (info.loading && !info.data) return <Loading />
  if (info.error) return <ErrorBox message={info.error} />
  if (!info.data || !database) return null

  const owner = { password: revealed[userKey(null)] }
  return (
    <div className="space-y-4">
      {info.data.bootstrap_pending && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-status-warning/40 bg-status-warning/10 p-3 text-sm text-status-warning" data-testid="bootstrap-pending">
          {t('postgresStudio.connection.bootstrapPending')}
          {canAdmin && (
            <Button size="sm" variant="secondary" onClick={() => void retryBootstrap()}>
              <RefreshCw className="h-3.5 w-3.5" />
              {t('postgresStudio.connection.bootstrapRetry')}
            </Button>
          )}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title={<span className="inline-flex items-center gap-2"><Server className="h-4 w-4" />{t('postgresStudio.connection.internal')}</span>}>
          <p className="font-mono text-sm text-on-surface" data-testid="endpoint-internal">{info.data.internal.host}:{info.data.internal.port}</p>
          <p className="mt-1 text-xs text-on-surface-variant">{t('postgresStudio.connection.internalHint')}</p>
        </Section>
        <Section title={<span className="inline-flex items-center gap-2"><Globe className="h-4 w-4" />{t('postgresStudio.connection.external')}</span>}>
          {external ? (
            <>
              <p className="font-mono text-sm text-on-surface" data-testid="endpoint-external">{external.host}:{external.port}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Badge variant={external.reachable ? 'success' : 'default'}>{external.reachable ? t('postgresStudio.connection.reachable') : t('postgresStudio.connection.localOnly')}</Badge>
                <Badge variant={external.ssl_required ? 'success' : 'warning'}>{external.ssl_required ? t('postgresStudio.connection.sslRequired') : t('postgresStudio.connection.sslOptional')}</Badge>
                {info.data.ssl_certificate && (
                  <Button size="sm" variant="ghost" onClick={downloadCert}>
                    <Download className="h-3.5 w-3.5" />
                    {t('postgresStudio.connection.certificate')}
                  </Button>
                )}
              </div>
              {!external.reachable && (
                <p className="mt-2 text-xs text-on-surface-variant">
                  {external.blocked_by === 'loopback' ? t('postgresStudio.connection.loopbackHint') : t('postgresStudio.connection.localOnlyHint')}
                </p>
              )}
            </>
          ) : (
            <p className="text-sm text-on-surface-variant">{t('postgresStudio.connection.sharedNoExternal')}</p>
          )}
        </Section>
      </div>

      <Section
        title={<span className="inline-flex items-center gap-2"><KeyRound className="h-4 w-4" />{t('postgresStudio.connection.credentials')}</span>}
        actions={
          canAdmin ? (
            <Button size="sm" variant="secondary" onClick={() => void createUser()} data-testid="new-db-user">
              <Plus className="h-3.5 w-3.5" />
              {t('postgresStudio.connection.newUser')}
            </Button>
          ) : undefined
        }
      >
        <ul className="space-y-2">
          <CredentialRow
            label={t('postgresStudio.connection.owner')}
            username={database.owner_role}
            password={owner.password}
            canReveal={canAdmin && database.owner_revealable}
            revealHint={!database.owner_revealable ? t('postgresStudio.connection.ownerNotRevealable') : undefined}
            onReveal={() => void reveal(null)}
          />
          {database.users.map((user) => (
            <CredentialRow
              key={user.id}
              label={t('postgresStudio.connection.appUser')}
              username={user.username}
              password={revealed[userKey(user.id)]}
              canReveal={canAdmin && user.password_stored}
              revealHint={!user.password_stored ? t('postgresStudio.connection.notStored') : undefined}
              onReveal={() => void reveal(user.id)}
              actions={
                canAdmin ? (
                  <>
                    <Button size="icon" variant="ghost" aria-label={t('postgresStudio.connection.rotate', { user: user.username })} onClick={() => void rotate(user)}>
                      <RotateCw className="h-4 w-4" />
                    </Button>
                    <Button size="icon" variant="ghost" aria-label={t('postgresStudio.connection.deleteUser')} onClick={() => void deleteUser(user)}>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </>
                ) : undefined
              }
            />
          ))}
        </ul>
      </Section>

      <Section title={t('postgresStudio.connection.snippets')}>
        <div className="grid gap-3 md:grid-cols-2">
          <Field label={t('postgresStudio.connection.route')}>
            <Dropdown
              value={where}
              onChange={(v) => setWhere(v as Where)}
              options={[
                { value: 'internal', label: t('postgresStudio.connection.internal') },
                ...(external ? [{ value: 'external', label: t('postgresStudio.connection.external'), disabled: !external.reachable }] : []),
              ]}
              aria-label={t('postgresStudio.connection.route')}
            />
          </Field>
          <Field label={t('postgresStudio.connection.user')}>
            <Dropdown
              value={who}
              onChange={setWho}
              options={[{ value: 'owner', label: `${database.owner_role} (${t('postgresStudio.connection.owner')})` }, ...database.users.map((u) => ({ value: String(u.id), label: u.username }))]}
              aria-label={t('postgresStudio.connection.user')}
            />
          </Field>
        </div>
        {target && !target.password && <p className="mt-2 text-xs text-on-surface-variant">{t('postgresStudio.connection.placeholderHint')}</p>}
        {target && (
          <div data-testid="snippets">
            {buildSnippets(target).map((snippet) => (
              <CodeBlock key={snippet.key} label={snippet.label} code={snippet.code} copyLabel={t('common.copy')} copiedLabel={t('common.copied')} testId={`snippet-${snippet.key}`} />
            ))}
          </div>
        )}
      </Section>

      {info.data.kind === 'dedicated' && external && canAdmin && (
        <NetworkSection external={external} onSaved={() => void info.reload()} />
      )}
    </div>
  )
}

function CredentialRow({ label, username, password, canReveal, revealHint, onReveal, actions }: {
  label: string
  username: string
  password?: string
  canReveal: boolean
  revealHint?: string
  onReveal: () => void
  actions?: ReactNode
}) {
  const { t } = useTranslation()
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-outline-variant p-3">
      <div className="min-w-0">
        <span className="text-xs text-on-surface-variant">{label}</span>
        <p className="font-mono text-sm text-on-surface">{username}</p>
        {password && (
          <p className="mt-1 break-all rounded bg-status-warning/10 px-2 py-1 font-mono text-xs text-status-warning" data-testid={`password-${username}`}>
            {password}
          </p>
        )}
        {revealHint && <p className="mt-1 text-xs text-on-surface-variant">{revealHint}</p>}
      </div>
      <div className="flex items-center gap-1">
        {canReveal && !password && (
          <Button size="sm" variant="secondary" onClick={onReveal} data-testid={`reveal-${username}`}>
            <Eye className="h-3.5 w-3.5" />
            {t('postgresStudio.connection.reveal')}
          </Button>
        )}
        {actions}
      </div>
    </li>
  )
}

function NetworkSection({ external, onSaved }: { external: { ssl_required: boolean; allowed_cidrs: string[] }; onSaved: () => void }) {
  const { t } = useTranslation()
  const { serverId } = useStudio()
  const [cidrs, setCidrs] = useState(external.allowed_cidrs.join('\n'))
  const [ssl, setSsl] = useState(external.ssl_required)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      await api(`/servers/${serverId}/databases/instance/network`, {
        method: 'PUT',
        body: JSON.stringify({ allowed_cidrs: cidrs.split('\n').map((c) => c.trim()).filter(Boolean), ssl_required: ssl }),
      })
      toast.success(t('postgresStudio.connection.networkSaved'))
      onSaved()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Section title={<span className="inline-flex items-center gap-2"><Network className="h-4 w-4" />{t('postgresStudio.connection.network')}</span>}>
      <p className="mb-3 text-xs text-on-surface-variant">{t('postgresStudio.connection.networkHint')}</p>
      <Textarea mono rows={5} label={t('postgresStudio.connection.allowlist')} value={cidrs} onChange={(e) => setCidrs(e.target.value)} placeholder={'203.0.113.10/32\n198.51.100.0/24'} data-testid="allowlist" />
      <label className="mt-3 flex items-center gap-2 text-sm text-on-surface">
        <Switch checked={ssl} onCheckedChange={setSsl} />
        {t('postgresStudio.connection.sslRequired')}
      </label>
      <div className="mt-3">
        <ErrorBox message={error} />
      </div>
      <Button className="mt-3" onClick={() => void save()} disabled={busy}>{busy ? t('common.saving') : t('common.save')}</Button>
    </Section>
  )
}
