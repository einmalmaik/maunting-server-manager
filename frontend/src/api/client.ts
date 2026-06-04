import i18n from '@/i18n'
import { toast } from '@/stores/toastStore'

const API_BASE = '/api'

function getCsrfToken(): string | null {
  const match = document.cookie.match(new RegExp('(^| )__Secure-csrf_token=([^;]+)'))
  return match ? decodeURIComponent(match[2]) : null
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
  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: 'POST',
    credentials: 'include',
  })
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

  const makeRequest = async (): Promise<Response> => {
    return fetch(`${API_BASE}${path}`, fetchOptions)
  }

  let res = await makeRequest()

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
      res = await fetch(`${API_BASE}${path}`, {
        ...fetchOptions,
        headers: newHeaders,
      })
    } catch {
      // Refresh fehlgeschlagen — Weiterleitung zum Login im Aufrufer.
      // Lokalisierte Meldung, damit der Caller die Fehlermeldung direkt
      // anzeigen kann (kein doppelter `t()`-Aufruf noetig).
      throw new Error(i18n.t('errors.SESSION_EXPIRED'))
    }
  }

  if (!res.ok) {
    if (res.status === 429) {
      const message = i18n.t('errors.RATE_LIMITED')
      toast.error(message)
      throw new Error(message)
    }
    const text = await res.text()
    let message: string | null = null
    if (text) {
      try {
        const parsed = JSON.parse(text)
        message = extractErrorMessage(parsed.detail ?? parsed.message ?? parsed.error ?? parsed)
      } catch {
        message = text
      }
      if (message) {
        message = i18n.t(message)
      }
    }
    throw new Error(message || res.statusText || `HTTP ${res.status}`)
  }

  if (res.status === 204) {
    return {} as T
  }

  return res.json()
}
