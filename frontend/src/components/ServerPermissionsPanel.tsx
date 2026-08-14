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
      // Eine gescheiterte Abfrage darf nicht als "keine Rechte" durchgehen:
      // sonst verschwindet eine tatsächlich vergebene Delegation lautlos aus
      // der Tabelle. Wir sammeln die Fehlschläge und nennen sie beim Namen.
      const gescheitert: string[] = []
      const fetched = await Promise.all(
        candidates.map(async (u) => {
          try {
            const res = await rbacApi.getServerPermissions(u.id, serverId)
            return { user: u, permissions: res.permissions } as UserPermissionRow
          } catch {
            gescheitert.push(u.username)
            return null
          }
        }),
      )
      setRows(
        fetched
          .filter((r): r is UserPermissionRow => r !== null && r.permissions.length > 0)
          .sort((a, b) => a.user.username.localeCompare(b.user.username)),
      )
      if (gescheitert.length > 0) {
        toast.error(t('serverPermissions.loadFailed', { users: gescheitert.join(', ') }))
      }
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
    <div className="space-y-5">
      <div>
        <h3 className="font-headline text-base text-primary">{t('serverPermissions.title')}</h3>
        <p className="mt-1 font-body-md text-sm text-on-surface-variant">
          {t('serverPermissions.subtitle')}
        </p>
        <p className="mt-1.5 font-body-md text-xs text-on-surface-variant">
          {t('serverPermissions.ownerHint')}
        </p>
      </div>

      {/* User hinzufuegen */}
      <div className="flex flex-col gap-2 rounded-xl border border-outline-variant/40 bg-surface-container-low/40 p-3 sm:flex-row sm:items-end">
        <div className="min-w-0 flex-1">
          <label className="mb-1.5 block font-label-md text-[10px] uppercase tracking-wider text-on-surface-variant">
            {t('serverPermissions.selectUser')}
          </label>
          <Dropdown
            value={addingUserId === '' ? null : String(addingUserId)}
            onChange={(value) => setAddingUserId(value ? Number(value) : '')}
            placeholder={t('serverPermissions.selectUser')}
            options={usersWithoutDelegation.map((u) => ({ value: String(u.id), label: u.username }))}
            buttonClassName="text-sm py-2"
            aria-label={t('serverPermissions.selectUser')}
          />
        </div>
        <button
          type="button"
          onClick={addUser}
          disabled={!addingUserId}
          className="msm-btn-primary inline-flex min-h-10 shrink-0 items-center justify-center gap-2 px-4 py-2 disabled:opacity-50"
        >
          <Plus className="w-4 h-4" />
          {t('serverPermissions.addUser')}
        </button>
      </div>

      {rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-outline-variant/50 px-4 py-8 text-center font-body-md text-sm text-on-surface-variant">
          {t('serverPermissions.noUsers')}
        </div>
      ) : (
        <div className="divide-y divide-outline-variant/30 border-y border-outline-variant/30">
          {rows.map((row) => {
            const isEditing = editing === row.user.id
            const visiblePermissions = row.permissions.slice(0, 3)
            const hiddenPermissionCount = row.permissions.length - visiblePermissions.length
            return (
              <article key={row.user.id} className="py-4">
                <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 md:grid-cols-[minmax(10rem,1fr)_minmax(12rem,1.4fr)_auto]">
                  <div className="flex min-w-0 items-center gap-3">
                    <span
                      aria-hidden="true"
                      className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-outline-variant/50 bg-surface-container-high font-label-md text-xs font-semibold text-primary"
                    >
                      {row.user.username.slice(0, 2).toLocaleUpperCase()}
                    </span>
                    <span className="min-w-0">
                      <strong className="block break-words font-body-md text-sm font-semibold text-on-surface">
                        {row.user.username}
                      </strong>
                      <span className="mt-0.5 block break-all text-xs text-on-surface-variant">
                        {row.user.email}
                      </span>
                    </span>
                  </div>

                  {!isEditing && (
                    <div className="col-span-2 min-w-0 pl-12 md:col-span-1 md:pl-0">
                      <strong className="block font-label-md text-xs font-medium text-on-surface">
                        {t('serverPermissions.permissionCount', { count: row.permissions.length })}
                      </strong>
                      <p className="mt-1 break-words font-mono text-[10px] leading-4 text-on-surface-variant">
                        {visiblePermissions.join(' · ')}
                        {hiddenPermissionCount > 0 ? ` · +${hiddenPermissionCount}` : ''}
                      </p>
                    </div>
                  )}

                  <div className="col-start-2 row-start-1 flex items-center justify-end gap-1 md:col-start-3">
                    {isEditing ? (
                      <>
                        <button
                          type="button"
                          onClick={() => save(row.user.id)}
                          className="msm-btn-primary inline-flex min-h-9 items-center gap-1 px-3 py-1 text-xs"
                        >
                          <Save className="w-3.5 h-3.5" />
                          {t('common.save')}
                        </button>
                        <button
                          type="button"
                          onClick={cancelEdit}
                          className="msm-btn-secondary inline-flex min-h-9 items-center gap-1 px-3 py-1 text-xs"
                        >
                          <X className="w-3.5 h-3.5" />
                          {t('common.cancel')}
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        onClick={() => startEdit(row)}
                        className="rounded-lg px-3 py-2 font-label-md text-xs text-primary transition-colors hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70"
                      >
                        {t('common.edit')}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => revoke(row.user.id)}
                      className="grid h-9 w-9 place-items-center rounded-lg text-status-error transition-colors hover:bg-status-error/10 hover:text-status-error/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-status-error/60"
                      title={t('serverPermissions.revoke')}
                      aria-label={`${t('serverPermissions.revoke')}: ${row.user.username}`}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {isEditing ? (
                  <div className="mt-4 border-t border-outline-variant/30 pt-4">
                    <PermissionEditor
                      permissions={catalog.server_permissions}
                      selected={editSelection}
                      onChange={setEditSelection}
                    />
                  </div>
                ) : null}
              </article>
            )
          })}
        </div>
      )}
    </div>
  )
}
