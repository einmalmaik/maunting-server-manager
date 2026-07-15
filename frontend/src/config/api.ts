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

const envApiUrl = (import.meta.env.VITE_API_URL as string | undefined)?.trim() || ''
const envWsUrl = (import.meta.env.VITE_WS_URL as string | undefined)?.trim() || ''

/** True when the FE talks to a different API origin (Vercel / local split). */
export const isAbsoluteApi = Boolean(envApiUrl)

/**
 * HTTP(S) origin of the panel API (no trailing slash, no /api suffix).
 * Empty string means same-origin relative mode.
 */
export const API_ORIGIN = envApiUrl
  ? trimTrailingSlash(envApiUrl)
  : typeof window !== 'undefined' && window.location?.origin
    ? trimTrailingSlash(window.location.origin)
    : ''

/**
 * Base for REST path joins when building with a path like `/auth/login`.
 * Relative `/api` when unset; absolute `{origin}/api` when VITE_API_URL is set.
 */
export const API_BASE = envApiUrl ? `${trimTrailingSlash(envApiUrl)}/api` : '/api'

/** WebSocket origin (ws:// or wss://), no trailing slash. Empty → resolve at call time. */
export const WS_BASE = envWsUrl
  ? trimTrailingSlash(envWsUrl)
  : envApiUrl
    ? trimTrailingSlash(envApiUrl).replace(/^https:/i, 'wss:').replace(/^http:/i, 'ws:')
    : ''

/**
 * Absolute or same-origin URL for an API path.
 * Accepts `/auth/login`, `/api/auth/login`, or a full URL.
 */
export function apiUrl(path: string): string {
  if (!path) return API_BASE
  if (/^https?:\/\//i.test(path)) return path

  if (path.startsWith('/api/') || path === '/api') {
    if (!envApiUrl) return path
    return `${trimTrailingSlash(envApiUrl)}${path}`
  }

  const normalized = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE}${normalized}`
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
  const base = WS_BASE || resolveDefaultWsOrigin()
  return `${base}${normalized}`
}

function resolveDefaultWsOrigin(): string {
  if (typeof window === 'undefined') return ''
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}`
}
