import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { Shell } from './Shell'

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
  it('renders the privacy footer link without an impressum link', () => {
    render(
      <MemoryRouter>
        <Routes>
          <Route path="/" element={<Shell />}>
            <Route index element={<div>Dashboard</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: 'Datenschutz' })).toHaveAttribute('href', '/privacy')
    expect(screen.queryByText(/Impressum/i)).toBeNull()
  })
})
