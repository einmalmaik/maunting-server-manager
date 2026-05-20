export interface User {
  id: number
  username: string
  role?: string
  totp_enabled?: boolean
  permissions: string[]
}

// ── Full user profile (from /account/me and /users) ───────────────────────────

export type UserRole = 'owner' | 'admin' | 'user'

export interface UserProfile {
  id: number
  username: string
  email: string | null
  role: UserRole
  permissions: string[]
  is_active: boolean
  totp_enabled: boolean
  backup_codes_downloaded?: boolean
  backup_codes_remaining?: number
  can_download_backup_codes?: boolean
  created_at: string
  last_login_at: string | null
}

export interface PermissionEntry {
  key: string
  label: string
}

export interface BackupRun {
  timestamp: string
  mission_present: boolean
  profile_present: boolean
}

export interface BackupFileEntry {
  path: string
  size: number
  archive: string
}

export interface BackupFileContent {
  timestamp: string
  path: string
  archive: string
  content: string
}

export interface CoreStatus {
  server_installed: boolean
  server_running: boolean
  steamlogin_set: boolean
  steampassword_set: boolean
  mission_folder: string
  config_path: string
  language: string
  backup_root: string
  autorestart_mode?: string
  autorestart_summary?: string
  port?: number
  queryport?: number
  rconport?: number
  rcon_enabled?: boolean
}

export interface PanelStatus {
  installed: boolean
  service_state: string
  proxy_name?: string
  proxy_state?: string
  nginx_state: string
  database_state: string
  cron_state: string
  cron_installed: boolean
  cron_active: boolean
  cron_service_name: string
  runtime_user: string
  url: string
}

export interface AutorestartData {
  mode: string
  configured_mode?: string
  mode_name: string
  summary: string
  interval_hours: string
  times: string[]
  effective_times?: string[]
  config_path: string
  scheduler_ready: boolean
  scheduler_error: string | null
  cron_installed: boolean
  cron_active: boolean
  cron_service_name: string
  cron_block_present: boolean
  log_path: string
}

export interface WorkshopData {
  workshop_cfg: string
  configured_mod_count: number
  autoupdate_enabled: boolean
  autoupdate_interval_minutes: number | null
  autoupdate_display: string
  scheduler_ready: boolean
  scheduler_error: string | null
  cron_installed: boolean
  cron_active: boolean
  cron_service_name: string
  autoupdate_cron_block_present: boolean
  autoupdate_log_path: string
}

export interface ModAutoupdateData {
  enabled: boolean
  interval_minutes: number | null
  display: string
  scheduler_ready: boolean
  scheduler_error: string | null
  cron_active: boolean
  cron_installed: boolean
  cron_service_name: string
  log_path: string | null
}

export interface AuditEntry {
  id: number
  actor_username: string
  action: string
  target: string | null
  status: string
  detail: string | null
  created_at: string
}

export interface DashboardData {
  core_status: CoreStatus | null
  panel_status: PanelStatus | null
  autorestart: AutorestartData | null
  workshop: WorkshopData | null
  backup_runs: BackupRun[]
  audit_entries: AuditEntry[]
  bridge_error: string | null
  task: Task | null
}

export interface Task {
  id?: number
  action: string
  status: 'queued' | 'running' | 'finished' | 'failed' | 'started' | 'timeout'
  started_at: string
  finished_at?: string
  returncode?: number
  error?: string
  progress?: number
}

export interface ActionStatusResponse {
  task: Task | null
  log: string[]
}

export interface AutorestartUpdate {
  mode: 'off' | 'times' | 'interval'
  times?: string
  interval_hours?: string
}

// ── Mods ──────────────────────────────────────────────────────────────────────

export interface ModEntry {
  id: string
  name: string
  client: boolean
  server: boolean
}

export interface ModsData {
  mods: ModEntry[]
}

export interface ModAnalysisConflict {
  code: string
  message: string
}

export interface ModAnalysisEntry {
  id: string
  name: string
  title: string
  sources: string[]
  installed: boolean
  enabled_client: boolean
  enabled_server: boolean
  symlink_path: string | null
  symlink_target: string | null
  expected_target: string
  dependencies: string[]
  required_by: string[]
  local_timestamp: number
  steam_timestamp: number
  conflicts: ModAnalysisConflict[]
}

export interface ModAnalysisData {
  mods: ModAnalysisEntry[]
  summary: {
    configured_mods: number
    conflicts: number
    stray_symlinks: number
    config_only_mods: number
  }
  stray_symlinks: Array<{ name: string; path: string | null; target: string | null }>
  config_only_mods: string[]
  steam_dependency_status: 'verified' | 'unverified'
  steam_dependency_error: string | null
}

export interface ModDryRunAction {
  type: string
  id: string
  name: string
  reason: string
}

export interface ModDryRunData {
  actions: ModDryRunAction[]
  summary: {
    total: number
    noop: number
    has_changes: boolean
  }
}

export interface ModAddResponse {
  ok: boolean
  installed_dependencies: Array<{ id: string; name: string }>
  dep_warning?: string
  confirm_required?: boolean
  dependency_status?: 'verified' | 'unverified'
  message?: string
}

export interface SteamMod {
  publishedfileid: string
  title: string
  short_description: string
  preview_url: string
  subscriptions: number
}

export interface SteamModDetail extends SteamMod {
  result?: number
  children?: Array<{ publishedfileid: string; filetype: number }>
}

export interface SteamModWithDeps {
  mod: SteamModDetail
  dependencies: SteamModDetail[]
}

// ── Mod Update Status ─────────────────────────────────────────────────────────

export interface ModUpdateStatus {
  id: string
  name: string
  local_ts: number
  steam_ts: number
  update_available: boolean
}

export interface ModUpdatesData {
  mods: ModUpdateStatus[]
}

// ── Servers ───────────────────────────────────────────────────────────────────

export interface GameInfo {
  id: string
  name: string
  short_name: string
  supports_mods: boolean
  mod_system: string
  default_ports: Array<{ name: string; port: number; protocol: string }>
}

export interface ServerEntry {
  name: string
  display_name: string | null
  game_id?: string
}

export interface ServersData {
  servers: ServerEntry[]
  current: string | null
}

export interface GamesData {
  games: GameInfo[]
}

// ── Files ─────────────────────────────────────────────────────────────────────

export interface FileEntry {
  name: string
  path: string
  is_dir: boolean
  size: number | null
  modified: number | null
}

export interface DirListing {
  path: string
  entries: FileEntry[]
}

export interface FileContent {
  path: string
  content: string
  size: number
  modified: number
}

export interface ConfigQuickFile {
  key: string
  label: string
  path: string
  exists: boolean
}

export interface ConfigQuickDirectory {
  key: string
  label: string
  path: string
  exists: boolean
}

export interface RecentFileEntry {
  name: string
  path: string
  modified: number
  size: number
}

export interface ConfigOverviewData {
  mission_folder: string | null
  quick_files: ConfigQuickFile[]
  quick_directories: ConfigQuickDirectory[]
  recent_files: RecentFileEntry[]
  schema_source: string
}

export interface ServerConfigGroup {
  key: string
  title: string
}

export interface ServerConfigFieldMeta {
  name: string
  group: string
  kind: string
}

export interface ServerConfigData {
  path: string
  raw: string
  known: Record<string, unknown>
  custom_raw: string
  groups: ServerConfigGroup[]
  fields: ServerConfigFieldMeta[]
  schema_source: string
}

// ── Console ───────────────────────────────────────────────────────────────────

export type ConsoleSource = 'log'

export type LogLevel = 'ERROR' | 'WARN' | 'INFO' | 'DEBUG' | 'SCRIPT' | 'ADMIN' | 'PLAIN'

export interface ConsoleLine {
  id: number
  text: string
  level: LogLevel
}

export type ConsoleFrame =
  | { type: 'line';  data: ConsoleLine[] }
  | { type: 'error'; data: string }
  | { type: 'ping' }

export interface ConsoleTokenResponse {
  token: string
  expires_in: number
  source: ConsoleSource
}

export interface PterodactylCandidate {
  pterodactyl_path: string
  volume_name: string
  server_name: string
  db_size: number
  db_modified: number
  mods_count: number
  max_players: number
  admin_password: string
}

