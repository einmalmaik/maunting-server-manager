import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import { useBrowserBestaetigung } from '@/stores/browserBestaetigung'

const { api, oeffneBrowser } = vi.hoisted(() => ({ api: vi.fn(), oeffneBrowser: vi.fn() }))

vi.mock('@/api/client', async (original) => ({
  ...(await original<typeof import('@/api/client')>()),
  api,
}))
vi.mock('@/desktop/tauri', () => ({
  oeffneBrowser,
  pruefeBiometrieVerfuegbar: vi.fn(),
  verifiziereBiometrie: vi.fn(),
}))

import { passkeyNachweis } from './passkeyService'

/** Antwortet wie der Server: erst der Vorgang, dann die Stände der Reihe nach. */
function server(staende: string[]) {
  api.mockImplementation(async (pfad: string) => {
    if (pfad === '/auth/passkey/browser') return { vorgang: 'v'.repeat(32), zahl: 47, url: 'https://panel.example/bestaetigen#' + 'v'.repeat(32) }
    if (pfad === '/auth/passkey/browser/stand') return { stand: staende.shift() ?? 'offen' }
    throw new Error(`unerwartet: ${pfad}`)
  })
}

describe('passkeyNachweis in der App', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    api.mockReset()
    oeffneBrowser.mockReset().mockResolvedValue(undefined)
  })
  afterEach(() => {
    vi.useRealTimers()
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
    useBrowserBestaetigung.getState().setzen(null)
  })

  // Bis 28.09.2026 fragte die App den Passkey unter `tauri.localhost`, wo es keinen gibt.
  it('fragt nicht im WebView, sondern öffnet den Browser und wartet auf die Bestätigung', async () => {
    const credentials = { get: vi.fn() }
    vi.stubGlobal('navigator', { ...navigator, credentials })
    server(['offen', 'bestaetigt'])

    const nachweis = passkeyNachweis('data_export')
    await vi.advanceTimersByTimeAsync(0)
    expect(oeffneBrowser).toHaveBeenCalledWith(expect.stringMatching(/\/bestaetigen#v+$/))
    expect(useBrowserBestaetigung.getState().offen?.zahl).toBe(47)

    await vi.advanceTimersByTimeAsync(4000)
    await expect(nachweis).resolves.toEqual({ type: 'browser', vorgang: 'v'.repeat(32) })
    expect(useBrowserBestaetigung.getState().offen).toBeNull()
    expect(credentials.get).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })

  it('Abbrechen beendet das Warten mit Fehler', async () => {
    server([])
    const nachweis = passkeyNachweis('data_export')
    const fehler = expect(nachweis).rejects.toThrow(i18n.t('auth.passkeyErrors.cancelled'))
    await vi.advanceTimersByTimeAsync(0)
    useBrowserBestaetigung.getState().offen?.abbrechen()
    await fehler
    expect(useBrowserBestaetigung.getState().offen).toBeNull()
  })

  it('ein verfallener Vorgang endet mit Fehler', async () => {
    server(['verfallen'])
    const nachweis = passkeyNachweis('data_export')
    const fehler = expect(nachweis).rejects.toThrow(i18n.t('auth.browserBestaetigung.expired'))
    await vi.advanceTimersByTimeAsync(2000)
    await fehler
  })
})
