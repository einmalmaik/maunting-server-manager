import type { User } from './types'

export const UI_PERMISSIONS = {
  dashboardView: 'dashboard.view',
  serversView: 'servers.view',
  backupsView: 'backups.view',
  backupsRestore: 'backups.restore',
  autorestartView: 'autorestart.view',
  autorestartManage: 'autorestart.manage',
  modsView: 'mods.view',
  modsInstall: 'mods.install',
  modsManage: 'mods.manage',
  modsUpdate: 'mods.update',
  modsReorder: 'mods.reorder',
  workshopUpdate: 'dashboard.workshop.update',
  consoleView: 'console.view.log',
  filesRead: 'files.read',
  filesWrite: 'files.write',
  usersView: 'users.view',
} as const

const ROUTE_ORDER: Array<{ path: string; permission?: string }> = [
  { path: '/dashboard', permission: UI_PERMISSIONS.dashboardView },
  { path: '/servers', permission: UI_PERMISSIONS.serversView },
  { path: '/backups', permission: UI_PERMISSIONS.backupsView },
  { path: '/autorestart', permission: UI_PERMISSIONS.autorestartView },
  { path: '/mods', permission: UI_PERMISSIONS.modsView },
  { path: '/console', permission: UI_PERMISSIONS.consoleView },
  { path: '/config', permission: UI_PERMISSIONS.filesRead },
  { path: '/files', permission: UI_PERMISSIONS.filesRead },
  { path: '/users', permission: UI_PERMISSIONS.usersView },
  { path: '/account' },
]

export function hasPermission(user: User | null | undefined, permission?: string): boolean {
  if (!permission) return Boolean(user)
  return Boolean(user?.permissions?.includes(permission))
}

export function getDefaultRoute(user: User | null | undefined): string {
  if (!user) return '/login'
  for (const route of ROUTE_ORDER) {
    if (hasPermission(user, route.permission)) {
      return route.path
    }
  }
  return '/account'
}
