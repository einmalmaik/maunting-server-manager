import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { oauthApi } from '@/api/oauth'
import i18n from '@/i18n'
import { useAuthStore } from '@/stores/authStore'
import { LinkedAccountsTab } from './LinkedAccountsTab'

vi.mock('@/api/oauth', () => ({
  oauthApi: { startLink: vi.fn(), unlinkProvider: vi.fn() },
}))
vi.mock('./useOAuthLinks', () => ({
  useOAuthLinks: () => ({
    oauthLinks: [],
    oauthAvailable: [{ slug: 'google', name: 'Google', preset: 'google', position: 0 }],
    loading: false,
    reload: vi.fn(),
  }),
}))
vi.mock('./ConnectedMailboxesSection', () => ({ ConnectedMailboxesSection: () => null }))
vi.mock('./ConnectedCalendarsSection', () => ({ ConnectedCalendarsSection: () => null }))

const t = (key: string, opts?: Record<string, unknown>) => i18n.t(key, opts)
const assign = vi.fn()
const echteLocation = window.location

function konto(felder: Record<string, unknown>) {
  useAuthStore.setState({
    user: { id: 1, username: 'anna', email: 'anna@example.invalid', two_factor_enabled: false, ...felder } as any,
  })
}

function oeffnen() {
  render(
    <MemoryRouter>
      <LinkedAccountsTab />
    </MemoryRouter>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'Google' }))
}

describe('LinkedAccountsTab: Verknüpfen nur mit Nachweis', () => {
  beforeEach(() => {
    vi.mocked(oauthApi.startLink).mockReset()
    assign.mockReset()
    Object.defineProperty(window, 'location', { value: { ...echteLocation, assign }, configurable: true })
  })
  afterEach(() => {
    Object.defineProperty(window, 'location', { value: echteLocation, configurable: true })
  })

  it('fragt vor dem Verknüpfen nach dem Passwort und geht dann zum Anbieter', async () => {
    konto({ has_password: true })
    vi.mocked(oauthApi.startLink).mockResolvedValueOnce({ url: 'https://accounts.example.invalid/auth' })
    oeffnen()

    expect(screen.getByText(t('profile.linkedAccounts.linkTitle', { provider: 'Google' }))).toBeInTheDocument()
    expect(oauthApi.startLink).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText(t('profile.currentPassword')), { target: { value: 'geheim-123' } })
    fireEvent.click(screen.getByRole('button', { name: t('profile.linkedAccounts.linkContinue') }))

    await waitFor(() => {
      expect(oauthApi.startLink).toHaveBeenCalledWith('google', { password: 'geheim-123', otp_code: '', passkey: null })
      expect(assign).toHaveBeenCalledWith('https://accounts.example.invalid/auth')
    })
  })

  it('Konto ohne Passwort und ohne 2FA legt zuerst ein Passwort fest', () => {
    konto({ has_password: false })
    oeffnen()

    expect(screen.getByText(t('profile.linkedAccounts.linkNeedsPassword'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('profile.linkedAccounts.linkContinue') })).not.toBeInTheDocument()
    expect(oauthApi.startLink).not.toHaveBeenCalled()
  })
})
