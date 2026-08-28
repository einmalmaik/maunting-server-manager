import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import i18n from '@/i18n'
import { useAuthStore } from '@/stores/authStore'
import { AccountTab } from './AccountTab'

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, api: vi.fn() }
})

const t = (key: string) => i18n.t(key)
const getCurrentPosition = vi.fn()

function setUser(locationSharingEnabled = false) {
  useAuthStore.setState({
    user: {
      id: 1,
      username: 'test-user',
      email: 'test-user@example.invalid',
      is_owner: false,
      is_active: true,
      email_verified: true,
      two_factor_enabled: false,
      email_notifications: false,
      ai_notifications: false,
      role_id: null,
      created_at: '2026-08-28T00:00:00Z',
      location_sharing_enabled: locationSharingEnabled,
    },
  })
}

describe('Standortfreigabe im Konto', () => {
  beforeEach(() => {
    vi.mocked(api).mockReset()
    getCurrentPosition.mockReset()
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: { getCurrentPosition },
    })
    setUser()
  })

  it('fordert den Browser-Standort erst nach einem ausdrücklichen Klick an und speichert nur die Einwilligung', async () => {
    getCurrentPosition.mockImplementation((success: () => void) => success())
    vi.mocked(api).mockResolvedValueOnce({ location_sharing_enabled: true })
    render(<AccountTab />)

    expect(getCurrentPosition).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: t('profile.locationSharingEnable') }))

    await waitFor(() => {
      expect(getCurrentPosition).toHaveBeenCalledOnce()
      expect(api).toHaveBeenCalledWith('/auth/me/location-sharing', {
        method: 'PATCH',
        body: JSON.stringify({ enabled: true }),
      })
    })
    expect(screen.getByText(t('profile.locationSharingEnabled'))).toBeInTheDocument()
  })

  it('zeigt bei abgelehnter Berechtigung eine verständliche Meldung und ändert die Einwilligung nicht', async () => {
    getCurrentPosition.mockImplementation((_: unknown, failure: (error: { code: number }) => void) => failure({ code: 1 }))
    render(<AccountTab />)

    fireEvent.click(screen.getByRole('button', { name: t('profile.locationSharingEnable') }))

    expect(await screen.findByRole('alert')).toHaveTextContent(t('profile.locationSharingPermissionError'))
    expect(api).not.toHaveBeenCalled()
    expect(useAuthStore.getState().user?.location_sharing_enabled).toBe(false)
  })

  it('deaktiviert die Einwilligung ohne eine weitere Browser-Abfrage', async () => {
    setUser(true)
    vi.mocked(api).mockResolvedValueOnce({ location_sharing_enabled: false })
    render(<AccountTab />)

    fireEvent.click(screen.getByRole('button', { name: t('profile.locationSharingDisable') }))

    await waitFor(() => {
      expect(api).toHaveBeenCalledWith('/auth/me/location-sharing', {
        method: 'PATCH',
        body: JSON.stringify({ enabled: false }),
      })
    })
    expect(getCurrentPosition).not.toHaveBeenCalled()
  })
})
