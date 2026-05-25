import { Navigate } from 'react-router-dom'
import { usePermissionsStore } from '@/stores/permissionsStore'

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
  if (!me) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
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
