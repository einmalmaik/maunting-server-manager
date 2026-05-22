const API_BASE = '/api'

function getCsrfToken(): string | null {
  const match = document.cookie.match(new RegExp('(^| )__Secure-csrf_token=([^;]+)'))
  return match ? decodeURIComponent(match[2]) : null
}

function extractErrorMessage(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((d: any) => d.msg || String(d)).join(', ')
  }
  if (detail && typeof detail === 'object') {
    const obj = detail as Record<string, unknown>
    if (typeof obj.message === 'string') return obj.message
    if (typeof obj.error === 'string') return obj.error
    return JSON.stringify(detail)
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

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options?.headers as Record<string, string>) || {}),
  }

  if (isStateChanging) {
    const csrf = getCsrfToken()
    if (csrf) {
      headers['X-CSRF-Token'] = csrf
    }
  }

  const makeRequest = async (): Promise<Response> => {
    return fetch(`${API_BASE}${path}`, {
      ...options,
      credentials: 'include',
      headers,
    })
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
        ...options,
        credentials: 'include',
        headers: newHeaders,
      })
    } catch {
      // Refresh fehlgeschlagen — Weiterleitung zum Login im Aufrufer
      throw new Error('SESSION_EXPIRED')
    }
  }

  if (!res.ok) {
    if (res.status === 429) {
      throw new Error('RATE_LIMITED')
    }
    const err = await res.json().catch(() => ({ detail: 'Unbekannter Fehler' }))
    throw new Error(extractErrorMessage(err.detail) || `HTTP ${res.status}`)
  }

  if (res.status === 204) {
    return {} as T
  }

  return res.json()
}
