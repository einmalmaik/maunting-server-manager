import i18n from '@/i18n'
import { apiUrl } from '@/config/api'
import { toast } from '@/stores/toastStore'
import { useAuthStore } from '@/stores/authStore'

export { API_BASE, apiUrl } from '@/config/api'
import { getEffectiveApiUrl } from '@/config/api'

/**
 * Checks whether a given URL points to the same origin / internal API.
 * External URLs (third-party domains) must NEVER receive authorization headers,
 * session cookies, or CSRF tokens.
 */
export function isInternalApiUrl(url: string): boolean {
  if (!url) return false
  const trimmed = url.trim()
  if (!trimmed) return false
  // Protocol-relative URLs (e.g. "//attacker.com/evil") are external
  if (trimmed.startsWith('//')) return false
  // Relative paths without scheme: internal (e.g. "/api/foo", "api/foo", "avatar.png")
  if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(trimmed)) {
    return true
  }
  // Data or blob URLs don't receive API auth tokens
  if (/^(data:|blob:)/i.test(trimmed)) {
    return false
  }
  // Absolute HTTP/HTTPS URLs: check if origin matches allowed origin
  try {
    const parsed = new URL(trimmed)
    const allowed = new Set<string>()
    const effective = getEffectiveApiUrl()
    if (effective) {
      // Wenn eine explizite Backend-URL konfiguriert ist (z. B. Desktop-App,
      // Android-App oder getrenntes Hosting), ist NUR diese Backend-URL der API-Server.
      try {
        allowed.add(new URL(effective).origin.toLowerCase())
      } catch {}
    } else if (typeof window !== 'undefined' && window.location?.origin) {
      // Im Same-Origin-Modus (Standard-Webdeployment) liefert das Backend
      // das Frontend selbst aus — die Web-Domain ist also der API-Server.
      allowed.add(window.location.origin.toLowerCase())
    }
    return allowed.has(parsed.origin.toLowerCase())
  } catch {
    return false
  }
}

/**
 * Bekommt diese Adresse das Zugangstoken? Nur die API selbst, nicht alles auf
 * ihrer Herkunft. Beim lokalen LiveKit und beim Same-Origin-Hosting liegen dort
 * auch `/livekit/…` und die Oberfläche; die brauchen kein Token.
 */
export function bekommtAnmeldung(url: string): boolean {
  if (!isInternalApiUrl(url)) return false
  try {
    const basis =
      getEffectiveApiUrl() || (typeof window !== 'undefined' ? window.location.origin : 'http://localhost')
    const pfad = new URL(url.trim(), basis).pathname
    return pfad === '/api' || pfad.startsWith('/api/')
  } catch {
    return false
  }
}

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

import { isNetworkOrOfflineError } from '@/lib/networkErrors'
export { isNetworkOrOfflineError }

/**
 * Die native Sitzung der Desktop-App (MSS) — im Panel immer `null`.
 *
 * Das Panel authentifiziert über HttpOnly-Cookies; ein Tauri-WebView bekommt
 * auf einem fremden Origin nie welche. Die App registriert deshalb beim Start
 * genau ein Objekt: woher das Access-Token kommt und wie rotiert wird
 * (Refresh-Token liegt im OS-Tresor, nicht hier). Ist es gesetzt, tragen
 * `api()`/`apiStream()` einen `Authorization: Bearer` und delegieren den
 * 401-Refresh — alles andere (Fehlerübersetzung, 429-Toast, SESSION_EXPIRED →
 * `clearSession`) bleibt derselbe Weg wie im Browser. CSRF entfällt nicht per
 * Sonderfall: ohne Cookies findet `getCsrfToken()` schlicht nichts, und das
 * Backend befreit gültige Bearer ohnehin (`dependencies.verify_csrf`).
 */
export interface NativeSitzung {
  /** Das aktuelle Access-Token — `null`, solange keines da ist. */
  token(): string | null
  /** Rotiert über den Tresor. `false` heißt: die Sitzung ist endgültig weg. */
  erneuern(): Promise<boolean>
}

let nativeSitzung: NativeSitzung | null = null

export function registriereNativeSitzung(sitzung: NativeSitzung): void {
  nativeSitzung = sitzung
}

/**
 * Restlaufzeit eines JWT in Sekunden — `null`, wenn die Nutzlast nicht
 * lesbar ist. Nur gelesen, nie geprüft: die Prüfung bleibt beim Server,
 * hier geht es allein darum, ein absehbar totes Token nicht erst in einen
 * Handshake zu tragen.
 */
function tokenRestSekunden(token: string): number | null {
  try {
    const nutzlast = JSON.parse(
      atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')),
    ) as { exp?: unknown }
    const exp = Number(nutzlast.exp)
    if (!Number.isFinite(exp)) return null
    return exp - Math.floor(Date.now() / 1000)
  } catch {
    return null
  }
}

/**
 * Subprotokolle für authentifizierte WebSockets (`msm.bearer, <token>`).
 *
 * Ein Browser-WebSocket kann keinen Authorization-Header setzen; das
 * Subprotokoll-Feld ist der eine Header, den er erreicht — und ein Token in
 * der URL wäre einer in Zugriffs- und Proxy-Logs. Im Panel `undefined`:
 * dort authentifiziert der WS-Handshake über das mitgesendete Cookie.
 *
 * Async und frischegeprüft, weil ein WS-Handshake — anders als HTTP — keinen
 * 401-Retry hat: ein abgelaufenes Token heißt close(1008) **vor** accept, und
 * die Oberfläche sieht nur „Verbindung verloren". Genau so starb der
 * Sprachmodus der Desktop-App: das Access-Token lebt 15 Minuten, das Overlay
 * rotiert es nie von selbst, und der planmäßige Reconnect nach der
 * Sitzungshöchstdauer (== Token-Laufzeit) kam damit immer mit einem toten
 * Token an. Unter 60 s Rest — oder wenn die Nutzlast nicht lesbar ist — wird
 * deshalb erst rotiert.
 */
export async function wsProtokolle(): Promise<string[] | undefined> {
  if (!nativeSitzung) return undefined
  const token = nativeSitzung.token()
  const rest = token ? tokenRestSekunden(token) : 0
  if (rest === null || rest < 60) {
    await nativeSitzung.erneuern().catch(() => false)
  }
  const frisch = nativeSitzung.token()
  return frisch ? ['msm.bearer', frisch] : undefined
}

function nativesToken(): string | null {
  return nativeSitzung?.token() ?? null
}

/**
 * CSRF value held in memory for cross-origin setups where document.cookie
 * cannot read the API host's `__Secure-csrf_token` (different site).
 * Populated from `X-CSRF-Token` response headers (login, refresh, /me).
 */
let csrfTokenMemory: string | null = null
let isCurrentlyOffline = false

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

/**
 * Macht aus einem reinen Fehlercode einen Satz.
 *
 * Die Zustandsfehler der KI-Aktionen antworten mit `{"code": "..."}` und ganz
 * ohne Text (routers/ai_actions.py::_state_error). Das ist Absicht: der Code
 * ist die stabile Kennung an der Schnittstelle, den Satz dazu hält die
 * Oberfläche. Nur hielt sie ihn bisher nirgends — die Meldung fiel auf
 * `res.statusText` zurück, und der Benutzer las „Conflict", statt zu erfahren,
 * dass sich die Datei seit der Vorschau geändert hat.
 *
 * Nachgeschlagen wird im einzigen Katalog, den es dafür gibt. Kennt er den
 * Code nicht, bleibt es beim bisherigen Rückfall: ein Statustext ist immer noch
 * besser als ein roher Schlüssel in der Oberfläche.
 */
function translateErrorCode(code: string): string | null {
  const key = `ai.errors.codes.${code}`
  return i18n.exists(key) ? i18n.t(key) : null
}

export class AuthExpiredError extends Error {
  constructor(message = 'Session abgelaufen') {
    super(message)
    this.name = 'AuthExpiredError'
  }
}

let refreshPromise: Promise<void> | null = null

async function doRefresh(): Promise<void> {
  // Nativ (Desktop-App): die Rotation läuft über den OS-Tresor, nicht über
  // Cookies — aber durch denselben Trichter hier, damit gleichzeitige 401er
  // weiterhin genau einen Refresh auslösen.
  if (nativeSitzung) {
    let ok = false
    try {
      ok = await nativeSitzung.erneuern()
    } catch (err) {
      if (err instanceof AuthExpiredError) {
        throw err
      }
      throw err
    }
    if (!ok) {
      throw new Error(i18n.t('auth.errors.refreshUnavailable'))
    }
    return
  }
  let res: Response
  try {
    res = await fetch(apiUrl('/auth/refresh'), {
      method: 'POST',
      credentials: 'include',
    })
  } catch (err) {
    // Verbindungsabbruch, Offline, Timeout etc.
    // Das ist KEIN Ablauf der Sitzung; Fehler werfen, ohne die Sitzung zu räumen.
    throw err
  }
  captureCsrfFromResponse(res)
  if (res.status === 401 || res.status === 403) {
    throw new AuthExpiredError('Session abgelaufen')
  }
  if (!res.ok) {
    throw new Error(i18n.t('auth.errors.refreshFailed', { status: res.status }))
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

  const url = apiUrl(path)
  const isInternal = isInternalApiUrl(url)

  if (isInternal) {
    const bearer = bekommtAnmeldung(url) ? nativesToken() : null
    if (bearer) {
      headers['Authorization'] = `Bearer ${bearer}`
    }

    if (isStateChanging) {
      const csrf = getCsrfToken()
      if (csrf) {
        headers['X-CSRF-Token'] = csrf
      }
    }
  } else {
    delete headers['Authorization']
    delete headers['X-CSRF-Token']
  }

  const fetchOptions: RequestInit = {
    ...options,
    credentials: isInternal ? 'include' : 'omit',
    headers,
    ...(method === 'GET' ? { cache: 'no-store' } : {}),
  }

  const makeRequest = async (): Promise<Response> => {
    return fetch(url, fetchOptions)
  }

  let res: Response
  try {
    res = await makeRequest()
    if (isCurrentlyOffline) {
      isCurrentlyOffline = false
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('msm:network-online'))
      }
    }
  } catch (fetchErr) {
    if (!isCurrentlyOffline && typeof window !== 'undefined' && isNetworkOrOfflineError(fetchErr)) {
      isCurrentlyOffline = true
      window.dispatchEvent(new CustomEvent('msm:network-offline'))
    }
    throw fetchErr
  }
  captureCsrfFromResponse(res)

  // Token-Refresh bei 401 (nur bei internen Endpoints und ausser bei Login/Refresh selbst)
  if (res.status === 401 && isInternal && path !== '/auth/refresh' && path !== '/auth/login') {
    let refreshed = false
    try {
      await refreshToken()
      refreshed = true
    } catch (refreshErr) {
      // Nur bei echter Authentifizierungsablehnung (401/403 auf /auth/refresh) wird
      // die Sitzung geräumt. Bei Verbindungsabbrüchen, Timeouts, 502/503 oder Abort
      // bleibt der Sitzungsspeicher unverändert.
      if (refreshErr instanceof AuthExpiredError) {
        useAuthStore.getState().clearSession()
        throw new SanitizedApiError(i18n.t('errors.SESSION_EXPIRED'), { status: 401 })
      }
      throw refreshErr
    }

    if (refreshed) {
      // Header neu bauen (CSRF und Bearer koennten sich geaendert haben)
      const newHeaders = { ...headers }
      const neuesBearer = bekommtAnmeldung(url) ? nativesToken() : null
      if (neuesBearer) {
        newHeaders['Authorization'] = `Bearer ${neuesBearer}`
      }
      const newCsrf = getCsrfToken()
      if (newCsrf) {
        newHeaders['X-CSRF-Token'] = newCsrf
      } else {
        delete newHeaders['X-CSRF-Token']
      }
      try {
        res = await fetch(url, {
          ...fetchOptions,
          headers: newHeaders,
        })
        captureCsrfFromResponse(res)
      } catch (retryErr) {
        // Ein Netzwerkfehler, Timeout oder AbortError beim wiederholten Request
        // darf NIEMALS useAuthStore.getState().clearSession() aufrufen!
        if (!isCurrentlyOffline && typeof window !== 'undefined' && isNetworkOrOfflineError(retryErr)) {
          isCurrentlyOffline = true
          window.dispatchEvent(new CustomEvent('msm:network-offline'))
        }
        throw retryErr
      }
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
        // Kein JSON — dann hat nicht das Backend geantwortet, sondern etwas
        // davor (Proxy-Fehlerseite bei 502/504, abgeschnittener Rumpf). Dieser
        // Text ist nie durch die Bereinigung des Backends gelaufen und darf
        // deshalb nicht in die Meldung: er zeigte dem Benutzer sonst die
        // komplette HTML-Seite samt Kennung und Version des Proxys.
        // `message` bleibt null, der Rückfall auf statusText greift von allein.
      }
      if (message) {
        message = i18n.t(message)
      }
    }
    // Bewusst erst nach `i18n.t(message)`: der Satz aus dem Katalog ist bereits
    // übersetzt und darf nicht ein zweites Mal als Schlüssel gelesen werden.
    if (!message && code) {
      message = translateErrorCode(code)
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

/**
 * Fuehrt einen authentifizierten API-Request aus, ohne den Response-Body zu
 * konsumieren. Das ist fuer POST-SSE notwendig: EventSource unterstuetzt
 * weder POST noch den CSRF-Header. Auth-, CSRF- und Fehlerverhalten bleiben
 * damit identisch zum normalen API-Client.
 */
export async function apiStream(path: string, options: RequestInit): Promise<Response> {
  const method = (options.method || 'GET').toUpperCase()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
    ...((options.headers as Record<string, string>) || {}),
  }

  const url = apiUrl(path)
  const isInternal = isInternalApiUrl(url)

  if (isInternal) {
    const bearer = bekommtAnmeldung(url) ? nativesToken() : null
    if (bearer) headers['Authorization'] = `Bearer ${bearer}`
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
      const csrf = getCsrfToken()
      if (csrf) headers['X-CSRF-Token'] = csrf
    }
  } else {
    delete headers['Authorization']
    delete headers['X-CSRF-Token']
  }

  const fetchOptions: RequestInit = {
    ...options,
    method,
    credentials: isInternal ? 'include' : 'omit',
    headers,
    cache: 'no-store',
  }
  let res = await fetch(url, fetchOptions)
  captureCsrfFromResponse(res)

  if (res.status === 401 && isInternal && path !== '/auth/refresh' && path !== '/auth/login') {
    let refreshed = false
    try {
      await refreshToken()
      refreshed = true
    } catch (refreshErr) {
      if (refreshErr instanceof AuthExpiredError) {
        useAuthStore.getState().clearSession()
        throw new SanitizedApiError(i18n.t('errors.SESSION_EXPIRED'), { status: 401 })
      }
      throw refreshErr
    }

    if (refreshed) {
      const retryHeaders = { ...headers }
      const neuesBearer = bekommtAnmeldung(url) ? nativesToken() : null
      if (neuesBearer) retryHeaders['Authorization'] = `Bearer ${neuesBearer}`
      const csrf = getCsrfToken()
      if (csrf) retryHeaders['X-CSRF-Token'] = csrf
      else delete retryHeaders['X-CSRF-Token']
      try {
        res = await fetch(url, { ...fetchOptions, headers: retryHeaders })
        captureCsrfFromResponse(res)
      } catch (retryErr) {
        throw retryErr
      }
    }
  }

  if (!res.ok) {
    if (res.status === 429) {
      const message = i18n.t('errors.RATE_LIMITED')
      toast.error(message)
      throw new SanitizedApiError(message, { status: 429 })
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
        // Kein JSON — siehe `api()`: der Rohtext stammt nicht vom Backend und
        // bleibt außen vor.
      }
    }
    // Derselbe Rückfall wie in `api()`. Der Stream-Start scheitert mit genau
    // derselben Antwort, sobald ein Vorschlag nicht mehr ausführbar ist.
    const ausCode = !message && code ? translateErrorCode(code) : null
    throw new SanitizedApiError(
      (message ? i18n.t(message) : ausCode) || res.statusText || `HTTP ${res.status}`,
      { status: res.status, code: code ?? undefined },
    )
  }

  return res
}
