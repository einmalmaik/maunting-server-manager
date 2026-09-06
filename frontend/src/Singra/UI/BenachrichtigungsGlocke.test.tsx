import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BenachrichtigungsGlocke } from './BenachrichtigungsGlocke'
import { useAuthStore } from '@/stores/authStore'
import { useToastStore } from '@/stores/toastStore'
import { api } from '@/api/client'
import i18n from '@/i18n'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

function setUser(email = true, ai = true, device = true) {
  useAuthStore.setState({
    user: {
      id: 1,
      username: 'owner',
      email: 'owner@example.test',
      is_owner: true,
      is_active: true,
      email_verified: true,
      two_factor_enabled: false,
      email_notifications: email,
      ai_notifications: ai,
      device_notifications: device,
      avatar_url: '/api/auth/avatar/test.png',
      role_id: null,
      created_at: '2026-05-31T00:00:00Z',
    },
    isAuthenticated: true,
    isLoading: false,
  })
}

describe('BenachrichtigungsGlocke', () => {
  beforeEach(() => {
    i18n.changeLanguage('de')
    vi.mocked(api).mockReset().mockResolvedValue({})
    useToastStore.setState({ toasts: [] })
    setUser(true, true, true)
  })

  it('renders bell button and opens menu with bottom/right placement (Tauri style)', () => {
    render(<BenachrichtigungsGlocke placement="bottom" align="right" />)

    const btn = screen.getByRole('button', { name: /benachrichtigungen/i })
    expect(btn).toBeInTheDocument()

    fireEvent.click(btn)
    const menu = screen.getByRole('menu')
    expect(menu).toBeInTheDocument()
    expect(menu.className).toContain('top-full')
    expect(menu.className).toContain('right-0')
  })

  it('renders bell button and opens menu with top/sidebar placement', () => {
    render(<BenachrichtigungsGlocke placement="top" align="sidebar" />)

    const btn = screen.getByRole('button', { name: /benachrichtigungen/i })
    fireEvent.click(btn)
    const menu = screen.getByRole('menu')
    expect(menu).toBeInTheDocument()
    expect(menu.className).toContain('bottom-full')
  })

  it('toggles email and device notifications correctly', async () => {
    render(<BenachrichtigungsGlocke placement="bottom" align="right" />)

    fireEvent.click(screen.getByRole('button', { name: /benachrichtigungen/i }))
    const emailSwitch = screen.getByRole('switch', { name: 'E-Mail-Benachrichtigungen' })
    fireEvent.click(emailSwitch)

    await waitFor(() => {
      expect(api).toHaveBeenCalledWith('/auth/me/notifications?enabled=false', { method: 'PATCH' })
    })

    const deviceSwitch = screen.getByRole('switch', { name: 'Geräte-Benachrichtigungen' })
    fireEvent.click(deviceSwitch)

    await waitFor(() => {
      expect(api).toHaveBeenCalledWith('/auth/me/notifications?device=false', { method: 'PATCH' })
    })
  })

  it('dynamically adapts to top-full and right-0 when header collision is detected', () => {
    // Mock viewport and top-right positioning
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1024 })
    Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: 768 })

    const { container } = render(<BenachrichtigungsGlocke placement="top" align="sidebar" />)
    const bellWrapper = container.firstElementChild as HTMLElement
    vi.spyOn(bellWrapper, 'getBoundingClientRect').mockReturnValue({
      width: 36,
      height: 36,
      top: 10,
      bottom: 46,
      left: 980,
      right: 1016,
      x: 980,
      y: 10,
      toJSON: () => {},
    })

    const btn = screen.getByRole('button', { name: /benachrichtigungen/i })
    fireEvent.click(btn)

    const menu = screen.getByRole('menu')
    expect(menu).toBeInTheDocument()
    // spaceAbove = 10 (< 320), so adapts to top-full (placement="bottom")
    expect(menu.className).toContain('top-full')
    // spaceRight = 1024 - 1016 = 8 (< 320), so adapts to right-0 (align="right")
    expect(menu.className).toContain('right-0')
  })
})
