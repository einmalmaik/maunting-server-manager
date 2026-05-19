const STORAGE_PREFIX = 'conan-panel-workspace:v1'

function hasWindow() {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'
}

export function loadLocalValue<T>(key: string, fallback: T): T {
  if (!hasWindow()) return fallback
  try {
    const raw = window.localStorage.getItem(key)
    if (!raw) return fallback
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

export function saveLocalValue<T>(key: string, value: T): void {
  if (!hasWindow()) return
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
  } catch (error) {
    console.warn(`Failed to persist workspace state for ${key}.`, error)
  }
}

function buildServerWorkspaceKey(scope: string, server: string | null): string | null {
  if (!server) return null
  return `${STORAGE_PREFIX}:${scope}:${server}`
}

export function loadServerWorkspace<T>(scope: string, server: string | null, fallback: T): T {
  const key = buildServerWorkspaceKey(scope, server)
  if (!key) return fallback
  return loadLocalValue(key, fallback)
}

export function saveServerWorkspace<T>(scope: string, server: string | null, value: T): void {
  const key = buildServerWorkspaceKey(scope, server)
  if (!key) return
  saveLocalValue(key, value)
}

export function updateServerWorkspace<T>(
  scope: string,
  server: string | null,
  fallback: T,
  updater: (current: T) => T,
): T {
  const current = loadServerWorkspace(scope, server, fallback)
  const next = updater(current)
  saveServerWorkspace(scope, server, next)
  return next
}
