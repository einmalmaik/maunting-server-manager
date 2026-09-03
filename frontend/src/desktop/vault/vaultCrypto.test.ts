import { describe, it, expect } from 'vitest'
import {
  SecureBuffer,
  bytesToBase64,
  base64ToBytes,
  deriveVaultKeys,
  encryptVaultEntry,
  decryptVaultEntry,
  generateSecurePassword,
  VAULT_ENVELOPE_V1_PREFIX,
  BIOMETRIC_ENVELOPE_PREFIX,
  wrapVaultCredentialsForBiometrics,
  unwrapVaultCredentialsFromBiometrics,
} from './vaultCrypto'

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

  it('wraps and unwraps credentials for biometric quick unlock', async () => {
    const masterPw = 'my-super-strong-master-password-2026'
    const wrapped = await wrapVaultCredentialsForBiometrics(masterPw)
    expect(wrapped.startsWith(BIOMETRIC_ENVELOPE_PREFIX)).toBe(true)

    const unwrapped = await unwrapVaultCredentialsFromBiometrics(wrapped)
    expect(unwrapped).toBe(masterPw)

    // Invalid prefix / corrupted envelope must throw
    await expect(unwrapVaultCredentialsFromBiometrics('invalid-prefix:12345')).rejects.toThrow()
  })
})
