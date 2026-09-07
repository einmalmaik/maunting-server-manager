import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  useVaultStore,
  blindVaultSync,
  getPendingQueue,
  cleanseVulnerableBiometricData,
  runBiometricsMigration,
} from './vaultStore'
import { biometrieLoeschen, pruefeBiometrieVerfuegbar } from '../tauri'

vi.mock('../tauri', () => ({
  biometrieSpeichern: vi.fn().mockResolvedValue(undefined),
  biometrieEntsperren: vi.fn().mockImplementation(async () => 'super-strong-master-password-2026'),
  biometrieLoeschen: vi.fn().mockResolvedValue(undefined),
  pruefeBiometrieVerfuegbar: vi.fn().mockResolvedValue(true),
  verifiziereBiometrie: vi.fn().mockResolvedValue(true),
  setzeTresorSchutz: vi.fn().mockResolvedValue(undefined),
}))

describe('useVaultStore - Security & Operations', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    useVaultStore.setState({
      isInitialized: false,
      isUnlocked: false,
      isUnlocking: false,
      failedUnlockAttempts: 0,
      lockedUntilMs: 0,
      userKey: null,
      bucketId: null,
      items: [],
      selectedItemId: null,
      autoLockMinutes: 15,
      lockOnWindowBlur: false,
      isBiometricsEnabled: false,
      lastActivityTime: Date.now(),
    })
  })

  it('updates autoLockMinutes and persists to localStorage', () => {
    const store = useVaultStore.getState()
    store.setAutoLockMinutes(30)
    expect(useVaultStore.getState().autoLockMinutes).toBe(30)
    expect(localStorage.getItem('mss:vault_autolock_minutes')).toBe('30')
  })

  it('updates lockOnWindowBlur and persists to localStorage', () => {
    const store = useVaultStore.getState()
    store.setLockOnWindowBlur(true)
    expect(useVaultStore.getState().lockOnWindowBlur).toBe(true)
    expect(localStorage.getItem('mss:vault_lock_on_blur')).toBe('true')
  })

  it('records activity and updates lastActivityTime', () => {
    const past = Date.now() - 10000
    useVaultStore.setState({ lastActivityTime: past })
    useVaultStore.getState().recordActivity()
    expect(useVaultStore.getState().lastActivityTime).toBeGreaterThanOrEqual(past + 5000)
  })

  it('memory hygiene on lock() clears all sensitive state from RAM (SEC-05)', () => {
    const fakeKey = {} as CryptoKey
    useVaultStore.setState({
      isUnlocked: true,
      userKey: fakeKey,
      bucketId: 'abcdef',
      selectedItemId: 'item-1',
      items: [
        { id: 'item-1', service: 'SecretService', username: 'admin', password: 'secretpassword', createdAt: 1, updatedAt: 1, revision: 1 },
      ],
    })

    useVaultStore.getState().lock()

    const state = useVaultStore.getState()
    expect(state.isUnlocked).toBe(false)
    expect(state.userKey).toBeNull()
    expect(state.bucketId).toBeNull()
    expect(state.selectedItemId).toBeNull()
    expect(state.items).toHaveLength(0)
  })

  it('auto-locks when inactivity exceeds autoLockMinutes', () => {
    const fakeKey = {} as CryptoKey
    useVaultStore.setState({
      isUnlocked: true,
      userKey: fakeKey,
      items: [{ id: '1', service: 'Test', username: '', password: 'abc', createdAt: 1, updatedAt: 1, revision: 1 }],
      autoLockMinutes: 10,
      lastActivityTime: Date.now() - 11 * 60 * 1000, // 11 minutes ago
    })

    const locked = useVaultStore.getState().checkAutoLock()
    expect(locked).toBe(true)

    const state = useVaultStore.getState()
    expect(state.isUnlocked).toBe(false)
    expect(state.userKey).toBeNull()
    expect(state.items).toHaveLength(0)
  })

  it('does not auto-lock when within autoLockMinutes threshold', () => {
    const fakeKey = {} as CryptoKey
    useVaultStore.setState({
      isUnlocked: true,
      userKey: fakeKey,
      autoLockMinutes: 15,
      lastActivityTime: Date.now() - 5 * 60 * 1000, // 5 minutes ago
    })

    const locked = useVaultStore.getState().checkAutoLock()
    expect(locked).toBe(false)

    const state = useVaultStore.getState()
    expect(state.isUnlocked).toBe(true)
    expect(state.userKey).toBe(fakeKey)
  })

  it('enforces payload attachment limit (<500 KB) in saveItem (SEC-08)', async () => {
    const fakeKey = {} as CryptoKey
    useVaultStore.setState({
      isUnlocked: true,
      userKey: fakeKey,
      bucketId: 'a'.repeat(64),
    })

    // Oversized attachment (600 KB)
    const oversizedAttachment = {
      id: 'att-1',
      name: 'large_backup.bin',
      size: 600 * 1024,
      mimeType: 'application/octet-stream',
      dataBase64: 'AAAA'.repeat(150 * 1024),
    }

    await expect(
      useVaultStore.getState().saveItem({
        service: 'Important Service',
        attachments: [oversizedAttachment],
      }),
    ).rejects.toThrow(/500 KB/)
  })

  it('blocks brute-force attempts with lockout window (SEC-07)', async () => {
    useVaultStore.setState({
      lockedUntilMs: Date.now() + 5000, // locked for 5 seconds
      failedUnlockAttempts: 3,
    })

    const success = await useVaultStore.getState().unlock('any-password')
    expect(success).toBe(false)
    expect(useVaultStore.getState().unlockError).toMatch(/Zu viele Fehlversuche/)
  })

  it('disables biometrics, clears enabled flag, and cleanses biometric data from keyring', async () => {
    localStorage.setItem('mss:vault_bio_wrapped', 'some_envelope')
    localStorage.setItem('mss:vault_biometrics_enabled', 'true')
    useVaultStore.setState({ isBiometricsEnabled: true })

    await useVaultStore.getState().disableBiometrics()

    expect(useVaultStore.getState().isBiometricsEnabled).toBe(false)
    expect(localStorage.getItem('mss:vault_bio_wrapped')).toBeNull()
    expect(localStorage.getItem('mss:vault_biometrics_enabled')).toBe('false')
  })

  it('cleanseVulnerableBiometricData unconditionally removes mss:vault_bio_wrapped and mss:vault_device_salt (SEC-CRIT-01)', async () => {
    localStorage.setItem('mss:vault_bio_wrapped', 'bad_legacy_envelope')
    localStorage.setItem('mss:vault_device_salt', 'bad_legacy_salt')

    await cleanseVulnerableBiometricData()

    expect(localStorage.getItem('mss:vault_bio_wrapped')).toBeNull()
    expect(localStorage.getItem('mss:vault_device_salt')).toBeNull()
    expect(biometrieLoeschen).toHaveBeenCalled()
  })

  it('runBiometricsMigration cleanses legacy envelopes, resets biometrics_enabled flag, and marks migrated (SEC-CRIT-01)', () => {
    localStorage.setItem('mss:vault_bio_wrapped', 'bad_legacy_envelope')
    localStorage.setItem('mss:vault_device_salt', 'bad_legacy_salt')
    localStorage.setItem('mss:vault_biometrics_enabled', 'true')
    localStorage.removeItem('mss:vault_bio_migrated_v2')

    runBiometricsMigration()

    expect(localStorage.getItem('mss:vault_bio_wrapped')).toBeNull()
    expect(localStorage.getItem('mss:vault_device_salt')).toBeNull()
    expect(localStorage.getItem('mss:vault_biometrics_enabled')).toBeNull()
    expect(localStorage.getItem('mss:vault_bio_migrated_v2')).toBe('true')
  })

  it('runBiometricsMigration cleanses re-introduced legacy keys even if VAULT_BIO_MIGRATED_KEY is present (SEC-CRIT-01)', () => {
    localStorage.setItem('mss:vault_bio_migrated_v2', 'true')
    localStorage.setItem('mss:vault_bio_wrapped', 'bad_legacy_envelope')
    localStorage.setItem('mss:vault_device_salt', 'bad_legacy_salt')
    localStorage.setItem('mss:vault_biometrics_enabled', 'true')

    runBiometricsMigration()

    expect(localStorage.getItem('mss:vault_bio_wrapped')).toBeNull()
    expect(localStorage.getItem('mss:vault_device_salt')).toBeNull()
    expect(localStorage.getItem('mss:vault_biometrics_enabled')).toBeNull()
  })

  it('enableBiometrics rejects when biometrics is not supported (SEC-CRIT-01)', async () => {
    vi.mocked(pruefeBiometrieVerfuegbar).mockResolvedValue(false)
    try {
      const store = useVaultStore.getState()
      await store.initializeVault('master-password-123')

      await expect(store.enableBiometrics('master-password-123')).rejects.toThrow(/nicht unterstützt/)
    } finally {
      vi.mocked(pruefeBiometrieVerfuegbar).mockResolvedValue(true)
    }
  })

  it('unlockWithBiometrics fails safely when biometrics is not supported on the device (SEC-CRIT-01)', async () => {
    vi.mocked(pruefeBiometrieVerfuegbar).mockResolvedValue(false)
    try {
      const store = useVaultStore.getState()

      const ok = await store.unlockWithBiometrics()
      expect(ok).toBe(false)
      expect(useVaultStore.getState().unlockError).toMatch(/nicht unterstützt/)
    } finally {
      vi.mocked(pruefeBiometrieVerfuegbar).mockResolvedValue(true)
    }
  })

  it('checkBiometricsSupport synchronizes isBiometricsEnabled with platform support', async () => {
    localStorage.setItem('mss:vault_biometrics_enabled', 'true')
    vi.mocked(pruefeBiometrieVerfuegbar).mockResolvedValue(false)
    try {
      const supported = await useVaultStore.getState().checkBiometricsSupport()
      expect(supported).toBe(false)
      expect(useVaultStore.getState().isBiometricsSupported).toBe(false)
      expect(useVaultStore.getState().isBiometricsEnabled).toBe(false)
    } finally {
      vi.mocked(pruefeBiometrieVerfuegbar).mockResolvedValue(true)
    }
  })

  it('enableBiometrics persists secrets exclusively in hardware Credential Store, never in localStorage (SEC-CRIT-01)', async () => {
    const masterPassword = 'super-strong-master-password-2026'
    const store = useVaultStore.getState()
    await store.initializeVault(masterPassword)

    await store.enableBiometrics(masterPassword)

    expect(useVaultStore.getState().isBiometricsEnabled).toBe(true)
    expect(localStorage.getItem('mss:vault_biometrics_enabled')).toBe('true')
    // Crucial: master password or wrapped envelopes MUST NOT exist in localStorage
    expect(localStorage.getItem('mss:vault_bio_wrapped')).toBeNull()
    expect(localStorage.getItem('mss:vault_device_salt')).toBeNull()

    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      const val = localStorage.getItem(key!)
      expect(val).not.toContain(masterPassword)
      expect(val).not.toContain('sv-bio-v1:')
    }
  })

  it('enableBiometrics rejects wrong password even when canary is missing from localStorage', async () => {
    const store = useVaultStore.getState()
    await store.initializeVault('correct-password-123')

    const currentBucket = useVaultStore.getState().bucketId
    expect(currentBucket).toBeTruthy()

    // Simulate missing canary (e.g. storage clear or sync from another device)
    localStorage.removeItem(`mss:vault_canary_${currentBucket}`)

    // Attempting to enable biometrics with wrong password must throw and not save to keyring
    await expect(store.enableBiometrics('wrong-password-456')).rejects.toThrow(/Falsches Master-Passwort/)
    expect(useVaultStore.getState().isBiometricsEnabled).toBe(false)
  })

  it('unlockWithBiometrics unlocks using OS Credential Store without reading from localStorage (SEC-CRIT-01)', async () => {
    const masterPassword = 'super-strong-master-password-2026'
    await useVaultStore.getState().initializeVault(masterPassword)
    useVaultStore.getState().lock()
    expect(useVaultStore.getState().isUnlocked).toBe(false)

    // Ensure localStorage contains no biometric secret
    expect(localStorage.getItem('mss:vault_bio_wrapped')).toBeNull()
    expect(localStorage.getItem('mss:vault_device_salt')).toBeNull()

    // Unlock via biometrics
    const success = await useVaultStore.getState().unlockWithBiometrics()
    expect(success).toBe(true)
    expect(useVaultStore.getState().isUnlocked).toBe(true)
  })

  it('resetLocalVaultState removes all sensitive keys including legacy salts and resets initialized status', () => {
    localStorage.setItem('mss:vault_setup_done', 'true')
    localStorage.setItem('mss:vault_salt', '0123456789abcdef')
    localStorage.setItem('mss:vault_server_bucket', 'bucket-123')
    localStorage.setItem('mss:vault_biometrics_enabled', 'true')
    localStorage.setItem('mss:vault_bio_wrapped', 'envelope-xyz')
    localStorage.setItem('mss:vault_device_salt', 'salt-xyz')

    useVaultStore.setState({ isInitialized: true, isUnlocked: true })
    useVaultStore.getState().resetLocalVaultState()

    expect(useVaultStore.getState().isInitialized).toBe(false)
    expect(useVaultStore.getState().isUnlocked).toBe(false)
    expect(localStorage.getItem('mss:vault_setup_done')).toBeNull()
    expect(localStorage.getItem('mss:vault_salt')).toBeNull()
    expect(localStorage.getItem('mss:vault_server_bucket')).toBeNull()
    expect(localStorage.getItem('mss:vault_biometrics_enabled')).toBeNull()
    expect(localStorage.getItem('mss:vault_bio_wrapped')).toBeNull()
    expect(localStorage.getItem('mss:vault_device_salt')).toBeNull()
  })

  it('rejects unlock when derived bucket does not match server bucket (ZKP validation)', async () => {
    localStorage.setItem('mss:vault_salt', '00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff')
    localStorage.setItem('mss:vault_server_bucket', 'different-server-bucket-id')

    const success = await useVaultStore.getState().unlock('master-password-123')
    expect(success).toBe(false)
    expect(useVaultStore.getState().unlockError).toMatch(/Falsches Master-Passwort/)
  })

  it('blindVaultSync sends POST to /api/vault/blind-sync with credentials: omit and auth_token', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => ({ server_revision: 4, entries: [] }),
    } as Response)

    const payload = {
      bucket_id: 'a'.repeat(64),
      auth_token: 'b'.repeat(64),
      since_revision: 2,
      mutations: [],
    }

    const response = await blindVaultSync(payload)
    expect(response.server_revision).toBe(4)
    expect(fetchSpy).toHaveBeenCalledTimes(1)

    const [calledUrl, calledInit] = fetchSpy.mock.calls[0]
    expect(String(calledUrl)).toContain('/api/vault/blind-sync')
    expect(calledInit?.method).toBe('POST')
    expect(calledInit?.credentials).toBe('omit')
    // No Authorization or X-CSRF-Token headers
    const headers = calledInit?.headers as Record<string, string>
    expect(headers?.['Authorization']).toBeUndefined()
    expect(headers?.['X-CSRF-Token']).toBeUndefined()
    expect(headers?.['Content-Type']).toBe('application/json')
  })

  it('unlock does not make background call to checkHintStatus', async () => {
    const checkHintSpy = vi.spyOn(useVaultStore.getState(), 'checkHintStatus')
    localStorage.setItem('mss:vault_salt', '00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff')

    await useVaultStore.getState().unlock('test-password-check')

    // checkHintStatus should not be triggered during unlock
    expect(checkHintSpy).not.toHaveBeenCalled()
  })

  it('syncWithServer preserves in-flight pending mutations and writes canary if missing', async () => {
    const bucketId = 'e'.repeat(64)
    const rawKey = new Uint8Array(32).fill(7)
    const userKey = await window.crypto.subtle.importKey(
      'raw',
      rawKey,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt'],
    )

    useVaultStore.setState({
      userKey,
      bucketId,
      bucketAuthToken: 'token-' + 'e'.repeat(58),
      syncStatus: 'synced',
      items: [],
    })

    const initialMutation = { id: 'mut-1', ciphertext: 'sv-vault-v1:c1', revision: 1, is_deleted: false }
    localStorage.setItem(`mss:vault_pending_${bucketId}`, JSON.stringify([initialMutation]))

    // Mock blindVaultSync to simulate an in-flight mutation added while request is awaiting response
    vi.spyOn(globalThis, 'fetch').mockImplementationOnce(async () => {
      // Simulate concurrent mutation added in flight
      const inFlightMutation = { id: 'mut-2', ciphertext: 'sv-vault-v1:c2', revision: 2, is_deleted: false }
      const current = JSON.parse(localStorage.getItem(`mss:vault_pending_${bucketId}`) || '[]')
      localStorage.setItem(`mss:vault_pending_${bucketId}`, JSON.stringify([...current, inFlightMutation]))

      return {
        ok: true,
        json: async () => ({
          server_revision: 5,
          entries: [{ id: 'mut-1', ciphertext: 'sv-vault-v1:c1', revision: 5, is_deleted: false }],
        }),
      } as Response
    })

    await useVaultStore.getState().syncWithServer()

    // mut-1 was synced, but mut-2 added in flight must be preserved!
    const pendingRemaining = JSON.parse(localStorage.getItem(`mss:vault_pending_${bucketId}`) || '[]')
    expect(pendingRemaining).toHaveLength(1)
    expect(pendingRemaining[0].id).toBe('mut-2')

    // Canary should be created since it was missing
    const canary = localStorage.getItem(`mss:vault_canary_${bucketId}`)
    expect(canary).not.toBeNull()
    expect(canary?.startsWith('sv-vault-v1:')).toBe(true)
  })

  it('syncWithServer sets syncStatus to error on 401 response', async () => {
    const bucketId = 'd'.repeat(64)
    const rawKey = new Uint8Array(32).fill(8)
    const userKey = await window.crypto.subtle.importKey(
      'raw',
      rawKey,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt'],
    )

    useVaultStore.setState({
      userKey,
      bucketId,
      bucketAuthToken: 'token-' + 'd'.repeat(58),
      syncStatus: 'synced',
    })

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 401,
    } as Response)

    await useVaultStore.getState().syncWithServer()

    expect(useVaultStore.getState().syncStatus).toBe('error')
  })

  it('unlock succeeds and does not lock out user when canary is valid but a cached blob is corrupted', async () => {
    const salt = '00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff'
    localStorage.setItem('mss:vault_salt', salt)
    const masterPassword = 'correct-master-password-123'

    // First unlock initializes canary and derives bucketId
    await useVaultStore.getState().unlock(masterPassword)
    const bucketId = useVaultStore.getState().bucketId!
    expect(useVaultStore.getState().isUnlocked).toBe(true)

    // Lock the store
    useVaultStore.getState().lock()
    expect(useVaultStore.getState().isUnlocked).toBe(false)

    // Inject a corrupted/tampered ciphertext blob into local storage cache
    const corruptedBlobs = [
      {
        id: 'corrupted-item-1',
        ciphertext: 'sv-vault-v1:bad_ciphertext_data_tampered',
        revision: 1,
        is_deleted: false,
      },
    ]
    localStorage.setItem(`mss:vault_blobs_${bucketId}`, JSON.stringify(corruptedBlobs))

    // Re-unlocking with the correct master password MUST succeed because canary is valid!
    await expect(useVaultStore.getState().unlock(masterPassword)).resolves.not.toThrow()

    const state = useVaultStore.getState()
    expect(state.isUnlocked).toBe(true)
    expect(state.failedUnlockAttempts).toBe(0)
    // The corrupted item was safely skipped
    expect(state.items).toHaveLength(0)
  })

  it('unlock succeeds when cached blobs contain corrupted non-JSON data', async () => {
    const salt = '00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff'
    localStorage.setItem('mss:vault_salt', salt)
    const masterPassword = 'correct-master-password-456'

    await useVaultStore.getState().unlock(masterPassword)
    const bucketId = useVaultStore.getState().bucketId!
    useVaultStore.getState().lock()

    // Write malformed non-JSON data to local storage
    localStorage.setItem(`mss:vault_blobs_${bucketId}`, '{not valid json!!!')

    await expect(useVaultStore.getState().unlock(masterPassword)).resolves.not.toThrow()
    expect(useVaultStore.getState().isUnlocked).toBe(true)
    expect(useVaultStore.getState().failedUnlockAttempts).toBe(0)
  })

  it('getPendingQueue handles corrupted JSON gracefully without throwing', () => {
    const bucketId = 'test-corrupt-pending-bucket'
    localStorage.setItem(`mss:vault_pending_${bucketId}`, 'corrupted [[ not json')

    const queue = getPendingQueue(bucketId)
    expect(queue).toEqual([])
  })
})

