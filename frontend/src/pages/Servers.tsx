import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { useHostInterfaces } from '@/hooks/useHostInterfaces'
import { useHasPermission } from '@/hooks/useHasPermission'
import type { Server, GameInfo } from '@/types'
import { labelRole, mapBlueprintPorts } from '@/utils/portRoles'
import { Server as ServerIcon, Plus, Activity, Cpu, HardDrive } from 'lucide-react'

export function Servers() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const canCreateServer = useHasPermission('servers.create')
  const [servers, setServers] = useState<Server[]>([])
  const [games, setGames] = useState<GameInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [creating, setCreating] = useState(false)
  const { interfaces, defaultBindIp } = useHostInterfaces()
  const [form, setForm] = useState({
    name: '',
    game_type: 'conan_exiles_ue5',
    cpu_limit_percent: '',
    ram_limit_mb: '',
    disk_limit_gb: '',
    game_port: '',
    query_port: '',
    rcon_port: '',
    ports: {} as Record<string, string>,
    public_bind_ip: '',
  })

  // Default-Bind-IP setzen, sobald sie vom Backend kommt.
  useEffect(() => {
    if (defaultBindIp && !form.public_bind_ip) {
      setForm((prev) => ({ ...prev, public_bind_ip: defaultBindIp }))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultBindIp])

  const fetchServers = async () => {
    try {
      const [srvs, gms] = await Promise.all([
        api<Server[]>('/servers'),
        api<GameInfo[]>('/system/games'),
      ])
      setServers(srvs)
      setGames(gms)
    } catch {
      // silent
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchServers()
    const interval = setInterval(fetchServers, 5000)
    return () => clearInterval(interval)
  }, [])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setCreating(true)
    try {
      const portsPayload: Record<string, number | null> = {}
      const selectedGame = games.find((g) => g.id === form.game_type)
      const portDefs = selectedGame?.ports ?? [
        { name: 'game', protocol: 'udp' },
        { name: 'query', protocol: 'udp' },
        { name: 'rcon', protocol: 'tcp' },
      ]
      mapBlueprintPorts(portDefs).forEach((p) => {
        const role = p.mappedRole
        let valStr = ''
        if (role === 'game') valStr = form.game_port
        else if (role === 'query') valStr = form.query_port
        else if (role === 'rcon') valStr = form.rcon_port
        else valStr = form.ports[role] || ''
        
        portsPayload[role] = valStr ? parseInt(valStr) : null
      })

      await api<Server>('/servers', {
        method: 'POST',
        body: JSON.stringify({
          name: form.name,
          game_type: form.game_type,
          cpu_limit_percent: form.cpu_limit_percent ? parseInt(form.cpu_limit_percent) : null,
          ram_limit_mb: form.ram_limit_mb ? parseInt(form.ram_limit_mb) : null,
          disk_limit_gb: form.disk_limit_gb ? parseInt(form.disk_limit_gb) : null,
          game_port: form.game_port ? parseInt(form.game_port) : null,
          query_port: form.query_port ? parseInt(form.query_port) : null,
          rcon_port: form.rcon_port ? parseInt(form.rcon_port) : null,
          ports: portsPayload,
          public_bind_ip: form.public_bind_ip || null,
        }),
      })
      setShowCreate(false)
      setForm({
        name: '',
        game_type: 'conan_exiles_ue5',
        cpu_limit_percent: '',
        ram_limit_mb: '',
        disk_limit_gb: '',
        game_port: '',
        query_port: '',
        rcon_port: '',
        ports: {},
        public_bind_ip: defaultBindIp || '',
      })
      fetchServers()
    } catch (err: any) {
      const msg = t(err.message) || err.message || t('common.error')
      toast.error(msg)
    } finally {
      setCreating(false)
    }
  }

  const statusClasses = (status: string) => {
    switch (status) {
      case 'running':
        return 'bg-status-success/10 border-status-success/30 text-status-success'
      case 'stopped':
        return 'bg-surface-container-highest border-outline text-on-surface-variant'
      case 'starting':
      case 'stopping':
      case 'restarting':
        return 'bg-status-warning/10 border-status-warning/30 text-status-warning'
      case 'installing':
      case 'updating':
      case 'awaiting_files':
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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-headline text-headline-sm text-primary">{t('nav.servers')}</h1>
          <p className="font-body-md text-body-md text-on-surface-variant mt-1">
            {t('servers.subtitle')}
          </p>
        </div>
        {canCreateServer && (
          <button
            onClick={() => setShowCreate(true)}
            className="msm-btn-primary flex items-center gap-2 px-4 py-2"
          >
            <Plus className="w-4 h-4" />
            {t('servers.create')}
          </button>
        )}
      </div>

      {servers.length === 0 && (
        <div className="msm-card p-12 text-center border-dashed border-2 border-outline-variant">
          <ServerIcon className="w-10 h-10 text-on-surface-variant mx-auto mb-4" />
          <h3 className="font-headline text-body-lg text-on-surface mb-1">
            {t('servers.noServers')}
          </h3>
          <p className="font-body-md text-sm text-on-surface-variant">
            {t('servers.createHint')}
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {servers.map((server) => (
          <div
            key={server.id}
            className="msm-card p-5 cursor-pointer hover:border-mint-accent/40 transition-all"
            onClick={() => navigate(`/servers/${server.id}`)}
          >
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <ServerIcon className="w-4 h-4 text-on-surface-variant" />
                <h3 className="font-headline text-body-md text-on-surface">{server.name}</h3>
              </div>
              <span className={`font-mono-sm text-mono-sm px-2 py-0.5 rounded-full border ${statusClasses(server.status)}`}>
                {t(`servers.status.${server.status}`, { defaultValue: server.status })}
              </span>
            </div>
            <p className="font-body-md text-sm text-on-surface-variant mb-4">
              {gameName(server.game_type)}
            </p>
            <div className="grid grid-cols-3 gap-3 text-xs text-on-surface-variant">
              <div className="flex items-center gap-1.5">
                <Cpu className="w-3.5 h-3.5" />
                <span className="font-body-md">
                  {server.cpu_limit_percent ? `${server.cpu_limit_percent}%` : t('common.unlimited')}
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <Activity className="w-3.5 h-3.5" />
                <span className="font-body-md">
                  {server.ram_limit_mb ? `${server.ram_limit_mb} MB` : t('common.unlimited')}
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <HardDrive className="w-3.5 h-3.5" />
                <span className="font-body-md">
                  {server.disk_limit_gb ? `${server.disk_limit_gb} GB` : t('common.unlimited')}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Create Modal */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 overflow-y-auto">
          <div className="msm-card w-full max-w-lg p-6 my-8">
            <h2 className="font-headline text-headline-md text-primary mb-1">
              {t('servers.create')}
            </h2>
            <p className="font-body-md text-sm text-on-surface-variant mb-6">
              {t('servers.createDescription')}
            </p>
            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('servers.name')}
                </label>
                <input
                  type="text"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  className="msm-input"
                  required
                />
              </div>
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('servers.game')}
                </label>
                <select
                  className="msm-input"
                  value={form.game_type}
                  onChange={(e) => setForm({ ...form, game_type: e.target.value })}
                >
                  {games.map((g) => (
                    <option key={g.id} value={g.id}>{g.name}</option>
                  ))}
                </select>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                    {t('servers.cpuLimit')}
                  </label>
                  <input
                    type="number"
                    min={10}
                    max={100}
                    value={form.cpu_limit_percent}
                    onChange={(e) => setForm({ ...form, cpu_limit_percent: e.target.value })}
                    className="msm-input"
                    placeholder="%"
                  />
                </div>
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                    {t('servers.ramLimit')}
                  </label>
                  <input
                    type="number"
                    min={512}
                    value={form.ram_limit_mb}
                    onChange={(e) => setForm({ ...form, ram_limit_mb: e.target.value })}
                    className="msm-input"
                    placeholder="MB"
                  />
                </div>
                <div>
                  <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                    {t('servers.diskLimit')}
                  </label>
                  <input
                    type="number"
                    min={1}
                    value={form.disk_limit_gb}
                    onChange={(e) => setForm({ ...form, disk_limit_gb: e.target.value })}
                    className="msm-input"
                    placeholder="GB"
                  />
                </div>
              </div>

              {/* Phase 2: Bind-IP + Ports */}
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('servers.publicBindIp')}
                </label>
                <select
                  className="msm-input"
                  value={form.public_bind_ip}
                  onChange={(e) => setForm({ ...form, public_bind_ip: e.target.value })}
                >
                  {interfaces.length === 0 && (
                    <option value="">{t('servers.bindIp.noneAvailable')}</option>
                  )}
                  {interfaces.map((iface) => (
                    <option key={`${iface.interface}-${iface.ip}`} value={iface.ip}>
                      {iface.ip} · {iface.interface}
                      {iface.is_loopback ? ` (${t('servers.bindIp.loopback')})` : ''}
                      {iface.is_private && !iface.is_loopback ? ` (${t('servers.bindIp.private')})` : ''}
                    </option>
                  ))}
                </select>
                <p className="font-body-md text-xs text-on-surface-variant mt-1">
                  {t('servers.bindIp.hint')}
                </p>
              </div>

              {(() => {
                const selectedGame = games.find((g) => g.id === form.game_type)
                const portDefs = selectedGame?.ports ?? [
                  { name: 'game', protocol: 'udp' },
                  { name: 'query', protocol: 'udp' },
                  { name: 'rcon', protocol: 'tcp' },
                ]
                if (portDefs.length === 0) return null
                
                const mappedPorts = mapBlueprintPorts(portDefs)

                return (
                  <div className="grid grid-cols-3 gap-3" data-testid="port-fields">
                    {mappedPorts.map((p) => {
                      const role = p.mappedRole
                      const isLegacy = role === 'game' || role === 'query' || role === 'rcon'
                      const val = isLegacy
                        ? (role === 'game' ? form.game_port : role === 'query' ? form.query_port : form.rcon_port)
                        : (form.ports[role] || '')
                      
                      const baseRole = labelRole(role)
                      const label = baseRole === 'game'
                        ? t('servers.gamePort')
                        : baseRole === 'query'
                        ? t('servers.queryPort')
                        : baseRole === 'rcon'
                        ? t('servers.rconPort')
                        : `${role.replace('_', ' ').toUpperCase()} (${p.protocol.toUpperCase()})`

                      return (
                        <div key={role}>
                          <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                            {label}
                          </label>
                          <input
                            type="number"
                            min={1024}
                            max={65535}
                            value={val}
                            onChange={(e) => {
                              if (isLegacy) {
                                const fieldKey = role === 'game' ? 'game_port' : role === 'query' ? 'query_port' : 'rcon_port'
                                setForm({ ...form, [fieldKey]: e.target.value })
                              } else {
                                setForm({
                                  ...form,
                                  ports: {
                                    ...form.ports,
                                    [role]: e.target.value,
                                  },
                                })
                              }
                            }}
                            className="msm-input"
                            placeholder={t('servers.portAuto')}
                            data-testid={`port-input-${role}`}
                          />
                        </div>
                      )
                    })}
                  </div>
                )
              })()}
              <div className="flex gap-3 pt-2">
                <button
                  type="button"
                  className="msm-btn-secondary flex-1 py-2"
                  onClick={() => setShowCreate(false)}
                >
                  {t('common.cancel')}
                </button>
                <button
                  type="submit"
                  className="msm-btn-primary flex-1 py-2 disabled:opacity-50"
                  disabled={creating}
                >
                  {creating ? t('common.loading') : t('servers.create')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
