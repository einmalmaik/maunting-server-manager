import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ServerPermissionsPanel } from './ServerPermissionsPanel'
import { api } from '@/api/client'
import { rbacApi } from '@/api/rbac'
import { confirm } from '@/stores/confirmStore'
import i18n from '@/i18n'
import type { User } from '@/types'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

vi.mock('@/api/rbac', () => ({
  rbacApi: {
    catalog: vi.fn(),
    getServerPermissions: vi.fn(),
    setServerPermissions: vi.fn(),
    revokeServerPermissions: vi.fn(),
  },
}))

vi.mock('@/stores/confirmStore', () => ({
  confirm: vi.fn(),
}))

function user(id: number, username: string, isOwner = false): User {
  return {
    id,
    username,
    email: `${username}@example.invalid`,
    is_owner: isOwner,
    is_active: true,
    email_verified: true,
    two_factor_enabled: false,
    email_notifications: false,
    role_id: null,
    created_at: '2026-01-01T00:00:00Z',
  }
}

const owner = user(1, 'owner', true)
const delegated = user(2, 'delegated-user')
const candidate = user(3, 'candidate-user')

describe('ServerPermissionsPanel delegation wiring', () => {
  beforeEach(async () => {
    vi.clearAllMocks()
    await i18n.changeLanguage('de')
    vi.mocked(api).mockResolvedValue([owner, delegated, candidate] as never)
    vi.mocked(rbacApi.catalog).mockResolvedValue({
      global_permissions: [],
      server_permissions: [
        { key: 'server.view', group: 'server', label: 'Server anzeigen' },
        { key: 'server.stop', group: 'server', label: 'Server stoppen' },
      ],
    })
    vi.mocked(rbacApi.getServerPermissions).mockImplementation(async (userId, serverId) => ({
      server_id: serverId,
      permissions: userId === delegated.id ? ['server.view', 'server.stop'] : [],
    }))
    vi.mocked(rbacApi.setServerPermissions).mockResolvedValue({
      server_id: 41,
      permissions: [],
    })
    vi.mocked(rbacApi.revokeServerPermissions).mockResolvedValue(undefined)
    vi.mocked(confirm).mockResolvedValue(true)
  })

  it('adds only server.view for a newly delegated non-owner user', async () => {
    render(<ServerPermissionsPanel serverId={41} />)

    const picker = await screen.findByRole('button', { name: 'User wählen' })
    fireEvent.click(picker)
    fireEvent.click(await screen.findByRole('option', { name: 'candidate-user' }))
    fireEvent.click(screen.getByRole('button', { name: 'User hinzufügen' }))

    await waitFor(() => {
      expect(rbacApi.setServerPermissions).toHaveBeenCalledWith(3, 41, ['server.view'])
    })
    expect(screen.queryByRole('option', { name: 'owner' })).not.toBeInTheDocument()
  })

  it('keeps edit and confirmed revoke operations bound to the selected server', async () => {
    render(<ServerPermissionsPanel serverId={41} />)

    await screen.findByText('delegated-user')
    fireEvent.click(screen.getByRole('button', { name: 'Bearbeiten' }))
    expect(screen.getByText('Server anzeigen')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => {
      expect(rbacApi.setServerPermissions).toHaveBeenCalledWith(
        delegated.id,
        41,
        ['server.stop', 'server.view'],
      )
    })

    fireEvent.click(screen.getByRole('button', { name: /Komplett entfernen: delegated-user/ }))
    await waitFor(() => {
      expect(confirm).toHaveBeenCalledWith({
        message: 'Alle Berechtigungen dieses Users für diesen Server entfernen?',
        danger: true,
      })
      expect(rbacApi.revokeServerPermissions).toHaveBeenCalledWith(delegated.id, 41)
    })
  })
})
