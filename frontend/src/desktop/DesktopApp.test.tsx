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
// Die Auftragsschleife pollt endlos — hier reicht: sie existiert.
vi.mock('./useAuftragsschleife', () => ({
  useAuftragsschleife: () => null,
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
})
