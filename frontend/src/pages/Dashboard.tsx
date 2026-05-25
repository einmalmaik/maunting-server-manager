import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { Server, GameInfo } from '@/types'
import { UpdateBanner } from '@/components/UpdateBanner'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Server as ServerIcon, Activity, Users } from 'lucide-react'

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

      <div>
        <h1 className="font-headline text-headline-sm text-primary">{t('dashboard.title')}</h1>
        <p className="font-body-md text-body-md text-on-surface-variant mt-1">
          {t('dashboard.subtitle')}
        </p>
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
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

        <div className="msm-card p-5">
          <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-2">
            {t('dashboard.supportedGames')}
          </p>
          <span className="font-headline text-display-sm text-primary">{games.length}</span>
          <div className="flex flex-wrap gap-2 mt-3">
            {games.map((g) => (
              <span
                key={g.id}
                className="font-mono-sm text-mono-sm px-2 py-0.5 rounded-full border border-outline text-on-surface-variant"
              >
                {g.name}
              </span>
            ))}
          </div>
        </div>

        <div className="msm-card p-5">
          <p className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-2">
            {t('dashboard.system')}
          </p>
          <span className="font-headline text-display-sm text-status-success">OK</span>
          <p className="font-body-md text-sm text-on-surface-variant mt-2">
            {t('dashboard.allSystemsOperational')}
          </p>
        </div>
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
            <div key={server.id} className="msm-card p-5">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <ServerIcon className="w-4 h-4 text-on-surface-variant" />
                  <h3 className="font-headline text-body-md text-on-surface">{server.name}</h3>
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
                  <Users className="w-3.5 h-3.5" />
                  <span className="font-body-md">
                    RAM: {server.ram_limit_mb ? `${server.ram_limit_mb} MB` : t('common.unlimited')}
                  </span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}