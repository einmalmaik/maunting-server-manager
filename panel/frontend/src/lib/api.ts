import type {
  ActionStatusResponse,
  AutorestartData,
  AutorestartUpdate,
  BackupFileContent,
  BackupFileEntry,
  BackupRun,
  ConfigOverviewData,
  ConsoleSource,
  ConsoleTokenResponse,
  DashboardData,
  DirListing,
  FileContent,
  ModAddResponse,
  ModAnalysisData,
  ModAutoupdateData,
  ModDryRunData,
  ModsData,
  ModUpdatesData,
  PermissionEntry,
  RecentFileEntry,
  ServersData,
  ServerConfigData,
  SteamMod,
  SteamModWithDeps,
  User,
  UserProfile,
  PterodactylCandidate,
  GamesData,
} from './types'

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }

  /** Returns true if this is a permission denied error (403) */
  get isPermissionDenied(): boolean {
    return this.status === 403
  }

  /** Returns a user-friendly error message */
  getUserMessage(): string {
    const isGerman =
      typeof document !== 'undefined' &&
      (document.documentElement.lang || '').toLowerCase().startsWith('de')
    if (this.status === 403) {
      return isGerman
        ? 'Zugriff verweigert. Dir fehlen die erforderlichen Berechtigungen fuer diese Aktion.'
        : 'Permission denied. You do not have the required permissions for this action.'
    }
    if (this.status === 401) {
      return isGerman
        ? 'Authentifizierung erforderlich. Bitte erneut anmelden.'
        : 'Authentication required. Please log in again.'
    }
    if (this.status === 404) {
      return isGerman ? 'Ressource nicht gefunden.' : 'Resource not found.'
    }
    return this.message || (isGerman ? 'Ein unerwarteter Fehler ist aufgetreten.' : 'An unexpected error occurred.')
  }
}

type UploadBatchEntry = {
  file: File
  relativePath: string
}

export type BatchDownloadResponse = {
  blob: Blob
  filename: string
}

async function _handleError(res: Response): Promise<never> {
  let detail = res.statusText
  try {
    const data = await res.json()
    detail = data?.detail ?? detail
  } catch {
    // ignore parse errors
  }
  throw new ApiError(res.status, detail)
}

/** For endpoints that return a JSON body. */
async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: 'include',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (!res.ok) return _handleError(res)
  try {
    return await res.json() as T
  } catch {
    const isGerman =
      typeof document !== 'undefined' &&
      (document.documentElement.lang || '').toLowerCase().startsWith('de')
    throw new ApiError(res.status, isGerman ? 'Ungueltige JSON-Antwort vom Server.' : 'Invalid JSON response from server.')
  }
}

/** For endpoints that return an empty 200/204 body. */
async function requestVoid(
  method: string,
  path: string,
  body?: unknown,
): Promise<void> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: 'include',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (!res.ok) return _handleError(res)
}

async function uploadBatchEntriesRequest(dirPath: string, entries: UploadBatchEntry[]): Promise<void> {
  const form = new FormData()
  for (const entry of entries) {
    form.append('files', entry.file, entry.relativePath)
  }
  const res = await fetch(`/api/files/upload/batch?path=${encodeURIComponent(dirPath)}`, {
    method: 'POST',
    credentials: 'include',
    body: form,
  })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* ignore */ }
    throw new ApiError(res.status, detail)
  }
}

async function downloadBatchRequest(paths: string[]): Promise<BatchDownloadResponse> {
  const res = await fetch('/api/files/download-batch', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paths }),
  })

  if (!res.ok) return _handleError(res)

  const blob = await res.blob()
  const disposition = res.headers.get('content-disposition') ?? ''
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  const asciiMatch = disposition.match(/filename="([^"]+)"/i)
  const rawName = utf8Match?.[1] ?? asciiMatch?.[1] ?? 'conan-files.zip'
  let filename: string
  try {
    filename = decodeURIComponent(rawName)
  } catch {
    filename = rawName
  }

  return { blob, filename }
}

// ── Auth ──────────────────────────────────────────────────────────────────────
export const authApi = {
  login: (username: string, password: string) =>
    request<{ user: User } | { needs_2fa: true }>('POST', '/auth/login', { username, password }),

  logout: () =>
    requestVoid('POST', '/auth/logout'),

  me: () =>
    request<{ user: User }>('GET', '/auth/me'),

  register: (username: string, email: string, password: string) =>
    request<{ ok: boolean; message: string }>('POST', '/auth/register', { username, email, password }),

  forgotPassword: (email: string) =>
    request<{ ok: boolean; message: string }>('POST', '/auth/forgot-password', { email }),

  resetPassword: (token: string, new_password: string) =>
    request<{ ok: boolean; message: string }>('POST', '/auth/reset-password', { token, new_password }),

  verifyEmail: (token: string) =>
    request<{ ok: boolean; message: string }>('GET', `/auth/verify-email?token=${encodeURIComponent(token)}`),
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
export const dashboardApi = {
  get: () =>
    request<DashboardData>('GET', '/dashboard'),
}

// ── Backups ───────────────────────────────────────────────────────────────────
export const backupsApi = {
  list: () =>
    request<{ runs: BackupRun[] }>('GET', '/backups'),

  create: () =>
    requestVoid('POST', '/backups/create'),

  restore: (timestamp: string) =>
    requestVoid('POST', '/backups/restore', { timestamp }),

  listFiles: (timestamp: string) =>
    request<{ timestamp: string; entries: BackupFileEntry[] }>('GET', `/backups/files?timestamp=${encodeURIComponent(timestamp)}`),

  readFileContent: (timestamp: string, path: string) =>
    request<BackupFileContent>('GET', `/backups/file-content?timestamp=${encodeURIComponent(timestamp)}&path=${encodeURIComponent(path)}`),

  restoreFile: (timestamp: string, path: string) =>
    requestVoid('POST', '/backups/restore-file', { timestamp, path }),
}

// ── Autorestart ───────────────────────────────────────────────────────────────
export const autorestartApi = {
  get: () =>
    request<AutorestartData>('GET', '/autorestart'),

  update: (payload: AutorestartUpdate) =>
    request<AutorestartData>('POST', '/autorestart', payload),
}

// ── Actions ───────────────────────────────────────────────────────────────────
export const actionsApi = {
  invoke: (actionName: string) =>
    request<{ ok: boolean; async?: boolean; job_id?: number; state?: string }>('POST', `/actions/${encodeURIComponent(actionName)}`),

  status: (channel: 'default' | 'workshop' = 'default') =>
    request<ActionStatusResponse>('GET', `/actions/status?channel=${encodeURIComponent(channel)}`),
}

// ── Mods ──────────────────────────────────────────────────────────────────────
export const modsApi = {
  list: () =>
    request<ModsData>('GET', '/mods'),

  add: (
    mod_id: string,
    mod_name: string,
    options?: { confirmUnverifiedDependencies?: boolean },
  ) =>
    request<ModAddResponse>('POST', '/mods', {
      mod_id,
      mod_name,
      confirm_unverified_dependencies: options?.confirmUnverifiedDependencies === true,
    }),

  remove: (mod_id: string) =>
    requestVoid('DELETE', `/mods/${encodeURIComponent(mod_id)}`),

  toggle: (mod_id: string, mod_type: 'client' | 'server', enabled: boolean) =>
    requestVoid('PATCH', `/mods/${encodeURIComponent(mod_id)}/toggle`, { mod_type, enabled }),

  steamSearch: (q: string) =>
    request<{ response: { publishedfiledetails?: SteamMod[] } }>('GET', `/mods/steam/search?q=${encodeURIComponent(q)}`),

  steamDetails: (mod_id: string) =>
    request<{ response: { publishedfiledetails?: SteamMod[] } }>('GET', `/mods/steam/${encodeURIComponent(mod_id)}`),

  steamWithDeps: (mod_id: string) =>
    request<SteamModWithDeps>('GET', `/mods/steam/${encodeURIComponent(mod_id)}/with-deps`),

  checkUpdates: () =>
    request<ModUpdatesData>('GET', '/mods/updates'),

  updateSelective: (mod_ids: string[]) =>
    request<{ ok: boolean; async?: boolean; job_id?: number; state?: string }>('POST', '/mods/update-selective', { mod_ids }),

  getAutoupdate: () =>
    request<ModAutoupdateData>('GET', '/mods/autoupdate'),

  setAutoupdate: (interval_minutes: number | null) =>
    request<ModAutoupdateData>('POST', '/mods/autoupdate', { interval_minutes }),

  reorder: (mod_ids: string[]) =>
    request<{ ok: boolean }>('PATCH', '/mods/reorder', { mod_ids }),

  analysis: () =>
    request<ModAnalysisData>('GET', '/mods/analysis'),

  dryRun: () =>
    request<ModDryRunData>('POST', '/mods/dry-run'),
}

// ── Console ───────────────────────────────────────────────────────────────────
export const consoleApi = {
  getToken: (source: ConsoleSource) =>
    request<ConsoleTokenResponse>('GET', `/console/token?source=${encodeURIComponent(source)}`),
}

// ── Servers ───────────────────────────────────────────────────────────────────
export const serversApi = {
  list: () =>
    request<ServersData>('GET', '/servers'),

  current: () =>
    request<{ current_server: string | null }>('GET', '/servers/current'),

  select: (name: string) =>
    request<{ ok: boolean; current_server: string }>('POST', '/servers/select', { name }),

  create: (name: string, game_id?: string) =>
    request<{ ok: boolean; name: string; game_id: string }>('POST', '/servers', { name, game_id: game_id || 'conan_exiles' }),

  clone: (source: string, name: string) =>
    request<{ ok: boolean; source: string; name: string; current_server: string }>('POST', '/servers/clone', { source, name }),

  delete: (name: string) =>
    request<{ ok: boolean; name: string }>('DELETE', `/servers/${encodeURIComponent(name)}`),

  legacyCheck: () =>
    request<{ legacy: boolean }>('GET', '/servers/legacy-check'),

  migrate: (name: string) =>
    request<{ ok: boolean; name: string }>('POST', '/servers/migrate', { name }),

  listPterodactylCandidates: (rootPath?: string) =>
    request<PterodactylCandidate[]>('GET', `/servers/pterodactyl/candidates${rootPath ? `?root_path=${encodeURIComponent(rootPath)}` : ''}`),

  migratePterodactyl: (payload: { pterodactyl_path: string; target_server_name: string; create_target?: boolean }) =>
    request<{ ok: boolean; name: string; target_dir: string }>('POST', '/servers/pterodactyl/migrate', payload),
}

// ── Games ─────────────────────────────────────────────────────────────────────
export const gamesApi = {
  list: () =>
    request<GamesData>('GET', '/games'),
}

// ── Language ──────────────────────────────────────────────────────────────────
export const languageApi = {
  get: () =>
    request<{ language: string }>('GET', '/language'),

  set: (language: 'en' | 'de') =>
    request<{ ok: boolean; language: string }>('POST', '/language', { language }),
}

// ── Setup ─────────────────────────────────────────────────────────────────────
export const setupApi = {
  status: () =>
    request<{ needs_setup: boolean }>('GET', '/setup/status'),

  createOwner: (username: string, password: string) =>
    request<{ user: User }>('POST', '/setup/create-owner', { username, password }),
}

// ── Files ─────────────────────────────────────────────────────────────────────
export const filesApi = {
  list: (path: string) =>
    request<DirListing>('GET', `/files?path=${encodeURIComponent(path)}`),

  recent: (path: string, limit = 20) =>
    request<{ path: string; entries: RecentFileEntry[] }>('GET', `/files/recent?path=${encodeURIComponent(path)}&limit=${limit}`),

  readContent: (path: string) =>
    request<FileContent>('GET', `/files/content?path=${encodeURIComponent(path)}`),

  writeContent: (path: string, content: string) =>
    requestVoid('PUT', '/files/content', { path, content }),

  upload: async (dirPath: string, file: File): Promise<void> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`/api/files/upload?path=${encodeURIComponent(dirPath)}`, {
      method: 'POST',
      credentials: 'include',
      body: form,
    })
    if (!res.ok) {
      let detail = res.statusText
      try { detail = (await res.json()).detail ?? detail } catch { /* ignore */ }
      throw new ApiError(res.status, detail)
    }
  },

  uploadBatch: async (dirPath: string, files: File[]): Promise<void> => {
    return uploadBatchEntriesRequest(
      dirPath,
      files.map((file) => ({
        file,
        relativePath: (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name,
      })),
    )
  },

  uploadBatchEntries: (dirPath: string, entries: UploadBatchEntry[]) =>
    uploadBatchEntriesRequest(dirPath, entries),

  delete: (path: string) =>
    requestVoid('DELETE', `/files?path=${encodeURIComponent(path)}`),

  deleteBatch: (paths: string[]) =>
    requestVoid('POST', '/files/delete-batch', { paths }),

  rename: (path: string, new_name: string) =>
    request<{ ok: boolean; path: string; name: string }>('PATCH', '/files/rename', { path, new_name }),

  mkdir: (path: string) =>
    requestVoid('POST', '/files/mkdir', { path }),

  extractArchive: (path: string) =>
    requestVoid('POST', '/files/extract', { path }),

  downloadUrl: (path: string) =>
    `/api/files/download?path=${encodeURIComponent(path)}`,

  downloadBatch: (paths: string[]) =>
    downloadBatchRequest(paths),
}

export const configApi = {
  overview: () =>
    request<ConfigOverviewData>('GET', '/config/overview'),

  getServerConfig: () =>
    request<ServerConfigData>('GET', '/config/serverconfig'),

  saveServerConfig: (payload: { known: Record<string, unknown>; custom_raw: string }) =>
    request<ServerConfigData>('PUT', '/config/serverconfig', payload),
}

// ── Users (owner/admin) ───────────────────────────────────────────────────────
export const usersApi = {
  list: () =>
    request<{ users: UserProfile[] }>('GET', '/users'),

  permissions: () =>
    request<{ permissions: PermissionEntry[] }>('GET', '/users/permissions'),

  create: (data: { username: string; email?: string; password: string; role: string; permissions?: string[] }) =>
    request<{ user: UserProfile }>('POST', '/users', data),

  get: (id: number) =>
    request<{ user: UserProfile }>('GET', `/users/${id}`),

  update: (id: number, data: { email?: string | null; role?: string; permissions?: string[]; is_active?: boolean }) =>
    request<{ user: UserProfile }>('PATCH', `/users/${id}`, data),

  delete: (id: number) =>
    requestVoid('DELETE', `/users/${id}`),

  resetPassword: (id: number, new_password: string) =>
    requestVoid('POST', `/users/${id}/reset-password`, { new_password }),
}

// ── Account self-service ──────────────────────────────────────────────────────
export const accountApi = {
  me: () =>
    request<{ id: number; username: string; email: string | null; role: string; permissions: string[]; totp_enabled: boolean; backup_codes_downloaded: boolean; backup_codes_remaining: number; can_download_backup_codes: boolean; is_active: boolean }>('GET', '/account/me'),

  changePassword: (current_password: string, new_password: string) =>
    requestVoid('POST', '/account/change-password', { current_password, new_password }),

  setup2fa: () =>
    request<{ secret: string; uri: string; already_enabled: boolean }>('GET', '/account/2fa/setup'),

  enable2fa: (secret: string, code: string) =>
    requestVoid('POST', '/account/2fa/enable', { secret, code }),

  downloadBackupCodes: () =>
    request<{ codes: string[] }>('POST', '/account/2fa/backup-codes/download'),

  disable2fa: (password: string, code: string) =>
    requestVoid('POST', '/account/2fa/disable', { password, code }),
}

// ── Auth 2FA ──────────────────────────────────────────────────────────────────
export const auth2faApi = {
  verify: (code: string) =>
    request<{ user: User }>('POST', '/auth/2fa', { code }),
}
