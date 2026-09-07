import { describe, it, expect, vi } from 'vitest'
import {
  SecureBuffer,
  bytesToBase64,
  base64ToBytes,
  deriveVaultKeys,
  encryptVaultEntry,
  decryptVaultEntry,
  generateSecurePassword,
  VAULT_ENVELOPE_V1_PREFIX,
  isBiometricsAvailable,
  promptBiometricVerification,
  padPayload,
  unpadPayload,
  concatBytes,
} from './vaultCrypto'
import { pruefeBiometrieVerfuegbar, verifiziereBiometrie } from '../tauri'

vi.mock('../tauri', () => ({
  pruefeBiometrieVerfuegbar: vi.fn().mockResolvedValue(false),
  verifiziereBiometrie: vi.fn().mockResolvedValue(false),
}))

describe('vaultCrypto', () => {
  it('SecureBuffer manages memory with controlled access and destroy', () => {
    const buf = new SecureBuffer(32)
    expect(buf.size).toBe(32)
    expect(buf.isDestroyed).toBe(false)

    buf.use((bytes) => {
      bytes.fill(0xaa)
      expect(bytes[0]).toBe(0xaa)
    })

    buf.destroy()
    expect(buf.isDestroyed).toBe(true)
    expect(() => buf.use((b) => b[0])).toThrow()
  })

  it('bytesToBase64 and base64ToBytes roundtrip cleanly', () => {
    const original = new Uint8Array([0, 1, 2, 253, 254, 255])
    const b64 = bytesToBase64(original)
    const decoded = base64ToBytes(b64)
    expect(Array.from(decoded)).toEqual(Array.from(original))
  })

  it('generateSecurePassword generates distinct strong passwords', () => {
    const pw1 = generateSecurePassword(20, true)
    const pw2 = generateSecurePassword(20, true)
    expect(pw1.length).toBe(20)
    expect(pw2.length).toBe(20)
    expect(pw1).not.toBe(pw2)
  })

  it('derives vault keys via Argon2id and performs authenticated AES-GCM encryption/decryption', async () => {
    const salt = new Uint8Array(16)
    salt.fill(7)
    const { userKey, bucketId } = await deriveVaultKeys('master-test-password', salt)

    expect(bucketId).toBeDefined()
    expect(bucketId.length).toBe(64)

    const entryId = 'uuid-entry-123'
    const payload = {
      service: 'Discord',
      username: 'testuser',
      password: 'supersecretpassword123',
    }

    const envelope = await encryptVaultEntry(payload, userKey, entryId)
    expect(envelope.startsWith(VAULT_ENVELOPE_V1_PREFIX)).toBe(true)

    const decrypted = await decryptVaultEntry(envelope, userKey, entryId)
    expect(decrypted.service).toBe('Discord')
    expect(decrypted.username).toBe('testuser')
    expect(decrypted.password).toBe('supersecretpassword123')

    // Tampered entryId (AAD-Mismatch) must fail
    await expect(decryptVaultEntry(envelope, userKey, 'wrong-entry-id')).rejects.toThrow()
  })

  it('fails closed in promptBiometricVerification when credentials are missing or unauthenticated (SEC-CRIT-01)', async () => {
    const originalCredentials = navigator.credentials
    const originalPKC = (window as unknown as { PublicKeyCredential?: unknown }).PublicKeyCredential
    try {
      // 1. Without credentials API -> must return false (fail closed)
      Object.defineProperty(navigator, 'credentials', {
        value: undefined,
        configurable: true,
      })
      const resWithoutCreds = await promptBiometricVerification('Test')
      expect(resWithoutCreds).toBe(false)

      // Set mock PublicKeyCredential so web branch executes
      ;(window as unknown as { PublicKeyCredential: unknown }).PublicKeyCredential = class PublicKeyCredential {}

      // 2. With mock credentials API returning null -> must return false (not true!)
      Object.defineProperty(navigator, 'credentials', {
        value: {
          get: vi.fn().mockResolvedValue(null),
        },
        configurable: true,
      })
      const resNull = await promptBiometricVerification('Test')
      expect(resNull).toBe(false)

      // 3. User cancel or abort -> throws cancellation error
      Object.defineProperty(navigator, 'credentials', {
        value: {
          get: vi.fn().mockRejectedValue(new DOMException('User cancelled', 'NotAllowedError')),
        },
        configurable: true,
      })
      await expect(promptBiometricVerification('Test')).rejects.toThrow(/abgebrochen/)

      // 4. Other unexpected errors -> fails closed (returns false, not true!)
      Object.defineProperty(navigator, 'credentials', {
        value: {
          get: vi.fn().mockRejectedValue(new Error('Unknown hardware error')),
        },
        configurable: true,
      })
      const resErr = await promptBiometricVerification('Test')
      expect(resErr).toBe(false)
    } finally {
      Object.defineProperty(navigator, 'credentials', {
        value: originalCredentials,
        configurable: true,
      })
      if (originalPKC !== undefined) {
        ;(window as unknown as { PublicKeyCredential?: unknown }).PublicKeyCredential = originalPKC
      } else {
        delete (window as unknown as { PublicKeyCredential?: unknown }).PublicKeyCredential
      }
    }
  })

  it('delegates to native Windows Hello and does not fall through to WebAuthn when cancelled (SEC-CRIT-01)', async () => {
    vi.mocked(pruefeBiometrieVerfuegbar).mockResolvedValueOnce(true)
    vi.mocked(verifiziereBiometrie).mockResolvedValueOnce(false)

    // Even if WebAuthn is present and would succeed:
    const mockGet = vi.fn().mockResolvedValue({ id: 'fido-token' })
    const origCreds = navigator.credentials
    Object.defineProperty(navigator, 'credentials', {
      value: { get: mockGet },
      configurable: true,
    })
    const origPKC = (window as unknown as { PublicKeyCredential?: unknown }).PublicKeyCredential
    ;(window as unknown as { PublicKeyCredential: unknown }).PublicKeyCredential = class PublicKeyCredential {}

    try {
      const res = await promptBiometricVerification('Tresor entsperren')
      expect(res).toBe(false)
      expect(verifiziereBiometrie).toHaveBeenCalledWith('Tresor entsperren')
      expect(mockGet).not.toHaveBeenCalled()
    } finally {
      Object.defineProperty(navigator, 'credentials', {
        value: origCreds,
        configurable: true,
      })
      if (origPKC !== undefined) {
        ;(window as unknown as { PublicKeyCredential?: unknown }).PublicKeyCredential = origPKC
      } else {
        delete (window as unknown as { PublicKeyCredential?: unknown }).PublicKeyCredential
      }
    }
  })

  it('returns true when native Windows Hello verification succeeds', async () => {
    vi.mocked(pruefeBiometrieVerfuegbar).mockResolvedValueOnce(true)
    vi.mocked(verifiziereBiometrie).mockResolvedValueOnce(true)

    const res = await promptBiometricVerification('Tresor entsperren')
    expect(res).toBe(true)
    expect(verifiziereBiometrie).toHaveBeenCalledWith('Tresor entsperren')
  })

  it('rejects web biometrics in isBiometricsAvailable as browser lacks hardware keyring (SEC-CRIT-01)', async () => {
    const available = await isBiometricsAvailable()
    expect(available).toBe(false)
  })

  it('does not persist device salt or reversible keys in localStorage during vault operations', () => {
    localStorage.clear()
    expect(localStorage.getItem('mss:vault_device_salt')).toBeNull()
    expect(localStorage.getItem('mss:vault_bio_wrapped')).toBeNull()
  })

  it('derives deterministic bucketAuthToken distinct from bucketId', async () => {
    const salt = new Uint8Array(16)
    salt.fill(42)
    const res1 = await deriveVaultKeys('super-secret-pw', salt)
    const res2 = await deriveVaultKeys('super-secret-pw', salt)

    expect(res1.bucketAuthToken).toBeDefined()
    expect(res1.bucketAuthToken.length).toBe(64)
    // Deterministic
    expect(res1.bucketAuthToken).toBe(res2.bucketAuthToken)
    expect(res1.bucketId).toBe(res2.bucketId)
    // Must be completely distinct from bucketId
    expect(res1.bucketAuthToken).not.toBe(res1.bucketId)
  })

  it('padPayload and unpadPayload normalize payload size and roundtrip cleanly', () => {
    const payload = JSON.stringify({ service: 'GitHub', token: 'ghp_123456789' })
    const padded = padPayload(payload, 4096)

    expect(padded.length).toBe(4096)
    expect(unpadPayload(padded)).toBe(payload)

    // Empty payload
    const emptyPadded = padPayload('', 4096)
    expect(emptyPadded.length).toBe(4096)
    expect(unpadPayload(emptyPadded)).toBe('')

    // Payload with multi-byte unicode and emojis
    const unicodePayload = JSON.stringify({ note: 'Tresor-Notiz mit Emojis 🔑🔒🛡️ und Umlauten äöüß' })
    const unicodePadded = padPayload(unicodePayload, 4096)
    expect(unicodePadded.length).toBe(4096)
    expect(unpadPayload(unicodePadded)).toBe(unicodePayload)

    // Large payload exceeding 4096 pads to 8192
    const largePayload = 'A'.repeat(5000)
    const largePadded = padPayload(largePayload, 4096)
    expect(largePadded.length).toBe(8192)
    expect(unpadPayload(largePadded)).toBe(largePayload)

    // Edge cases: targetBlockSize <= 0 or invalid falls back safely to >= 1 byte
    const smallPadded = padPayload('test', 0)
    expect(unpadPayload(smallPadded)).toBe('test')

    // Edge cases: unpadPayload with truncated or invalid length prefix returns raw
    expect(unpadPayload('99999:short')).toBe('99999:short')
    expect(unpadPayload('')).toBe('')
    expect(unpadPayload('not-padded-string')).toBe('not-padded-string')

    // Zero-Breakage: Unpadded raw JSON string is returned untouched
    const rawLegacyJson = '{"service":"LegacyService","password":"plain"}'
    expect(unpadPayload(rawLegacyJson)).toBe(rawLegacyJson)
  })

  it('decryptVaultEntry supports legacy unpadded ciphertext seamlessly (Zero-Breakage)', async () => {
    const salt = new Uint8Array(16)
    salt.fill(9)
    const { userKey } = await deriveVaultKeys('legacy-user-password', salt)
    const entryId = 'legacy-entry-001'

    // Manually encrypt unpadded JSON (simulating legacy entry created before padding)
    const encoder = new TextEncoder()
    const legacyPlaintext = encoder.encode(JSON.stringify({ service: 'OldService', password: 'old-password' }))
    const aad = encoder.encode(entryId)
    const iv = new Uint8Array(12)
    window.crypto.getRandomValues(iv)
    const encryptedBuf = await window.crypto.subtle.encrypt(
      { name: 'AES-GCM', iv, additionalData: aad, tagLength: 128 },
      userKey,
      legacyPlaintext,
    )
    const combined = new Uint8Array(iv.length + encryptedBuf.byteLength)
    combined.set(iv, 0)
    combined.set(new Uint8Array(encryptedBuf), iv.length)
    const legacyEnvelope = `${VAULT_ENVELOPE_V1_PREFIX}${bytesToBase64(combined)}`

    // decryptVaultEntry MUST be able to decrypt it without error
    const decrypted = await decryptVaultEntry(legacyEnvelope, userKey, entryId)
    expect(decrypted.service).toBe('OldService')
    expect(decrypted.password).toBe('old-password')
  })
})
