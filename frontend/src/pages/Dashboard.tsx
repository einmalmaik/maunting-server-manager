import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { api } from '@/api/client'
import { Server, GameInfo } from '@/types'
import { UpdateBanner } from '@/components/UpdateBanner'
import { CloudRestoreBanner } from '@/components/setup/CloudRestoreBanner'
import { CloudMigrationBanner } from '@/components/setup/CloudMigrationBanner'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Server as ServerIcon, Activity, MemoryStick, CheckCircle2, AlertTriangle, XCircle, Loader2, Clock } from 'lucide-react'
import { UptimeDisplay } from '@/components/server/UptimeDisplay'

interface ServiceStatus {
  status: 'ok' | 'degraded' | 'error'
  detail: string
}

interface HealthResponse {
  overall: 'ok' | 'degraded' | 'error'
  services: {
    docker: ServiceStatus
    caddy: ServiceStatus
    database: ServiceStatus
  }
  checked_in_ms: number
}

function ServiceDot({ status }: { status: 'ok' | 'degraded' | 'error' }) {
  if (status === 'ok') return <span className="inline-block w-2 h-2 rounded-full bg-status-success" />
  if (status === 'degraded') return <span className="inline-block w-2 h-2 rounded-full bg-status-warning" />
  return <span className="inline-block w-2 h-2 rounded-full bg-status-destructive" />
}

function SystemStatusCard() {
  const { t } = useTranslation()
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api<HealthResponse>('/system/health')
      .then(setHealth)
      .catch(() => setHealth(null))
      .finally(() => setLoading(false))
  }, [])

  const serviceNames: Record<string, string> = {
    docker: t('dashboard.systemServices.docker'),
    caddy: t('dashboard.systemServices.caddy'),
    database: t('dashboard.systemServices.database'),
  }

  const overallLabel = loading
    ? t('dashboard.systemChecking')
    : health === null
      ? t('dashboard.statusError')
      : health.overall === 'ok'
        ? t('dashboard.allSystemsOperational')
        : health.overall === 'degraded'
          ? t('dashboard.systemDegraded')
          : t('dashboard.systemError')

  const overallColor = loading || health === null
    ? 'text-on-surface-variant'
    : health.overall === 'ok'
      ? 'text-status-success'
      : health.overall === 'degraded'
        ? 'text-status-warning'
        : 'text-status-destructive'

  const OverallIcon = loading
    ? Loader2
    : health === null || (health.overall === 'error')
      ? XCircle
      : health.overall === 'degraded'
        ? AlertTriangle
        : CheckCircle2

  return (
    <div className="msm-card p-5">
      <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-3">
        {t('dashboard.system')}
      </p>
      <div className="flex items-center gap-2 mb-4">
        <OverallIcon className={`w-5 h-5 ${overallColor} ${loading ? 'animate-spin' : ''}`} />
        <span className={`font-headline text-body-lg font-bold ${overallColor}`}>{overallLabel}</span>
      </div>
      {health && (
        <div className="space-y-2">
          {Object.entries(health.services).map(([key, svc]) => (
            <div key={key} className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <ServiceDot status={svc.status} />
                <span className="font-body-md text-sm text-on-surface-variant">
                  {serviceNames[key] ?? key}
                </span>
              </div>
              <span className="font-mono-sm text-xs text-on-surface-variant/60 truncate max-w-[120px]" title={svc.detail}>
                {svc.detail}
              </span>
            </div>
          ))}
        </div>
      )}
      {!loading && !health && (
        <p className="font-body-md text-sm text-status-destructive/80">
          {t('dashboard.systemError')}
        </p>
      )}
    </div>
  )
}

export function Dashboard() {
  const { t } = useTranslation()
  const canCreateServer = useHasPermission('servers.create')
  const [servers, setServers] = useState<Server[]>([])
  const [games, setGames] = useState<GameInfo[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      api<Server[]>('/servers'),
      api<GameInfo[]>('/system/games'),
    ])
      .then(([srvs, gms]) => {
        setServers(srvs)
        setGames(gms)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const runningCount = servers.filter((s) => s.status === 'running').length

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <span className="w-6 h-6 border-2 border-secondary border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <UpdateBanner />
      <CloudRestoreBanner />
      <CloudMigrationBanner />

      <div>
        <h1 className="font-headline text-headline-sm text-primary">{t('dashboard.title')}</h1>
        <p className="font-body-md text-body-md text-on-surface-variant mt-1">
          {t('dashboard.subtitle')}
        </p>
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Server-Zähler */}
        <div className="msm-card p-5">
          <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-2">
            {t('dashboard.servers')}
          </p>
          <div className="flex items-baseline gap-3">
            <span className="font-headline text-display-sm text-primary">{servers.length}</span>
            <span className={`font-body-md text-sm px-2 py-0.5 rounded-full border ${
              runningCount > 0
                ? 'bg-status-success/10 border-status-success/30 text-status-success'
                : 'bg-surface-container-highest border-outline text-on-surface-variant'
            }`}>
              {runningCount} {t('dashboard.running')}
            </span>
          </div>
        </div>

        {/* Unterstützte Spiele — kompakt */}
        <div className="msm-card p-5">
          <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-2">
            {t('dashboard.supportedGames')}
          </p>
          <span className="font-headline text-display-sm text-primary">{games.length}</span>
          {games.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-3 max-h-20 overflow-hidden">
              {games.slice(0, 6).map((g) => (
                <span
                  key={g.id}
                  className="font-mono-sm text-mono-sm px-2 py-0.5 rounded-full border border-outline text-on-surface-variant text-xs"
                >
                  {g.name}
                </span>
              ))}
              {games.length > 6 && (
                <span className="font-mono-sm text-mono-sm px-2 py-0.5 rounded-full bg-surface-container-highest text-on-surface-variant text-xs">
                  +{games.length - 6}
                </span>
              )}
            </div>
          )}
        </div>

        {/* Echter Systemstatus */}
        <SystemStatusCard />
      </div>

      {/* Empty State */}
      {servers.length === 0 && (
        <div className="msm-card p-12 text-center border-dashed border-2 border-outline-variant">
          <ServerIcon className="w-10 h-10 text-on-surface-variant mx-auto mb-4" />
          <h3 className="font-headline text-body-lg text-on-surface mb-1">
            {t('dashboard.noServers')}
          </h3>
          <p className="font-body-md text-sm text-on-surface-variant mb-4">
            {t('dashboard.createFirstServer')}
          </p>
          {canCreateServer && (
            <a
              href="/servers"
              className="msm-btn-primary inline-flex items-center gap-2 px-4 py-2"
            >
              {t('dashboard.createServer')}
            </a>
          )}
        </div>
      )}

      {/* Server Cards */}
      {servers.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {servers.map((server) => (
            <Link
              key={server.id}
              to={`/servers/${server.id}`}
              className="msm-card p-5 block hover:border-primary/40 hover:bg-surface-container-high/40 transition-all duration-200 group cursor-pointer"
            >
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <ServerIcon className="w-4 h-4 text-on-surface-variant group-hover:text-primary transition-colors" />
                  <h3 className="font-headline text-body-md text-on-surface group-hover:text-primary transition-colors">{server.name}</h3>
                </div>
                <span className={`font-mono-sm text-mono-sm px-2 py-0.5 rounded-full border ${
                  server.status === 'running'
                    ? 'bg-status-success/10 border-status-success/30 text-status-success'
                    : server.status === 'stopped'
                    ? 'bg-surface-container-highest border-outline text-on-surface-variant'
                    : 'bg-status-warning/10 border-status-warning/30 text-status-warning'
                }`}>
                  {t(`servers.status.${server.status}`, { defaultValue: server.status })}
                </span>
              </div>
              <p className="font-body-md text-sm text-on-surface-variant mb-4">
                {games.find((g) => g.id === server.game_type)?.name || server.game_type}
              </p>
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div className="flex items-center gap-2 text-on-surface-variant">
                  <Activity className="w-3.5 h-3.5" />
                  <span className="font-body-md">
                    CPU: {server.cpu_limit_percent ? `${server.cpu_limit_percent}%` : t('common.unlimited')}
                  </span>
                </div>
                <div className="flex items-center gap-2 text-on-surface-variant">
                  <MemoryStick className="w-3.5 h-3.5" />
                  <span className="font-body-md">
                    RAM: {server.ram_limit_mb ? `${server.ram_limit_mb} MB` : t('common.unlimited')}
                  </span>
                </div>
                {server.status === 'running' && (
                  <div className="flex items-center gap-2 text-on-surface-variant col-span-2">
                    <Clock className="w-3.5 h-3.5" />
                    <UptimeDisplay server={server} label={t('serverDetail.uptime', { defaultValue: 'Uptime' })} />
                  </div>
                )}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
