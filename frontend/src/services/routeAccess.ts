import type { MePermissions } from '@/types/permissions'

export type RouteAccessState = 'loading' | 'allowed' | 'forbidden' | 'notFound' | 'error'

export const routeAccessRules = {
  dashboard: null,
  docs: null,
  users: ['users.read', 'users.manage'],
  roles: ['roles.manage'],
  settings: ['panel.settings.read'],
  blueprints: ['panel.settings.read'],
  panelBackups: ['panel.settings.write'],
  panelDatabase: ['panel.database.read'],
  nodes: ['nodes.read', 'nodes.manage'],
} as const

export function resolveRouteAccessState({
  routeKey,
  me,
  isLoading,
  error,
}: {
  routeKey: string
  me: MePermissions | null
  isLoading: boolean
  error: string | null
}): RouteAccessState {
  if (!isKnownRouteKey(routeKey)) return 'notFound'

  const requiredKeys = routeAccessRules[routeKey]
  if (!requiredKeys) return 'allowed'

  if (me?.is_owner) return 'allowed'
  if (me && requiredKeys.some((key) => me.global_keys.includes(key))) return 'allowed'
  if (me) return 'forbidden'
  if (error) return 'error'
  if (isLoading || !me) return 'loading'

  return 'error'
}

function isKnownRouteKey(routeKey: string): routeKey is keyof typeof routeAccessRules {
  return Object.prototype.hasOwnProperty.call(routeAccessRules, routeKey)
}
