import { useEffect, useState } from 'react'

/**
 * Reaktiver Hook für den Netzwerkstatus des Browsers.
 *
 * Gibt `true` zurück, wenn `navigator.onLine` `true` meldet, und reagiert
 * auf die Browser-Events `online` / `offline`. Auf dem Server (SSR) wird
 * konservativ `true` angenommen.
 */
export function useIsOnline(): boolean {
  const [isOnline, setIsOnline] = useState(
    () => (typeof navigator !== 'undefined' ? navigator.onLine : true),
  )

  useEffect(() => {
    const handleOnline = () => setIsOnline(true)
    const handleOffline = () => setIsOnline(false)
    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)
    return () => {
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
    }
  }, [])

  return isOnline
}
