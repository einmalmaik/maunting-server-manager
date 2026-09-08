/**
 * Der Boot-Ablauf des Hauptfensters: Konfiguration → Assistent oder Sitzung.
 *
 * Die KI-Seite selbst und die Glocke sind gemockt — sie haben eigene Tests
 * im Panel, und hier zaehlt nur die Weiche: wer landet wo, und was passiert,
 * wenn die stille Anmeldung scheitert.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { useAuthStore } from '@/stores/authStore'
import type { User } from '@/types'

const invokeMock = vi.fn()
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}))
type Listener = (event: { payload?: unknown }) => void
const eventListeners = new Map<string, Set<Listener>>()

function triggerTauriEvent(event: string, payload?: unknown) {
  const listeners = eventListeners.get(event)
  listeners?.forEach((cb) => cb({ payload }))
}

vi.mock('@tauri-apps/api/event', () => ({
  emit: vi.fn((event: string, payload?: unknown) => {
    triggerTauriEvent(event, payload)
    return Promise.resolve()
  }),
  listen: vi.fn((event: string, cb: Listener) => {
    if (!eventListeners.has(event)) {
      eventListeners.set(event, new Set())
    }
    eventListeners.get(event)!.add(cb)
    return Promise.resolve(() => {
      eventListeners.get(event)?.delete(cb)
    })
  }),
}))
vi.mock('@tauri-apps/plugin-autostart', () => ({
  enable: vi.fn(),
  disable: vi.fn(),
  isEnabled: vi.fn(() => Promise.resolve(false)),
}))
vi.mock('@tauri-apps/plugin-dialog', () => ({
  open: vi.fn(),
}))
// Die schweren Panel-Stuecke: hier zaehlt nur, DASS sie montiert werden.
vi.mock('@/pages/Ai', () => ({
  Ai: () => <div data-testid="ki-seite" />,
}))
vi.mock('@/pages/Messenger', () => ({
  Messenger: () => <div data-testid="messenger-seite" />,
}))
vi.mock('./vault/VaultView', () => ({
  VaultView: () => <div data-testid="tresor-seite" />,
}))
vi.mock('@/pages/Calendar', () => ({
  Calendar: () => <div data-testid="kalender-seite" />,
}))
vi.mock('@/pages/Notes', () => ({
  Notes: () => <div data-testid="notizen-seite" />,
}))
vi.mock('./Einstellungen', () => ({
  Einstellungen: () => <div data-testid="einstellungen-seite" />,
}))
vi.mock('@/components/ai/AiMemoryManager', () => ({
  AiMemoryManager: () => <div data-testid="gedaechtnis-seite" />,
}))
vi.mock('@/components/ai/AiRunNotice', () => ({
  AiRunNotice: () => null,
}))
vi.mock('@/hooks/useHasPermission', () => ({
  useHasPermission: () => true,
}))
// Der Splash wuerde jeden Test 10 Sekunden warten lassen.
vi.mock('./Splash', () => ({
  Splash: () => null,
}))
// Die Auftragsschleife pollt endlos — gemockt bleibt sie stumm. Womit sie
// aufgerufen wird, ist aber genau die Frage in einem der Tests unten.
const schleifeAktiv: boolean[] = []
vi.mock('./useAuftragsschleife', () => ({
  useAuftragsschleife: (aktiv: boolean) => {
    schleifeAktiv.push(aktiv)
    return null
  },
}))

import { DesktopApp } from './DesktopApp'
import { setzeAccessToken } from './transport'

const BENUTZER: User = {
  id: 1,
  username: 'tester',
  email: 't@example.com',
  is_owner: false,
  agent_name: 'Jarvis',
} as unknown as User

const LETZTE_ROUTE_KEY = 'mss:letzte_route'

function konfigMock(konfig: Record<string, unknown>) {
  invokeMock.mockImplementation((befehl: string) => {
    if (befehl === 'konfig_laden') return Promise.resolve(konfig)
    if (befehl === 'refresh_token_laden') return Promise.resolve('tresor-token')
    return Promise.resolve(null)
  })
}

describe('DesktopApp', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    invokeMock.mockReset()
    eventListeners.clear()
    schleifeAktiv.length = 0
    setzeAccessToken(null)
    localStorage.clear()
    useAuthStore.setState({ user: null, isAuthenticated: false, isLoading: false })
  })

  it('ohne Einrichtung beginnt der Assistent beim Adress-Schritt', async () => {
    konfigMock({ backend_url: null, sandbox_pfad: null, eingerichtet: false })
    render(<DesktopApp />)
    await waitFor(() => {
      expect(screen.getByText(i18n.t('mss.wizard.adresseLabel'))).toBeInTheDocument()
    })
  })

  it('mit gueltigem Tresor-Token landet man in der Hauptansicht', async () => {
    konfigMock({
      backend_url: 'https://api.example.com',
      // Ein eingerichteter Sandbox-Ordner gehoert zu diesem Fall dazu: fehlt
      // er, bietet die App zuerst den Sandbox-Schritt an (siehe unten).
      sandbox_pfad: 'C:\\Users\\tester\\MSS-Sandbox',
      eingerichtet: true,
    })
    vi.stubGlobal(
      'fetch',
      vi.fn((eingabe: RequestInfo | URL) => {
        const url = String(eingabe)
        if (url.includes('/auth/refresh')) {
          return Promise.resolve(
            new Response(JSON.stringify({ access_token: 'a', refresh_token: 'r' }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        if (url.includes('/auth/me')) {
          return Promise.resolve(
            new Response(JSON.stringify(BENUTZER), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        // Rechte-Refresh und alles Weitere: leer, aber gueltig.
        return Promise.resolve(
          new Response(JSON.stringify({ global_permissions: [], server_permissions: {} }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }),
    )

    render(<DesktopApp />)
    await waitFor(() => {
      expect(screen.getByTestId('ki-seite')).toBeInTheDocument()
    })
    // Der Agent-Name steht in der Kopfleiste — dieselbe Quelle wie im Panel.
    expect(screen.getByText('Jarvis')).toBeInTheDocument()
  })

  it('ohne Sandbox-Ordner bietet die App genau diesen Schritt noch einmal an', async () => {
    // Der Bestandsnutzer, dem `konfig.rs` beim Laden einen inzwischen
    // unzulaessigen Pfad vergessen hat. Das Feld dafuer gibt es nur im
    // Assistenten — kaeme der nicht wieder, verloere die KI ihren
    // Dateizugriff fuer immer, und ihre Absage („der Benutzer legt ihn fest")
    // verwiese ins Leere.
    konfigMock({ backend_url: 'https://api.example.com', sandbox_pfad: null, eingerichtet: true })
    vi.stubGlobal(
      'fetch',
      vi.fn((eingabe: RequestInfo | URL) => {
        const url = String(eingabe)
        if (url.includes('/auth/refresh')) {
          return Promise.resolve(
            new Response(JSON.stringify({ access_token: 'a', refresh_token: 'r' }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        if (url.includes('/auth/me')) {
          return Promise.resolve(
            new Response(JSON.stringify(BENUTZER), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        return Promise.resolve(
          new Response(JSON.stringify({ global_permissions: [], server_permissions: {} }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }),
    )

    render(<DesktopApp />)
    await waitFor(() => {
      expect(screen.getByText(i18n.t('mss.wizard.sandboxLabel'))).toBeInTheDocument()
    })
    // Nur dieser eine Schritt: die Kopplung ist laengst erledigt, ein zweiter
    // Code fuer einen Ordner waere absurd. Und wer nicht will, kommt vorbei.
    expect(screen.queryByText(i18n.t('mss.wizard.codeLabel'))).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: i18n.t('mss.wizard.spaeterFestlegen') }),
    ).toBeInTheDocument()

    // Und waehrenddessen arbeitet der Rechner weiter. Das Wake-Word startet
    // die Sprachsitzung unabhaengig von diesem Schritt aus Rust; liefe die
    // Auftragsschleife hier nicht, koennte die KI reden, waehrend jeder
    // Werkzeugaufruf in die 60-Sekunden-Grenze laeuft und „nicht abgewartet"
    // meldet, obwohl ihn niemand abgeholt hat.
    expect(schleifeAktiv[schleifeAktiv.length - 1]).toBe(true)
  })

  it('vor der Anmeldung fragt der Rechner nicht nach Auftraegen', async () => {
    // Die Gegenprobe: ohne Kopplung gibt es kein Token, und jede Frage waere
    // ein 401 im Sekundentakt.
    konfigMock({ backend_url: null, sandbox_pfad: null, eingerichtet: false })
    render(<DesktopApp />)
    await waitFor(() => {
      expect(screen.getByText(i18n.t('mss.wizard.adresseLabel'))).toBeInTheDocument()
    })
    expect(schleifeAktiv[schleifeAktiv.length - 1]).toBe(false)
  })

  it('eine abgelehnte Rotation fuehrt zur Kopplung, nicht zu einer Passwortmaske', async () => {
    konfigMock({ backend_url: 'https://api.example.com', sandbox_pfad: null, eingerichtet: true })
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response('{}', { status: 401 }))),
    )

    render(<DesktopApp />)
    await waitFor(() => {
      expect(screen.getByText(i18n.t('mss.wizard.codeLabel'))).toBeInTheDocument()
    })
    // Die Passwortstrecke ist geloescht, nicht versteckt.
    expect(screen.queryByLabelText(/passwor/i)).not.toBeInTheDocument()
  })

  it('zeigt den Computer-Use Deaktiviert-Hinweis im Chat, wenn computer_use_aktiv false ist', async () => {
    konfigMock({
      backend_url: 'https://api.example.com',
      sandbox_pfad: 'C:\\Users\\tester\\MSS-Sandbox',
      eingerichtet: true,
      computer_use_aktiv: false,
    })
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/auth/refresh')) {
          return Promise.resolve(
            new Response(JSON.stringify({ access_token: 'a', refresh_token: 'r' }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        if (url.includes('/auth/me')) {
          return Promise.resolve(
            new Response(JSON.stringify(BENUTZER), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        return Promise.resolve(
          new Response(JSON.stringify({ global_permissions: ['ai.chat.use'], server_permissions: {} }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }),
    )

    render(<DesktopApp />)
    await waitFor(() => {
      expect(screen.getByText(i18n.t('mss.einstellungen.banner.computerUseHinweis'))).toBeInTheDocument()
      expect(screen.getByRole('button', { name: i18n.t('mss.einstellungen.banner.computerUseLink') })).toBeInTheDocument()
    })
  })

  it('beim Start ohne Internet/Server erreichbar oeffnet die App direkt den Offline-Modus', async () => {
    konfigMock({
      backend_url: 'https://api.example.com',
      sandbox_pfad: 'C:\\Users\\tester\\MSS-Sandbox',
      eingerichtet: true,
    })
    // Server antwortet gar nicht / Offline
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch (Offline / Flugmodus)'))),
    )

    render(<DesktopApp />)
    // Landet direkt in der Hauptansicht (bereit), nicht im Wizard, und öffnet den Passwort-Manager
    await waitFor(() => {
      expect(screen.getByText(i18n.t('mss.app.tresor'))).toBeInTheDocument()
      expect(screen.queryByTestId('ki-seite')).not.toBeInTheDocument()
      expect(screen.queryByText(i18n.t('mss.wizard.codeLabel'))).not.toBeInTheDocument()
    })
  })

  it('erfolgreicher Refresh mit verzoegertem/fehlgeschlagenem checkAuth faellt nicht in Kopplung', async () => {
    konfigMock({
      backend_url: 'https://api.example.com',
      sandbox_pfad: 'C:\\Users\\tester\\MSS-Sandbox',
      eingerichtet: true,
    })
    // Refresh gelingt (Token im Tresor gueltig), aber /auth/me scheitert (z.B. Timeout/Netzwerkabbruch)
    vi.stubGlobal(
      'fetch',
      vi.fn((eingabe: RequestInfo | URL) => {
        const url = String(eingabe)
        if (url.includes('/auth/refresh')) {
          return Promise.resolve(
            new Response(JSON.stringify({ access_token: 'valid-a', refresh_token: 'valid-r' }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        return Promise.reject(new TypeError('Network dropped after token refresh'))
      }),
    )

    render(<DesktopApp />)
    // Bleibt in bereit (Offline-Modus), keine Kopplungs-Maske, landet im Tresor
    await waitFor(() => {
      expect(screen.getByText(i18n.t('mss.app.tresor'))).toBeInTheDocument()
      expect(screen.queryByTestId('ki-seite')).not.toBeInTheDocument()
      expect(screen.queryByText(i18n.t('mss.wizard.codeLabel'))).not.toBeInTheDocument()
    })
  })

  it('blockiert den Kaltstart nicht mit Zwangs-Updates oder automatischem Neustart', async () => {
    invokeMock.mockImplementation((befehl: string) => {
      if (befehl === 'konfig_laden') {
        return Promise.resolve({
          backend_url: 'https://api.example.com',
          sandbox_pfad: 'C:\\Users\\tester\\MSS-Sandbox',
          eingerichtet: true,
        })
      }
      if (befehl === 'refresh_token_laden') return Promise.resolve('tresor-token')
      return Promise.resolve(null)
    })
    vi.stubGlobal(
      'fetch',
      vi.fn((eingabe: RequestInfo | URL) => {
        const url = String(eingabe)
        if (url.includes('/auth/refresh')) {
          return Promise.resolve(
            new Response(JSON.stringify({ access_token: 'a', refresh_token: 'r' }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        if (url.includes('/auth/me')) {
          return Promise.resolve(
            new Response(JSON.stringify(BENUTZER), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        return Promise.resolve(
          new Response(JSON.stringify({ global_permissions: [], server_permissions: {} }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }),
    )

    render(<DesktopApp />)

    await waitFor(() => {
      expect(screen.getByTestId('ki-seite')).toBeInTheDocument()
    })
    expect(invokeMock).not.toHaveBeenCalledWith('update_installieren')
    expect(invokeMock).not.toHaveBeenCalledWith('app_neu_starten')
  })

  it('stellt die gemerkte letzte Ansicht aus dem localStorage wieder her', async () => {
    localStorage.setItem('mss:letzte_route', '/kalender')
    konfigMock({
      backend_url: 'https://api.example.com',
      sandbox_pfad: 'C:\\Users\\tester\\MSS-Sandbox',
      eingerichtet: true,
    })
    vi.stubGlobal(
      'fetch',
      vi.fn((eingabe: RequestInfo | URL) => {
        const url = String(eingabe)
        if (url.includes('/auth/refresh')) {
          return Promise.resolve(
            new Response(JSON.stringify({ access_token: 'a', refresh_token: 'r' }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        if (url.includes('/auth/me')) {
          return Promise.resolve(
            new Response(JSON.stringify(BENUTZER), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        return Promise.resolve(
          new Response(JSON.stringify({ global_permissions: ['ai.calendar.use'], server_permissions: {} }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }),
    )

    render(<DesktopApp />)

    await waitFor(() => {
      // In der Kalenderansicht wird nicht die KI-Seite gerendert
      expect(screen.queryByTestId('ki-seite')).not.toBeInTheDocument()
    })
  })

  it('führt bei verfügbarem Update die Installation beim Start aus', async () => {
    invokeMock.mockImplementation((befehl: string) => {
      if (befehl === 'konfig_laden') {
        return Promise.resolve({
          backend_url: 'https://api.example.com',
          sandbox_pfad: 'C:\\Users\\tester\\MSS-Sandbox',
          eingerichtet: true,
        })
      }
      if (befehl === 'refresh_token_laden') return Promise.resolve('tresor-token')
      if (befehl === 'update_pruefen') {
        return Promise.resolve({
          verfuegbar: true,
          neue_version: '4.3.4',
          aktuelle_version: '4.3.3',
          download_url: null,
          notizen: null,
          ist_android: false,
        })
      }
      if (befehl === 'update_installieren') return Promise.resolve()
      return Promise.resolve(null)
    })
    vi.stubGlobal(
      'fetch',
      vi.fn((eingabe: RequestInfo | URL) => {
        const url = String(eingabe)
        if (url.includes('/auth/refresh')) {
          return Promise.resolve(
            new Response(JSON.stringify({ access_token: 'a', refresh_token: 'r' }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        if (url.includes('/auth/me')) {
          return Promise.resolve(
            new Response(JSON.stringify(BENUTZER), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        return Promise.resolve(
          new Response(JSON.stringify({ global_permissions: [], server_permissions: {} }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }),
    )

    render(<DesktopApp />)

    await waitFor(() => {
      expect(invokeMock).toHaveBeenCalledWith('update_installieren')
    })
  })

  it('zeigt im Offline-Modus nur Passwort-Manager, Notizen und Kalender und behält den lokalen Account', async () => {
    localStorage.setItem('msm_cached_user', JSON.stringify(BENUTZER))
    useAuthStore.setState({ user: BENUTZER, isAuthenticated: true })

    konfigMock({
      backend_url: 'https://api.example.com',
      sandbox_pfad: 'C:\\Users\\tester\\MSS-Sandbox',
      eingerichtet: true,
    })

    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch (Offline / Flugmodus)'))),
    )

    render(<DesktopApp />)

    // Lokaler Account bleibt sichtbar und Offline-Badge wird angezeigt
    await waitFor(() => {
      expect(screen.getByText('Jarvis')).toBeInTheDocument()
      expect(screen.getByText('Offline')).toBeInTheDocument()
      // Nur Passwort-Manager, Notizen und Kalender werden angezeigt
      expect(screen.getByText(i18n.t('mss.app.tresor'))).toBeInTheDocument()
      expect(screen.getByText(i18n.t('mss.app.kalender'))).toBeInTheDocument()
      expect(screen.getByText(i18n.t('mss.app.notizen', 'Notizen'))).toBeInTheDocument()

      // KI-Assistent, Messenger und Gedächtnis sind offline ausgeblendet
      expect(screen.queryByText(i18n.t('mss.app.ki'))).not.toBeInTheDocument()
      expect(screen.queryByText(i18n.t('mss.app.messenger'))).not.toBeInTheDocument()
      expect(screen.queryByText(i18n.t('mss.app.gedaechtnis'))).not.toBeInTheDocument()
      expect(screen.queryByTestId('ki-seite')).not.toBeInTheDocument()
      expect(screen.queryByTestId('messenger-seite')).not.toBeInTheDocument()
    })
  })

  it('zeigt den Messenger-Reiter online und rendert die Messenger-Seite beim Navigieren zu /chat', async () => {
    localStorage.setItem(LETZTE_ROUTE_KEY, '/chat')
    localStorage.setItem('msm_cached_user', JSON.stringify(BENUTZER))
    useAuthStore.setState({ user: BENUTZER, isAuthenticated: true })

    konfigMock({
      backend_url: 'https://api.example.com',
      sandbox_pfad: 'C:\\Users\\tester\\MSS-Sandbox',
      eingerichtet: true,
    })

    vi.stubGlobal(
      'fetch',
      vi.fn((eingabe: RequestInfo | URL) => {
        const url = String(eingabe)
        if (url.includes('/auth/refresh')) {
          return Promise.resolve(
            new Response(JSON.stringify({ access_token: 'a', refresh_token: 'r' }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        if (url.includes('/auth/me')) {
          return Promise.resolve(
            new Response(JSON.stringify(BENUTZER), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        return Promise.resolve(new Response('{}', { status: 200 }))
      }),
    )

    render(<DesktopApp />)

    await waitFor(() => {
      expect(screen.getByTestId('messenger-seite')).toBeInTheDocument()
      expect(screen.getByText(i18n.t('mss.app.messenger'))).toBeInTheDocument()
    })
  })

  describe('Negativtests & Missbrauchsschutz im Offline-Modus', () => {
    it('leitet beim Start im Offline-Modus von einer gespeicherten verbotenen Route (/ai) automatisch auf /tresor um', async () => {
      localStorage.setItem(LETZTE_ROUTE_KEY, '/ai')
      localStorage.setItem('msm_cached_user', JSON.stringify(BENUTZER))
      useAuthStore.setState({ user: BENUTZER, isAuthenticated: true })

      konfigMock({
        backend_url: 'https://api.example.com',
        sandbox_pfad: 'C:\\Users\\tester\\MSS-Sandbox',
        eingerichtet: true,
      })

      vi.stubGlobal(
        'fetch',
        vi.fn(() => Promise.reject(new TypeError('Failed to fetch (Offline)'))),
      )

      render(<DesktopApp />)

      await waitFor(() => {
        expect(screen.getByTestId('tresor-seite')).toBeInTheDocument()
      })
      expect(screen.queryByTestId('ki-seite')).not.toBeInTheDocument()
    })

    it('leitet beim Start im Offline-Modus von /einstellungen und /gedaechtnis automatisch auf /tresor um', async () => {
      localStorage.setItem(LETZTE_ROUTE_KEY, '/einstellungen')
      localStorage.setItem('msm_cached_user', JSON.stringify(BENUTZER))
      useAuthStore.setState({ user: BENUTZER, isAuthenticated: true })

      konfigMock({
        backend_url: 'https://api.example.com',
        sandbox_pfad: 'C:\\Users\\tester\\MSS-Sandbox',
        eingerichtet: true,
      })

      vi.stubGlobal(
        'fetch',
        vi.fn(() => Promise.reject(new TypeError('Failed to fetch (Offline)'))),
      )

      render(<DesktopApp />)

      await waitFor(() => {
        expect(screen.getByTestId('tresor-seite')).toBeInTheDocument()
      })
      expect(screen.queryByTestId('einstellungen-seite')).not.toBeInTheDocument()
    })

    it('neutralisiert manipulierte oder bösartige Routen im localStorage sicher ohne Absturz', async () => {
      localStorage.setItem(LETZTE_ROUTE_KEY, 'javascript:alert(1)')
      localStorage.setItem('msm_cached_user', JSON.stringify(BENUTZER))
      useAuthStore.setState({ user: BENUTZER, isAuthenticated: true })

      konfigMock({
        backend_url: 'https://api.example.com',
        sandbox_pfad: 'C:\\Users\\tester\\MSS-Sandbox',
        eingerichtet: true,
      })

      vi.stubGlobal(
        'fetch',
        vi.fn(() => Promise.reject(new TypeError('Failed to fetch (Offline)'))),
      )

      render(<DesktopApp />)

      await waitFor(() => {
        expect(screen.getByTestId('tresor-seite')).toBeInTheDocument()
      })
      expect(screen.queryByTestId('ki-seite')).not.toBeInTheDocument()
    })

    it('fängt externe mss:navigiere-zu Events auf verbotene Routen (/ai, /einstellungen) offline ab und leitet auf /tresor', async () => {
      localStorage.setItem('msm_cached_user', JSON.stringify(BENUTZER))
      useAuthStore.setState({ user: BENUTZER, isAuthenticated: true })

      konfigMock({
        backend_url: 'https://api.example.com',
        sandbox_pfad: 'C:\\Users\\tester\\MSS-Sandbox',
        eingerichtet: true,
      })

      vi.stubGlobal(
        'fetch',
        vi.fn(() => Promise.reject(new TypeError('Failed to fetch (Offline)'))),
      )

      render(<DesktopApp />)

      await waitFor(() => {
        expect(screen.getByTestId('tresor-seite')).toBeInTheDocument()
      })

      // Missbrauchsversuch 1: Externes Event auf /ai
      triggerTauriEvent('mss:navigiere-zu', '/ai')

      await waitFor(() => {
        expect(screen.getByTestId('tresor-seite')).toBeInTheDocument()
        expect(screen.queryByTestId('ki-seite')).not.toBeInTheDocument()
      })

      // Missbrauchsversuch 2: Externes Event auf /einstellungen
      triggerTauriEvent('mss:navigiere-zu', '/einstellungen')

      await waitFor(() => {
        expect(screen.getByTestId('tresor-seite')).toBeInTheDocument()
        expect(screen.queryByTestId('einstellungen-seite')).not.toBeInTheDocument()
      })

      // Legitime Offline-Routen funktionieren weiterhin
      triggerTauriEvent('mss:navigiere-zu', '/kalender')
      await waitFor(() => {
        expect(screen.getByTestId('kalender-seite')).toBeInTheDocument()
      })

      triggerTauriEvent('mss:navigiere-zu', '/notizen')
      await waitFor(() => {
        expect(screen.getByTestId('notizen-seite')).toBeInTheDocument()
      })
    })

    it('leitet bei dynamischem Netzwerkverlust (offline Event) während aktiver KI-Ansicht sofort auf /tresor um', async () => {
      konfigMock({
        backend_url: 'https://api.example.com',
        sandbox_pfad: 'C:\\Users\\tester\\MSS-Sandbox',
        eingerichtet: true,
      })

      vi.stubGlobal(
        'fetch',
        vi.fn((eingabe: RequestInfo | URL) => {
          const url = String(eingabe)
          if (url.includes('/auth/refresh')) {
            return Promise.resolve(
              new Response(JSON.stringify({ access_token: 'a', refresh_token: 'r' }), {
                status: 200,
                headers: { 'Content-Type': 'application/json' },
              }),
            )
          }
          if (url.includes('/auth/me')) {
            return Promise.resolve(
              new Response(JSON.stringify(BENUTZER), {
                status: 200,
                headers: { 'Content-Type': 'application/json' },
              }),
            )
          }
          return Promise.resolve(
            new Response(JSON.stringify({ global_permissions: [], server_permissions: {} }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }),
      )

      render(<DesktopApp />)

      // Online: KI-Seite ist aktiv
      await waitFor(() => {
        expect(screen.getByTestId('ki-seite')).toBeInTheDocument()
      })

      // Plötzlicher Netzwerkausfall
      window.dispatchEvent(new Event('offline'))

      // Sofortige Reaktion: Offline-Badge erscheint, KI verschwindet, Tresor wird aktiviert
      await waitFor(() => {
        expect(screen.getByText('Offline')).toBeInTheDocument()
        expect(screen.queryByTestId('ki-seite')).not.toBeInTheDocument()
        expect(screen.getByTestId('tresor-seite')).toBeInTheDocument()
      })
    })

    it('bleibt stabil bei extrem schnellem Wechsel (Flapping) zwischen online und offline Events', async () => {
      konfigMock({
        backend_url: 'https://api.example.com',
        sandbox_pfad: 'C:\\Users\\tester\\MSS-Sandbox',
        eingerichtet: true,
      })

      vi.stubGlobal(
        'fetch',
        vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
      )

      localStorage.setItem('msm_cached_user', JSON.stringify(BENUTZER))
      useAuthStore.setState({ user: BENUTZER, isAuthenticated: true })

      render(<DesktopApp />)

      await waitFor(() => {
        expect(screen.getByTestId('tresor-seite')).toBeInTheDocument()
      })

      // Flapping-Sturm: 10 schnelle Wechsel
      for (let i = 0; i < 10; i++) {
        window.dispatchEvent(new Event('online'))
        window.dispatchEvent(new Event('offline'))
      }

      // App darf nicht abstürzen und bleibt im sauberen Offline-Schutzmodus
      await waitFor(() => {
        expect(screen.getByTestId('tresor-seite')).toBeInTheDocument()
        expect(screen.getByText('Offline')).toBeInTheDocument()
      })
    })

    it('lässt bei Offline-Start ohne gespeicherten Benutzer keinen unautorisierten Zugriff zu', async () => {
      konfigMock({
        backend_url: 'https://api.example.com',
        sandbox_pfad: 'C:\\Users\\tester\\MSS-Sandbox',
        eingerichtet: true,
      })

      vi.stubGlobal(
        'fetch',
        vi.fn(() => Promise.reject(new TypeError('Failed to fetch (Offline)'))),
      )

      // Kein Benutzer im Speicher
      localStorage.removeItem('msm_cached_user')
      useAuthStore.setState({ user: null, isAuthenticated: false })

      render(<DesktopApp />)

      // Tresor-Ansicht und Menüs dürfen für unautorisierte Nutzer nicht geöffnet werden
      await waitFor(() => {
        expect(screen.queryByTestId('tresor-seite')).not.toBeInTheDocument()
        expect(screen.queryByTestId('ki-seite')).not.toBeInTheDocument()
      })
    })
  })
})
