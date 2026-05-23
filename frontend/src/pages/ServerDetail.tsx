import { useEffect, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import type { Server, GameInfo } from '@/types'
import {
  Play,
  Square,
  RefreshCw,
  Terminal,
  ArrowLeft,
  FileText,
  Package,
  HardDrive,
  Network,
  Download,
  Trash2,
} from 'lucide-react'

export function ServerDetail() {
  const { t } = useTranslation()
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [server, setServer] = useState<Server | null>(null)
  const [status, setStatus] = useState<any>(null)
  const [consoleLogs, setConsoleLogs] = useState('')
  const [games, setGames] = useState<GameInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState<string | null>(null)

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const consoleRef = useRef<HTMLPreElement>(null)

  const serverId = parseInt(id || '0')

  const fetchAll = async () => {
    if (!serverId) return
    try {
      const [srv, st, cl, gms] = await Promise.all([
        api<Server>(`/servers/${serverId}`),
        api<any>(`/servers/${serverId}/status`).catch(() => null),
        api<any>(`/servers/${serverId}/console?lines=200`).catch(() => ({ logs: '' })),
        api<GameInfo[]>('/system/games'),
      ])
      setServer(srv)
      setStatus(st)
      setConsoleLogs(cl.logs || '')
      setGames(gms)
    } catch {
      // silent
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchAll()
    const interval = setInterval(fetchAll, 5000)
    return () => clearInterval(interval)
  }, [serverId])

  useEffect(() => {
    if (consoleRef.current) {
      consoleRef.current.scrollTop = consoleRef.current.scrollHeight
    }
  }, [consoleLogs])

  const doAction = async (action: string) => {
    setActionLoading(action)
    try {
      await api(`/servers/${serverId}/${action}`, { method: 'POST' })
      fetchAll()
    } catch (err: any) {
      const msg = t(err.message) || err.message || t('common.error')
      toast.error(msg)
    } finally {
      setActionLoading(null)
    }
  }

  const handleDelete = async () => {
    setActionLoading('delete')
    try {
      await api(`/servers/${serverId}`, { method: 'DELETE' })
      toast.success(t('servers.deleted'))
      navigate('/servers')
    } catch (err: any) {
      const msg = t(err.message) || err.message || t('common.error')
      toast.error(msg)
    } finally {
      setActionLoading(null)
      setShowDeleteConfirm(false)
    }
  }

  const statusClasses = (s: string) => {
    switch (s) {
      case 'running':
        return 'bg-status-success/10 border-status-success/30 text-status-success'
      case 'stopped':
        return 'bg-surface-container-highest border-outline text-on-surface-variant'
      case 'installing':
      case 'updating':
        return 'bg-status-warning/10 border-status-warning/30 text-status-warning'
      default:
        return 'bg-status-error/10 border-status-error/30 text-status-error'
    }
  }

  const gameName = (id: string) => games.find((g) => g.id === id)?.name || id

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <span className="w-6 h-6 border-2 border-secondary border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (!server) {
    return (
      <div className="text-center py-12">
        <p className="font-body-md text-on-surface-variant">{t('servers.notFound')}</p>
        <button
          className="msm-btn-secondary mt-4 inline-flex items-center gap-2 px-4 py-2"
          onClick={() => navigate('/servers')}
        >
          <ArrowLeft className="w-4 h-4" />
          {t('servers.backToList')}
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <button
            className="p-2 rounded-md border border-outline bg-surface-container-highest hover:bg-surface-container text-on-surface transition-colors"
            onClick={() => navigate('/servers')}
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div>
            <h1 className="font-headline text-headline-sm text-primary">{server.name}</h1>
            <p className="font-body-md text-sm text-on-surface-variant">{gameName(server.game_type)}</p>
          </div>
        </div>
        <span className={`font-mono-sm text-mono-sm px-3 py-1 rounded-full border ${statusClasses(server.status)}`}>
          {server.status}
        </span>
      </div>

      {/* Actions */}
      <div className="flex gap-3 flex-wrap">
        {server.status !== 'running' && server.status !== 'installing' && (
          <button
            onClick={() => doAction('start')}
            disabled={!!actionLoading}
            className="msm-btn-primary flex items-center gap-2 px-4 py-2 disabled:opacity-50"
          >
            <Play className="w-4 h-4" />
            {actionLoading === 'start' ? t('common.loading') : t('servers.start')}
          </button>
        )}
        {server.status === 'running' && (
          <button
            onClick={() => doAction('stop')}
            disabled={!!actionLoading}
            className="msm-btn-danger flex items-center gap-2 px-4 py-2 disabled:opacity-50"
          >
            <Square className="w-4 h-4" />
            {actionLoading === 'stop' ? t('common.loading') : t('servers.stop')}
          </button>
        )}
        <button
          onClick={() => doAction('restart')}
          disabled={!!actionLoading || server.status === 'installing'}
          className="msm-btn-secondary flex items-center gap-2 px-4 py-2 disabled:opacity-50"
        >
          <RefreshCw className="w-4 h-4" />
          {actionLoading === 'restart' ? t('common.loading') : t('servers.restart')}
        </button>
        {server.status !== 'installing' && (
          <button
            onClick={() => doAction('install')}
            disabled={!!actionLoading}
            className="msm-btn-secondary flex items-center gap-2 px-4 py-2 disabled:opacity-50"
          >
            <Download className="w-4 h-4" />
            {actionLoading === 'install' ? t('common.loading') : t('servers.install')}
          </button>
        )}
        <button
          onClick={() => setShowDeleteConfirm(true)}
          disabled={!!actionLoading}
          className="msm-btn-danger flex items-center gap-2 px-4 py-2 disabled:opacity-50 ml-auto"
        >
          <Trash2 className="w-4 h-4" />
          {t('common.delete')}
        </button>
      </div>

      {/* Delete Confirmation */}
      {showDeleteConfirm && (
        <div className="msm-card p-5 border-status-error/50">
          <p className="font-body-md text-on-surface mb-4">{t('servers.confirmDelete')}</p>
          <div className="flex gap-3">
            <button
              onClick={handleDelete}
              disabled={!!actionLoading}
              className="msm-btn-danger flex items-center gap-2 px-4 py-2 disabled:opacity-50"
            >
              {actionLoading === 'delete' ? t('common.loading') : t('common.confirm')}
            </button>
            <button
              onClick={() => setShowDeleteConfirm(false)}
              className="msm-btn-secondary px-4 py-2"
            >
              {t('common.cancel')}
            </button>
          </div>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="msm-card p-5">
          <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-2">CPU</p>
          <p className="font-headline text-display-sm text-primary">{status?.cpu_percent ?? '-'}</p>
        </div>
        <div className="msm-card p-5">
          <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-2">RAM (MB)</p>
          <p className="font-headline text-display-sm text-primary">{status?.ram_mb ?? '-'}</p>
        </div>
        <div className="msm-card p-5">
          <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-2">Disk (MB)</p>
          <p className="font-headline text-display-sm text-primary">{status?.disk_mb ?? '-'}</p>
        </div>
        <div className="msm-card p-5">
          <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-2">Players</p>
          <p className="font-headline text-display-sm text-primary">{status?.players_online ?? '-'}</p>
        </div>
      </div>

      {/* Links */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div
          className="msm-card p-5 cursor-pointer hover:border-mint-accent/40 transition-all flex items-center gap-3"
          onClick={() => navigate(`/servers/${serverId}/files`)}
        >
          <FileText className="w-5 h-5 text-secondary" />
          <span className="font-body-md text-base text-on-surface">{t('servers.fileManager')}</span>
        </div>
        <div
          className="msm-card p-5 cursor-pointer hover:border-mint-accent/40 transition-all flex items-center gap-3"
          onClick={() => navigate(`/servers/${serverId}/mods`)}
        >
          <Package className="w-5 h-5 text-secondary" />
          <span className="font-body-md text-base text-on-surface">{t('servers.modManager')}</span>
        </div>
        <div
          className="msm-card p-5 cursor-pointer hover:border-mint-accent/40 transition-all flex items-center gap-3"
          onClick={() => navigate(`/servers/${serverId}/backups`)}
        >
          <HardDrive className="w-5 h-5 text-secondary" />
          <span className="font-body-md text-base text-on-surface">{t('servers.backups')}</span>
        </div>
      </div>

      {/* Ports */}
      {(server.game_port || server.query_port || server.rcon_port) && (
        <div className="msm-card p-5">
          <div className="flex items-center gap-2 mb-4">
            <Network className="w-4 h-4 text-on-surface-variant" />
            <h3 className="font-headline text-body-md text-on-surface">Netzwerk</h3>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {server.game_port && (
              <div>
                <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-1">Game Port</p>
                <p className="font-headline text-display-sm text-primary">{server.game_port} <span className="text-sm font-body-md text-on-surface-variant">UDP</span></p>
              </div>
            )}
            {server.query_port && (
              <div>
                <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-1">Query Port</p>
                <p className="font-headline text-display-sm text-primary">{server.query_port} <span className="text-sm font-body-md text-on-surface-variant">UDP</span></p>
              </div>
            )}
            {server.rcon_port && (
              <div>
                <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-1">RCON Port</p>
                <p className="font-headline text-display-sm text-primary">{server.rcon_port} <span className="text-sm font-body-md text-on-surface-variant">TCP</span></p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Console */}
      <div className="msm-card">
        <div className="p-5 border-b border-outline flex items-center gap-3">
          <Terminal className="w-4 h-4 text-on-surface-variant" />
          <h3 className="font-headline text-body-md text-on-surface">{t('servers.console')}</h3>
        </div>
        <div className="p-5">
          <pre
            ref={consoleRef}
            className="bg-surface-darkest border border-outline rounded-md p-4 h-80 overflow-auto font-mono text-xs text-on-surface-variant whitespace-pre-wrap"
          >
            {consoleLogs || t('servers.noLogs')}
          </pre>
        </div>
      </div>
    </div>
  )
}
