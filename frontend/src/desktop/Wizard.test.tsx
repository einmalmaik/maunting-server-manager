/**
 * Der Kopplungsschritt — der einzige Weg hinein.
 *
 * Geprueft wird die Kette Code → /auth/devices/redeem → Token im Tresor →
 * hydrierter authStore. Und dass ein falscher Code eine Meldung zeigt statt
 * die App zu verlassen.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { useAuthStore } from '@/stores/authStore'

const invokeMock = vi.fn()
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}))
vi.mock('@tauri-apps/api/event', () => ({
  emit: vi.fn(() => Promise.resolve()),
  listen: vi.fn(() => Promise.resolve(() => {})),
}))
vi.mock('@tauri-apps/plugin-dialog', () => ({ open: vi.fn() }))
// Der Wake-Word-Schritt hat eigene Belange; hier reicht ein Platzhalter.
vi.mock('./WakewordEinrichtung', () => ({
  WakewordEinrichtung: () => null,
}))

import { Wizard } from './Wizard'
import { setzeAccessToken } from './transport'

const KONFIG = { backend_url: 'https://api.example.com', sandbox_pfad: null, eingerichtet: true }

function json(status: number, koerper: unknown): Response {
  return new Response(JSON.stringify(koerper), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('Wizard: Kopplung', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    invokeMock.mockReset()
    invokeMock.mockResolvedValue(null)
    setzeAccessToken(null)
    useAuthStore.setState({ user: null, isAuthenticated: false, isLoading: false })
  })

  it('ein eingeloester Code fuellt Tresor und Sitzung', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((eingabe: RequestInfo | URL) => {
        const url = String(eingabe)
        if (url.includes('/auth/devices/redeem')) {
          return Promise.resolve(
            json(200, { access_token: 'acc', refresh_token: 'ref', expires_in: 900 }),
          )
        }
        if (url.includes('/auth/me')) {
          return Promise.resolve(json(200, { id: 1, username: 'tester', agent_name: null }))
        }
        return Promise.resolve(json(200, { global_permissions: [], server_permissions: {} }))
      }),
    )
    const fertig = vi.fn()
    render(<Wizard konfig={KONFIG} startSchritt="kopplung" nurKopplung onFertig={fertig} />)

    fireEvent.change(screen.getByLabelText(i18n.t('mss.wizard.codeLabel')), {
      target: { value: 'abcd efgh jklm' },
    })
    fireEvent.change(screen.getByLabelText(i18n.t('mss.wizard.geraetenameLabel')), {
      target: { value: 'Arbeitsrechner' },
    })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.wizard.koppeln') }))

    await waitFor(() => expect(fertig).toHaveBeenCalled())
    expect(invokeMock).toHaveBeenCalledWith('refresh_token_speichern', { token: 'ref' })
    expect(useAuthStore.getState().isAuthenticated).toBe(true)
  })

  it('ein abgelehnter Code zeigt eine Meldung und bleibt im Schritt', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(json(404, { detail: 'Unbekannter oder abgelaufener Code' }))),
    )
    const fertig = vi.fn()
    render(<Wizard konfig={KONFIG} startSchritt="kopplung" nurKopplung onFertig={fertig} />)

    fireEvent.change(screen.getByLabelText(i18n.t('mss.wizard.codeLabel')), {
      target: { value: 'FALSCH' },
    })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.wizard.koppeln') }))

    await waitFor(() => {
      expect(screen.getByText('Unbekannter oder abgelaufener Code')).toBeInTheDocument()
    })
    expect(fertig).not.toHaveBeenCalled()
    expect(invokeMock).not.toHaveBeenCalledWith('refresh_token_speichern', expect.anything())
  })
})
