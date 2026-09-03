/**
 * API / WebSocket base URLs for same-origin and decoupled (Vercel) hosting.
 *
 * - Default (no VITE_API_URL): relative `/api` → Vite proxy or backend-served SPA.
 * - Split hosting: set VITE_API_URL (and optionally VITE_WS_URL) to absolute API origin.
 *
 * Never put agent tokens or panel secrets in Vite env vars.
 */

function trimTrailingSlash(url: string): string {
  return url.replace(/\/+$/, '')
}

export function setRuntimeApiUrl(url: string | null): void {
  ;(globalThis as { __MSM_API_URL?: string }).__MSM_API_URL = (url ?? '').trim()
}

export function getEffectiveApiUrl(): string {
  const laufzeit = ((globalThis as { __MSM_API_URL?: string }).__MSM_API_URL ?? '').trim()
  if (laufzeit) return trimTrailingSlash(laufzeit)
  const env = (import.meta.env.VITE_API_URL as string | undefined)?.trim() || ''
  return env ? trimTrailingSlash(env) : ''
}

/** True when the FE talks to a different API origin (Vercel / local split / desktop app). */
export function getIsAbsoluteApi(): boolean {
  return Boolean(getEffectiveApiUrl())
}

export const isAbsoluteApi = Boolean(
  ((globalThis as { __MSM_API_URL?: string }).__MSM_API_URL ?? '').trim() ||
    (import.meta.env.VITE_API_URL as string | undefined)?.trim(),
)

/**
 * HTTP(S) origin of the panel API (no trailing slash, no /api suffix).
 * Empty string means same-origin relative mode.
 */
export const API_ORIGIN = getEffectiveApiUrl() || (typeof window !== 'undefined' && window.location?.origin ? trimTrailingSlash(window.location.origin) : '')

/**
 * Base for REST path joins when building with a path like `/auth/login`.
 * Relative `/api` when unset; absolute `{origin}/api` when VITE_API_URL is set.
 */
export const API_BASE = getEffectiveApiUrl() ? `${getEffectiveApiUrl()}/api` : '/api'

/** WebSocket origin (ws:// or wss://), no trailing slash. Empty → resolve at call time. */
export const WS_BASE = getEffectiveApiUrl()
  ? getEffectiveApiUrl().replace(/^https:/i, 'wss:').replace(/^http:/i, 'ws:')
  : (import.meta.env.VITE_WS_URL as string | undefined)?.trim() || ''

/**
 * Absolute or same-origin URL for an API path.
 * Accepts `/auth/login`, `/api/auth/login`, or a full URL.
 */
export function apiUrl(path: string): string {
  const effective = getEffectiveApiUrl()
  const base = effective ? `${effective}/api` : '/api'

  if (!path) return base
  if (/^(https?:|blob:|data:)/i.test(path)) return path

  if (path.startsWith('/api/') || path === '/api') {
    if (!effective) return path
    return `${effective}${path}`
  }

  const normalized = path.startsWith('/') ? path : `/${path}`
  return `${base}${normalized}`
}

/**
 * Absolute WebSocket URL for a path like `/api/servers/1/console/ws`.
 * If `path` is already `ws:`/`wss:`, it is returned unchanged.
 */
export function wsUrl(path: string): string {
  if (!path) {
    return resolveDefaultWsOrigin()
  }
  if (/^wss?:\/\//i.test(path)) return path
  if (/^https?:\/\//i.test(path)) {
    return path.replace(/^https:/i, 'wss:').replace(/^http:/i, 'ws:')
  }

  const normalized = path.startsWith('/') ? path : `/${path}`
  const effective = getEffectiveApiUrl()
  let base = ''
  if (effective) {
    base = effective.replace(/^https:/i, 'wss:').replace(/^http:/i, 'ws:')
  } else {
    const envWs = (import.meta.env.VITE_WS_URL as string | undefined)?.trim() || ''
    if (envWs) {
      base = trimTrailingSlash(envWs)
    } else {
      base = resolveDefaultWsOrigin()
    }
  }
  return `${base}${normalized}`
}

function resolveDefaultWsOrigin(): string {
  if (typeof window === 'undefined') return ''
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}`
}
