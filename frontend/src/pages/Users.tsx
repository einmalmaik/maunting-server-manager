import { useState, useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, Shield, Mail, CheckCircle, XCircle, Search, Server as ServerIcon } from 'lucide-react'
import { api } from '@/api/client'
import { rbacApi } from '@/api/rbac'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { useAuthStore } from '@/stores/authStore'
import { ServerPermissionsPanel } from '@/components/ServerPermissionsPanel'
import { PasswordInput } from '@/components/ui/PasswordInput'
import type { Server, User } from '@/types'
import type { Role } from '@/types/permissions'
import { PageHeader } from '@/Singra/UI/PageHeader'
import { MultiSelect } from '@/Singra/UI/MultiSelect'

export function Users() {
  const { t } = useTranslation()
  const currentUser = useAuthStore((s) => s.user)
  const canManageUsers = useHasPermission('users.manage')
  const canManagePermissions = useHasPermission('users.permissions.manage')
  const [users, setUsers] = useState<User[]>([])
  const [roles, setRoles] = useState<Role[]>([])
  const [servers, setServers] = useState<Server[]>([])
  const [permServerId, setPermServerId] = useState<number | ''>('')
  const [serverSearch, setServerSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [createForm, setCreateForm] = useState({
    username: '',
    email: '',
    password: '',
    is_owner: false,
    auto_verify: false,
  })
  const [creating, setCreating] = useState(false)
  const [savingRoleUserId, setSavingRoleUserId] = useState<number | null>(null)

  const filteredServers = useMemo(() => {
    const query = serverSearch.trim().toLocaleLowerCase()
    if (!query) return servers
    return servers.filter((server) => server.name.toLocaleLowerCase().includes(query))
  }, [serverSearch, servers])

  const selectedServer = useMemo(
    () => servers.find((server) => server.id === permServerId),
    [permServerId, servers],
  )

  const fetchAll = async () => {
    try {
      const [u, r, s] = await Promise.all([
        api<User[]>('/admin/users'),
        rbacApi.listRoles().catch(() => [] as Role[]),
        canManagePermissions ? api<Server[]>('/servers').catch(() => [] as Server[]) : Promise.resolve([] as Server[]),
      ])
      setUsers(u)
      setRoles(r)
      setServers(s)
      if (canManagePermissions && s.length > 0 && permServerId === '') {
        setPermServerId(s[0].id)
      }
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void fetchAll()
  }, [])

  /** Speichert das vollständige Rollen-Set atomar und verhindert parallele Updates pro Ansicht. */
  const assignRoles = async (user: User, roleIds: number[]) => {
    if (savingRoleUserId !== null) return
    setSavingRoleUserId(user.id)
    try {
      const updated = await rbacApi.assignRoles(user.id, roleIds)
      setUsers((current) => current.map((entry) => entry.id === updated.id ? updated : entry))
      toast.success(t('users.roleSaved'))
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setSavingRoleUserId(null)
    }
  }

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setCreating(true)
    try {
      await api('/admin/users', {
        method: 'POST',
        body: JSON.stringify(createForm),
      })
      setShowCreate(false)
      setCreateForm({ username: '', email: '', password: '', is_owner: false, auto_verify: false })
      await fetchAll()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async (userId: number) => {
    if (!(await confirm({ message: t('users.confirmDelete'), danger: true, confirmText: t('common.delete') }))) return
    try {
      await api(`/admin/users/${userId}`, { method: 'DELETE' })
      await fetchAll()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="msm-page">
      <PageHeader eyebrow={t('pageContext.administration', 'Administration')} title={t('nav.users')} description={t('users.subtitle')} status={<span className="msm-badge-info">{users.length} {t('nav.users')}</span>} actions={canManageUsers ? (
          <button
            onClick={() => setShowCreate(!showCreate)}
            className="msm-btn-primary min-h-11 px-4 py-2 inline-flex items-center gap-2"
          >
            <Plus className="w-4 h-4" />
            {t('users.createUser')}
          </button>) : undefined} />

      {showCreate && (
        <div className="msm-card p-6">
          <h3 className="font-headline text-body-lg text-primary mb-4">
            {t('users.createUser')}
          </h3>
          <form onSubmit={handleCreate} className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('auth.username')}
              </label>
              <input
                type="text"
                value={createForm.username}
                onChange={(e) => setCreateForm({ ...createForm, username: e.target.value })}
                className="msm-input"
                required
                minLength={3}
              />
            </div>
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('auth.email')}
              </label>
              <input
                type="email"
                value={createForm.email}
                onChange={(e) => setCreateForm({ ...createForm, email: e.target.value })}
                className="msm-input"
                required
              />
            </div>
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('auth.password')}
              </label>
              <PasswordInput
                value={createForm.password}
                onChange={(e) => setCreateForm({ ...createForm, password: e.target.value })}
                required
                minLength={8}
              />
            </div>
            <div className="flex items-end gap-6">
              {/* Owner-Accounts darf ausschliesslich der Owner selbst anlegen.
                  Backend lehnt is_owner=true fuer Non-Owner mit 403 ab; das UI
                  spiegelt diese Invariante, damit Admins die Option nicht
                  sehen und nicht versehentlich versuchen. */}
              {currentUser?.is_owner && (
                <label className="flex items-center gap-2 cursor-pointer">
                  <div className={`relative w-10 h-6 rounded-full transition-colors ${createForm.is_owner ? 'bg-secondary' : 'bg-surface-container-highest'}`}>
                    <input
                      type="checkbox"
                      checked={createForm.is_owner}
                      onChange={(e) => setCreateForm({ ...createForm, is_owner: e.target.checked })}
                      className="sr-only"
                    />
                    <span className={`absolute top-1 left-1 w-4 h-4 bg-on-surface rounded-full transition-transform ${createForm.is_owner ? 'translate-x-4 bg-on-secondary' : ''}`} />
                  </div>
                  <span className="font-body-md text-sm text-on-surface-variant">
                    {t('users.isOwner')}
                  </span>
                </label>
              )}
              <label className="flex items-center gap-2 cursor-pointer">
                <div className={`relative w-10 h-6 rounded-full transition-colors ${createForm.auto_verify ? 'bg-secondary' : 'bg-surface-container-highest'}`}>
                  <input
                    type="checkbox"
                    checked={createForm.auto_verify}
                    onChange={(e) => setCreateForm({ ...createForm, auto_verify: e.target.checked })}
                    className="sr-only"
                  />
                  <span className={`absolute top-1 left-1 w-4 h-4 bg-on-surface rounded-full transition-transform ${createForm.auto_verify ? 'translate-x-4 bg-on-secondary' : ''}`} />
                </div>
                <span className="font-body-md text-sm text-on-surface-variant">
                  {t('users.autoVerify')}
                </span>
              </label>
            </div>
            <div className="md:col-span-2 flex gap-3">
              <button
                type="button"
                onClick={() => setShowCreate(false)}
                className="msm-btn-secondary px-4 py-2"
              >
                {t('common.cancel')}
              </button>
              <button
                type="submit"
                disabled={creating}
                className="msm-btn-primary px-4 py-2 disabled:opacity-50"
              >
                {creating ? t('common.loading') : t('users.createUser')}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Server-Berechtigungen verwalten (verschoben aus ServerDetail).
          Quelle der Wahrheit bleibt das Backend; UI ist nur Convenience. */}
      {canManagePermissions && servers.length > 0 && (
        <section aria-labelledby="server-access-title" className="space-y-3">
          <div>
            <h2 id="server-access-title" className="font-headline text-body-lg text-primary">
              {t('serverPermissions.adminTitle')}
            </h2>
            <p className="mt-1 font-body-md text-sm text-on-surface-variant">
              {t('serverPermissions.adminSubtitle')}
            </p>
          </div>

          <div
            className="msm-card grid min-w-0 overflow-hidden lg:grid-cols-[19rem_minmax(0,1fr)]"
            data-testid="server-access-workspace"
          >
            <aside className="min-w-0 border-b border-outline-variant/50 bg-surface-container-low/35 p-4 lg:border-b-0 lg:border-r">
              <label
                htmlFor="server-access-search"
                className="mb-2 block font-label-md text-xs uppercase tracking-wider text-on-surface-variant"
              >
                {t('serverPermissions.selectServer')}
              </label>
              <div className="relative">
                <Search
                  aria-hidden="true"
                  className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-on-surface-variant"
                />
                <input
                  id="server-access-search"
                  type="search"
                  value={serverSearch}
                  onChange={(event) => setServerSearch(event.target.value)}
                  placeholder={t('serverPermissions.searchServer')}
                  className="msm-input pl-9 text-sm"
                />
              </div>

              <div
                className="mt-3 flex max-h-72 flex-col gap-1.5 overflow-y-auto pr-1 lg:max-h-[32rem]"
                aria-label={t('serverPermissions.selectServer')}
              >
                {filteredServers.length > 0 ? (
                  filteredServers.map((server) => {
                    const isSelected = server.id === permServerId
                    return (
                      <button
                        key={server.id}
                        type="button"
                        onClick={() => setPermServerId(server.id)}
                        aria-pressed={isSelected}
                        aria-current={isSelected ? 'true' : undefined}
                        className={`grid min-h-14 w-full grid-cols-[1rem_minmax(0,1fr)] gap-2.5 rounded-lg border px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70 ${
                          isSelected
                            ? 'border-primary/35 bg-primary/10 text-primary'
                            : 'border-transparent text-on-surface hover:border-outline-variant/50 hover:bg-surface-container-high/60'
                        }`}
                      >
                        <span
                          aria-hidden="true"
                          className={`mt-1.5 h-1.5 w-1.5 rounded-full ${
                            server.status === 'running' ? 'bg-status-success' : 'bg-on-surface-variant/45'
                          }`}
                        />
                        <span className="min-w-0">
                          <span className="block break-words font-body-md text-sm font-semibold leading-5">
                            {server.name}
                          </span>
                          <span className="mt-1 block font-mono text-[10px] uppercase tracking-wide text-on-surface-variant">
                            {server.game_type}
                          </span>
                        </span>
                      </button>
                    )
                  })
                ) : (
                  <p className="px-3 py-6 text-center font-body-md text-sm text-on-surface-variant">
                    {t('serverPermissions.noServersFound')}
                  </p>
                )}
              </div>
            </aside>

            <div className="min-w-0">
              {selectedServer && typeof permServerId === 'number' ? (
                <>
                  <header className="flex min-w-0 items-start gap-3 border-b border-outline-variant/40 px-4 py-4 sm:px-6">
                    <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-primary/20 bg-primary/5 text-primary">
                      <ServerIcon aria-hidden="true" className="h-4 w-4" />
                    </span>
                    <div className="min-w-0">
                      <p className="font-label-md text-[10px] uppercase tracking-wider text-on-surface-variant">
                        {t('serverPermissions.selectedServer')}
                      </p>
                      <h3 className="mt-1 break-words font-headline text-base leading-6 text-on-surface">
                        {selectedServer.name}
                      </h3>
                    </div>
                  </header>
                  <div className="p-4 sm:p-6">
                    <ServerPermissionsPanel serverId={permServerId} />
                  </div>
                </>
              ) : null}
            </div>
          </div>
        </section>
      )}

      <section aria-labelledby="registered-users-title" className="space-y-3">
        <div>
          <h2 id="registered-users-title" className="font-headline text-body-lg text-primary">
            {t('users.registeredTitle')}
          </h2>
          <p className="mt-1 font-body-md text-sm text-on-surface-variant">
            {t('users.registeredSubtitle')}
          </p>
        </div>

        <div className="msm-card min-w-0" data-testid="user-directory">
          <div
            aria-hidden="true"
            className="hidden grid-cols-[minmax(9rem,1fr)_minmax(12rem,1.35fr)_7rem_minmax(10rem,12rem)_2.75rem] gap-4 border-b border-outline-variant/50 bg-surface-container-low/35 px-5 py-3 font-label-md text-[10px] uppercase tracking-wider text-on-surface-variant md:grid md:rounded-t-lg"
          >
            <span>{t('auth.username')}</span>
            <span>{t('auth.email')}</span>
            <span>Status</span>
            <span>{t('users.role')}</span>
            <span />
          </div>

          <div className="divide-y divide-outline-variant/30">
            {users.map((user) => {
              const assignedRoleIds = user.role_ids?.length
                ? user.role_ids
                : user.role_id != null
                  ? [user.role_id]
                  : []
              const assignedRoles = roles.filter((candidate) => assignedRoleIds.includes(candidate.id))
              const roleLabel = assignedRoles.length > 0
                ? assignedRoles.map((role) => role.is_system
                    ? t(`roles.systemNames.${role.name}`, { defaultValue: role.name })
                    : role.name,
                  ).join(', ')
                : t('users.noRole')

              return (
                <article
                  key={user.id}
                  className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-3 px-4 py-4 transition-colors last:rounded-b-lg hover:bg-surface-container-high/30 md:grid-cols-[minmax(9rem,1fr)_minmax(12rem,1.35fr)_7rem_minmax(10rem,12rem)_2.75rem] md:items-center md:gap-4 md:px-5"
                >
                  <div className="flex min-w-0 items-center gap-3">
                    <span
                      aria-hidden="true"
                      className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-outline-variant/50 bg-surface-container-high font-label-md text-xs font-semibold text-primary"
                    >
                      {user.username.slice(0, 2).toLocaleUpperCase()}
                    </span>
                    <span className="min-w-0">
                      <span className="flex items-center gap-1.5 break-words font-body-md text-sm font-semibold text-on-surface">
                        {user.is_owner && <Shield aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-status-warning" />}
                        {user.username}
                      </span>
                      {user.id === currentUser?.id && (
                        <span className="mt-0.5 block text-xs text-on-surface-variant">{t('users.currentUser')}</span>
                      )}
                    </span>
                  </div>

                  <div className="col-span-2 min-w-0 md:col-span-1">
                    <div className="flex min-w-0 items-start gap-2">
                      <Mail aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-on-surface-variant" />
                      <span className="min-w-0 break-all font-body-md text-sm text-on-surface-variant">
                        {user.email}
                      </span>
                    </div>
                    <span
                      className={`mt-1.5 inline-flex items-center gap-1 text-xs ${
                        user.email_verified ? 'text-status-success' : 'text-status-warning'
                      }`}
                    >
                      {user.email_verified ? (
                        <CheckCircle aria-hidden="true" className="h-3 w-3" />
                      ) : (
                        <XCircle aria-hidden="true" className="h-3 w-3" />
                      )}
                      {user.email_verified ? t('users.emailVerified') : t('users.emailNotVerified')}
                    </span>
                  </div>

                  <span className="col-start-1 inline-flex items-center gap-2 text-xs text-on-surface-variant md:col-auto">
                    <span
                      aria-hidden="true"
                      className={`h-1.5 w-1.5 rounded-full ${
                        user.is_active ? 'bg-status-success' : 'bg-on-surface-variant/45'
                      }`}
                    />
                    {user.is_active ? t('users.active') : t('users.inactive')}
                  </span>

                  <div className="col-span-2 min-w-0 md:col-span-1">
                    {user.is_owner ? (
                      <span className="font-mono-sm text-mono-sm text-status-warning">owner</span>
                    ) : canManagePermissions && user.id !== currentUser?.id ? (
                      <MultiSelect
                        values={assignedRoleIds.map(String)}
                        onChange={(values) => void assignRoles(user, values.map(Number))}
                        placeholder={t('users.noRole')}
                        options={roles.map((candidate) => ({
                          value: String(candidate.id),
                          label: candidate.is_system
                            ? t(`roles.systemNames.${candidate.name}`, { defaultValue: candidate.name })
                            : candidate.name,
                          disabled: candidate.name === 'admin' && !currentUser?.is_owner,
                        }))}
                        disabled={savingRoleUserId !== null}
                        aria-label={`${t('users.assignRole')}: ${user.username}`}
                      />
                    ) : (
                      <span className="font-mono-sm text-mono-sm text-on-surface-variant">{roleLabel}</span>
                    )}
                  </div>

                  <div className="col-start-2 row-start-1 flex justify-end md:col-auto md:row-auto">
                    {canManageUsers && user.id !== currentUser?.id && (!user.is_owner || currentUser?.is_owner) && (
                      <button
                        type="button"
                        onClick={() => handleDelete(user.id)}
                        className="grid h-10 w-10 place-items-center rounded-lg text-status-error transition-colors hover:bg-status-error/10 hover:text-status-error/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-status-error/60"
                        title={t('users.delete')}
                        aria-label={`${t('users.delete')}: ${user.username}`}
                      >
                        <Trash2 aria-hidden="true" className="h-4 w-4" />
                      </button>
                    )}
                  </div>
                </article>
              )
            })}
          </div>
        </div>
      </section>
    </div>
  )
}
