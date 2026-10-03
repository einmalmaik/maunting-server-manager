import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { Login } from './Login'
import * as client from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import { usePermissionsStore } from '@/stores/permissionsStore'
import i18n from '@/i18n'

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, api: vi.fn() }
})

const PASSKEY_OPTIONEN = { challenge: 'abc', rpId: 'localhost', allowCredentials: [] }
const NACHWEIS = { id: 'k', rawId: 'k', type: 'public-key', response: { signatur: 'echt' } }
vi.mock('@/services/passkeyService', () => ({ passkeyBestaetigen: vi.fn(async () => NACHWEIS) }))

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

  it('nennt die Felder mit ihrer Überschrift, nicht mit dem Platzhalter', async () => {
    // Bis 03.10.2026 hießen sie für Screenreader „admin“ und gar nichts.
    renderLogin()
    expect(await screen.findByRole('textbox', { name: 'Benutzername' })).toHaveAttribute('placeholder', 'admin')
    expect(screen.getByLabelText('Passwort')).toHaveAttribute('type', 'password')
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

describe('Login — zweiter Faktor per Passkey', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null, isAuthenticated: false, isLoading: true })
    usePermissionsStore.setState({ me: null, isLoading: false, error: null })
    vi.mocked(client.api).mockReset()
    vi.mocked(client.api).mockImplementation(async (path: string, opt?: RequestInit) => {
      if (path === '/auth/login') {
        const body = JSON.parse(String(opt?.body ?? '{}'))
        if (body.passkey) {
          return { access_token: '', requires_2fa: false, requires_verification: false, email: '' } as any
        }
        return {
          access_token: '', requires_2fa: true, requires_verification: false, email: '',
          two_factor_methods: ['passkey'], passkey_options: PASSKEY_OPTIONEN,
        } as any
      }
      if (path === '/auth/me') return { id: 1, username: 'admin', is_owner: true } as any
      if (path === '/permissions/me') return { is_owner: true, roles: [], permissions: [] } as any
      if (path === '/auth/captcha-config') return { enabled: false, provider: 'none', site_key: '' } as any
      return [] as any
    })
  })

  it('zeigt nur den Passkey und schickt die unterschriebene Antwort', async () => {
    const { passkeyBestaetigen } = await import('@/services/passkeyService')
    const { container } = renderLogin()
    await anmelden(container)

    const knopf = await screen.findByRole('button', { name: /Passkey/ })
    expect(screen.queryByPlaceholderText('000000')).not.toBeInTheDocument()

    fireEvent.click(knopf)
    await waitFor(() => expect(screen.getByTestId('dashboard')).toBeInTheDocument())
    expect(passkeyBestaetigen).toHaveBeenCalledWith(PASSKEY_OPTIONEN)
    const anmeldungen = vi.mocked(client.api).mock.calls.filter(([pfad]) => pfad === '/auth/login')
    const letzte = JSON.parse(String((anmeldungen.at(-1)?.[1] as RequestInit).body))
    expect(letzte.passkey).toEqual(NACHWEIS)
    expect(letzte).not.toHaveProperty('passkey_verified')
  })
})

describe('Login — mehrere zweite Faktoren', () => {
  it('beginnt mit dem zuletzt genutzten und wechselt mit einem Klick', async () => {
    useAuthStore.setState({ user: null, isAuthenticated: false, isLoading: true })
    usePermissionsStore.setState({ me: null, isLoading: false, error: null })
    vi.mocked(client.api).mockReset()
    vi.mocked(client.api).mockImplementation(async (path: string, opt?: RequestInit) => {
      if (path === '/auth/login') {
        const body = JSON.parse(String(opt?.body ?? '{}'))
        if (body.passkey) {
          return { access_token: '', requires_2fa: false, requires_verification: false, email: '' } as any
        }
        // Zuletzt per App angemeldet: die App steht vorn.
        return {
          access_token: '', requires_2fa: true, requires_verification: false, email: '',
          two_factor_methods: ['totp', 'passkey'], passkey_options: PASSKEY_OPTIONEN,
        } as any
      }
      if (path === '/auth/me') return { id: 1, username: 'admin', is_owner: true } as any
      if (path === '/permissions/me') return { is_owner: true, roles: [], permissions: [] } as any
      if (path === '/auth/captcha-config') return { enabled: false, provider: 'none', site_key: '' } as any
      return [] as any
    })
    const { passkeyBestaetigen } = await import('@/services/passkeyService')
    const { container } = renderLogin()
    await anmelden(container)

    await screen.findByPlaceholderText('000000')
    expect(screen.getByRole('button', { name: i18n.t('auth.useBackupCode') })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: i18n.t('auth.zweitfaktor.wegPasskey') }))

    fireEvent.click(await screen.findByRole('button', { name: i18n.t('auth.loginWithPasskey') }))
    await waitFor(() => expect(screen.getByTestId('dashboard')).toBeInTheDocument())
    expect(passkeyBestaetigen).toHaveBeenCalledWith(PASSKEY_OPTIONEN)
  })
})

describe('Login — verbrauchter Zwischenschein', () => {
  it('beginnt nach drei Fehlversuchen von vorn statt am alten Captcha zu scheitern', async () => {
    useAuthStore.setState({ user: null, isAuthenticated: false, isLoading: true })
    vi.mocked(client.api).mockReset()
    vi.mocked(client.api).mockImplementation(async (path: string, opt?: RequestInit) => {
      if (path === '/auth/login') {
        const body = JSON.parse(String(opt?.body ?? '{}'))
        if (body.otp_code) {
          throw new client.SanitizedApiError('Zu viele Fehlversuche. Bitte erneut anmelden.', { status: 403 })
        }
        return {
          access_token: '', requires_2fa: true, requires_verification: false, email: '',
          two_factor_methods: ['totp'], login_challenge: 'schein-1',
        } as any
      }
      if (path === '/auth/captcha-config') return { enabled: false, provider: 'none', site_key: '' } as any
      return [] as any
    })
    const { container } = renderLogin()
    await anmelden(container)

    const code = await screen.findByPlaceholderText('000000')
    fireEvent.change(code, { target: { value: '123456' } })
    fireEvent.submit(container.querySelector('form') as HTMLFormElement)

    await screen.findByText('Zu viele Fehlversuche. Bitte erneut anmelden.')
    expect(screen.queryByPlaceholderText('000000')).not.toBeInTheDocument()
    expect(container.querySelector('input[type="password"]')).not.toBeDisabled()
  })
})

describe('Login — zweiter Faktor nach Social Login', () => {
  const OAUTH_ZIEL = '/login?step=oauth_2fa&challenge=c1&slug=github'

  function renderOAuth2FA() {
    return render(
      <MemoryRouter initialEntries={[OAUTH_ZIEL]}>
        <Routes>
          <Route path="/login" element={<Login />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  function antwort(status: number, daten: unknown = {}) {
    return { ok: status >= 200 && status < 300, status, type: 'basic', json: async () => daten }
  }

  beforeEach(() => {
    vi.mocked(client.api).mockReset()
    vi.mocked(client.api).mockImplementation(async () => [] as any)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('zeigt eine gescheiterte Faktorabfrage an, statt TOTP anzunehmen', async () => {
    // Bis 09/2026 galt nach einem 429 still TOTP: Passkey-Konten sahen nur ein Codefeld.
    const abruf = vi.fn()
      .mockResolvedValueOnce(antwort(429))
      .mockResolvedValueOnce(antwort(200, { methoden: ['passkey'], passkey_options: PASSKEY_OPTIONEN }))
    vi.stubGlobal('fetch', abruf)
    renderOAuth2FA()

    await screen.findByText(i18n.t('auth.oauth2faMethodRateLimited'))
    expect(screen.queryByPlaceholderText('000000')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: i18n.t('common.retry') }))
    await screen.findByRole('button', { name: i18n.t('auth.loginWithPasskey') })
    expect(screen.getByRole('button', { name: i18n.t('auth.useBackupCode') })).toBeInTheDocument()
    expect(abruf).toHaveBeenCalledTimes(2)
  })

  it('bietet einem Konto mit Passkey und App beide Wege an', async () => {
    const abruf = vi.fn(async (pfad: string) =>
      pfad.endsWith('/2fa/methode')
        ? antwort(200, { methoden: ['passkey', 'totp'], passkey_options: PASSKEY_OPTIONEN })
        : antwort(401, { detail: 'falsch' }),
    )
    vi.stubGlobal('fetch', abruf)
    const { container } = renderOAuth2FA()

    await screen.findByRole('button', { name: i18n.t('auth.loginWithPasskey') })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('auth.zweitfaktor.wegTotp') }))
    fireEvent.change(screen.getByPlaceholderText('000000'), { target: { value: '123456' } })
    fireEvent.submit(container.querySelector('form') as HTMLFormElement)

    await waitFor(() => expect(abruf).toHaveBeenCalledTimes(2))
    const [, optionen] = abruf.mock.calls[1] as unknown as [string, RequestInit]
    expect(JSON.parse(String(optionen.body))).toEqual({ challenge: 'c1', otp_code: '123456' })
  })

  it('bietet auch TOTP-Konten den Backup-Code an', async () => {
    const abruf = vi.fn(async (pfad: string) =>
      pfad.endsWith('/2fa/methode') ? antwort(200, { methoden: ['totp'] }) : antwort(401, { detail: 'falsch' }),
    )
    vi.stubGlobal('fetch', abruf)
    const { container } = renderOAuth2FA()

    await screen.findByPlaceholderText('000000')
    fireEvent.click(screen.getByRole('button', { name: i18n.t('auth.useBackupCode') }))
    const feld = screen.getByPlaceholderText('XXXX-XXXX')
    expect(feld).toHaveAttribute('maxLength', '12')
    fireEvent.change(feld, { target: { value: 'ABCD-EFGH' } })
    fireEvent.submit(container.querySelector('form') as HTMLFormElement)

    await waitFor(() => expect(abruf).toHaveBeenCalledTimes(2))
    const [pfad, optionen] = abruf.mock.calls[1] as unknown as [string, RequestInit]
    expect(pfad).toContain('/oauth/github/2fa')
    expect(JSON.parse(String(optionen.body))).toEqual({ challenge: 'c1', otp_code: 'ABCD-EFGH' })
  })
})
