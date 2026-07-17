import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Shield, Trash2, Pencil, X } from 'lucide-react'
import { rbacApi } from '@/api/rbac'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import type { PermissionCatalog, Role } from '@/types/permissions'
import { PermissionEditor } from '@/Singra/UI/PermissionEditor'

interface RoleFormProps {
  catalog: PermissionCatalog
  initial: Role | null
  onSubmit: (name: string, description: string | null, permissions: string[]) => Promise<void>
  onCancel: () => void
}

function RoleForm({ catalog, initial, onSubmit, onCancel }: RoleFormProps) {
  const { t } = useTranslation()
  const isAdminRole = initial?.is_system && initial?.name === 'admin'
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [selected, setSelected] = useState<Set<string>>(
    new Set(initial?.permissions ?? []),
  )
  const [saving, setSaving] = useState(false)

  const allPerms = useMemo(() => [
    ...catalog.global_permissions,
    ...catalog.server_permissions,
  ], [catalog])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      await onSubmit(name.trim(), description.trim() || null, Array.from(selected).sort())
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {isAdminRole && (
        <div className="msm-alert-warning text-sm">{t('roles.adminLocked')}</div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
            {t('roles.name')}
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t('roles.namePlaceholder')}
            className="msm-input"
            disabled={!!initial?.is_system}
            required
            minLength={2}
            maxLength={64}
            pattern="^[a-zA-Z0-9_-]+$"
          />
        </div>
        <div>
          <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
            {t('roles.description')}
          </label>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={t('roles.descriptionPlaceholder')}
            className="msm-input"
            maxLength={255}
          />
        </div>
      </div>

      <div className="space-y-2">
        <span className="block font-label-md text-label-md text-on-surface-variant uppercase tracking-wider">
          {t('roles.permissions')} ({selected.size})
        </span>
        <PermissionEditor
          permissions={allPerms}
          selected={selected}
          onChange={setSelected}
          disabled={isAdminRole}
        />
      </div>

      <div className="flex gap-3 pt-2">
        <button type="button" onClick={onCancel} className="msm-btn-secondary px-4 py-2">
          {t('common.cancel')}
        </button>
        <button
          type="submit"
          disabled={saving || isAdminRole}
          className="msm-btn-primary px-4 py-2 disabled:opacity-50"
        >
          {saving ? t('common.loading') : t('common.save')}
        </button>
      </div>
    </form>
  )
}

export function Roles() {
  const { t } = useTranslation()
  const canManage = useHasPermission('roles.manage')
  const [catalog, setCatalog] = useState<PermissionCatalog | null>(null)
  const [roles, setRoles] = useState<Role[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<Role | null>(null)
  const [creating, setCreating] = useState(false)

  const refresh = async () => {
    try {
      const [cat, list] = await Promise.all([rbacApi.catalog(), rbacApi.listRoles()])
      setCatalog(cat)
      setRoles(list)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  const handleCreate = async (name: string, description: string | null, permissions: string[]) => {
    try {
      await rbacApi.createRole({ name, description, permissions })
      toast.success(t('roles.created'))
      setCreating(false)
      await refresh()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  const handleUpdate = async (
    role: Role,
    name: string,
    description: string | null,
    permissions: string[],
  ) => {
    try {
      await rbacApi.updateRole(role.id, {
        name: role.is_system ? null : name,
        description,
        permissions,
      })
      toast.success(t('roles.updated'))
      setEditing(null)
      await refresh()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  const handleDelete = async (role: Role) => {
    if (!(await confirm({ message: t('roles.confirmDelete'), danger: true, confirmText: t('common.delete') }))) return
    try {
      await rbacApi.deleteRole(role.id)
      toast.success(t('roles.deleted'))
      await refresh()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  const sortedRoles = useMemo(
    () => [...roles].sort((a, b) => Number(b.is_system) - Number(a.is_system) || a.name.localeCompare(b.name)),
    [roles],
  )

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (!catalog) {
    return null
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-headline text-headline-sm text-primary">{t('roles.title')}</h1>
          <p className="font-body-md text-body-md text-on-surface-variant mt-1">
            {t('roles.subtitle')}
          </p>
        </div>
        {canManage && (
          <button
            onClick={() => {
              setEditing(null)
              setCreating(true)
            }}
            className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2"
          >
            <Plus className="w-4 h-4" />
            {t('roles.create')}
          </button>
        )}
      </div>

      {creating && (
        <div className="msm-card p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-headline text-body-lg text-primary">{t('roles.create')}</h3>
            <button onClick={() => setCreating(false)} className="text-on-surface-variant hover:text-on-surface">
              <X className="w-4 h-4" />
            </button>
          </div>
          <RoleForm
            catalog={catalog}
            initial={null}
            onSubmit={handleCreate}
            onCancel={() => setCreating(false)}
          />
        </div>
      )}

      <div className="msm-card overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-outline-variant/50">
              <th className="text-left font-label-md text-label-md text-on-surface-variant p-4 uppercase tracking-wider">
                {t('roles.name')}
              </th>
              <th className="text-left font-label-md text-label-md text-on-surface-variant p-4 uppercase tracking-wider">
                {t('roles.description')}
              </th>
              <th className="text-left font-label-md text-label-md text-on-surface-variant p-4 uppercase tracking-wider">
                {t('roles.permissions')}
              </th>
              <th className="text-right font-label-md text-label-md text-on-surface-variant p-4 uppercase tracking-wider">
                {t('users.actions')}
              </th>
            </tr>
          </thead>
          <tbody>
            {sortedRoles.map((role) => (
              <tr key={role.id} className="border-b border-outline-variant/30 hover:bg-surface-container-high/50 transition-colors">
                <td className="p-4">
                  <div className="flex items-center gap-2">
                    {role.is_system && <Shield className="w-4 h-4 text-status-warning" />}
                    <span className="font-body-md text-on-surface">
                      {role.is_system
                        ? t(`roles.systemNames.${role.name}`, { defaultValue: role.name })
                        : role.name}
                    </span>
                    {role.is_system ? (
                      <span className="text-xs px-1.5 py-0.5 rounded bg-status-warning/10 text-status-warning border border-status-warning/30">
                        {t('roles.system')}
                      </span>
                    ) : (
                      <span className="text-xs px-1.5 py-0.5 rounded bg-status-info/10 text-status-info border border-status-info/30">
                        {t('roles.custom')}
                      </span>
                    )}
                  </div>
                </td>
                <td className="p-4 text-on-surface-variant font-body-md text-sm">
                  {role.description || '—'}
                </td>
                <td className="p-4 font-mono-sm text-mono-sm text-on-surface-variant">
                  {role.permissions.length}
                </td>
                <td className="p-4 text-right space-x-3">
                  {canManage && (
                    <button
                      onClick={() => {
                        setCreating(false)
                        setEditing(role)
                      }}
                      className="text-primary hover:text-primary/80 transition-colors inline-flex items-center"
                      title={t('common.edit')}
                    >
                      <Pencil className="w-4 h-4" />
                    </button>
                  )}
                  {canManage && !role.is_system && (
                    <button
                      onClick={() => handleDelete(role)}
                      className="text-status-error hover:text-status-error/80 transition-colors inline-flex items-center"
                      title={t('common.delete')}
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

      {editing && (
        <div className="msm-card p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-headline text-body-lg text-primary">
              {t('common.edit')}: {editing.name}
            </h3>
            <button onClick={() => setEditing(null)} className="text-on-surface-variant hover:text-on-surface">
              <X className="w-4 h-4" />
            </button>
          </div>
          <RoleForm
            catalog={catalog}
            initial={editing}
            onSubmit={(n, d, p) => handleUpdate(editing, n, d, p)}
            onCancel={() => setEditing(null)}
          />
        </div>
      )}
    </div>
  )
}
