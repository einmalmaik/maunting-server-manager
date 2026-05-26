import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowLeft,
  Download,
  FileText,
  HardDrive,
  Network,
  Package,
  Play,
  RefreshCw,
  Square,
  Terminal,
  Trash2,
} from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { useHostInterfaces } from '@/hooks/useHostInterfaces'
import { FileManager } from './FileManager'
import { ModManager } from './ModManager'
import { Backups } from './Backups'
import { ServerConsolePanel } from '@/components/server/ServerConsolePanel'
import type { GameInfo, Server } from '@/types'

type TabKey = 'files' | 'console' | 'mods' | 'backups'
const VALID_TABS: TabKey[] = ['files', 'console', 'mods', 'backups']

interface ServerStatus {
  status?: string
  cpu_percent?: number | null
  ram_mb?: number | null
  disk_used_mb?: number | null
  disk_free_mb?: number | null
  cpu_limit_percent?: number | null
  ram_limit_mb?: number | null
  disk_limit_gb?: number | null
  message?: string | null
}

/** Formatiert MB als kompakte Angabe (MB / GB). */
function formatMb(mb: number | null | undefined): string {
  if (mb == null) return '-'
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`
  return `${Math.round(mb)} MB`
}

function statusClasses(s: string | undefined): string {
  switch (s) {
    case 'running':
      return 'bg-status-success/10 border-status-success/30 text-status-success'
    case 'stopped':
      return 'bg-surface-container-highest border-outline text-on-surface-variant'
    case 'installing':
    case 'updating':
    case 'awaiting_files':
      return 'bg-status-warning/10 border-status-warning/30 text-status-warning'
    default:
      return 'bg-status-error/10 border-status-error/30 text-status-error'
  }
}

export function ServerDetail() {
  const { t } = useTranslation()
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [server, setServer] = useState<Server | null>(null)
  const [status, setStatus] = useState<ServerStatus | null>(null)
  const [games, setGames] = useState<GameInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState<string | null>(null)

  const [showEditNetwork, setShowEditNetwork] = useState(false)
  const [savingNetwork, setSavingNetwork] = useState(false)
  const [networkForm, setNetworkForm] = useState({
    public_bind_ip: '',
    game_port: '',
    query_port: '',
    rcon_port: '',
  })
  const { interfaces } = useHostInterfaces()

  const serverId = parseInt(id || '0')

  const fetchAll = async () => {
    if (!serverId) return
    try {
      const [srv, st, gms] = await Promise.all([
        api<Server>(`/servers/${serverId}`),
        api<ServerStatus>(`/servers/${serverId}/status`).catch(() => null),
        api<GameInfo[]>('/system/games'),
      ])
      setServer(srv)
      setStatus(st)
      setGames(gms)
    } catch {
      // silent
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void fetchAll()
    const handle = setInterval(fetchAll, 5000)
    return () => clearInterval(handle)
  }, [serverId])

  useEffect(() => {
    if (server && showEditNetwork) {
      setNetworkForm({
        public_bind_ip: server.public_bind_ip || '',
        game_port: server.game_port ? String(server.game_port) : '',
        query_port: server.query_port ? String(server.query_port) : '',
        rcon_port: server.rcon_port ? String(server.rcon_port) : '',
      })
    }
  }, [server, showEditNetwork])

  // Capability-Flag: nur wenn das Plugin das Steam-Workshop unterstuetzt, wird
  // der Tab gerendert. Die Quelle ist /api/system/games — dort steht jetzt
  // `supports_steam_workshop`. UI-Filter ist NUR Convenience; Backend
  // verweigert /api/mods/* ohnehin, wenn das Plugin keine Mods kann.
  const gameInfo = useMemo(
    () => games.find((g) => g.id === server?.game_type),
    [games, server?.game_type],
  )
  const showModTab = !!gameInfo?.supports_steam_workshop

  const tabs = useMemo(() => {
    const list: { key: TabKey; label: string; icon: typeof FileText }[] = [
      { key: 'files', label: t('tabs.files'), icon: FileText },
      { key: 'console', label: t('tabs.console'), icon: Terminal },
    ]
    if (showModTab) list.push({ key: 'mods', label: t('tabs.mods'), icon: Package })
    list.push({ key: 'backups', label: t('tabs.backups'), icon: HardDrive })
    return list
  }, [t, showModTab])

  const rawTab = (searchParams.get('tab') || 'files') as TabKey
  const activeTab: TabKey = VALID_TABS.includes(rawTab) && (rawTab !== 'mods' || showModTab)
    ? rawTab
    : 'files'

  const setActiveTab = (next: TabKey) => {
    const params = new URLSearchParams(searchParams)
    params.set('tab', next)
    setSearchParams(params, { replace: true })
  }

  const doAction = async (action: string) => {
    setActionLoading(action)
    try {
      await api(`/servers/${serverId}/${action}`, { method: 'POST' })
      void fetchAll()
    } catch (err: unknown) {
      const raw = err instanceof Error ? err.message : String(err)
      const msg = t(raw, { defaultValue: raw }) || t('common.error')
      toast.error(msg)
    } finally {
      setActionLoading(null)
    }
  }

  const handleSaveNetwork = async (e: React.FormEvent) => {
    e.preventDefault()
    setSavingNetwork(true)
    try {
      const body: Record<string, unknown> = {}
      if (networkForm.public_bind_ip !== (server?.public_bind_ip || '')) {
        body.public_bind_ip = networkForm.public_bind_ip || null
      }
      const portChanged = (field: 'game_port' | 'query_port' | 'rcon_port') => {
        const current = server?.[field] ? String(server[field]) : ''
        return networkForm[field] !== current
      }
      if (portChanged('game_port')) {
        body.game_port = networkForm.game_port ? parseInt(networkForm.game_port) : null
      }
      if (portChanged('query_port')) {
        body.query_port = networkForm.query_port ? parseInt(networkForm.query_port) : null
      }
      if (portChanged('rcon_port')) {
        body.rcon_port = networkForm.rcon_port ? parseInt(networkForm.rcon_port) : null
      }
      if (Object.keys(body).length === 0) {
        setShowEditNetwork(false)
        return
      }
      await api<Server>(`/servers/${serverId}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      })
      toast.success(t('servers.networkSaved'))
      setShowEditNetwork(false)
      void fetchAll()
    } catch (err: unknown) {
      const raw = err instanceof Error ? err.message : String(err)
      toast.error(t(raw, { defaultValue: raw }) || t('common.error'))
    } finally {
      setSavingNetwork(false)
    }
  }

  const handleDelete = async () => {
    const ok = await confirm({
      message: t('servers.confirmDelete'),
      danger: true,
      confirmText: t('common.delete'),
    })
    if (!ok) return
    setActionLoading('delete')
    try {
      await api(`/servers/${serverId}`, { method: 'DELETE' })
      toast.success(t('servers.deleted'))
      navigate('/servers')
    } catch (err: unknown) {
      const raw = err instanceof Error ? err.message : String(err)
      toast.error(t(raw, { defaultValue: raw }) || t('common.error'))
    } finally {
      setActionLoading(null)
    }
  }

  const gameName = (gameId: string) => games.find((g) => g.id === gameId)?.name || gameId

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
      <div className="flex items-center justify-between gap-3 flex-wrap">
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
          {t(`servers.status.${server.status}`, { defaultValue: server.status })}
        </span>
      </div>

      {/* Warnung: keine Bind-IP */}
      {!server.public_bind_ip && server.status !== 'running' && (
        <div className="msm-card p-4 border-status-warning/40 bg-status-warning/5 flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-status-warning flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="font-headline text-body-md text-on-surface mb-1">
              {t('servers.bindIp.startBlockedTitle')}
            </p>
            <p className="font-body-md text-sm text-on-surface-variant">
              {t('servers.bindIp.startBlockedBody')}
            </p>
          </div>
          <button
            onClick={() => setShowEditNetwork(true)}
            className="msm-btn-primary px-3 py-1.5 text-sm"
          >
            {t('servers.bindIp.assignNow')}
          </button>
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-3 flex-wrap">
        {server.status !== 'running' && server.status !== 'installing' && (
          <button
            onClick={() => doAction('start')}
            disabled={!!actionLoading || !server.public_bind_ip}
            className="msm-btn-primary flex items-center gap-2 px-4 py-2 disabled:opacity-50"
            title={!server.public_bind_ip ? t('servers.bindIp.startBlockedTitle') : undefined}
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
          onClick={handleDelete}
          disabled={!!actionLoading}
          className="msm-btn-danger flex items-center gap-2 px-4 py-2 disabled:opacity-50 ml-auto"
        >
          <Trash2 className="w-4 h-4" />
          {t('common.delete')}
        </button>
      </div>

      {/* Stats (CPU / RAM / Disk) — Player-Stat ist absichtlich entfernt (KISS, kein A2S) */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="msm-card p-5">
          <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-2">CPU</p>
          <p className="font-headline text-display-sm text-primary">
            {status?.cpu_percent != null ? `${status.cpu_percent.toFixed(1)}%` : '-'}
          </p>
          <p className="font-body-md text-xs text-on-surface-variant mt-1">
            {t('serverDetail.limit')}: {status?.cpu_limit_percent ? `${status.cpu_limit_percent}%` : t('common.unlimited')}
          </p>
        </div>
        <div className="msm-card p-5">
          <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-2">RAM</p>
          <p className="font-headline text-display-sm text-primary">
            {status?.ram_mb != null ? formatMb(status.ram_mb) : '-'}
          </p>
          <p className="font-body-md text-xs text-on-surface-variant mt-1">
            {t('serverDetail.limit')}: {status?.ram_limit_mb ? formatMb(status.ram_limit_mb) : t('common.unlimited')}
          </p>
        </div>
        <div className="msm-card p-5">
          <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-2">Disk</p>
          <p className="font-headline text-display-sm text-primary">
            {status?.disk_used_mb != null ? formatMb(status.disk_used_mb) : '-'}
          </p>
          <p className="font-body-md text-xs text-on-surface-variant mt-1">
            {status?.disk_limit_gb
              ? `${t('serverDetail.limit')}: ${status.disk_limit_gb} GB`
              : status?.disk_free_mb != null
                ? `${formatMb(status.disk_free_mb)} ${t('serverDetail.free')}`
                : t('common.unlimited')}
          </p>
        </div>
      </div>

      {/* Network */}
      <div className="msm-card p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Network className="w-4 h-4 text-on-surface-variant" />
            <h3 className="font-headline text-body-md text-on-surface">{t('servers.network')}</h3>
          </div>
          <button
            onClick={() => setShowEditNetwork(true)}
            className="msm-btn-secondary px-3 py-1.5 text-sm"
          >
            {t('common.edit')}
          </button>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div>
            <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-1">{t('servers.publicBindIp')}</p>
            <p className="font-headline text-body-md text-primary break-all">
              {server.public_bind_ip || <span className="text-status-warning">{t('servers.bindIp.unset')}</span>}
            </p>
          </div>
          <div>
            <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-1">{t('servers.gamePort')}</p>
            <p className="font-headline text-display-sm text-primary">{server.game_port ?? '-'} <span className="text-sm font-body-md text-on-surface-variant">UDP</span></p>
          </div>
          <div>
            <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-1">{t('servers.queryPort')}</p>
            <p className="font-headline text-display-sm text-primary">{server.query_port ?? '-'} <span className="text-sm font-body-md text-on-surface-variant">UDP</span></p>
          </div>
          <div>
            <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-1">{t('servers.rconPort')}</p>
            <p className="font-headline text-display-sm text-primary">{server.rcon_port ?? '-'} <span className="text-sm font-body-md text-on-surface-variant">TCP</span></p>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-outline overflow-x-auto -mb-px">
        <div className="flex gap-1 min-w-max">
          {tabs.map((tab) => {
            const Icon = tab.icon
            const isActive = activeTab === tab.key
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`px-4 py-2.5 inline-flex items-center gap-2 border-b-2 text-sm font-body-md transition-colors ${
                  isActive
                    ? 'border-secondary text-primary'
                    : 'border-transparent text-on-surface-variant hover:text-on-surface'
                }`}
              >
                <Icon className="w-4 h-4" />
                {tab.label}
              </button>
            )
          })}
        </div>
      </div>

      <div>
        {activeTab === 'files' && <FileManager serverId={serverId} />}
        {activeTab === 'console' && <ServerConsolePanel serverId={serverId} />}
        {activeTab === 'mods' && showModTab && <ModManager serverId={serverId} />}
        {activeTab === 'backups' && <Backups serverId={serverId} />}
      </div>

      {/* Edit-Network Modal */}
      {showEditNetwork && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 overflow-y-auto">
          <div className="msm-card w-full max-w-lg p-6 my-8">
            <h2 className="font-headline text-headline-md text-primary mb-1">
              {t('servers.editNetworkTitle')}
            </h2>
            <p className="font-body-md text-sm text-on-surface-variant mb-6">
              {t('servers.editNetworkDescription')}
            </p>
            <form onSubmit={handleSaveNetwork} className="space-y-4">
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('servers.publicBindIp')}
                </label>
                <select
                  className="msm-input"
                  value={networkForm.public_bind_ip}
                  onChange={(e) => setNetworkForm({ ...networkForm, public_bind_ip: e.target.value })}
                  required
                >
                  <option value="">{t('servers.bindIp.choose')}</option>
                  {interfaces.map((iface) => (
                    <option key={`${iface.interface}-${iface.ip}`} value={iface.ip}>
                      {iface.ip} · {iface.interface}
                      {iface.is_loopback ? ` (${t('servers.bindIp.loopback')})` : ''}
                      {iface.is_private && !iface.is_loopback ? ` (${t('servers.bindIp.private')})` : ''}
                    </option>
                  ))}
                </select>
                <p className="font-body-md text-xs text-on-surface-variant mt-1">{t('servers.bindIp.hint')}</p>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                    {t('servers.gamePort')}
                  </label>
                  <input
                    type="number"
                    min={1024}
                    max={65535}
                    value={networkForm.game_port}
                    onChange={(e) => setNetworkForm({ ...networkForm, game_port: e.target.value })}
                    className="msm-input"
                  />
                </div>
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                    {t('servers.queryPort')}
                  </label>
                  <input
                    type="number"
                    min={1024}
                    max={65535}
                    value={networkForm.query_port}
                    onChange={(e) => setNetworkForm({ ...networkForm, query_port: e.target.value })}
                    className="msm-input"
                  />
                </div>
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                    {t('servers.rconPort')}
                  </label>
                  <input
                    type="number"
                    min={1024}
                    max={65535}
                    value={networkForm.rcon_port}
                    onChange={(e) => setNetworkForm({ ...networkForm, rcon_port: e.target.value })}
                    className="msm-input"
                  />
                </div>
              </div>
              <div className="flex gap-3 pt-2">
                <button
                  type="button"
                  className="msm-btn-secondary flex-1 py-2"
                  onClick={() => setShowEditNetwork(false)}
                >
                  {t('common.cancel')}
                </button>
                <button
                  type="submit"
                  className="msm-btn-primary flex-1 py-2 disabled:opacity-50"
                  disabled={savingNetwork}
                >
                  {savingNetwork ? t('common.loading') : t('common.save')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
