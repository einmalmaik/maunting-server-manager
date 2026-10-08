/**
 * Ein Herzschlag, der beim Abmelden noch unterwegs ist, schrieb danach
 * „offline“ (= gekoppelt) zurück (AGENTS.md 45, 101; Bugjagd 08.10.2026,
 * am Stand 229b690d rot).
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const { herzschlag } = vi.hoisted(() => ({ herzschlag: { ablehnen: null as null | ((e: unknown) => void) } }))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string) =>
    Promise.resolve(befehl === 'konfig_laden' ? { backend_url: 'https://panel.example', eingerichtet: true } : null),
}))
vi.mock('@/api/client', () => ({
  api: () => new Promise((_, ablehnen) => (herzschlag.ablehnen = ablehnen)),
  isNetworkOrOfflineError: () => true,
}))
vi.mock('@/desktop/auth', () => ({ ABGEMELDET: 'mss:abgemeldet' }))
vi.mock('@/desktop/transport', () => ({ stillAnmeldenDetail: () => Promise.resolve({ status: 'ok' }) }))
vi.mock('@/stores/authStore', () => ({ useAuthStore: { getState: () => ({ checkAuth: () => Promise.resolve() }) } }))
vi.mock('@/stores/publicSettingsStore', () => ({ usePublicSettingsStore: { getState: () => ({ refresh: () => Promise.resolve() }) } }))

const { useSitzung, useSitzungsLauf, istGekoppelt } = await import('./sitzung')

afterEach(() => vi.useRealTimers())

describe('Sitzung', () => {
  it('nach dem Abmelden bleibt der Browser abgemeldet, auch wenn ein Herzschlag danach scheitert', async () => {
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    vi.useFakeTimers()
    renderHook(() => useSitzungsLauf())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
    expect(useSitzung.getState().stand).toBe('an')
    // Herzschlag geht los und hängt im Netz.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(45_000)
    })
    expect(herzschlag.ablehnen).not.toBeNull()
    // Der Nutzer meldet sich ab.
    act(() => {
      window.dispatchEvent(new Event('mss:abgemeldet'))
    })
    expect(useSitzung.getState().stand).toBe('aus')
    // Die alte Anfrage scheitert erst jetzt.
    await act(async () => {
      herzschlag.ablehnen!(new TypeError('Failed to fetch'))
      await vi.advanceTimersByTimeAsync(0)
    })
    const stand = useSitzung.getState().stand
    expect({ stand, gekoppelt: istGekoppelt(stand) }).toEqual({ stand: 'aus', gekoppelt: false })
  })
})
