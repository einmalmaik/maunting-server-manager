import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Server, GameInfo } from '@/types'
import { Server as ServerIcon, Activity, Users } from 'lucide-react'

export function Dashboard() {
  const { t } = useTranslation()
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
        <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">{t('dashboard.title')}</h1>
        <p className="text-muted-foreground mt-1">{t('dashboard.subtitle')}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>{t('dashboard.servers')}</CardDescription>
            <div className="flex items-baseline gap-2">
              <CardTitle className="text-3xl">{servers.length}</CardTitle>
              <Badge variant={runningCount > 0 ? 'success' : 'default'}>
                {runningCount} {t('dashboard.running')}
              </Badge>
            </div>
          </CardHeader>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription>{t('dashboard.supportedGames')}</CardDescription>
            <div className="flex items-baseline gap-2">
              <CardTitle className="text-3xl">{games.length}</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {games.map((g) => (
                <Badge key={g.id} variant="info">
                  {g.name}
                </Badge>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription>{t('dashboard.system')}</CardDescription>
            <CardTitle className="text-3xl">OK</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">{t('dashboard.allSystemsOperational')}</p>
          </CardContent>
        </Card>
      </div>

      {servers.length === 0 && (
        <Card className="border-dashed border-2">
          <CardContent className="py-12 text-center">
            <ServerIcon className="w-10 h-10 text-muted-foreground mx-auto mb-4" />
            <h3 className="text-lg font-medium text-foreground">{t('dashboard.noServers')}</h3>
            <p className="text-sm text-muted-foreground mt-1 mb-4">{t('dashboard.createFirstServer')}</p>
            <a href="/servers">
              <Badge variant="info" className="cursor-pointer">{t('dashboard.createServer')}</Badge>
            </a>
          </CardContent>
        </Card>
      )}

      {servers.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {servers.map((server) => (
            <Card key={server.id}>
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <ServerIcon className="w-4 h-4 text-muted-foreground" />
                    <CardTitle className="text-base">{server.name}</CardTitle>
                  </div>
                  <Badge
                    variant={
                      server.status === 'running'
                        ? 'success'
                        : server.status === 'stopped'
                        ? 'default'
                        : 'warning'
                    }
                  >
                    {server.status}
                  </Badge>
                </div>
                <CardDescription>
                  {games.find((g) => g.id === server.game_type)?.name || server.game_type}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 gap-4 text-sm">
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <Activity className="w-3.5 h-3.5" />
                    CPU: {server.cpu_limit_percent ? `${server.cpu_limit_percent}%` : t('common.unlimited')}
                  </div>
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <Users className="w-3.5 h-3.5" />
                    RAM: {server.ram_limit_mb ? `${server.ram_limit_mb} MB` : t('common.unlimited')}
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
