import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Sidebar } from './Sidebar'
import { useAuthStore } from '@/stores/authStore'
import { useToastStore } from '@/stores/toastStore'
import { api } from '@/api/client'
import i18n from '@/i18n'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

function setUser(emailNotifications = true, aiNotifications = true, deviceNotifications = true) {
  useAuthStore.setState({
    user: {
      id: 1,
      username: 'owner',
      email: 'owner@example.test',
      is_owner: true,
      is_active: true,
      email_verified: true,
      two_factor_enabled: false,
      email_notifications: emailNotifications,
      ai_notifications: aiNotifications,
      device_notifications: deviceNotifications,
      avatar_url: '/api/auth/avatar/test.png',
      role_id: null,
      created_at: '2026-05-31T00:00:00Z',
    },
    isAuthenticated: true,
    isLoading: false,
  })
}

function oeffneGlocke() {
  fireEvent.click(screen.getByRole('button', { name: /benachrichtigungen/i }))
}

describe('Sidebar', () => {
  beforeEach(() => {
    i18n.changeLanguage('de')
    vi.mocked(api).mockReset().mockResolvedValue({})
    useToastStore.setState({ toasts: [] })
    setUser(true)
  })

  it('renders user discord widget and opens user menu', () => {
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )

    const userBtn = screen.getByRole('button', { name: /benutzermenü|user menu/i })
    expect(userBtn).toBeInTheDocument()
    expect(screen.getByText('owner')).toBeInTheDocument()

    fireEvent.click(userBtn)
    expect(screen.getByRole('menuitem', { name: /profil/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /abmelden/i })).toBeInTheDocument()
  })

  it('schaltet die E-Mail-Benachrichtigungen über die Glocke im Sidebar-Footer', async () => {
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )

    oeffneGlocke()
    fireEvent.click(screen.getByRole('switch', { name: 'E-Mail-Benachrichtigungen' }))

    await waitFor(() => {
      expect(api).toHaveBeenCalledWith('/auth/me/notifications?enabled=false', { method: 'PATCH' })
    })
    expect(useAuthStore.getState().user?.email_notifications).toBe(false)
  })

  it('schaltet die KI-Meldungen getrennt davon', async () => {
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )

    oeffneGlocke()
    fireEvent.click(screen.getByRole('switch', { name: 'KI-Meldungen im Panel' }))

    await waitFor(() => {
      expect(api).toHaveBeenCalledWith('/auth/me/notifications?ai=false', { method: 'PATCH' })
    })
    expect(useAuthStore.getState().user?.ai_notifications).toBe(false)
    expect(useAuthStore.getState().user?.email_notifications).toBe(true)
  })
})
