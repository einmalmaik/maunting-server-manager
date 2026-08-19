import { useEffect } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { ProtectedRoute } from './ProtectedRoute'
import { PublicOnlyRoute } from './PublicOnlyRoute'
import { useAuthStore } from '@/stores/authStore'
import { usePermissionsStore } from '@/stores/permissionsStore'
import * as client from '@/api/client'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
  clearCsrfTokenMemory: vi.fn(),
}))

function resetStore() {
  useAuthStore.setState({ user: null, isAuthenticated: false, isLoading: true })
  usePermissionsStore.setState({ me: null, isLoading: false, error: null })
}

function TestApp({ initialPath = '/', children = <div data-testid="protected-content">Protected</div> }: {
  initialPath?: string
  /** Nur der Räumungs-Test braucht ein Kind, das sein Abhängen bemerkt. */
  children?: React.ReactNode
}) {
  return (
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/login" element={<div data-testid="login-page">Login</div>} />
        <Route path="/*" element={
          <ProtectedRoute>{children}</ProtectedRoute>
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

  it('hängt die geschützten Seiten ab, sobald die Sitzung geräumt wird', async () => {
    // Die Gegenprobe zum Test darüber — und die Stelle, an der `clearSession`
    // mehr tut, als Speicher zu leeren. `authStore` sagt es zu: `isAuthenticated:
    // false` ist zugleich der Griff, der die offenen Verbindungen schließt.
    // Die Wache hier hängt daran, hängt die geschützten Seiten ab, und deren
    // Aufräumen beendet die WebSockets und SSE-Ströme, die dort und nur dort
    // geöffnet wurden. Deshalb gibt es kein Register offener Verbindungen —
    // bliebe ein Kind stehen, hielte es seine Leitung mit den Serverdaten
    // darauf offen, während der Benutzer die Anmeldeseite sieht.
    const leitungGeschlossen = vi.fn()
    function OffeneLeitung() {
      useEffect(() => leitungGeschlossen, [])
      return <div data-testid="protected-content">Protected</div>
    }
    useAuthStore.setState({
      user: { id: 1, username: 'test', is_owner: true } as any,
      isAuthenticated: true,
      isLoading: false,
    })

    render(<TestApp><OffeneLeitung /></TestApp>)
    await screen.findByTestId('protected-content')
    expect(leitungGeschlossen).not.toHaveBeenCalled()

    act(() => useAuthStore.getState().clearSession())

    await waitFor(() => {
      expect(screen.getByTestId('login-page')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('protected-content')).not.toBeInTheDocument()
    expect(leitungGeschlossen).toHaveBeenCalledTimes(1)
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
