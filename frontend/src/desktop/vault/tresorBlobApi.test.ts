/**
 * Schreibende Datei-Anfragen nennen ihren Bucket. Bis 02.10.2026 nahm der
 * Server den Bucket des Kontos: nach einem Zurücksetzen auf einem anderen
 * Gerät lud ein Gerät mit dem alten Tresor in den neuen hoch.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
// Zuerst der Store: client → authStore → vaultStore → tresorBlobApi ist ein
// Kreis, und vaultStore meldet beim Laden seinen Bucket-Melder an.
import './vaultStore'
import { blobFertig, blobLoeschen, blobReservieren, blobStand, chunkHochladen } from './tresorBlobApi'

const BUCKET = 'f'.repeat(64)
const ID = 'a'.repeat(32)

function kopf(init: RequestInit | undefined, name: string): string | null {
  return new Headers(init?.headers).get(name)
}

describe('tresorBlobApi', () => {
  afterEach(() => vi.restoreAllMocks())

  it('nennt bei jeder schreibenden Anfrage den Bucket des Tresors', async () => {
    const gesehen: { pfad: string; methode: string; bucket: string | null }[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (eingabe, init) => {
      const pfad = new URL(String(eingabe), 'http://panel.test').pathname
      gesehen.push({ pfad, methode: (init?.method ?? 'GET').toUpperCase(), bucket: kopf(init, 'X-MSM-Vault-Bucket') })
      const body = pfad.endsWith('/status') ? { state: 'offen', chunk_count: 1, vorhanden: [] } : {}
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })

    await blobReservieren(BUCKET, ID, 1, 100, '0'.repeat(64))
    await blobStand(BUCKET, ID)
    await chunkHochladen(BUCKET, ID, 0, new Uint8Array(100))
    await blobFertig(BUCKET, ID)
    await blobLoeschen(BUCKET, ID, '1'.repeat(64))

    expect(gesehen.map((a) => a.methode)).toEqual(['POST', 'GET', 'PUT', 'POST', 'DELETE'])
    for (const anfrage of gesehen) expect(anfrage.bucket, anfrage.pfad).toBe(BUCKET)
  })
})
