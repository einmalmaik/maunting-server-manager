import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { ProtectedRoute } from './ProtectedRoute'
import { PublicOnlyRoute } from './PublicOnlyRoute'
import { useAuthStore } from '@/stores/authStore'
import { usePermissionsStore } from '@/stores/permissionsStore'
import * as client from '@/api/client'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

function resetStore() {
  useAuthStore.setState({ user: null, isAuthenticated: false, isLoading: true })
  usePermissionsStore.setState({ me: null, isLoading: false, error: null })
}

function TestApp({ initialPath = '/' }: { initialPath?: string }) {
  return (
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/login" element={<div data-testid="login-page">Login</div>} />
        <Route path="/*" element={
          <ProtectedRoute>
            <div data-testid="protected-content">Protected</div>
          </ProtectedRoute>
        } />
      </Routes>
    </MemoryRouter>
  )
}

function PublicApp({ initialPath = '/login' }: { initialPath?: string }) {
  return (
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/login" element={
          <PublicOnlyRoute>
            <div data-testid="public-content">Login Page</div>
          </PublicOnlyRoute>
        } />
        <Route path="/" element={<div data-testid="dashboard">Dashboard</div>} />
      </Routes>
    </MemoryRouter>
  )
}

describe('ProtectedRoute', () => {
  beforeEach(() => {
    resetStore()
    vi.mocked(client.api).mockReset()
  })

  it('should show loading spinner while checking auth', async () => {
    vi.mocked(client.api).mockImplementation(() => new Promise(() => {}))

    render(<TestApp />)
    expect(document.querySelector('.animate-spin')).toBeInTheDocument()
  })

  it('should redirect to /login when not authenticated', async () => {
    vi.mocked(client.api).mockRejectedValue(new Error('Unauthorized'))

    render(<TestApp />)

    await waitFor(() => {
      expect(screen.getByTestId('login-page')).toBeInTheDocument()
    })
  })

  it('should render protected content when authenticated', async () => {
    // Directly set authenticated state (checkAuth flow is tested in authStore.test.ts)
    useAuthStore.setState({
      user: { id: 1, username: 'test', is_owner: true } as any,
      isAuthenticated: true,
      isLoading: false,
    })

    render(<TestApp />)

    await waitFor(() => {
      expect(screen.getByTestId('protected-content')).toBeInTheDocument()
    })
  })
})

describe('PublicOnlyRoute', () => {
  beforeEach(() => {
    resetStore()
    vi.mocked(client.api).mockReset()
  })

  it('should redirect to / when already authenticated', async () => {
    useAuthStore.setState({
      user: { id: 1, username: 'test', is_owner: true } as any,
      isAuthenticated: true,
      isLoading: false,
    })

    render(<PublicApp initialPath="/login" />)

    await waitFor(() => {
      expect(screen.getByTestId('dashboard')).toBeInTheDocument()
    })
  })

  it('should render public content when not authenticated', async () => {
    vi.mocked(client.api).mockRejectedValue(new Error('Unauthorized'))

    render(<PublicApp initialPath="/login" />)

    await waitFor(() => {
      expect(screen.getByTestId('public-content')).toBeInTheDocument()
    })
  })
})
