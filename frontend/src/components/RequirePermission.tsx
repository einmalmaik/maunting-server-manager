import { useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { Loader } from '@/components/ui/Loader'

/** Maximale Wartezeit auf das initiale Permissions-Laden, bevor zur Startseite
 * redirected wird. Schuetzt vor unendlichem Spinner, falls
 * `/api/me/permissions` bei valider Session aus irgendeinem Grund nie
 * antwortet (z.B. 500 ohne Body, oder transienter Netzwerkfehler ohne
 * Retry). 5s ist grosszuegig genug fuer einen lokalen API-Call.
 */
const PERMISSIONS_LOAD_TIMEOUT_MS = 5000

/** Route-Level-Guard: rendert children nur, wenn der User mindestens eine der
 * angegebenen Permissions besitzt (oder Owner ist).
 *
 * KISS: kein Toast, kein 403-Banner — wer keine Berechtigung hat, wird zur
 * Startseite redirected. Sidebar versteckt die Routen bereits; dies hier ist
 * die Absicherung gegen Direkt-URLs und Bookmarks.
 */
export function RequirePermission({
  keys,
  children,
}: {
  keys: string | string[]
  children: React.ReactNode
}) {
  const me = usePermissionsStore((s) => s.me)
  const [timedOut, setTimedOut] = useState(false)

  // Sicherheits-Timeout: wenn Permissions nach 5s noch nicht da sind, kein
  // ewiger Spinner — redirect zur Startseite (Dashboard braucht keine
  // spezifische Permission).
  useEffect(() => {
    if (me) return
    const handle = setTimeout(() => setTimedOut(true), PERMISSIONS_LOAD_TIMEOUT_MS)
    return () => clearTimeout(handle)
  }, [me])

  if (!me) {
    if (timedOut) return <Navigate to="/" replace />
    return (
      <div className="flex items-center justify-center h-64">
        <Loader label="Maunting Server Manager" />
      </div>
    )
  }
  if (me.is_owner) return <>{children}</>

  const list = Array.isArray(keys) ? keys : [keys]
  const allowed = list.some((k) => me.global_keys.includes(k))
  if (!allowed) {
    return <Navigate to="/" replace />
  }
  return <>{children}</>
}
