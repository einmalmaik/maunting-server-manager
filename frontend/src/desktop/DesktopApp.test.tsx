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
vi.mock('@tauri-apps/api/event', () => ({
  emit: vi.fn(() => Promise.resolve()),
  listen: vi.fn(() => Promise.resolve(() => {})),
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
    invokeMock.mockReset()
    schleifeAktiv.length = 0
    setzeAccessToken(null)
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
    // Landet direkt in der Hauptansicht (bereit), nicht im Wizard
    await waitFor(() => {
      expect(screen.getByTestId('ki-seite')).toBeInTheDocument()
    })
    expect(screen.queryByText(i18n.t('mss.wizard.codeLabel'))).not.toBeInTheDocument()
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
    // Bleibt in bereit (Offline-Modus), keine Kopplungs-Maske
    await waitFor(() => {
      expect(screen.getByTestId('ki-seite')).toBeInTheDocument()
    })
    expect(screen.queryByText(i18n.t('mss.wizard.codeLabel'))).not.toBeInTheDocument()
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
})
