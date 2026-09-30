/**
 * Der Tresor-Store auf der IndexedDB-Ablage: was gespeichert wird, überlebt
 * einen Neustart, und ungesendete Änderungen gehen nach dem Abgleich raus.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import 'fake-indexeddb/auto'
import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import { useVaultStore, type VaultBlindSyncPayload } from './vaultStore'
import { encryptVaultEntry } from './vaultCrypto'
import { ablageInIndexedDb, ablageLaden, ablageSchliessen, blobsLesen, warteschlangeLesen } from './tresorAblage'

vi.mock('../tauri', () => ({
  FACH_TRESOR: 'vault_biometric_key',
  biometrieSpeichern: vi.fn().mockResolvedValue(undefined),
  biometrieEntsperren: vi.fn().mockResolvedValue(''),
  biometrieLoeschen: vi.fn().mockResolvedValue(undefined),
  pruefeBiometrieVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherFragtSelbst: vi.fn().mockResolvedValue(false),
  verifiziereBiometrie: vi.fn().mockResolvedValue(false),
  setzeTresorSchutz: vi.fn().mockResolvedValue(undefined),
}))

const BUCKET = 'd'.repeat(64)

async function userKeyAnlegen(): Promise<CryptoKey> {
  return window.crypto.subtle.importKey('raw', new Uint8Array(32).fill(9), { name: 'AES-GCM', length: 256 }, false, [
    'encrypt',
    'decrypt',
  ])
}

function echoServer() {
  let revision = 10
  const koerper: VaultBlindSyncPayload[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => {
    const body = JSON.parse(String(init?.body)) as VaultBlindSyncPayload
    koerper.push(body)
    const entries = body.mutations.map((m) => ({ ...m, revision: ++revision, updated_at: '2026-09-30T00:00:00Z' }))
    return { ok: true, status: 200, json: async () => ({ server_revision: revision, entries }) } as Response
  })
  return koerper
}

describe('Tresor-Store auf IndexedDB', () => {
  beforeEach(() => {
    ablageSchliessen()
    localStorage.clear()
    globalThis.indexedDB = new IDBFactory()
    setzeAngemeldetesKonto(1)
    vi.restoreAllMocks()
  })

  afterEach(() => {
    ablageSchliessen()
    setzeAngemeldetesKonto(null)
    vi.restoreAllMocks()
  })

  it('behält eine offline gespeicherte Änderung über den Neustart und sendet sie danach', async () => {
    const userKey = await userKeyAnlegen()
    await ablageLaden(BUCKET)
    expect(ablageInIndexedDb(BUCKET)).toBe(true)

    const ciphertext = await encryptVaultEntry(
      { service: 'Bank', category: 'login', password: 'geheim', createdAt: 1, updatedAt: 10 },
      userKey,
      'e1',
    )
    useVaultStore.setState({ userKey, bucketId: BUCKET, bucketAuthToken: 'b'.repeat(64), isUnlocked: true, syncStatus: 'synced', items: [] })
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        server_revision: 10,
        entries: [{ id: 'e1', ciphertext, revision: 10, is_deleted: false, updated_at: '2026-09-30T00:00:00Z' }],
      }),
    } as Response)
    await useVaultStore.getState().syncWithServer()
    vi.restoreAllMocks()

    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))
    await useVaultStore.getState().trashItem('e1')
    await vi.waitFor(() => expect(useVaultStore.getState().syncStatus).toBe('offline'))
    await new Promise((r) => setTimeout(r, 20))

    // Neustart: nichts im localStorage, alles aus IndexedDB.
    ablageSchliessen()
    expect(localStorage.getItem(`mss:vault_pending_${BUCKET}`)).toBeNull()
    await ablageLaden(BUCKET)
    expect(warteschlangeLesen(BUCKET).map((m) => m.id)).toEqual(['e1'])
    expect(blobsLesen(BUCKET).find((b) => b.id === 'e1')?.serverRev).toBe(10)

    vi.restoreAllMocks()
    const koerper = echoServer()
    await useVaultStore.getState().syncWithServer()

    expect(koerper[0].mutations.map((m) => m.id)).toEqual(['e1'])
    await new Promise((r) => setTimeout(r, 20))
    ablageSchliessen()
    await ablageLaden(BUCKET)
    expect(warteschlangeLesen(BUCKET)).toEqual([])
  })
})
