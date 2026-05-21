export interface User {
  id: number
  username: string
  email: string
  is_owner: boolean
  is_active: boolean
  email_verified: boolean
  two_factor_enabled: boolean
  created_at: string
}

export interface Server {
  id: number
  name: string
  game_type: string
  install_dir: string
  linux_user: string
  status: string
  status_message: string | null
  auto_restart: boolean
  restart_interval_hours: number | null
  restart_time_utc: string | null
  cpu_limit_percent: number | null
  ram_limit_mb: number | null
  disk_limit_gb: number | null
  game_port: number | null
  query_port: number | null
  rcon_port: number | null
  created_at: string
}

export interface GameInfo {
  id: string
  name: string
  platform: string
  mod_support: boolean
}

export interface VersionInfo {
  current_version: string
  latest_version: string | null
  update_available: boolean
  release_url: string | null
  auto_update_enabled: boolean
  github_repo: string
}
