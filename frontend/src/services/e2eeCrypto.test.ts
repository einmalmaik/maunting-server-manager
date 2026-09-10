import { beforeAll, describe, expect, it } from 'vitest'
import {
  deriveBlindMailboxId,
  deriveDirectChannelKey,
  encryptE2eeMessage,
  decryptE2eeMessage,
  deriveTeamBlindMailboxId,
  deriveTeamChannelKey,
  encryptTeamE2eeMessage,
  decryptTeamE2eeMessage,
  generateLocalE2eeKeyPair,
  encryptE2eeHybrid,
  decryptE2eeHybrid,
  encryptE2eeAttachmentBlob,
  decryptE2eeAttachmentBlob,
} from './e2eeCrypto'

describe('e2eeCrypto (@msdis/shield Zero-Knowledge)', () => {
  describe('Direct 1:1 Chat E2EE', () => {
    it('derives symmetric deterministic blind mailbox IDs', async () => {
      const box1 = await deriveBlindMailboxId(42, 99)
      const box2 = await deriveBlindMailboxId(99, 42)
      const boxOther = await deriveBlindMailboxId(42, 100)

      expect(box1).toBe(box2)
      expect(box1).toHaveLength(64) // SHA-256 hex
      expect(box1).not.toBe(boxOther)
    })

    it('derives valid WebCrypto AES-GCM channel keys', async () => {
      const key = await deriveDirectChannelKey(1, 2)
      expect(key).toBeDefined()
      expect(key.algorithm.name).toBe('AES-GCM')
    })

    it('encrypts and decrypts messages with sv-e2ee-v1: versioned envelope', async () => {
      const plaintext = 'Geheimes Passwort für den Server: 12345!'
      const userA = 10
      const userB = 20

      const envelope = await encryptE2eeMessage(plaintext, userA, userB)
      expect(envelope.startsWith('sv-e2ee-v1:')).toBe(true)

      // Decrypt as recipient
      const decryptedByB = await decryptE2eeMessage(envelope, userB, userA)
      expect(decryptedByB).toBe(plaintext)

      // Decrypt as sender
      const decryptedByA = await decryptE2eeMessage(envelope, userA, userB)
      expect(decryptedByA).toBe(plaintext)
    })

    it('supports channel keys with additional shared secrets', async () => {
      const secret = 'custom-super-secret-seed'
      const envelope = await encryptE2eeMessage('Top Secret', 5, 12, secret)

      // Decrypt with matching secret
      const roundtrip = await decryptE2eeMessage(envelope, 12, 5, secret)
      expect(roundtrip).toBe('Top Secret')

      // Decryption without secret fails cleanly
      await expect(decryptE2eeMessage(envelope, 12, 5)).rejects.toThrow()
    })

    it('rejects decryption with unauthorized third party keys', async () => {
      const plaintext = 'Streng vertraulich'
      const userA = 10
      const userB = 20
      const userC = 30 // Mallory

      const envelope = await encryptE2eeMessage(plaintext, userA, userB)

      // Attempt decryption by Mallory
      await expect(decryptE2eeMessage(envelope, userC, userA)).rejects.toThrow()
    })

    it('collapses tampered ciphertexts into DisDecryptionError', async () => {
      const envelope = await encryptE2eeMessage('Wichtige Anweisung', 7, 9)
      const tampered = envelope.slice(0, -4) + 'AAAA'
      await expect(decryptE2eeMessage(tampered, 9, 7)).rejects.toThrow()
    })

    it('handles edge cases: unicode, empty string, long text', async () => {
      const testCases = [
        '',
        '   ',
        '🚀🛡️ 🔒 Unicode & Emoticons & Umlaute: ÄÖÜäöüß',
        'A'.repeat(5000), // 5KB long message
      ]

      for (const msg of testCases) {
        const envelope = await encryptE2eeMessage(msg, 5, 8)
        const roundtrip = await decryptE2eeMessage(envelope, 8, 5)
        expect(roundtrip).toBe(msg)
      }
    })
  })

  describe('Team Chat E2EE (Pillar 4)', () => {
    it('derives deterministic team blind mailbox IDs', async () => {
      const boxA = await deriveTeamBlindMailboxId(42)
      const boxB = await deriveTeamBlindMailboxId(42)
      const boxOther = await deriveTeamBlindMailboxId(43)

      expect(boxA).toBe(boxB)
      expect(boxA).toHaveLength(64)
      expect(boxA).not.toBe(boxOther)
    })

    it('encrypts and decrypts team messages with sv-e2ee-team-v1: envelope', async () => {
      const teamId = 101
      const message = 'Team-Besprechung: Server-Update um 20:00 Uhr.'

      const envelope = await encryptTeamE2eeMessage(message, teamId)
      expect(envelope.startsWith('sv-e2ee-team-v1:')).toBe(true)

      const decrypted = await decryptTeamE2eeMessage(envelope, teamId)
      expect(decrypted).toBe(message)
    })

    it('rejects team message decryption for mismatched team IDs', async () => {
      const envelope = await encryptTeamE2eeMessage('Admin Secret', 50)
      await expect(decryptTeamE2eeMessage(envelope, 99)).rejects.toThrow()
    })
  })

  describe('Chat Group / Community E2EE', () => {
    it('derives deterministic group blind mailbox IDs', async () => {
      const { deriveGroupBlindMailboxId } = await import('./e2eeCrypto')
      const boxA = await deriveGroupBlindMailboxId(10)
      const boxB = await deriveGroupBlindMailboxId(10)
      const boxOther = await deriveGroupBlindMailboxId(20)

      expect(boxA).toBe(boxB)
      expect(boxA).toHaveLength(64)
      expect(boxA).not.toBe(boxOther)
    })

    it('encrypts and decrypts group messages with sv-e2ee-team-v1: envelope', async () => {
      const { encryptGroupE2eeMessage, decryptGroupE2eeMessage } = await import('./e2eeCrypto')
      const groupId = 55
      const message = 'Community Ankündigung: Event startet heute!'

      const envelope = await encryptGroupE2eeMessage(message, groupId)
      expect(envelope.startsWith('sv-e2ee-team-v1:')).toBe(true)

      const decrypted = await decryptGroupE2eeMessage(envelope, groupId)
      expect(decrypted).toBe(message)
    })

    it('rejects group message decryption for mismatched group IDs', async () => {
      const { encryptGroupE2eeMessage, decryptGroupE2eeMessage } = await import('./e2eeCrypto')
      const envelope = await encryptGroupE2eeMessage('Geheime Gruppen-Info', 77)
      await expect(decryptGroupE2eeMessage(envelope, 88)).rejects.toThrow()
    })
  })

  describe('Asymmetric Hybrid E2EE (RSA-OAEP)', () => {
    let aliceKeys: { publicKeyJwk: string; privateKeyJwk: string }

    beforeAll(async () => {
      aliceKeys = await generateLocalE2eeKeyPair()
    }, 30_000)

    it('generates key pairs and encrypts/decrypts hybrid envelopes', async () => {
      expect(aliceKeys.publicKeyJwk).toContain('"kty":"RSA"')
      expect(aliceKeys.privateKeyJwk).toContain('"d":')

      const message = 'Hybrid E2EE payload for public key recipient'
      const envelope = await encryptE2eeHybrid(message, aliceKeys.publicKeyJwk)
      expect(envelope.startsWith('sv-e2ee-hybrid-v1:')).toBe(true)

      const decrypted = await decryptE2eeHybrid(envelope, aliceKeys.privateKeyJwk)
      expect(decrypted).toBe(message)
    }, 30_000)

    it('rejects hybrid decryption with a mismatched private key', async () => {
      const bobKeys = await generateLocalE2eeKeyPair()

      const envelope = await encryptE2eeHybrid('For Alice eyes only', aliceKeys.publicKeyJwk)
      // Bob tries to decrypt with his private key
      await expect(decryptE2eeHybrid(envelope, bobKeys.privateKeyJwk)).rejects.toThrow()
    }, 30_000)

    it('supports dual-wrapped hybrid messages for both recipient and sender', async () => {
      const bobKeys = await generateLocalE2eeKeyPair()
      const message = 'Nachricht zwischen Alice und Bob'

      // Alice sends to Bob with both keys
      const envelope = await encryptE2eeHybrid(message, bobKeys.publicKeyJwk, aliceKeys.publicKeyJwk)
      expect(envelope.startsWith('sv-e2ee-hybrid-v1:')).toBe(true)

      // Bob decrypts as recipient
      const decryptedByBob = await decryptE2eeHybrid(envelope, bobKeys.privateKeyJwk)
      expect(decryptedByBob).toBe(message)

      // Alice decrypts as sender
      const decryptedByAlice = await decryptE2eeHybrid(envelope, aliceKeys.privateKeyJwk)
      expect(decryptedByAlice).toBe(message)
    }, 30_000)

    it('manages key pairs via KeyStore without plaintext private keys in localStorage', async () => {
      const { storeLocalKeyPair, getLocalKeyPair, getOrGenerateLocalKeyPair } = await import('./e2eeCrypto')
      const userId = 777

      const keyPair = await getOrGenerateLocalKeyPair(userId)
      expect(keyPair).toBeDefined()
      expect(keyPair.publicKeyJwk).toContain('"kty":"RSA"')

      // localStorage should NOT contain plaintext private key
      expect(localStorage.getItem(`msm_e2ee_identity_${userId}_priv`)).toBeNull()

      const retrieved = await getLocalKeyPair(userId)
      expect(retrieved?.publicKeyJwk).toBe(keyPair.publicKeyJwk)
      expect(retrieved?.privateKeyJwk).toBe(keyPair.privateKeyJwk)
    }, 30_000)

    it('clears in-memory keys on clearMemoryKeyStore (logout hygiene)', async () => {
      const { storeLocalKeyPair, clearMemoryKeyStore, getStoredLocalPrivateKey } = await import('./e2eeCrypto')
      const userId = 888
      storeLocalKeyPair(userId, {
        publicKeyJwk: '{"mock": true}',
        privateKeyJwk: '{"priv": true}',
      })
      expect(getStoredLocalPrivateKey(userId)).toBe('{"priv": true}')

      clearMemoryKeyStore()
      expect(getStoredLocalPrivateKey(userId)).toBeNull()
    })

    it('decrypts legacy hybrid envelopes where symmetric key was wrapped as JSON array', async () => {
      const { rsaOaepEncrypt, importRsaOaepPublicKey } = await import('@msdis/shield/asymmetric')
      const { formatEnvelope } = await import('@msdis/shield/format-versioning')
      const { encryptString, importAesGcmRawKey } = await import('@msdis/shield/aead')
      const { E2EE_HYBRID_ENVELOPE_SPEC } = await import('./e2eeCrypto')

      // Ephemeral symmetric key
      const symBytes = new Uint8Array(32)
      crypto.getRandomValues(symBytes)
      const symKey = await importAesGcmRawKey(symBytes, ['encrypt'])
      const ciphertext = await encryptString('Legacy Format Test', symKey, 'msm:hybrid:aad')

      // Legacy format: JSON array string of numbers
      const legacyKeyString = JSON.stringify(Array.from(symBytes))
      const pubKey = await importRsaOaepPublicKey(JSON.parse(aliceKeys.publicKeyJwk))
      const wrappedKey = await rsaOaepEncrypt(legacyKeyString, pubKey)

      const legacyPayload = `${wrappedKey}.${ciphertext}`
      const legacyEnvelope = formatEnvelope(E2EE_HYBRID_ENVELOPE_SPEC, legacyPayload)

      // Decrypt using decryptE2eeHybrid
      const decrypted = await decryptE2eeHybrid(legacyEnvelope, aliceKeys.privateKeyJwk)
      expect(decrypted).toBe('Legacy Format Test')
    }, 30_000)
  })

  describe('E2EE Media & Attachment Blobs', () => {
    it('encrypts and decrypts direct 1:1 attachment blobs with sv-blob-v1: envelope', async () => {
      const attachmentData = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
      const context = { userAId: 10, userBId: 20 }

      const envelope = await encryptE2eeAttachmentBlob(attachmentData, context)
      expect(envelope.startsWith('sv-blob-v1:')).toBe(true)

      // Recipient decrypts
      const decrypted = await decryptE2eeAttachmentBlob(envelope, { userAId: 20, userBId: 10 })
      expect(decrypted).toBe(attachmentData)

      // Sender can also decrypt
      const senderDecrypted = await decryptE2eeAttachmentBlob(envelope, context)
      expect(senderDecrypted).toBe(attachmentData)
    })

    it('encrypts and decrypts group chat attachment blobs', async () => {
      const documentPayload = 'Verschlüsseltes PDF Dokument im Gruppenchat'
      const context = { groupId: 42 }

      const envelope = await encryptE2eeAttachmentBlob(documentPayload, context)
      expect(envelope.startsWith('sv-blob-v1:')).toBe(true)

      const decrypted = await decryptE2eeAttachmentBlob(envelope, context)
      expect(decrypted).toBe(documentPayload)

      // Mismatched group ID fails cleanly
      await expect(decryptE2eeAttachmentBlob(envelope, { groupId: 99 })).rejects.toThrow()
    })

    it('encrypts and decrypts team chat attachment blobs', async () => {
      const teamSecret = 'Backup-Log-Datei für Team'
      const context = { teamId: 7 }

      const envelope = await encryptE2eeAttachmentBlob(teamSecret, context)
      expect(envelope.startsWith('sv-blob-v1:')).toBe(true)

      const decrypted = await decryptE2eeAttachmentBlob(envelope, context)
      expect(decrypted).toBe(teamSecret)

      // Mismatched team ID fails cleanly
      await expect(decryptE2eeAttachmentBlob(envelope, { teamId: 8 })).rejects.toThrow()
    })

    it('fails decryption on tampered attachment blob payload', async () => {
      const payload = 'Wichtige Daten'
      const context = { userAId: 5, userBId: 6 }
      const envelope = await encryptE2eeAttachmentBlob(payload, context)

      const tampered = envelope.slice(0, -6) + 'XXXXXX'
      await expect(decryptE2eeAttachmentBlob(tampered, context)).rejects.toThrow()
    })

    it('rejects invalid crypto context with missing IDs', async () => {
      await expect(encryptE2eeAttachmentBlob('test', {})).rejects.toThrow('Ungültiger Verschlüsselungskontext')
      await expect(decryptE2eeAttachmentBlob('sv-blob-v1:fake', {})).rejects.toThrow()
    })
  })
})
