import type { LucideIcon } from 'lucide-react'
import { Archive, BookOpen, Bot, Boxes, Calendar as CalendarIcon, Database, History, LayoutDashboard, Network, Server, Settings, Shield, Users, UsersRound } from 'lucide-react'

export type NavGroupName = 'Overview' | 'Infrastructure' | 'Administration' | 'Panel' | 'Help'
export interface NavigationItem { to: string; icon: LucideIcon; label: string; group: NavGroupName }

interface NavigationAccess {
  owner: boolean
  canManageUsers: boolean
  canManageRoles: boolean
  /** system.audit.read — privilegiertes Operator-Audit. */
  canViewAudit: boolean
  canViewSettings: boolean
  canManagePanelBackups: boolean
  canReadPanelDatabase: boolean
  canViewNodes: boolean
  canUseAi: boolean
  /**
   * ai.skills.use — reicht für /teams, aber nicht für den Chat.
   *
   * Seit die Skills aus den Profileinstellungen unter Teams gezogen sind, ist
   * das der einzige Weg zu den eigenen. Ohne diesen Eintrag verlöre ihn, wer
   * lesen darf, aber nicht chatten.
   */
  canUseSkills: boolean
}

/**
 * Baut die Sidebar-Navigation anhand von Labels und Permission-Flags.
 * Keine Secrets, keine Fachlogik — reine Sichtbarkeitsregeln.
 */
export function buildNavigation(labels: Record<string, string>, access: NavigationAccess): NavigationItem[] {
  return [
    { to: '/', icon: LayoutDashboard, label: labels.dashboard, group: 'Overview' },
    { to: '/calendar', icon: CalendarIcon, label: labels.calendar || 'Kalender', group: 'Overview' },
    ...(access.owner || access.canUseAi ? [{ to: '/ai', icon: Bot, label: labels.ai, group: 'Overview' as const }] : []),
    { to: '/servers', icon: Server, label: labels.servers, group: 'Infrastructure' },
    ...(access.owner || access.canViewNodes ? [{ to: '/admin/nodes', icon: Network, label: labels.nodes, group: 'Infrastructure' as const }] : []),
    ...(access.owner || access.canUseAi || access.canUseSkills ? [{ to: '/teams', icon: UsersRound, label: labels.teams, group: 'Infrastructure' as const }] : []),
    ...(access.owner || access.canManageUsers ? [{ to: '/users', icon: Users, label: labels.users, group: 'Administration' as const }] : []),
    ...(access.owner || access.canManageRoles ? [{ to: '/roles', icon: Shield, label: labels.roles, group: 'Administration' as const }] : []),
    ...(access.owner || access.canViewAudit ? [{ to: '/admin/audit', icon: History, label: labels.audit, group: 'Administration' as const }] : []),
    ...(access.owner || access.canViewSettings ? [
      { to: '/settings', icon: Settings, label: labels.settings, group: 'Panel' as const },
      { to: '/blueprints', icon: Boxes, label: labels.blueprints, group: 'Panel' as const },
    ] : []),
    ...(access.owner || access.canManagePanelBackups ? [{ to: '/panel-backups', icon: Archive, label: labels.panelBackups, group: 'Panel' as const }] : []),
    ...(access.owner || access.canReadPanelDatabase ? [{ to: '/panel-database', icon: Database, label: labels.panelDatabase, group: 'Panel' as const }] : []),
    { to: '/docs', icon: BookOpen, label: labels.docs, group: 'Help' },
  ]
}
