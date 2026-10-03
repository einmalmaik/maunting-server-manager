/**
 * Salz, Bucket und „eingerichtet“ gehören dem Konto, das den Tresor geöffnet
 * hat. Bis 03.10.2026 lagen sie ohne Konto im localStorage: nach einem
 * Kontowechsel bot der Tresor dem neuen Konto mit den Angaben des vorigen nur
 * „Entsperren“ an, einrichten ließ er sich nicht (Laufzeitprobe am Dev-Stack).
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import { useVaultStore } from './vaultStore'

vi.mock('../tauri', () => ({
  FACH_TRESOR: 'vault_biometric_key',
  biometrieSpeichern: vi.fn().mockResolvedValue(undefined),
  biometrieEntsperren: vi.fn().mockResolvedValue(null),
  biometrieLoeschen: vi.fn().mockResolvedValue(undefined),
  pruefeBiometrieVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherFragtSelbst: vi.fn().mockResolvedValue(false),
  verifiziereBiometrie: vi.fn().mockResolvedValue(false),
  setzeTresorSchutz: vi.fn().mockResolvedValue(undefined),
}))

const SALZ_A = 'a'.repeat(64)
const BUCKET_A = '1'.repeat(64)
const SALZ_B = 'b'.repeat(64)
const BUCKET_B = '2'.repeat(64)

/** Was `/api/vault/salt` für das angemeldete Konto antwortet. */
let server: { kdf_salt: string | null; bucket_id: string | null; has_vault: boolean }
let saltAbfragen = 0

function angabenVonA(): void {
  localStorage.setItem('mss:vault_salt', SALZ_A)
  localStorage.setItem('mss:vault_server_bucket', BUCKET_A)
  localStorage.setItem('mss:vault_setup_done', 'true')
  localStorage.setItem('mss:vault_biometrics_enabled', 'true')
  localStorage.setItem(`mss:vault_canary_${BUCKET_A}`, 'umschlag')
}

async function bisRuhig(): Promise<void> {
  for (let i = 0; i < 20; i++) await Promise.resolve()
  await new Promise((r) => setTimeout(r, 0))
}

describe('lokale Tresor-Angaben gehören einem Konto', () => {
  // Der Startlauf des Moduls (Biometrie-Migration nach 50 ms) soll vorher fertig sein.
  beforeAll(() => new Promise((r) => setTimeout(r, 120)))

  beforeEach(() => {
    localStorage.clear()
    saltAbfragen = 0
    setzeAngemeldetesKonto(null)
    useVaultStore.setState({ isInitialized: true, isUnlocked: false, isBiometricsEnabled: true })
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (eingabe) => {
      const url = typeof eingabe === 'string' ? eingabe : (eingabe as Request).url
      if (url.includes('/api/vault/salt')) {
        saltAbfragen += 1
        return new Response(JSON.stringify(server), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      return new Response('{}', { status: 404, headers: { 'Content-Type': 'application/json' } })
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    setzeAngemeldetesKonto(null)
  })

  it('verwirft die Angaben des vorigen Kontos und holt die des neuen', async () => {
    angabenVonA()
    localStorage.setItem('mss:vault_konto', '1')
    server = { kdf_salt: null, bucket_id: null, has_vault: false }

    setzeAngemeldetesKonto(2)
    await bisRuhig()

    expect(localStorage.getItem('mss:vault_salt')).toBeNull()
    expect(localStorage.getItem('mss:vault_server_bucket')).toBeNull()
    expect(localStorage.getItem('mss:vault_setup_done')).toBeNull()
    expect(localStorage.getItem('mss:vault_biometrics_enabled')).toBeNull()
    expect(localStorage.getItem(`mss:vault_canary_${BUCKET_A}`)).toBeNull()
    expect(localStorage.getItem('mss:vault_konto')).toBe('2')
    // Das neue Konto hat noch keinen Tresor: die Ansicht bietet das Einrichten an.
    expect(useVaultStore.getState().isInitialized).toBe(false)
    expect(useVaultStore.getState().isBiometricsEnabled).toBe(false)
  })

  it('übernimmt Salz und Bucket des neuen Kontos, wenn es schon einen Tresor hat', async () => {
    angabenVonA()
    localStorage.setItem('mss:vault_konto', '1')
    server = { kdf_salt: SALZ_B, bucket_id: BUCKET_B, has_vault: true }

    setzeAngemeldetesKonto(2)
    await bisRuhig()

    expect(localStorage.getItem('mss:vault_salt')).toBe(SALZ_B)
    expect(localStorage.getItem('mss:vault_server_bucket')).toBe(BUCKET_B)
    expect(useVaultStore.getState().isInitialized).toBe(true)
  })

  it('lässt die Angaben stehen, wenn dasselbe Konto wiederkommt', async () => {
    angabenVonA()
    localStorage.setItem('mss:vault_konto', '1')

    setzeAngemeldetesKonto(1)
    await bisRuhig()

    expect(saltAbfragen).toBe(0)
    expect(localStorage.getItem('mss:vault_biometrics_enabled')).toBe('true')
    expect(localStorage.getItem('mss:vault_server_bucket')).toBe(BUCKET_A)
  })

  it('behält Angaben ohne Inhaber, wenn der Server denselben Bucket nennt', async () => {
    angabenVonA()
    server = { kdf_salt: SALZ_A, bucket_id: BUCKET_A, has_vault: true }

    setzeAngemeldetesKonto(1)
    await bisRuhig()

    expect(localStorage.getItem('mss:vault_biometrics_enabled')).toBe('true')
    expect(localStorage.getItem('mss:vault_konto')).toBe('1')
  })

  it('verwirft Angaben ohne Inhaber, wenn der Server einen anderen Bucket nennt', async () => {
    angabenVonA()
    server = { kdf_salt: null, bucket_id: null, has_vault: false }

    setzeAngemeldetesKonto(2)
    await bisRuhig()

    expect(localStorage.getItem('mss:vault_server_bucket')).toBeNull()
    expect(localStorage.getItem('mss:vault_biometrics_enabled')).toBeNull()
    expect(useVaultStore.getState().isInitialized).toBe(false)
  })
})
