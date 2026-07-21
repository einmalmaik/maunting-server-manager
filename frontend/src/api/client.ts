import i18n from '@/i18n'
import { apiUrl } from '@/config/api'
import { toast } from '@/stores/toastStore'

export { API_BASE, apiUrl } from '@/config/api'

/**
 * Error thrown by the API client for failures that originated from a
 * processed backend HTTP response (non-2xx status, 429, session-expired
 * refresh failure). The backend is the authority for sanitizing these
 * messages — it must not emit host paths, socket paths, sensitive values,
 * stack traces, or raw command output (VAL-API-010). Callers may therefore
 * display `.message` directly.
 *
 * Client-side / runtime failures (fetch TypeError, unexpected exceptions,
 * thrown strings, non-Error values) are NOT wrapped in this class. Callers
 * must map those to a safe localized fallback instead of raw err.message.
 */
export class SanitizedApiError extends Error {
  readonly status: number | null
  readonly code: string | null

  constructor(message: string, options: { status?: number; code?: string } = {}) {
    super(message)
    this.name = 'SanitizedApiError'
    this.status = options.status ?? null
    this.code = options.code ?? null
  }
}

/**
 * CSRF value held in memory for cross-origin setups where document.cookie
 * cannot read the API host's `__Secure-csrf_token` (different site).
 * Populated from `X-CSRF-Token` response headers (login, refresh, /me).
 */
let csrfTokenMemory: string | null = null

export function getCsrfToken(): string | null {
  if (csrfTokenMemory) return csrfTokenMemory
  const match = document.cookie.match(new RegExp('(^| )__Secure-csrf_token=([^;]+)'))
  return match ? decodeURIComponent(match[2]) : null
}

/** Clears in-memory CSRF (e.g. after logout). Does not touch HttpOnly cookies. */
export function clearCsrfTokenMemory(): void {
  csrfTokenMemory = null
}

function captureCsrfFromResponse(res: Response): void {
  try {
    const header = res.headers?.get?.('X-CSRF-Token')
    if (header) {
      csrfTokenMemory = header
    }
  } catch {
    /* ignore incomplete Response mocks in tests */
  }
}

function extractErrorMessage(detail: unknown): string | null {
  if (detail == null) return null
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const parts = detail.map((d: any) => d.msg || String(d)).filter(Boolean)
    return parts.length ? parts.join(', ') : null
  }
  if (typeof detail === 'object') {
    const obj = detail as Record<string, unknown>
    // Falls das Backend strukturierte Validierungsfehler liefert (z. B. unter
    // {message, errors[]} wie der Blueprint-Importer), die Detail-Liste mit
    // anhaengen, damit der Nutzer sieht, welche Felder konkret kaputt sind.
    const errorsList = Array.isArray(obj.errors)
      ? (obj.errors as unknown[]).map((e) => String(e)).filter(Boolean)
      : []
    const baseMessage =
      (typeof obj.message === 'string' && obj.message) ||
      (typeof obj.error === 'string' && obj.error) ||
      (typeof obj.detail === 'string' && obj.detail) ||
      null
    if (baseMessage && errorsList.length) {
      return `${baseMessage}: ${errorsList.join('; ')}`
    }
    if (baseMessage) return baseMessage
    if (errorsList.length) return errorsList.join('; ')
    return null
  }
  return String(detail)
}

let refreshPromise: Promise<void> | null = null

async function doRefresh(): Promise<void> {
  const res = await fetch(apiUrl('/auth/refresh'), {
    method: 'POST',
    credentials: 'include',
  })
  captureCsrfFromResponse(res)
  if (!res.ok) {
    throw new Error('Session abgelaufen')
  }
}

async function refreshToken(): Promise<void> {
  if (refreshPromise) {
    return refreshPromise
  }
  refreshPromise = doRefresh()
  try {
    await refreshPromise
  } finally {
    refreshPromise = null
  }
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const method = (options?.method || 'GET').toUpperCase()
  const isStateChanging = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)

  const isFormData = typeof FormData !== 'undefined' && options?.body instanceof FormData
  const headers: Record<string, string> = {
    ...((options?.headers as Record<string, string>) || {}),
  }
  // Bei FormData darf KEIN Content-Type gesetzt werden — der Browser muss
  // ihn selbst inkl. `multipart/...; boundary=...` setzen.
  if (!isFormData && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }

  if (isStateChanging) {
    const csrf = getCsrfToken()
    if (csrf) {
      headers['X-CSRF-Token'] = csrf
    }
  }

  const fetchOptions: RequestInit = {
    ...options,
    credentials: 'include',
    headers,
    ...(method === 'GET' ? { cache: 'no-store' } : {}),
  }

  const url = apiUrl(path)

  const makeRequest = async (): Promise<Response> => {
    return fetch(url, fetchOptions)
  }

  let res = await makeRequest()
  captureCsrfFromResponse(res)

  // Token-Refresh bei 401 (ausser bei Login/Refresh selbst)
  if (res.status === 401 && path !== '/auth/refresh' && path !== '/auth/login') {
    try {
      await refreshToken()
      // Header neu bauen (CSRF koennte sich geaendert haben)
      const newHeaders = { ...headers }
      const newCsrf = getCsrfToken()
      if (newCsrf) {
        newHeaders['X-CSRF-Token'] = newCsrf
      } else {
        delete newHeaders['X-CSRF-Token']
      }
      res = await fetch(url, {
        ...fetchOptions,
        headers: newHeaders,
      })
      captureCsrfFromResponse(res)
    } catch {
      // Refresh fehlgeschlagen — Weiterleitung zum Login im Aufrufer.
      // Lokalisierte Meldung, damit der Caller die Fehlermeldung direkt
      // anzeigen kann (kein doppelter `t()`-Aufruf noetig). Diese Meldung
      // stammt aus einem verarbeiteten Backend-Response-Pfad und ist
      // sanitisiert (SanitizedApiError).
      throw new SanitizedApiError(i18n.t('errors.SESSION_EXPIRED'))
    }
  }

  if (!res.ok) {
    if (res.status === 429) {
      const message = i18n.t('errors.RATE_LIMITED')
      toast.error(message)
      throw new SanitizedApiError(message)
    }
    const text = await res.text()
    let message: string | null = null
    let code: string | null = null
    if (text) {
      try {
        const parsed = JSON.parse(text)
        const detail = parsed.detail ?? parsed.message ?? parsed.error ?? parsed
        message = extractErrorMessage(detail)
        if (detail && typeof detail === 'object' && typeof detail.code === 'string') {
          code = detail.code
        }
      } catch {
        message = text
      }
      if (message) {
        message = i18n.t(message)
      }
    }
    throw new SanitizedApiError(message || res.statusText || `HTTP ${res.status}`, {
      status: res.status,
      code: code ?? undefined,
    })
  }

  if (res.status === 204) {
    return {} as T
  }

  return res.json()
}
