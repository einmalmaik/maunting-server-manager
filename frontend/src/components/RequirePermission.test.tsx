import { describe, expect, it, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { RequirePermission } from './RequirePermission'
import { usePermissionsStore } from '@/stores/permissionsStore'
import type { MePermissions } from '@/types/permissions'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

const owner: MePermissions = {
  is_owner: true,
  role_id: null,
  role_name: null,
  global_keys: [],
  server_keys: {},
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname}</div>
}

function GuardApp({ routeKey = 'users' }: { routeKey?: string }) {
  return (
    <MemoryRouter initialEntries={['/users']}>
      <Routes>
        <Route path="/" element={<div>Dashboard</div>} />
        <Route
          path="/users"
          element={
            <>
              <LocationProbe />
              <RequirePermission routeKey={routeKey}>
                <h1>Benutzer</h1>
              </RequirePermission>
            </>
          }
        />
      </Routes>
    </MemoryRouter>
  )
}

describe('RequirePermission', () => {
  beforeEach(() => {
    usePermissionsStore.setState({ me: null, isLoading: false, error: null })
  })

  it('does not redirect to dashboard while permissions are loading', async () => {
    usePermissionsStore.setState({ me: null, isLoading: true, error: null })

    render(<GuardApp />)

    expect(screen.getByTestId('location')).toHaveTextContent('/users')
    expect(screen.queryByText('Dashboard')).toBeNull()

    usePermissionsStore.setState({ me: owner, isLoading: false, error: null })

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Benutzer' })).toBeInTheDocument()
    })
    expect(screen.getByTestId('location')).toHaveTextContent('/users')
  })

  it('shows forbidden on denied access without changing the route', () => {
    usePermissionsStore.setState({
      me: { ...owner, is_owner: false, global_keys: [] },
      isLoading: false,
      error: null,
    })

    render(<GuardApp routeKey="roles" />)

    expect(screen.getByText('Kein Zugriff')).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent('/users')
    expect(screen.queryByText('Dashboard')).toBeNull()
  })

  it('shows an explicit error when permission loading failed', () => {
    usePermissionsStore.setState({ me: null, isLoading: false, error: 'PERMISSIONS_LOAD_FAILED' })

    render(<GuardApp />)

    expect(screen.getByRole('alert')).toHaveTextContent('Berechtigungen konnten nicht geladen werden')
    expect(screen.getByTestId('location')).toHaveTextContent('/users')
  })
})
