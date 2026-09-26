import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { Login } from './Login'
import * as client from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import { usePermissionsStore } from '@/stores/permissionsStore'
import '@/i18n'

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, api: vi.fn() }
})

function mockApi() {
  vi.mocked(client.api).mockImplementation(async (path: string) => {
    if (path === '/auth/login') {
      return { access_token: 'egal', requires_2fa: false, requires_verification: false, email: '' } as any
    }
    if (path === '/auth/me') {
      return { id: 1, username: 'admin', is_owner: true } as any
    }
    if (path === '/permissions/me') {
      return { is_owner: true, roles: [], permissions: [] } as any
    }
    if (path === '/auth/captcha-config') {
      return { enabled: false, provider: 'none', site_key: '' } as any
    }
    return [] as any
  })
}

/** Rendert die Anmeldeseite mit einem gemerkten Ziel im History-State. */
function renderLogin(from?: string) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: '/login', state: from === undefined ? null : { from } }]}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<div data-testid="dashboard">Dashboard</div>} />
        <Route path="/servers/:id" element={<div data-testid="server-detail">Server</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

async function anmelden(container: HTMLElement) {
  // Erst wenn die Sicherheitsabfrage als „aus" bekannt ist, gibt die Seite frei.
  await waitFor(() => expect(container.querySelector('button[type="submit"]')).not.toBeDisabled())
  const benutzer = container.querySelector('input[type="text"]') as HTMLInputElement
  const passwort = container.querySelector('input[type="password"]') as HTMLInputElement
  fireEvent.change(benutzer, { target: { value: 'admin' } })
  fireEvent.change(passwort, { target: { value: 'geheim' } })
  fireEvent.submit(container.querySelector('form') as HTMLFormElement)
}

describe('Login — Ziel nach der Anmeldung', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null, isAuthenticated: false, isLoading: true })
    usePermissionsStore.setState({ me: null, isLoading: false, error: null })
    vi.mocked(client.api).mockReset()
    mockApi()
  })

  it('führt nach der Anmeldung auf die zuvor angefragte Seite', async () => {
    const { container } = renderLogin('/servers/7')

    await anmelden(container)

    await waitFor(() => {
      expect(screen.getByTestId('server-detail')).toBeInTheDocument()
    })
  })

  it('ignoriert ein protokollrelatives Ziel und geht auf die Wurzel', async () => {
    const { container } = renderLogin('//boese.example')

    await anmelden(container)

    await waitFor(() => {
      expect(screen.getByTestId('dashboard')).toBeInTheDocument()
    })
  })

  it('geht ohne gemerktes Ziel auf die Wurzel', async () => {
    const { container } = renderLogin()

    await anmelden(container)

    await waitFor(() => {
      expect(screen.getByTestId('dashboard')).toBeInTheDocument()
    })
  })
})

describe('Login — Sicherheitsabfrage sperrt auch den Social Login', () => {
  let optionen: Record<string, (token?: string) => void> = {}

  beforeEach(() => {
    useAuthStore.setState({ user: null, isAuthenticated: false, isLoading: true })
    vi.mocked(client.api).mockReset()
    vi.mocked(client.api).mockImplementation(async (path: string) => {
      if (path === '/auth/captcha-config') {
        return { enabled: true, provider: 'turnstile', site_key: 'oeffentlich' } as any
      }
      if (path.startsWith('/oauth')) {
        return [{ slug: 'github', name: 'GitHub' }] as any
      }
      return [] as any
    })
    ;(window as unknown as { turnstile: unknown }).turnstile = {
      render: (_el: HTMLElement, opts: Record<string, (token?: string) => void>) => {
        optionen = opts
        return 'widget-1'
      },
      remove: () => {},
    }
  })

  it('gibt Anmelden und Social Login erst nach bestandener Abfrage frei', async () => {
    const { container } = renderLogin()
    const social = await screen.findByRole('button', { name: /GitHub/ })
    expect(social).toBeDisabled()
    expect(container.querySelector('a[href*="/oauth/github/start"]')).toBeNull()
    expect(container.querySelector('button[type="submit"]')).toBeDisabled()

    await waitFor(() => expect(optionen.callback).toBeTypeOf('function'))
    await act(async () => optionen.callback('token-abc'))

    await waitFor(() =>
      expect(container.querySelector('a[href*="/oauth/github/start"]')).not.toBeNull(),
    )
    expect(container.querySelector('button[type="submit"]')).not.toBeDisabled()

    // Abgelaufenes Token: wieder zu.
    await act(async () => optionen['expired-callback']())
    expect(container.querySelector('a[href*="/oauth/github/start"]')).toBeNull()
  })
})
