import { describe, expect, it } from 'vitest'
import { buildNavigation } from './navigation'

const labels = {
  dashboard: 'Dashboard',
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
    })
    expect(nav.some((i) => i.to === '/admin/audit')).toBe(true)
  })

  it('shows AI only for AI chat, skill management, or owner', () => {
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
    }
    expect(buildNavigation(labels, access).some((item) => item.to === '/ai')).toBe(true)
    expect(buildNavigation(labels, { ...access, canUseAi: false }).some((item) => item.to === '/ai')).toBe(false)
  })
})
