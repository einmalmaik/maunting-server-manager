import { describe, expect, it } from 'vitest'
import { buildNavigation } from './navigation'

const labels = {
  dashboard: 'Dashboard',
  calendar: 'Calendar',
  servers: 'Servers',
  users: 'Users',
  roles: 'Roles',
  audit: 'Audit',
  settings: 'Settings',
  blueprints: 'Blueprints',
  panelBackups: 'Panel Backups',
  panelDatabase: 'Panel Database',
  nodes: 'Nodes',
  docs: 'Docs',
  ai: 'AI',
}

describe('buildNavigation', () => {
  it('shows audit under Administration only when canViewAudit', () => {
    const denied = buildNavigation(labels, {
      owner: false,
      canManageUsers: false,
      canManageRoles: false,
      canViewAudit: false,
      canViewSettings: false,
      canManagePanelBackups: false,
      canReadPanelDatabase: false,
      canViewNodes: false,
      canUseAi: false,
      canUseSkills: false,
    })
    expect(denied.some((i) => i.to === '/admin/audit')).toBe(false)

    const allowed = buildNavigation(labels, {
      owner: false,
      canManageUsers: false,
      canManageRoles: false,
      canViewAudit: true,
      canViewSettings: false,
      canManagePanelBackups: false,
      canReadPanelDatabase: false,
      canViewNodes: false,
      canUseAi: false,
      canUseSkills: false,
    })
    const audit = allowed.find((i) => i.to === '/admin/audit')
    expect(audit).toBeDefined()
    expect(audit?.group).toBe('Administration')
    expect(audit?.label).toBe('Audit')
  })

  it('shows audit for owner even without explicit flag', () => {
    const nav = buildNavigation(labels, {
      owner: true,
      canManageUsers: false,
      canManageRoles: false,
      canViewAudit: false,
      canViewSettings: false,
      canManagePanelBackups: false,
      canReadPanelDatabase: false,
      canViewNodes: false,
      canUseAi: false,
      canUseSkills: false,
    })
    expect(nav.some((i) => i.to === '/admin/audit')).toBe(true)
  })

  it('shows AI only for AI chat, skill management, or owner under Infrastructure', () => {
    const access = {
      owner: false,
      canManageUsers: false,
      canManageRoles: false,
      canViewAudit: false,
      canViewSettings: false,
      canManagePanelBackups: false,
      canReadPanelDatabase: false,
      canViewNodes: false,
      canUseAi: true,
      canUseSkills: false,
    }
    const items = buildNavigation(labels, access)
    const aiItem = items.find((item) => item.to === '/ai')
    expect(aiItem).toBeDefined()
    expect(aiItem?.group).toBe('Infrastructure')
    expect(buildNavigation(labels, { ...access, canUseAi: false }).some((item) => item.to === '/ai')).toBe(false)
  })

  it('zeigt Teams auch dem, der nur Skills lesen darf', () => {
    // Seit die Skills aus dem Profil unter Teams gezogen sind, ist das der
    // einzige Weg zu den eigenen. Wer lesen, aber nicht chatten darf, haette
    // sie sonst verloren — ohne dass ihm ein Recht genommen wurde.
    const access = {
      owner: false,
      canManageUsers: false,
      canManageRoles: false,
      canViewAudit: false,
      canViewSettings: false,
      canManagePanelBackups: false,
      canReadPanelDatabase: false,
      canViewNodes: false,
      canUseAi: false,
      canUseSkills: true,
    }
    expect(buildNavigation(labels, access).some((item) => item.to === '/teams')).toBe(true)
    // Der Chat bleibt davon unberuehrt: Skills lesen ist kein Chatrecht.
    expect(buildNavigation(labels, access).some((item) => item.to === '/ai')).toBe(false)
    expect(
      buildNavigation(labels, { ...access, canUseSkills: false }).some((item) => item.to === '/teams'),
    ).toBe(false)
  })

  it('hides calendar from sidebar when calendarEnabled is false', () => {
    const access = {
      owner: false,
      canManageUsers: false,
      canManageRoles: false,
      canViewAudit: false,
      canViewSettings: false,
      canManagePanelBackups: false,
      canReadPanelDatabase: false,
      canViewNodes: false,
      canUseAi: false,
      canUseSkills: false,
    }
    expect(buildNavigation(labels, access).some((item) => item.to === '/calendar')).toBe(true)
    expect(buildNavigation(labels, { ...access, calendarEnabled: true }).some((item) => item.to === '/calendar')).toBe(true)
    expect(buildNavigation(labels, { ...access, calendarEnabled: false }).some((item) => item.to === '/calendar')).toBe(false)
  })
})
