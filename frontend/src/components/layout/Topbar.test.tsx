import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { Topbar } from './Topbar'
import { useAuthStore } from '@/stores/authStore'
import { useConfirmStore } from '@/stores/confirmStore'
import { useToastStore } from '@/stores/toastStore'
import { api } from '@/api/client'
import i18n from '@/i18n'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

const originalConfirmRequest = useConfirmStore.getState().request
const originalConfirmResolve = useConfirmStore.getState().resolve

function setUser(emailNotifications: boolean) {
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
      role_id: null,
      created_at: '2026-05-31T00:00:00Z',
    },
    isAuthenticated: true,
    isLoading: false,
  })
}

describe('Topbar', () => {
  beforeEach(() => {
    i18n.changeLanguage('de')
    vi.mocked(api).mockResolvedValue({})
    useToastStore.setState({ toasts: [] })
    setUser(true)
  })

  afterEach(() => {
    useConfirmStore.setState({
      pending: null,
      request: originalConfirmRequest,
      resolve: originalConfirmResolve,
    })
  })

  it('keeps the email notification bell visible and toggles the backend setting', async () => {
    const request = vi.fn().mockResolvedValue(true)
    useConfirmStore.setState({ pending: null, request, resolve: vi.fn() })

    render(
      <MemoryRouter>
        <Topbar />
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'E-Mail-Benachrichtigungen: aktiv' }))

    await waitFor(() => {
      expect(api).toHaveBeenCalledWith('/auth/me/notifications?enabled=false', { method: 'PATCH' })
    })
    expect(request).toHaveBeenCalled()
    expect(useAuthStore.getState().user?.email_notifications).toBe(false)
  })
})
