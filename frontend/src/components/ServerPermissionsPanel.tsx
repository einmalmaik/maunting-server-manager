import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, Save, X } from 'lucide-react'
import { api } from '@/api/client'
import { rbacApi } from '@/api/rbac'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { Dropdown } from '@/components/ui/Dropdown'
import type { User } from '@/types'
import type { PermissionCatalog } from '@/types/permissions'
import { PermissionEditor } from '@/Singra/UI/PermissionEditor'

interface Props {
  serverId: number
}

interface UserPermissionRow {
  user: User
  permissions: string[]
}

/** Pro-Server-Delegation: zeigt pro Sub-User welche `server.*`-Keys er hier hat,
 *  und erlaubt Hinzufuegen, Editieren, komplettes Revoken.
 *
 *  Auf der Server-Ebene gibt es bewusst KEIN `servers.delete` (PLAN-Entscheidung).
 */
export function ServerPermissionsPanel({ serverId }: Props) {
  const { t } = useTranslation()
  const [catalog, setCatalog] = useState<PermissionCatalog | null>(null)
  const [allUsers, setAllUsers] = useState<User[]>([])
  const [rows, setRows] = useState<UserPermissionRow[]>([])
  const [loading, setLoading] = useState(true)
  const [addingUserId, setAddingUserId] = useState<number | ''>('')
  const [editing, setEditing] = useState<number | null>(null)
  const [editSelection, setEditSelection] = useState<Set<string>>(new Set())

  const refresh = async () => {
    try {
      const [cat, users] = await Promise.all([
        rbacApi.catalog(),
        api<User[]>('/admin/users'),
      ])
      setCatalog(cat)
      setAllUsers(users)

      // Fuer alle nicht-Owner-User schauen, ob es Permissions auf diesem Server gibt.
      const candidates = users.filter((u) => !u.is_owner)
      const fetched = await Promise.all(
        candidates.map(async (u) => {
          try {
            const res = await rbacApi.getServerPermissions(u.id, serverId)
            return { user: u, permissions: res.permissions } as UserPermissionRow
          } catch {
            return null
          }
        }),
      )
      setRows(
        fetched
          .filter((r): r is UserPermissionRow => r !== null && r.permissions.length > 0)
          .sort((a, b) => a.user.username.localeCompare(b.user.username)),
      )
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
  }, [serverId])

  const usersWithoutDelegation = useMemo(() => {
    const taken = new Set(rows.map((r) => r.user.id))
    return allUsers.filter((u) => !u.is_owner && !taken.has(u.id))
  }, [allUsers, rows])

  const startEdit = (row: UserPermissionRow) => {
    setEditing(row.user.id)
    setEditSelection(new Set(row.permissions))
  }

  const cancelEdit = () => {
    setEditing(null)
    setEditSelection(new Set())
  }

  const save = async (userId: number) => {
    try {
      await rbacApi.setServerPermissions(userId, serverId, Array.from(editSelection).sort())
      toast.success(t('serverPermissions.saved'))
      cancelEdit()
      await refresh()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  const addUser = async () => {
    if (!addingUserId || typeof addingUserId !== 'number') return
    try {
      await rbacApi.setServerPermissions(addingUserId, serverId, ['server.view'])
      toast.success(t('serverPermissions.saved'))
      setAddingUserId('')
      await refresh()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  const revoke = async (userId: number) => {
    if (!(await confirm({ message: t('serverPermissions.revokeConfirm'), danger: true }))) return
    try {
      await rbacApi.revokeServerPermissions(userId, serverId)
      toast.success(t('serverPermissions.saved'))
      await refresh()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-32">
        <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }
  if (!catalog) return null

  return (
    <div className="space-y-4">
      <div>
        <h3 className="font-headline text-body-lg text-primary">{t('serverPermissions.title')}</h3>
        <p className="font-body-md text-sm text-on-surface-variant mt-1">
          {t('serverPermissions.subtitle')}
        </p>
        <p className="font-body-md text-xs text-on-surface-variant mt-2">
          {t('serverPermissions.ownerHint')}
        </p>
      </div>

      {/* User hinzufuegen */}
      <div className="flex items-center gap-2">
        <Dropdown
          value={addingUserId === '' ? null : String(addingUserId)}
          onChange={(value) => setAddingUserId(value ? Number(value) : '')}
          placeholder={t('serverPermissions.selectUser')}
          options={usersWithoutDelegation.map((u) => ({ value: String(u.id), label: u.username }))}
          className="flex-1"
          buttonClassName="text-sm py-2"
          aria-label={t('serverPermissions.selectUser')}
        />
        <button
          onClick={addUser}
          disabled={!addingUserId}
          className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2 disabled:opacity-50"
        >
          <Plus className="w-4 h-4" />
          {t('serverPermissions.addUser')}
        </button>
      </div>

      {rows.length === 0 ? (
        <div className="msm-card p-6 text-center text-on-surface-variant">
          {t('serverPermissions.noUsers')}
        </div>
      ) : (
        <div className="space-y-2">
          {rows.map((row) => {
            const isEditing = editing === row.user.id
            return (
              <div key={row.user.id} className="msm-card p-4">
                <div className="flex items-center justify-between mb-3">
                  <span className="font-body-md text-on-surface">{row.user.username}</span>
                  <div className="flex gap-2">
                    {isEditing ? (
                      <>
                        <button
                          onClick={() => save(row.user.id)}
                          className="msm-btn-primary px-3 py-1 text-xs inline-flex items-center gap-1"
                        >
                          <Save className="w-3.5 h-3.5" />
                          {t('common.save')}
                        </button>
                        <button
                          onClick={cancelEdit}
                          className="msm-btn-secondary px-3 py-1 text-xs inline-flex items-center gap-1"
                        >
                          <X className="w-3.5 h-3.5" />
                          {t('common.cancel')}
                        </button>
                      </>
                    ) : (
                      <button
                        onClick={() => startEdit(row)}
                        className="msm-btn-secondary px-3 py-1 text-xs"
                      >
                        {t('common.edit')}
                      </button>
                    )}
                    <button
                      onClick={() => revoke(row.user.id)}
                      className="text-status-error hover:text-status-error/80 transition-colors"
                      title={t('serverPermissions.revoke')}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {isEditing ? (
                  <div className="mt-2 border-t border-outline-variant/30 pt-3">
                    <PermissionEditor
                      permissions={catalog.server_permissions}
                      selected={editSelection}
                      onChange={setEditSelection}
                    />
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {row.permissions.length === 0 ? (
                      <span className="text-xs text-on-surface-variant">{t('roles.noPermissions')}</span>
                    ) : (
                      row.permissions.map((k) => (
                        <span
                          key={k}
                          className="font-mono-sm text-mono-sm px-2 py-0.5 rounded bg-surface-container-high text-on-surface-variant border border-outline-variant/30"
                        >
                          {k}
                        </span>
                      ))
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
