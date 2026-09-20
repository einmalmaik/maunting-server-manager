/**
 * Prüft, ob ein Fehler durch Netzwerkunterbrechung, Flugmodus, Offline-Zustand,
 * DNS/Verbindungsabbruch oder temporäre Gateway-/Server-Neustart-Fehler (502/503/504)
 * verursacht wurde.
 */
export function isNetworkOrOfflineError(err: unknown): boolean {
  if (typeof navigator !== 'undefined' && !navigator.onLine) {
    return true
  }
  if (!err) return false

  // Statuscode-Prüfung (z. B. von SanitizedApiError oder Response-Objekten bei Reverse-Proxy-Ausfällen)
  const status =
    typeof (err as { status?: unknown }).status === 'number'
      ? (err as { status: number }).status
      : typeof (err as { statusCode?: unknown }).statusCode === 'number'
        ? (err as { statusCode: number }).statusCode
        : null

  if (status === 502 || status === 503 || status === 504) {
    return true
  }

  if (err instanceof Error) {
    if (err.name === 'TypeError' || err.name === 'AbortError') return true
    const msg = err.message.toLowerCase()
    if (
      msg.includes('fetch') ||
      msg.includes('network') ||
      msg.includes('offline') ||
      msg.includes('timeout') ||
      msg.includes('failed to fetch') ||
      msg.includes('connection refused') ||
      msg.includes('bad gateway') ||
      msg.includes('service unavailable') ||
      msg.includes('gateway timeout') ||
      msg.includes('502') ||
      msg.includes('503') ||
      msg.includes('504')
    ) {
      return true
    }
  }
  return false
}
