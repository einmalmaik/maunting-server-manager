/**
 * Prüft, ob ein Fehler durch Netzwerkunterbrechung, Flugmodus, Offline-Zustand
 * oder DNS/Verbindungsabbruch verursacht wurde.
 */
export function isNetworkOrOfflineError(err: unknown): boolean {
  if (typeof navigator !== 'undefined' && !navigator.onLine) {
    return true
  }
  if (!err) return false
  if (err instanceof Error) {
    if (err.name === 'TypeError' || err.name === 'AbortError') return true
    const msg = err.message.toLowerCase()
    if (
      msg.includes('fetch') ||
      msg.includes('network') ||
      msg.includes('offline') ||
      msg.includes('timeout') ||
      msg.includes('failed to fetch') ||
      msg.includes('connection refused')
    ) {
      return true
    }
  }
  return false
}
