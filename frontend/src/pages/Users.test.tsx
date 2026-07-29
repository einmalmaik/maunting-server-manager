import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { Users } from './Users'
import { api } from '@/api/client'
import { rbacApi } from '@/api/rbac'
import { useAuthStore } from '@/stores/authStore'
import { usePermissionsStore } from '@/stores/permissionsStore'
import i18n from '@/i18n'
import type { Server, User } from '@/types'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

vi.mock('@/api/rbac', () => ({
  rbacApi: {
    listRoles: vi.fn(),
    assignRole: vi.fn(),
  },
}))

const renderedServerIds: number[] = []
vi.mock('@/components/ServerPermissionsPanel', () => ({
  ServerPermissionsPanel: ({ serverId }: { serverId: number }) => {
    renderedServerIds.push(serverId)
    return <div data-testid="server-permissions-panel">Server {serverId}</div>
  },
}))

function user(overrides: Partial<User>): User {
  return {
    id: 1,
    username: 'owner',
    email: 'owner@example.invalid',
    is_owner: true,
    is_active: true,
    email_verified: true,
    two_factor_enabled: false,
    email_notifications: false,
    role_id: null,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function server(id: number, name: string): Server {
  return {
    id,
    name,
    game_type: 'synthetic_test_game',
    status: 'running',
  } as Server
}

const owner = user({})
const currentUser = user({
  id: 2,
  username: 'current-user',
  email: 'current-user@example.invalid',
  is_owner: false,
  role_id: 7,
})
const otherUser = user({
  id: 3,
  username: 'delegated-user',
  email: 'delegated-user@example.invalid',
  is_owner: false,
  role_id: null,
})

const longServerName =
  'Maunting Community Conan Exiles Produktionsserver Europa West mit vollständig lesbarem Namen'
const servers = [
  server(41, longServerName),
  server(77, 'Kompakter Testserver Nord'),
]

function mockFetches() {
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === '/admin/users') return [owner, currentUser, otherUser] as never
    if (path === '/servers') return servers as never
    throw new Error(`Unexpected API path: ${path}`)
  })
  vi.mocked(rbacApi.listRoles).mockResolvedValue([
    {
      id: 7,
      name: 'user',
      description: '',
      is_system: true,
      permissions: [],
      created_at: '2026-01-01T00:00:00Z',
    },
  ])
}

describe('Users access workspace', () => {
  beforeEach(async () => {
    vi.clearAllMocks()
    renderedServerIds.length = 0
    await i18n.changeLanguage('de')
    useAuthStore.setState({
      user: currentUser,
      isAuthenticated: true,
      isLoading: false,
    })
    usePermissionsStore.setState({
      me: {
        is_owner: false,
        role_id: 7,
        role_name: 'user',
        global_keys: ['users.manage', 'users.permissions.manage'],
        server_keys: {},
      },
      isLoading: false,
      error: null,
    })
    mockFetches()
  })

  it('shows long server names completely and filters the server rail', async () => {
    render(<Users />)

    const longNameButton = await screen.findByRole('button', { name: new RegExp(longServerName) })
    expect(longNameButton).toHaveAttribute('aria-pressed', 'true')
    expect(longNameButton.querySelector('.truncate')).toBeNull()
    expect(within(longNameButton).getByText(longServerName)).toHaveClass('break-words')

    fireEvent.change(screen.getByRole('searchbox', { name: /server/i }), {
      target: { value: 'nord' },
    })

    const serverRail = screen.getByTestId('server-access-workspace').querySelector('aside')
    expect(serverRail).not.toBeNull()
    expect(within(serverRail as HTMLElement).queryByText(longServerName)).not.toBeInTheDocument()
    expect(within(serverRail as HTMLElement).getByText('Kompakter Testserver Nord')).toBeInTheDocument()
  })

  it('selects a server by stable numeric ID', async () => {
    render(<Users />)

    await screen.findByRole('button', { name: /Kompakter Testserver Nord/i })
    expect(renderedServerIds).toContain(41)

    fireEvent.click(screen.getByRole('button', { name: /Kompakter Testserver Nord/i }))

    await waitFor(() => {
      expect(renderedServerIds[renderedServerIds.length - 1]).toBe(77)
      expect(screen.getByTestId('server-permissions-panel')).toHaveTextContent('Server 77')
    })
  })

  it('keeps owner and current-user role/delete safeguards and uses a responsive ledger', async () => {
    render(<Users />)

    const directory = await screen.findByTestId('user-directory')
    expect(within(directory).queryByRole('table')).not.toBeInTheDocument()
    expect(directory).toHaveClass('min-w-0')

    expect(screen.queryByRole('button', { name: /Rolle zuweisen: owner/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Rolle zuweisen: current-user/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Löschen: owner/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Löschen: current-user/i })).not.toBeInTheDocument()

    expect(screen.getByRole('button', { name: /Rolle zuweisen: delegated-user/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Löschen: delegated-user/i })).toBeInTheDocument()
  })
})
