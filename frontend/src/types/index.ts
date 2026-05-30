export interface User {
  id: number
  username: string
  email: string
  is_owner: boolean
  is_active: boolean
  email_verified: boolean
  two_factor_enabled: boolean
  email_notifications: boolean
  role_id: number | null
  created_at: string
}

export interface Server {
  id: number
  name: string
  game_type: string
  // install_dir + container_name entfernt (Security/data-min per review): waren in allen Responses inkl. view-only User.
  // Keine Verwendung im FE-Code (nur hier); interne Pfade bleiben server-only in DB/audit/owner flows.
  status: string
  status_message: string | null
  auto_restart: boolean
  restart_interval_hours: number | null
  restart_time_utc: string | null
  restart_times_utc: string | null
  cpu_limit_percent: number | null
  ram_limit_mb: number | null
  disk_limit_gb: number | null
  disk_usage_mb: number | null
  game_port: number | null
  query_port: number | null
  rcon_port: number | null
  public_bind_ip: string | null
  ports?: Array<{ role: string; port: number | null; protocol: string }>
  created_at: string
}

export type BlueprintPortRole = 'game' | 'query' | 'rcon' | 'voice' | 'web' | 'custom'
export type BlueprintPortProtocol = 'tcp' | 'udp'

export interface BlueprintPortDef {
  name: BlueprintPortRole
  protocol: BlueprintPortProtocol
}

export interface GameInfo {
  id: string
  name: string
  platform: string
  category?: string
  mod_support: boolean
  supports_steam_workshop: boolean
  ports?: BlueprintPortDef[]
  source?: 'native' | 'community'
}

export interface BlueprintListEntry {
  id: string
  name: string
  category: string
  author: string | null
  description: string | null
  origin: 'native' | 'community'
  version: number
  image: string
  source_type: 'steam' | 'http' | 'dockerOnly' | 'custom'
  supports_mods: boolean
  supports_steam_workshop: boolean
  mod_injection: 'none' | 'startupArg' | 'file'
  ports: BlueprintPortDef[]
}

export interface VersionInfo {
  current_version: string
  latest_version: string | null
  update_available: boolean
  release_url: string | null
  auto_update_enabled: boolean
  github_repo: string
}

export interface HostInterface {
  ip: string
  interface: string
  is_loopback: boolean
  is_private: boolean
  is_link_local: boolean
}

export interface HostInterfacesResponse {
  interfaces: HostInterface[]
  default_bind_ip: string | null
}
