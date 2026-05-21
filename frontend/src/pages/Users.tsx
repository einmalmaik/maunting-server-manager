import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, Shield, Mail, CheckCircle, XCircle } from 'lucide-react'
import { api } from '@/api/client'
import type { User } from '@/types'

export function Users() {
  const { t } = useTranslation()
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [createForm, setCreateForm] = useState({
    username: '',
    email: '',
    password: '',
    is_owner: false,
  })
  const [creating, setCreating] = useState(false)

  const fetchUsers = async () => {
    try {
      const data = await api<User[]>('/admin/users')
      setUsers(data)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchUsers()
  }, [])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setCreating(true)
    try {
      await api('/admin/users', {
        method: 'POST',
        body: JSON.stringify(createForm),
      })
      setShowCreate(false)
      setCreateForm({ username: '', email: '', password: '', is_owner: false })
      await fetchUsers()
    } catch (err: any) {
      setError(err.message)
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async (userId: number) => {
    if (!window.confirm(t('users.confirmDelete', 'User wirklich löschen?'))) return
    try {
      await api(`/admin/users/${userId}`, { method: 'DELETE' })
      await fetchUsers()
    } catch (err: any) {
      setError(err.message)
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
            Benutzer und Berechtigungen verwalten
          </p>
        </div>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className="msm-btn-primary px-4 py-2 inline-flex items-center gap-2"
        >
          <Plus className="w-4 h-4" />
          {t('users.createUser', 'User erstellen')}
        </button>
      </div>

      {error && (
        <div className="msm-alert-error text-sm">{error}</div>
      )}

      {showCreate && (
        <div className="msm-card p-6">
          <h3 className="font-headline text-body-lg text-primary mb-4">
            {t('users.createUser', 'User erstellen')}
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
              <input
                type="password"
                value={createForm.password}
                onChange={(e) => setCreateForm({ ...createForm, password: e.target.value })}
                className="msm-input"
                required
                minLength={8}
              />
            </div>
            <div className="flex items-end">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={createForm.is_owner}
                  onChange={(e) => setCreateForm({ ...createForm, is_owner: e.target.checked })}
                  className="w-4 h-4 rounded border-outline bg-surface-container-high"
                />
                <span className="font-body-md text-sm text-on-surface-variant">
                  {t('users.isOwner', 'Owner-Rechte')}
                </span>
              </label>
            </div>
            <div className="md:col-span-2 flex gap-3">
              <button
                type="button"
                onClick={() => setShowCreate(false)}
                className="msm-btn-secondary px-4 py-2"
              >
                {t('common.cancel', 'Abbrechen')}
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
              <th className="text-right font-label-md text-label-md text-on-surface-variant p-4 uppercase tracking-wider">
                Aktionen
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
                      <CheckCircle className="w-3.5 h-3.5 text-status-success" />
                    ) : (
                      <XCircle className="w-3.5 h-3.5 text-status-error" />
                    )}
                  </div>
                </td>
                <td className="p-4">
                  <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
                    user.is_active
                      ? 'bg-status-success/10 text-status-success border border-status-success/30'
                      : 'bg-status-error/10 text-status-error border border-status-error/30'
                  }`}>
                    {user.is_active ? 'Aktiv' : 'Inaktiv'}
                  </span>
                </td>
                <td className="p-4 text-right">
                  {!user.is_owner && (
                    <button
                      onClick={() => handleDelete(user.id)}
                      className="text-status-error hover:text-status-error/80 transition-colors"
                      title={t('users.delete', 'Löschen')}
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
