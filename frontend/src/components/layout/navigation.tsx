import type { LucideIcon } from 'lucide-react'
import { Archive, BookOpen, Boxes, Database, LayoutDashboard, Network, Server, Settings, Shield, Users } from 'lucide-react'

export type NavGroupName = 'Overview' | 'Infrastructure' | 'Administration' | 'Panel' | 'Help'
export interface NavigationItem { to: string; icon: LucideIcon; label: string; group: NavGroupName }

interface NavigationAccess {
  owner: boolean
  canManageUsers: boolean
  canManageRoles: boolean
  canViewSettings: boolean
  canManagePanelBackups: boolean
  canReadPanelDatabase: boolean
  canViewNodes: boolean
}

export function buildNavigation(labels: Record<string, string>, access: NavigationAccess): NavigationItem[] {
  return [
    { to: '/', icon: LayoutDashboard, label: labels.dashboard, group: 'Overview' },
    { to: '/servers', icon: Server, label: labels.servers, group: 'Infrastructure' },
    ...(access.owner || access.canViewNodes ? [{ to: '/admin/nodes', icon: Network, label: labels.nodes, group: 'Infrastructure' as const }] : []),
    ...(access.owner || access.canManageUsers ? [{ to: '/users', icon: Users, label: labels.users, group: 'Administration' as const }] : []),
    ...(access.owner || access.canManageRoles ? [{ to: '/roles', icon: Shield, label: labels.roles, group: 'Administration' as const }] : []),
    ...(access.owner || access.canViewSettings ? [
      { to: '/settings', icon: Settings, label: labels.settings, group: 'Panel' as const },
      { to: '/blueprints', icon: Boxes, label: labels.blueprints, group: 'Panel' as const },
    ] : []),
    ...(access.owner || access.canManagePanelBackups ? [{ to: '/panel-backups', icon: Archive, label: labels.panelBackups, group: 'Panel' as const }] : []),
    ...(access.owner || access.canReadPanelDatabase ? [{ to: '/panel-database', icon: Database, label: labels.panelDatabase, group: 'Panel' as const }] : []),
    { to: '/docs', icon: BookOpen, label: labels.docs, group: 'Help' },
  ]
}
