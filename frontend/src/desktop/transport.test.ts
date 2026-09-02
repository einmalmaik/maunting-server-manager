/**
 * Die native Sitzung: Bearer statt Cookies, Tresor statt Browser.
 *
 * Geprüft wird die Naht zwischen `transport.ts` und dem Panel-API-Client —
 * genau die Stelle, an der die Desktop-App alle Panel-Komponenten erbt:
 *
 * 1. Ein registriertes Token fährt als `Authorization: Bearer` mit.
 * 2. Ein 401 rotiert **einmal** über den OS-Tresor und wiederholt die
 *    Anfrage mit dem neuen Token.
 * 3. Eine abgelehnte Rotation endet wie im Browser: SESSION_EXPIRED und
 *    eine geräumte Sitzung — kein Sonderweg, derselbe `clearSession`-Pfad.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const invokeMock = vi.fn()

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}))

import { api } from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import { setzeAccessToken, transportEinrichten } from './transport'

function antwort(status: number, koerper: unknown): Response {
  return new Response(JSON.stringify(koerper), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('native Sitzung', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    invokeMock.mockReset()
    transportEinrichten()
    setzeAccessToken('altes-token')
  })

  afterEach(() => {
    setzeAccessToken(null)
  })

  it('traegt das Token als Bearer und rotiert bei 401 genau einmal', async () => {
    invokeMock.mockImplementation((befehl: string) => {
      if (befehl === 'refresh_token_laden') return Promise.resolve('tresor-refresh')
      return Promise.resolve(null)
    })
    const aufrufe: Array<{ url: string; auth: string | null }> = []
    vi.stubGlobal(
      'fetch',
      vi.fn((eingabe: RequestInfo | URL, init?: RequestInit) => {
        const url = String(eingabe)
        const headers = new Headers(init?.headers)
        aufrufe.push({ url, auth: headers.get('Authorization') })
        if (url.endsWith('/auth/refresh')) {
          return Promise.resolve(
            antwort(200, { access_token: 'neues-token', refresh_token: 'neuer-refresh' }),
          )
        }
        // Erster Versuch scheitert, der Wiederholte traegt das neue Token.
        const alt = headers.get('Authorization') === 'Bearer altes-token'
        return Promise.resolve(alt ? antwort(401, {}) : antwort(200, { ok: true }))
      }),
    )

    const ergebnis = await api<{ ok: boolean }>('/auth/me')
    expect(ergebnis).toEqual({ ok: true })

    expect(aufrufe.map((a) => a.auth)).toEqual([
      'Bearer altes-token',
      null, // der Refresh selbst laeuft ohne Bearer, mit dem Tresor-Token im Koerper
      'Bearer neues-token',
    ])
    // Das rotierte Refresh-Token landet im Tresor, nirgendwo sonst.
    expect(invokeMock).toHaveBeenCalledWith('refresh_token_speichern', {
      token: 'neuer-refresh',
    })
  })

  it('eine abgelehnte Rotation endet im selben clearSession wie im Browser', async () => {
    invokeMock.mockImplementation((befehl: string) => {
      if (befehl === 'refresh_token_laden') return Promise.resolve('verbrannt')
      return Promise.resolve(null)
    })
    vi.stubGlobal(
      'fetch',
      vi.fn((eingabe: RequestInfo | URL) => {
        const url = String(eingabe)
        if (url.endsWith('/auth/refresh')) return Promise.resolve(antwort(401, {}))
        return Promise.resolve(antwort(401, {}))
      }),
    )
    useAuthStore.setState({ isAuthenticated: true })

    await expect(api('/auth/me')).rejects.toThrow()
    expect(useAuthStore.getState().isAuthenticated).toBe(false)
    // Das verbrannte Token fliegt aus dem Tresor — Aufheben waere riskant.
    expect(invokeMock).toHaveBeenCalledWith('refresh_token_loeschen')
  })
})
