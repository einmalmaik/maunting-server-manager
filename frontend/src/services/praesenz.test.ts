/**
 * Der Rückweg zum Strom, zweiter Zweck: vorne oder hinten.
 *
 * Ohne diese Meldung gilt serverseitig weiter „irgendeine offene Verbindung
 * genügt" — ein Fenster im Hintergrund verschluckt dann die Meldung, und zwei
 * Fenster melden sie doppelt.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const rufe: { pfad: string; koerper: Record<string, unknown> | null }[] = []
let apiKaputt = false

vi.mock('@/api/client', () => ({
  api: async (pfad: string, optionen?: { body?: string }) => {
    if (apiKaputt) throw new Error('kein Netz')
    rufe.push({ pfad, koerper: optionen?.body ? JSON.parse(optionen.body) : null })
    return { ok: true }
  },
}))

import { merkePraesenzKennung, vergissPraesenzKennung } from './praesenz'

const ruhe = () => new Promise((r) => setTimeout(r, 0))

function sichtbarkeit(wert: 'visible' | 'hidden'): void {
  Object.defineProperty(document, 'visibilityState', { value: wert, configurable: true })
  document.dispatchEvent(new Event('visibilitychange'))
}

describe('praesenz', () => {
  beforeEach(() => {
    rufe.length = 0
    apiKaputt = false
    vergissPraesenzKennung()
    sichtbarkeit('visible')
    rufe.length = 0
  })

  it('meldet den Stand, sobald der Strom sich vorgestellt hat', async () => {
    merkePraesenzKennung('conn-1')
    await ruhe()

    expect(rufe).toEqual([
      { pfad: '/events/presence', koerper: { conn_id: 'conn-1', zustand: 'vordergrund' } },
    ])
  })

  it('meldet den Wechsel in den Hintergrund', async () => {
    merkePraesenzKennung('conn-1')
    await ruhe()

    sichtbarkeit('hidden')
    await ruhe()

    expect(rufe.at(-1)).toEqual({
      pfad: '/events/presence',
      koerper: { conn_id: 'conn-1', zustand: 'hintergrund' },
    })
  })

  it('meldet denselben Stand nicht zweimal', async () => {
    merkePraesenzKennung('conn-1')
    await ruhe()
    const nachErster = rufe.length

    sichtbarkeit('visible')
    await ruhe()

    expect(rufe.length).toBe(nachErster)
  })

  it('meldet nach einem Fehlschlag erneut', async () => {
    apiKaputt = true
    merkePraesenzKennung('conn-1')
    await ruhe()
    expect(rufe).toEqual([])

    apiKaputt = false
    sichtbarkeit('hidden')
    await ruhe()

    expect(rufe.at(-1)?.koerper).toEqual({ conn_id: 'conn-1', zustand: 'hintergrund' })
  })
})
