import { usePermissionsStore } from '@/stores/permissionsStore'

/** Prueft eine Permission im Frontend (nur UI \u2014 Backend prueft selbst).
 *
 * @param key Permission-Key aus dem Catalog (global oder server-scoped)
 * @param serverId Optional \u2014 wenn gesetzt, wird zusaetzlich nach
 *                 server-spezifischer Delegation geschaut.
 */
export function useHasPermission(key: string, serverId?: number): boolean {
  const me = usePermissionsStore((s) => s.me)
  if (!me) return false
  if (me.is_owner) return true
  if (me.global_keys.includes(key)) return true
  if (serverId !== undefined) {
    const perServer = me.server_keys[String(serverId)] ?? []
    if (perServer.includes(key)) return true
  }
  return false
}

export function useIsOwner(): boolean {
  const me = usePermissionsStore((s) => s.me)
  return !!me?.is_owner
}
