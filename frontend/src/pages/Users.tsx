import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, Shield, Mail, CheckCircle, XCircle } from 'lucide-react'
import { api } from '@/api/client'
import { rbacApi } from '@/api/rbac'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { useAuthStore } from '@/stores/authStore'
import { ServerPermissionsPanel } from '@/components/ServerPermissionsPanel'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { Dropdown } from '@/components/ui/Dropdown'
import type { Server, User } from '@/types'
import type { Role } from '@/types/permissions'

export function Users() {
  const { t } = useTranslation()
  const currentUser = useAuthStore((s) => s.user)
  const canManageUsers = useHasPermission('users.manage')
  const canManagePermissions = useHasPermission('users.permissions.manage')
  const [users, setUsers] = useState<User[]>([])
  const [roles, setRoles] = useState<Role[]>([])
  const [servers, setServers] = useState<Server[]>([])
  const [permServerId, setPermServerId] = useState<number | ''>('')
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

  const assignRole = async (user: User, roleId: number | null) => {
    try {
      await rbacApi.assignRole(user.id, roleId)
      toast.success(t('users.roleSaved'))
      await fetchAll()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
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
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-headline text-headline-sm text-primary">{t('nav.users')}</h1>
          <p className="font-body-md text-body-md text-on-surface-variant mt-1">
            {t('users.subtitle')}
          </p>
        </div>
        {canManageUsers && (
          <button
            onClick={() => setShowCreate(!showCreate)}
            className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2"
          >
            <Plus className="w-4 h-4" />
            {t('users.createUser')}
          </button>
        )}
      </div>

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
        <div className="msm-card p-5 space-y-4">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div>
              <h3 className="font-headline text-body-lg text-primary">{t('serverPermissions.adminTitle')}</h3>
              <p className="font-body-md text-sm text-on-surface-variant mt-1">{t('serverPermissions.adminSubtitle')}</p>
            </div>
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider text-xs">
                {t('serverPermissions.selectServer')}
              </label>
              <Dropdown
                value={permServerId === '' ? null : String(permServerId)}
                onChange={(value) => setPermServerId(value ? Number(value) : '')}
                placeholder={t('serverPermissions.selectServerPlaceholder')}
                options={servers.map((s) => ({ value: String(s.id), label: s.name }))}
                buttonClassName="text-sm"
              />
            </div>
          </div>
          {typeof permServerId === 'number' && (
            <div className="border-t border-outline-variant pt-4">
              <ServerPermissionsPanel serverId={permServerId} />
            </div>
          )}
        </div>
      )}

      <div className="msm-card overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-outline-variant/50">
              <th className="text-left font-label-md text-label-md text-on-surface-variant p-4 uppercase tracking-wider">
                {t('auth.username')}
              </th>
              <th className="text-left font-label-md text-label-md text-on-surface-variant p-4 uppercase tracking-wider">
                {t('auth.email')}
              </th>
              <th className="text-left font-label-md text-label-md text-on-surface-variant p-4 uppercase tracking-wider">
                Status
              </th>
              <th className="text-left font-label-md text-label-md text-on-surface-variant p-4 uppercase tracking-wider">
                {t('users.role')}
              </th>
              <th className="text-right font-label-md text-label-md text-on-surface-variant p-4 uppercase tracking-wider">
                {t('users.actions')}
              </th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr key={user.id} className="border-b border-outline-variant/30 hover:bg-surface-container-high/50 transition-colors">
                <td className="p-4">
                  <div className="flex items-center gap-2">
                    {user.is_owner && <Shield className="w-4 h-4 text-status-warning" />}
                    <span className="font-body-md text-on-surface">{user.username}</span>
                  </div>
                </td>
                <td className="p-4">
                  <div className="flex items-center gap-2">
                    <Mail className="w-3.5 h-3.5 text-on-surface-variant" />
                    <span className="font-body-md text-sm text-on-surface-variant">{user.email}</span>
                    {user.email_verified ? (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium bg-status-success/10 text-status-success border border-status-success/30" title={t('users.emailVerified')}>
                        <CheckCircle className="w-3 h-3" />
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium bg-status-error/10 text-status-error border border-status-error/30" title={t('users.emailNotVerified')}>
                        <XCircle className="w-3 h-3" />
                      </span>
                    )}
                  </div>
                </td>
                <td className="p-4">
                  <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
                    user.is_active
                      ? 'bg-status-success/10 text-status-success border border-status-success/30'
                      : 'bg-status-error/10 text-status-error border border-status-error/30'
                  }`}>
                    {user.is_active ? t('users.active') : t('users.inactive')}
                  </span>
                </td>
                <td className="p-4">
                  {user.is_owner ? (
                    <span className="font-mono-sm text-mono-sm text-on-surface-variant">owner</span>
                  ) : canManagePermissions && user.id !== currentUser?.id ? (
                    <Dropdown
                      value={user.role_id != null ? String(user.role_id) : null}
                      onChange={(value) => assignRole(user, value ? Number(value) : null)}
                      placeholder={t('users.noRole')}
                      options={roles.map((r) => ({
                        value: String(r.id),
                        label: r.is_system
                          ? t(`roles.systemNames.${r.name}`, { defaultValue: r.name })
                          : r.name,
                      }))}
                      buttonClassName="text-sm py-1"
                      aria-label={t('users.assignRole')}
                    />
                  ) : (
                    <span className="font-mono-sm text-mono-sm text-on-surface-variant">
                      {(() => {
                        const role = roles.find((r) => r.id === user.role_id)
                        if (!role) return t('users.noRole')
                        return role.is_system
                          ? t(`roles.systemNames.${role.name}`, { defaultValue: role.name })
                          : role.name
                      })()}
                    </span>
                  )}
                </td>
                <td className="p-4 text-right">
                  {canManageUsers && !user.is_owner && user.id !== currentUser?.id && (
                    <button
                      onClick={() => handleDelete(user.id)}
                      className="text-status-error hover:text-status-error/80 transition-colors"
                      title={t('users.delete')}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
