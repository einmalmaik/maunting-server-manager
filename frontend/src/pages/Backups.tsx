import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { HardDrive, Plus, RotateCcw, Trash2, ArrowLeft } from 'lucide-react'

interface Backup {
  id: number
  server_id: number
  filename: string
  size_mb: number | null
  created_at: string
  expires_at: string | null
}

export function Backups() {
  const { t } = useTranslation()
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const serverId = parseInt(id || '0')
  const [backups, setBackups] = useState<Backup[]>([])
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState<string | null>(null)

  const fetchBackups = async () => {
    if (!serverId) return
    try {
      const data = await api<Backup[]>(`/backups/${serverId}`)
      setBackups(data)
    } catch {
      // silent
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchBackups()
  }, [serverId])

  const createBackup = async () => {
    setActionLoading('create')
    try {
      await api(`/backups/${serverId}`, { method: 'POST' })
      toast.success(t('backups.created'))
      fetchBackups()
    } catch (err: any) {
      toast.error(err.message || t('common.error'))
    } finally {
      setActionLoading(null)
    }
  }

  const restoreBackup = async (backupId: number) => {
    if (!confirm(t('backups.confirmRestore'))) return
    setActionLoading(`restore-${backupId}`)
    try {
      await api(`/backups/${serverId}/restore/${backupId}`, { method: 'POST' })
      toast.success(t('backups.restored'))
    } catch (err: any) {
      toast.error(err.message || t('common.error'))
    } finally {
      setActionLoading(null)
    }
  }

  const deleteBackup = async (backupId: number) => {
    if (!confirm(t('backups.confirmDelete'))) return
    setActionLoading(`delete-${backupId}`)
    try {
      await api(`/backups/${serverId}/${backupId}`, { method: 'DELETE' })
      toast.success(t('backups.deletedBackup'))
      fetchBackups()
    } catch (err: any) {
      toast.error(err.message || t('common.error'))
    } finally {
      setActionLoading(null)
    }
  }

  const formatDate = (iso: string) => {
    try {
      return new Date(iso).toLocaleString()
    } catch {
      return iso
    }
  }

  if (!serverId) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="font-headline text-headline-sm text-primary">{t('nav.backups')}</h1>
          <p className="font-body-md text-body-md text-on-surface-variant mt-1">
            {t('backups.selectServer')}
          </p>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <span className="w-6 h-6 border-2 border-secondary border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <button
            className="p-2 rounded-md border border-outline bg-surface-container-highest hover:bg-surface-container text-on-surface transition-colors"
            onClick={() => navigate(`/servers/${serverId}`)}
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div>
            <h1 className="font-headline text-headline-sm text-primary">{t('nav.backups')}</h1>
            <p className="font-body-md text-body-md text-on-surface-variant mt-1">
              {t('backups.subtitle')}
            </p>
          </div>
        </div>
        <button
          onClick={createBackup}
          disabled={!!actionLoading}
          className="msm-btn-primary flex items-center gap-2 px-4 py-2 disabled:opacity-50"
        >
          <Plus className="w-4 h-4" />
          {actionLoading === 'create' ? t('common.loading') : t('backups.create')}
        </button>
      </div>

      {backups.length === 0 ? (
        <div className="msm-card p-12 text-center border-dashed border-2 border-outline-variant">
          <HardDrive className="w-10 h-10 text-on-surface-variant mx-auto mb-4" />
          <h3 className="font-headline text-body-lg text-on-surface mb-1">
            {t('backups.noBackups')}
          </h3>
          <p className="font-body-md text-sm text-on-surface-variant">
            {t('backups.createHint')}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {backups.map((backup) => (
            <div key={backup.id} className="msm-card p-4 flex items-center justify-between">
              <div className="flex items-center gap-4">
                <HardDrive className="w-5 h-5 text-on-surface-variant" />
                <div>
                  <p className="font-body-md text-on-surface text-sm">
                    {formatDate(backup.created_at)}
                  </p>
                  <p className="font-mono-sm text-xs text-on-surface-variant">
                    {backup.size_mb != null ? `${backup.size_mb} MB` : '—'}
                  </p>
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => restoreBackup(backup.id)}
                  disabled={!!actionLoading}
                  className="msm-btn-secondary flex items-center gap-1 px-3 py-1.5 text-sm disabled:opacity-50"
                  title={t('backups.restore')}
                >
                  <RotateCcw className="w-3.5 h-3.5" />
                  {t('backups.restore')}
                </button>
                <button
                  onClick={() => deleteBackup(backup.id)}
                  disabled={!!actionLoading}
                  className="msm-btn-danger flex items-center gap-1 px-3 py-1.5 text-sm disabled:opacity-50"
                  title={t('common.delete')}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
