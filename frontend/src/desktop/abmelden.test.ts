/**
 * Abmelden in der App (`desktop/auth.ts`). Auf Android fällt dabei die
 * Kamera-Sicherung des Kontos: ihr Zugang beim Server, solange die Sitzung ihn
 * noch entfernen darf, und Stand samt Aufträgen auf dem Telefon (AGENTS.md
 * Punkt 50). Bis 03.10.2026 räumte nur das Abmelden im Panel sie ab; in der
 * App lief der Job nach dem Abmelden weiter.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { reihenfolge } = vi.hoisted(() => ({ reihenfolge: [] as string[] }))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: vi.fn(async (befehl: string) => {
    reihenfolge.push(befehl)
    return befehl === 'refresh_token_laden' ? 'r' : null
  }),
}))

vi.mock('@/api/client', async (original) => ({
  ...(await original<typeof import('@/api/client')>()),
  api: vi.fn(async (pfad: string, init?: RequestInit) => {
    reihenfolge.push(`${init?.method ?? 'GET'} ${pfad}`)
    return null
  }),
}))

// Wie in kameraSicherung.test.ts: das echte Modul fände sich über authStore
// halb initialisiert vor.
vi.mock('./vault/tresorBlobApi', () => ({
  bucketMelderSetzen: vi.fn(),
  zurueckgesetztFrage: vi.fn(),
  blobStand: vi.fn(),
  sicherungszugangAnlegen: vi.fn(),
  sicherungszugangEntfernen: vi.fn(async () => {
    reihenfolge.push('zugang entfernen')
  }),
}))

vi.mock('./transport', () => ({ setzeAccessToken: vi.fn(), sitzungVerwerfen: vi.fn(async () => {}) }))

import { abmelden } from './auth'

const ua = navigator.userAgent

function alsApp(android: boolean) {
  Object.defineProperty(navigator, 'userAgent', {
    value: android ? 'Mozilla/5.0 (Linux; Android 15; Pixel) wv' : ua,
    configurable: true,
  })
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
}

describe('abmelden in der App', () => {
  beforeEach(() => {
    reihenfolge.length = 0
  })
  afterEach(() => {
    Object.defineProperty(navigator, 'userAgent', { value: ua, configurable: true })
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
  })

  it('nimmt auf Android die Kamera-Sicherung mit, bevor die Sitzung endet', async () => {
    alsApp(true)
    await abmelden()
    const zugang = reihenfolge.indexOf('zugang entfernen')
    const vergessen = reihenfolge.indexOf('medien_sicherung_vergessen')
    const logout = reihenfolge.indexOf('POST /auth/logout')
    expect(zugang).toBeGreaterThan(-1)
    expect(vergessen).toBeGreaterThan(-1)
    expect(logout).toBeGreaterThan(Math.max(zugang, vergessen))
  })

  it('fasst am Desktop keine Kamera-Sicherung an', async () => {
    alsApp(false)
    await abmelden()
    expect(reihenfolge).not.toContain('medien_sicherung_vergessen')
    expect(reihenfolge).not.toContain('zugang entfernen')
    expect(reihenfolge).toContain('POST /auth/logout')
  })
})
