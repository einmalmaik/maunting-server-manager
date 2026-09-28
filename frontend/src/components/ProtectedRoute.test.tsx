import { useEffect } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { ProtectedRoute } from './ProtectedRoute'
import { PublicOnlyRoute } from './PublicOnlyRoute'
import { useAuthStore } from '@/stores/authStore'
import { usePermissionsStore } from '@/stores/permissionsStore'
import * as client from '@/api/client'
import i18n from '@/i18n'

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

describe('ProtectedRoute: Namenswahl', () => {
  beforeEach(() => {
    resetStore()
    vi.mocked(client.api).mockReset()
  })

  it('zeigt statt des Panels die Namenswahl und danach das Panel an derselben Stelle', async () => {
    // Konten aus dem Social Login trugen bis 09/2026 die E-Mail ohne
    // Sonderzeichen als Namen. Jetzt waehlen sie einmal selbst.
    useAuthStore.setState({
      user: { id: 1, username: 'user_0a1b2c3d', username_gewaehlt: false, is_owner: false } as any,
      isAuthenticated: true,
      isLoading: false,
    })
    vi.mocked(client.api).mockResolvedValue({
      id: 1, username: 'maunting', username_gewaehlt: true, is_owner: false,
    } as any)

    render(<TestApp initialPath="/servers" />)

    expect(await screen.findByText(i18n.t('benutzername.titel'))).toBeInTheDocument()
    expect(screen.queryByTestId('protected-content')).not.toBeInTheDocument()
    // Ein Platzhalter ist kein Vorschlag.
    const feld = screen.getByLabelText(i18n.t('benutzername.label')) as HTMLInputElement
    expect(feld.value).toBe('')

    fireEvent.change(feld, { target: { value: 'maunting' } })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('benutzername.weiter') }))

    await screen.findByTestId('protected-content')
    expect(client.api).toHaveBeenCalledWith('/auth/me/username', {
      method: 'PATCH',
      body: JSON.stringify({ username: 'maunting' }),
    })
  })

  it('laesst einen Namen mit @ gar nicht erst abschicken', async () => {
    useAuthStore.setState({
      user: { id: 1, username: 'Alice.GH', username_gewaehlt: false, is_owner: false } as any,
      isAuthenticated: true,
      isLoading: false,
    })

    render(<TestApp />)

    const feld = (await screen.findByLabelText(i18n.t('benutzername.label'))) as HTMLInputElement
    // Ein brauchbarer Anbietername steht als Vorschlag im Feld.
    expect(feld.value).toBe('Alice.GH')
    fireEvent.change(feld, { target: { value: 'max@web.de' } })

    expect(screen.getByText(i18n.t('benutzername.fehlerForm'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('benutzername.weiter') })).toBeDisabled()
    expect(client.api).not.toHaveBeenCalled()
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
