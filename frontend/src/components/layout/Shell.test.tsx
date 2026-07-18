import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { Shell } from './Shell'
import { useAuthStore } from '@/stores/authStore'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { PrivacyNoticeVisibilityContext } from '@/components/ui/PrivacyNoticeVisibility'

// Stabilize Shell.test.tsx timeout (VAL-REL-005):
// Shell renders VersionFooter → useVersion (fetches GitHub API) and
// usePublicLegalSettings (fetches /api/system/legal). In jsdom 29, fetch
// is available and makes real HTTP requests that hang the test.
// Mocking these modules makes the test deterministic without weakening
// the assertion (privacy footer link, no impressum).
vi.mock('@/services/versionService', () => ({
  getCachedVersion: () => 'v1.0.0',
  getVersion: () => Promise.resolve('v1.0.0'),
  DEFAULT_VERSION: 'v1.0.0',
}))

vi.mock('@/api/legal', () => ({
  getPublicLegalSettings: () =>
    Promise.resolve({ imprint_enabled: false, imprint_url: '' }),
}))

describe('Shell', () => {
  const renderShell = () => render(
    <MemoryRouter>
      <Routes>
        <Route path="/" element={<Shell />}><Route index element={<div>Dashboard</div>} /></Route>
      </Routes>
    </MemoryRouter>,
  )

  it('renders the privacy footer link without an impressum link', () => {
    renderShell()

    expect(screen.getByRole('link', { name: 'Datenschutz' })).toHaveAttribute('href', '/privacy')
    expect(screen.queryByText(/Impressum/i)).toBeNull()
    const disBadge = screen.getByRole('link', { name: /Powered by DIS/i })
    expect(disBadge.closest('footer')).not.toBeNull()
  })

  it('does not render Powered by DIS while the privacy notice is open', () => {
    render(
      <PrivacyNoticeVisibilityContext.Provider value>
        <MemoryRouter>
          <Routes>
            <Route path="/" element={<Shell />}><Route index element={<div>Dashboard</div>} /></Route>
          </Routes>
        </MemoryRouter>
      </PrivacyNoticeVisibilityContext.Provider>,
    )

    expect(screen.queryByRole('link', { name: /Powered by DIS/i })).toBeNull()
  })

  it('opens the mobile drawer, closes with Escape and returns focus', async () => {
    useAuthStore.setState({ user: { username: 'owner', email: 'owner@example.invalid', is_owner: true } as never })
    usePermissionsStore.setState({ me: { is_owner: true, role_id: null, role_name: null, global_keys: [], server_keys: {} }, isLoading: false, error: null })
    renderShell()
    const trigger = screen.getByRole('button', { name: 'Open navigation' })
    fireEvent.click(trigger)
    const navigation = screen.getByRole('dialog', { name: 'Main navigation' })
    expect(navigation).toHaveClass('h-[100dvh]', 'w-full')
    expect(screen.getByTestId('mobile-navigation-layer')).toHaveClass('h-[100dvh]', 'w-screen', 'overflow-hidden')
    expect(document.body.style.overflow).toBe('hidden')
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Main navigation' })).toBeNull())
    await waitFor(() => expect(trigger).toHaveFocus())
    expect(document.body.style.overflow).toBe('')
  })
})
