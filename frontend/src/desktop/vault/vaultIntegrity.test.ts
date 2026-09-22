/**
 * Integrität des Tresors gegenüber dem Server (Audit 22.09.2026).
 *
 * Zero-Knowledge sagt: der Betreiber kann nichts **lesen**. Es sagt nichts
 * darüber, was er **schreiben** kann — und genau dort lag die Lücke. Der Client
 * übernahm aus der Sync-Antwort ungeprüft:
 *
 *   - `is_deleted` (ein Feld neben dem Umschlag, nicht darin): damit ließ sich
 *     jeder Tresor leerräumen. Der lokale Cache ist die einzige lesbare Kopie.
 *   - ältere Revisionen: damit ließ sich ein längst ersetztes Passwort
 *     zurückspielen, mit gültigem Tag, ohne dass etwas auffiel.
 *   - einen fremden `vault-canary`: damit ließ sich der Besitzer beim nächsten
 *     Entsperren mit „falsches Master-Passwort" aussperren.
 *
 * Diese Tests fahren die drei Angriffe gegen eine echte Sync-Antwort.
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { useVaultStore, VAULT_TOMBSTONE_MARKER } from './vaultStore'
import { encryptVaultEntry } from './vaultCrypto'

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

const BUCKET = 'a'.repeat(64)
const EINTRAG = 'eintrag-1'

async function userKeyAnlegen(): Promise<CryptoKey> {
  return window.crypto.subtle.importKey(
    'raw',
    new Uint8Array(32).fill(7),
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  )
}

/** Antwortet auf den nächsten blind-sync mit genau diesen Einträgen. */
function serverAntwortet(entries: unknown[], serverRevision = 99) {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ server_revision: serverRevision, entries }),
  } as Response)
}

/** Ein entsperrter Tresor mit einem Eintrag auf Serverstand 10. */
async function tresorMitEintrag(userKey: CryptoKey, passwort = 'aktuelles-passwort') {
  const ciphertext = await encryptVaultEntry(
    { service: 'Bank', username: 'ich', password: passwort, createdAt: 1, updatedAt: 1 },
    userKey,
    EINTRAG,
  )
  localStorage.setItem(
    `mss:vault_blobs_${BUCKET}`,
    JSON.stringify([{ id: EINTRAG, ciphertext, revision: 10, is_deleted: false }]),
  )
  localStorage.setItem(`mss:vault_rev_${BUCKET}`, '10')
  useVaultStore.setState({
    userKey,
    bucketId: BUCKET,
    bucketAuthToken: 'b'.repeat(64),
    isUnlocked: true,
    syncStatus: 'synced',
    items: [
      {
        id: EINTRAG,
        service: 'Bank',
        username: 'ich',
        password: passwort,
        createdAt: 1,
        updatedAt: 1,
        revision: 10,
      },
    ],
  })
  return ciphertext
}

describe('Tresor-Integrität gegenüber dem Server', () => {
  let userKey: CryptoKey

  beforeEach(async () => {
    localStorage.clear()
    vi.restoreAllMocks()
    userKey = await userKeyAnlegen()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('ignoriert eine Löschung ohne belegten Tombstone', async () => {
    await tresorMitEintrag(userKey)

    // Der Angriff: `is_deleted` gesetzt, Ciphertext leer — so sah jede
    // Löschung vor dem Audit aus, und so konnte sie jeder erfinden.
    serverAntwortet([
      { id: EINTRAG, ciphertext: '', revision: 11, is_deleted: true, updated_at: '2026-09-22T00:00:00Z' },
    ])

    await useVaultStore.getState().syncWithServer()

    expect(useVaultStore.getState().items.map((i) => i.id)).toContain(EINTRAG)
    const blobs = JSON.parse(localStorage.getItem(`mss:vault_blobs_${BUCKET}`) || '[]')
    expect(blobs.some((b: { id: string }) => b.id === EINTRAG)).toBe(true)
  })

  it('ignoriert eine Löschung mit fremdem Ciphertext', async () => {
    await tresorMitEintrag(userKey)

    const fremd = await window.crypto.subtle.importKey(
      'raw',
      new Uint8Array(32).fill(3),
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt'],
    )
    const gefaelscht = await encryptVaultEntry(
      { [VAULT_TOMBSTONE_MARKER]: true, deletedAt: Date.now() },
      fremd,
      EINTRAG,
    )
    serverAntwortet([
      { id: EINTRAG, ciphertext: gefaelscht, revision: 11, is_deleted: true, updated_at: '2026-09-22T00:00:00Z' },
    ])

    await useVaultStore.getState().syncWithServer()
    expect(useVaultStore.getState().items.map((i) => i.id)).toContain(EINTRAG)
  })

  it('vollzieht eine Löschung mit echtem Tombstone', async () => {
    await tresorMitEintrag(userKey)

    const echt = await encryptVaultEntry(
      { [VAULT_TOMBSTONE_MARKER]: true, deletedAt: Date.now() },
      userKey,
      EINTRAG,
    )
    serverAntwortet([
      { id: EINTRAG, ciphertext: echt, revision: 11, is_deleted: true, updated_at: '2026-09-22T00:00:00Z' },
    ])

    await useVaultStore.getState().syncWithServer()
    expect(useVaultStore.getState().items.map((i) => i.id)).not.toContain(EINTRAG)
  })

  it('schreibt einen belegten Tombstone in die Warteschlange, keinen leeren', async () => {
    await tresorMitEintrag(userKey)
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))

    await useVaultStore.getState().deleteItem(EINTRAG)

    const queue = JSON.parse(localStorage.getItem(`mss:vault_pending_${BUCKET}`) || '[]')
    const grabstein = queue.find((q: { id: string }) => q.id === EINTRAG)
    expect(grabstein.is_deleted).toBe(true)
    expect(grabstein.ciphertext).toMatch(/^sv-vault-v1:/)
  })

  it('verwirft einen Rücksprung auf eine ältere Revision', async () => {
    await tresorMitEintrag(userKey, 'neues-passwort')

    // Der Angriff: der alte, gültig verschlüsselte Eintrag von Revision 4 —
    // etwa das Passwort, das der Benutzer nach einem Leck gewechselt hat.
    const alt = await encryptVaultEntry(
      { service: 'Bank', username: 'ich', password: 'geleaktes-altes-passwort', createdAt: 1, updatedAt: 1 },
      userKey,
      EINTRAG,
    )
    serverAntwortet([
      { id: EINTRAG, ciphertext: alt, revision: 4, is_deleted: false, updated_at: '2026-09-01T00:00:00Z' },
    ])

    await useVaultStore.getState().syncWithServer()

    const eintrag = useVaultStore.getState().items.find((i) => i.id === EINTRAG)
    expect(eintrag?.password).toBe('neues-passwort')
  })

  it('nimmt eine neuere Revision weiterhin an', async () => {
    await tresorMitEintrag(userKey, 'altes-passwort')

    const neu = await encryptVaultEntry(
      { service: 'Bank', username: 'ich', password: 'vom-zweitgeraet', createdAt: 1, updatedAt: 2 },
      userKey,
      EINTRAG,
    )
    serverAntwortet([
      { id: EINTRAG, ciphertext: neu, revision: 12, is_deleted: false, updated_at: '2026-09-22T00:00:00Z' },
    ])

    await useVaultStore.getState().syncWithServer()

    const eintrag = useVaultStore.getState().items.find((i) => i.id === EINTRAG)
    expect(eintrag?.password).toBe('vom-zweitgeraet')
  })

  it('übernimmt keinen Canary, der sich nicht öffnen lässt', async () => {
    await tresorMitEintrag(userKey)
    const eigener = await encryptVaultEntry({ canary: 'mss-vault-initialized-v1' }, userKey, 'vault-canary')
    localStorage.setItem('mss:vault_canary', eigener)

    const fremd = await window.crypto.subtle.importKey(
      'raw',
      new Uint8Array(32).fill(9),
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt'],
    )
    const fremderCanary = await encryptVaultEntry({ canary: 'boese' }, fremd, 'vault-canary')
    serverAntwortet([
      { id: 'vault-canary', ciphertext: fremderCanary, revision: 11, is_deleted: false, updated_at: '2026-09-22T00:00:00Z' },
    ])

    await useVaultStore.getState().syncWithServer()

    expect(localStorage.getItem('mss:vault_canary')).toBe(eigener)
  })
})
