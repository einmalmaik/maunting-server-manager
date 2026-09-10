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

  describe('Public Key Validation & Private Key Leak Prevention', () => {
    it('validates safe RSA-OAEP public keys and rejects invalid formats', async () => {
      const { validatePublicKeyJwk } = await import('./e2eeCrypto')
      const validKey = await generateLocalE2eeKeyPair()

      // Valid key
      const resultValid = validatePublicKeyJwk(validKey.publicKeyJwk)
      expect(resultValid.valid).toBe(true)
      expect(resultValid.jwk?.kty).toBe('RSA')

      // Invalid: non-JSON
      expect(validatePublicKeyJwk('not-json').valid).toBe(false)
      expect(validatePublicKeyJwk('').valid).toBe(false)

      // Invalid: non-RSA key type
      expect(validatePublicKeyJwk(JSON.stringify({ kty: 'oct', k: 'secret' })).valid).toBe(false)

      // Invalid: missing modulus or exponent
      expect(validatePublicKeyJwk(JSON.stringify({ kty: 'RSA', e: 'AQAB' })).valid).toBe(false)
      expect(validatePublicKeyJwk(JSON.stringify({ kty: 'RSA', n: 'abc' })).valid).toBe(false)

      // CRITICAL LEAK PREVENTION: private parameter 'd' in public key
      const leakedPriv = JSON.stringify({
        kty: 'RSA',
        n: 'a'.repeat(350),
        e: 'AQAB',
        d: 'private-exponent-leak',
      })
      const resultLeak = validatePublicKeyJwk(leakedPriv)
      expect(resultLeak.valid).toBe(false)
      expect(resultLeak.error).toContain('Security violation')

      // CRITICAL LEAK PREVENTION: RFC 7517 CRT parameters dmp1, dmq1, coeff
      const leakedCrt = JSON.stringify({
        kty: 'RSA',
        n: 'a'.repeat(350),
        e: 'AQAB',
        dmp1: 'crt-leak',
      })
      expect(validatePublicKeyJwk(leakedCrt).valid).toBe(false)
      expect(validatePublicKeyJwk(leakedCrt).error).toContain('Security violation')

      // Short / weak modulus (< 300 chars)
      const weakModulus = JSON.stringify({
        kty: 'RSA',
        n: 'too-short',
        e: 'AQAB',
      })
      expect(validatePublicKeyJwk(weakModulus).valid).toBe(false)
    }, 30_000)

    it('rejects hybrid encryption when provided with an invalid public key', async () => {
      await expect(
        encryptE2eeHybrid('secret message', 'invalid-key')
      ).rejects.toThrow()

      await expect(
        encryptE2eeHybrid('secret message', JSON.stringify({ kty: 'oct', k: '1234' }))
      ).rejects.toThrow()
    })
  })

  describe('AAD Cryptographic Binding & Anti-Tampering (Session Swap Protection)', () => {
    it('rejects hybrid envelopes when wrappedKeys part is tampered or swapped', async () => {
      const alice = await generateLocalE2eeKeyPair()
      const bob = await generateLocalE2eeKeyPair()
      const mallory = await generateLocalE2eeKeyPair()

      // Alice sends message to Bob
      const originalEnvelope = await encryptE2eeHybrid(
        'Vertrauliche Finanzdaten',
        bob.publicKeyJwk,
        alice.publicKeyJwk
      )

      // Attacker tampers with the wrapped key prefix
      const parsedPrefix = 'sv-e2ee-hybrid-v1:'
      const payload = originalEnvelope.slice(parsedPrefix.length)
      const dotIdx = payload.indexOf('.')
      const wrappedKeys = payload.slice(0, dotIdx)
      const ciphertext = payload.slice(dotIdx + 1)

      // Mallory creates a fake wrapped key and replaces Bob's wrapped key
      const tamperedWrappedKeys = wrappedKeys.slice(0, -10) + 'AAAAAAAAAA'
      const tamperedEnvelope = `${parsedPrefix}${tamperedWrappedKeys}.${ciphertext}`

      // Decryption by Bob must fail because AAD auth tag mismatch
      await expect(decryptE2eeHybrid(tamperedEnvelope, bob.privateKeyJwk)).rejects.toThrow()
    }, 30_000)

    it('rejects direct messages when ciphertext auth tag is corrupted', async () => {
      const envelope = await encryptE2eeMessage('Geheimdokument', 10, 20)
      // Corrupt the base64 payload
      const validPayload = envelope.slice('sv-e2ee-v1:'.length)
      const corruptedPayload = validPayload.slice(0, -6) + 'XXXXXX'
      const corruptedEnvelope = `sv-e2ee-v1:${corruptedPayload}`

      await expect(decryptE2eeMessage(corruptedEnvelope, 20, 10)).rejects.toThrow()
    })
  })

  describe('Envelope Integrity & Plaintext Leak Validation', () => {
    it('validates envelope integrity and detects unencrypted plaintext leaks', async () => {
      const { validateEnvelopeIntegrity, decryptE2eeMessage } = await import('./e2eeCrypto')

      const validEnv = await encryptE2eeMessage('Test', 1, 2)
      const validResult = validateEnvelopeIntegrity(validEnv)
      expect(validResult.valid).toBe(true)
      expect(validResult.prefix).toBe('sv-e2ee-v1:')

      // Plaintext JSON leak: MUST be rejected
      const leakedEnv = 'sv-e2ee-v1:{"text": "Geheimer Plaintext", "sender_id": 1}'
      const leakResult = validateEnvelopeIntegrity(leakedEnv)
      expect(leakResult.valid).toBe(false)
      expect(leakResult.error).toContain('plaintext payload detected')

      // Decrypting a plaintext-leaking envelope must throw DisDecryptionError
      await expect(decryptE2eeMessage(leakedEnv, 1, 2)).rejects.toThrow()

      // Weak / Zero-IV envelope (12 zero bytes)
      const zeroIvBytes = new Uint8Array(28) // all zeros
      const zeroIvB64 = btoa(String.fromCharCode(...zeroIvBytes))
      const zeroIvEnv = `sv-e2ee-v1:${zeroIvB64}`
      const zeroIvResult = validateEnvelopeIntegrity(zeroIvEnv)
      expect(zeroIvResult.valid).toBe(false)
      expect(zeroIvResult.error).toContain('Weak/zero nonce/IV')

      // Unknown prefix
      expect(validateEnvelopeIntegrity('unencrypted:test').valid).toBe(false)

      // Empty envelope
      expect(validateEnvelopeIntegrity('').valid).toBe(false)

      // Truncated payload
      expect(validateEnvelopeIntegrity('sv-e2ee-v1:short').valid).toBe(false)
    })
  })

  describe('Ratchet Sessions & Forward Secrecy', () => {
    it('steps the ratchet forward and derives ephemeral keys per epoch', async () => {
      const { E2eeRatchetSession } = await import('./e2eeCrypto')
      const session = await E2eeRatchetSession.create(101, 202)

      expect(session.epoch).toBe(0)

      const step1 = await session.stepForward()
      expect(step1.epoch).toBe(0)
      expect(step1.messageKey).toBeDefined()
      expect(session.epoch).toBe(1)

      const step2 = await session.stepForward()
      expect(step2.epoch).toBe(1)
      expect(step2.messageKey).toBeDefined()
      expect(session.epoch).toBe(2)

      // Validate epoch: spent epochs are rejected
      const checkSpent = session.validateEpoch(0)
      expect(checkSpent.ok).toBe(false)
      expect(checkSpent.reason).toContain('bereits verbraucht')

      // Future epoch within window is accepted
      const checkValid = session.validateEpoch(2)
      expect(checkValid.ok).toBe(true)

      // Huge epoch jump beyond maxSkippedSteps is rejected
      const checkHugeJump = session.validateEpoch(500)
      expect(checkHugeJump.ok).toBe(false)
      expect(checkHugeJump.reason).toContain('maximales Fenster')

      session.destroy()
    })
  })

  describe('Key Rotation & Replay Protection', () => {
    it('rotates channel and team keys deterministically across epochs', async () => {
      const { rotateChannelKey, rotateTeamChannelKey, rotateLocalKeyPair } = await import('./e2eeCrypto')

      const baseSecret = 'master-channel-seed'
      const keyEpoch1 = await rotateChannelKey(baseSecret, 1)
      const keyEpoch2 = await rotateChannelKey(baseSecret, 2)

      expect(keyEpoch1).not.toBe(keyEpoch2)
      expect(keyEpoch1).toHaveLength(64)
      expect(keyEpoch2).toHaveLength(64)

      const teamKey1 = await rotateTeamChannelKey(42, 'secret-team-key', 'salt1')
      const teamKey2 = await rotateTeamChannelKey(42, 'secret-team-key', 'salt2')
      expect(teamKey1).not.toBe(teamKey2)

      // User public key rotation
      const rotated = await rotateLocalKeyPair(999)
      expect(rotated.publicKeyJwk).toContain('"kty":"RSA"')
    }, 30_000)

    it('detects and rejects duplicate/replayed envelopes with ReplayDetector', async () => {
      const { createReplayDetector } = await import('./e2eeCrypto')
      const detector = createReplayDetector(5)

      const envelope1 = await encryptE2eeMessage('Message 1', 1, 2)
      const envelope2 = await encryptE2eeMessage('Message 2', 1, 2)

      // First time seen: fresh
      expect(await detector.checkAndRecord(envelope1)).toBe(true)
      expect(await detector.checkAndRecord(envelope2)).toBe(true)

      // Second time seen: REPLAY ATTACK
      expect(await detector.checkAndRecord(envelope1)).toBe(false)
      expect(await detector.checkAndRecord(envelope2)).toBe(false)
    })

    it('scrubs plaintext chat caches and leaked private keys from storage', async () => {
      const { scrubPlaintextStorage } = await import('./e2eeCrypto')
      localStorage.setItem('msm:chat_cache:mailbox123', JSON.stringify([{ text: 'Leaked Plaintext' }]))
      localStorage.setItem('msm_e2ee_identity_42_priv', 'leaked-private-key')
      localStorage.setItem('msm_keep_this_unrelated_key', 'safe')

      sessionStorage.setItem('msm:chat_cache:session_leak', 'leak')
      sessionStorage.setItem('msm_e2ee_session_priv', 'secret')
      sessionStorage.setItem('keep_session_unrelated', 'ok')

      scrubPlaintextStorage()

      expect(localStorage.getItem('msm:chat_cache:mailbox123')).toBeNull()
      expect(localStorage.getItem('msm_e2ee_identity_42_priv')).toBeNull()
      expect(localStorage.getItem('msm_keep_this_unrelated_key')).toBe('safe')

      expect(sessionStorage.getItem('msm:chat_cache:session_leak')).toBeNull()
      expect(sessionStorage.getItem('msm_e2ee_session_priv')).toBeNull()
      expect(sessionStorage.getItem('keep_session_unrelated')).toBe('ok')

      localStorage.removeItem('msm_keep_this_unrelated_key')
      sessionStorage.removeItem('keep_session_unrelated')
    })

    it('rejects empty secret or key in rotation functions', async () => {
      const { rotateChannelKey, rotateTeamChannelKey } = await import('./e2eeCrypto')
      await expect(rotateChannelKey('')).rejects.toThrow()
      await expect(rotateChannelKey('   ')).rejects.toThrow()
      await expect(rotateTeamChannelKey(10, '')).rejects.toThrow()
      await expect(rotateTeamChannelKey(10, '   ')).rejects.toThrow()
    })
  })

  describe('Ratchet Encryption & Forward Secrecy (sv-e2ee-ratchet-v1:)', () => {
    it('encrypts and decrypts messages with sv-e2ee-ratchet-v1: envelope', async () => {
      const {
        E2eeRatchetSession,
        encryptRatchetMessage,
        decryptRatchetMessage,
        E2EE_RATCHET_ENVELOPE_SPEC,
      } = await import('./e2eeCrypto')

      const senderSession = await E2eeRatchetSession.create(10, 20, 'ratchet-salt-1')
      const receiverSession = await E2eeRatchetSession.create(10, 20, 'ratchet-salt-1')

      const secretText = 'Zukunftssichere Ratchet-Nachricht mit Forward Secrecy'
      const envelope = await encryptRatchetMessage(secretText, 10, 20, senderSession)

      expect(envelope.startsWith(E2EE_RATCHET_ENVELOPE_SPEC.currentPrefix)).toBe(true)
      expect(envelope).toContain('0.')

      // Receiver decrypts with their session
      const decrypted = await decryptRatchetMessage(envelope, 10, 20, receiverSession)
      expect(decrypted).toBe(secretText)

      senderSession.destroy()
      receiverSession.destroy()
    })

    it('rejects ratchet replay attacks when an epoch is reused', async () => {
      const {
        E2eeRatchetSession,
        encryptRatchetMessage,
        decryptRatchetMessage,
      } = await import('./e2eeCrypto')

      const senderSession = await E2eeRatchetSession.create(10, 20, 'ratchet-salt-replay')
      const receiverSession = await E2eeRatchetSession.create(10, 20, 'ratchet-salt-replay')

      const envelope = await encryptRatchetMessage('Einmalige Nachricht', 10, 20, senderSession)

      // First decryption succeeds and consumes epoch 0
      const decrypted = await decryptRatchetMessage(envelope, 10, 20, receiverSession)
      expect(decrypted).toBe('Einmalige Nachricht')

      // Second decryption of identical envelope MUST fail (replay attack detected!)
      await expect(
        decryptRatchetMessage(envelope, 10, 20, receiverSession)
      ).rejects.toThrow(/Replay-Attacke|bereits verbraucht/)

      senderSession.destroy()
      receiverSession.destroy()
    })

    it('rejects ratchet message when epoch in envelope is tampered (AAD session swap protection)', async () => {
      const {
        E2eeRatchetSession,
        encryptRatchetMessage,
        decryptRatchetMessage,
      } = await import('./e2eeCrypto')

      const senderSession = await E2eeRatchetSession.create(10, 20, 'salt-tamper')
      const receiverSession = await E2eeRatchetSession.create(10, 20, 'salt-tamper')

      const envelope = await encryptRatchetMessage('Geheimtext', 10, 20, senderSession)

      // Tamper: change epoch from 0 to 1 in envelope wire format
      const tamperedEnvelope = envelope.replace('sv-e2ee-ratchet-v1:0.', 'sv-e2ee-ratchet-v1:1.')

      // Decryption MUST fail due to AAD mismatch
      await expect(
        decryptRatchetMessage(tamperedEnvelope, 10, 20, receiverSession)
      ).rejects.toThrow()

      senderSession.destroy()
      receiverSession.destroy()
    })

    it('rejects ratchet message when user IDs are swapped (cross-user AAD binding)', async () => {
      const {
        E2eeRatchetSession,
        encryptRatchetMessage,
        decryptRatchetMessage,
      } = await import('./e2eeCrypto')

      const senderSession = await E2eeRatchetSession.create(10, 20, 'salt-user-swap')
      const eavesdropperSession = await E2eeRatchetSession.create(10, 99, 'salt-user-swap')

      const envelope = await encryptRatchetMessage('Streng vertraulich', 10, 20, senderSession)

      // Attacker (user 99) attempts to decrypt
      await expect(
        decryptRatchetMessage(envelope, 10, 99, eavesdropperSession)
      ).rejects.toThrow()

      senderSession.destroy()
      eavesdropperSession.destroy()
    })

    it('fast-forwards ratchet session with advanceToEpoch up to maxSkippedSteps', async () => {
      const { E2eeRatchetSession } = await import('./e2eeCrypto')
      const session = await E2eeRatchetSession.create(5, 15)

      expect(session.epoch).toBe(0)
      const target = await session.advanceToEpoch(5)
      expect(target.epoch).toBe(5)
      expect(target.messageKey).toBeDefined()
      expect(session.epoch).toBe(6)

      // Epochs 0 through 5 should now be marked as spent
      for (let ep = 0; ep <= 5; ep++) {
        expect(session.validateEpoch(ep).ok).toBe(false)
      }

      session.destroy()
    })
  })

  describe('Algorithm-Confusion & Key-Misuse Prevention in Public Key Validation', () => {
    it('rejects public keys with malicious or unapproved alg parameters', async () => {
      const { validatePublicKeyJwk, generateLocalE2eeKeyPair } = await import('./e2eeCrypto')
      const validPair = await generateLocalE2eeKeyPair()
      const baseJwk = JSON.parse(validPair.publicKeyJwk)

      // 1. alg: "none" attack
      const algNone = JSON.stringify({ ...baseJwk, alg: 'none' })
      const resNone = validatePublicKeyJwk(algNone)
      expect(resNone.valid).toBe(false)
      expect(resNone.error).toContain('Security violation')

      // 2. Signing algorithm confusion: alg: "RS256"
      const algRs256 = JSON.stringify({ ...baseJwk, alg: 'RS256' })
      const resRs256 = validatePublicKeyJwk(algRs256)
      expect(resRs256.valid).toBe(false)
      expect(resRs256.error).toContain('Security violation')

      // 3. Symmetric algorithm confusion: alg: "HS256"
      const algHs256 = JSON.stringify({ ...baseJwk, alg: 'HS256' })
      expect(validatePublicKeyJwk(algHs256).valid).toBe(false)

      // 4. Invalid key use: use: "sig"
      const useSig = JSON.stringify({ ...baseJwk, use: 'sig' })
      const resUse = validatePublicKeyJwk(useSig)
      expect(resUse.valid).toBe(false)
      expect(resUse.error).toContain('invalid key use')

      // 5. Forbidden signature key_ops
      const opsSig = JSON.stringify({ ...baseJwk, key_ops: ['sign', 'verify'] })
      const resOps = validatePublicKeyJwk(opsSig)
      expect(resOps.valid).toBe(false)
      expect(resOps.error).toContain('signature operations forbidden')

      // 6. Valid approval: alg: "RSA-OAEP" and use: "enc"
      const validCustom = JSON.stringify({ ...baseJwk, alg: 'RSA-OAEP', use: 'enc' })
      expect(validatePublicKeyJwk(validCustom).valid).toBe(true)
    }, 30_000)
  })
})


