/**
 * E2EE Cryptographic Service via @msdis/shield
 *
 * Implements Zero-Knowledge End-to-End Encryption for:
 * 1. Direct 1:1 Chat between users (Deterministic Blind Mailboxes + AES-256-GCM Channel Keys)
 * 2. Team Chat for Server/Project Teams (Team Blind Mailboxes + AES-256-GCM Team Keys)
 * 3. Asymmetric Hybrid Key Exchange (RSA-OAEP-4096 / AES-256-GCM using public keys stored on user profiles)
 *
 * Invariants:
 * - The server acts solely as a blind relay mailbox and never learns message plaintext,
 *   participant bindings, or channel keys.
 * - Versioned envelopes: 'sv-e2ee-v1:' for direct messages, 'sv-e2ee-team-v1:' for team messages,
 *   and 'sv-e2ee-hybrid-v1:' for hybrid envelopes.
 * - Follows DIS rule: uses subtle() from @msdis/shield/core, never raw crypto.subtle.
 * - Collapses all decryption failures into DisDecryptionError for AEAD oracle resistance.
 */

import { encryptString, decryptString, importAesGcmRawKey } from '@msdis/shield/aead'
import { sha256Bytes, sha256Hex } from '@msdis/shield/integrity'
import { formatEnvelope, parseEnvelope, type VersionedCipherEnvelopeSpec } from '@msdis/shield/format-versioning'
import {
  generateRsaOaepKeyPair,
  exportJwk,
  importRsaOaepPublicKey,
  importRsaOaepPrivateKey,
  rsaOaepEncrypt,
  rsaOaepDecrypt,
} from '@msdis/shield/asymmetric'
import { SecureBuffer } from '@msdis/shield/secure-memory'
import { DisDecryptionError, base64ToBytes, bytesToBase64 } from '@msdis/shield/core'

export const E2EE_ENVELOPE_SPEC: VersionedCipherEnvelopeSpec = {
  currentPrefix: 'sv-e2ee-v1:',
  familyPrefix: 'sv-e2ee-',
  subject: 'e2ee direct chat envelope',
}

export const E2EE_TEAM_ENVELOPE_SPEC: VersionedCipherEnvelopeSpec = {
  currentPrefix: 'sv-e2ee-team-v1:',
  familyPrefix: 'sv-e2ee-team-',
  subject: 'e2ee team chat envelope',
}

export const E2EE_GROUP_ENVELOPE_SPEC: VersionedCipherEnvelopeSpec = {
  currentPrefix: 'sv-e2ee-group-v1:',
  familyPrefix: 'sv-e2ee-group-',
  subject: 'e2ee group chat envelope',
}

export const E2EE_HYBRID_ENVELOPE_SPEC: VersionedCipherEnvelopeSpec = {
  currentPrefix: 'sv-e2ee-hybrid-v1:',
  familyPrefix: 'sv-e2ee-hybrid-',
  subject: 'e2ee hybrid envelope',
}

// ==========================================
// 1. Direct 1:1 Chat E2EE
// ==========================================

/**
 * Derives the deterministic blind mailbox identifier for two participants.
 * Zero-Knowledge: This mailbox ID contains no user identifiers or relational data on the wire.
 */
export async function deriveBlindMailboxId(userAId: number, userBId: number, salt: string = ''): Promise<string> {
  const minId = Math.min(userAId, userBId)
  const maxId = Math.max(userAId, userBId)
  const payload = `msm:dm:${minId}:${maxId}${salt ? `:${salt}` : ''}`
  const seed = new TextEncoder().encode(payload)
  try {
    return await sha256Hex(seed)
  } finally {
    seed.fill(0)
  }
}

/**
 * Derives a symmetric AES-256-GCM CryptoKey for the direct channel between two users.
 */
export async function deriveDirectChannelKey(
  userAId: number,
  userBId: number,
  sharedSecret?: string
): Promise<CryptoKey> {
  const minId = Math.min(userAId, userBId)
  const maxId = Math.max(userAId, userBId)
  const payload = `msm:dm:key:${minId}:${maxId}${sharedSecret ? `:${sharedSecret}` : ''}`
  const seed = new TextEncoder().encode(payload)
  const keyBytes = await sha256Bytes(seed)
  seed.fill(0)
  const secureKey = SecureBuffer.fromBytes(keyBytes)
  keyBytes.fill(0)
  try {
    return await secureKey.useAsync(async (bytes) => {
      return await importAesGcmRawKey(bytes, ['encrypt', 'decrypt'])
    })
  } finally {
    secureKey.destroy()
  }
}

/**
 * Encrypts a message using DIS AES-256-GCM and wraps it in a versioned direct envelope.
 */
export async function encryptE2eeMessage(
  message: string,
  userAId: number,
  userBId: number,
  sharedSecret?: string
): Promise<string> {
  const minId = Math.min(userAId, userBId)
  const maxId = Math.max(userAId, userBId)
  const aad = `msm:dm:aad:${minId}:${maxId}`
  const key = await deriveDirectChannelKey(userAId, userBId, sharedSecret)
  const ciphertext = await encryptString(message, key, aad)
  return formatEnvelope(E2EE_ENVELOPE_SPEC, ciphertext)
}

/**
 * Decrypts a versioned DIS envelope for the channel between two users.
 */
export async function decryptE2eeMessage(
  envelopeString: string,
  userAId: number,
  userBId: number,
  sharedSecret?: string
): Promise<string> {
  try {
    const parsed = parseEnvelope(E2EE_ENVELOPE_SPEC, envelopeString)
    const minId = Math.min(userAId, userBId)
    const maxId = Math.max(userAId, userBId)
    const aad = `msm:dm:aad:${minId}:${maxId}`
    const key = await deriveDirectChannelKey(userAId, userBId, sharedSecret)
    return await decryptString(parsed.payload, key, aad)
  } catch {
    throw new DisDecryptionError('E2EE-Entschlüsselung fehlgeschlagen oder manipuliert')
  }
}

// ==========================================
// 2. Team Chat E2EE (Pillar 4)
// ==========================================

/**
 * Derives the deterministic blind mailbox identifier for a team chat channel.
 */
export async function deriveTeamBlindMailboxId(teamId: number, teamSalt: string = ''): Promise<string> {
  const payload = `msm:team:${teamId}${teamSalt ? `:${teamSalt}` : ''}`
  const seed = new TextEncoder().encode(payload)
  try {
    return await sha256Hex(seed)
  } finally {
    seed.fill(0)
  }
}

/**
 * Derives the deterministic blind mailbox identifier for a chat group / community.
 */
export async function deriveGroupBlindMailboxId(groupId: number, groupSalt: string = ''): Promise<string> {
  const payload = `msm:group:${groupId}${groupSalt ? `:${groupSalt}` : ''}`
  const seed = new TextEncoder().encode(payload)
  try {
    return await sha256Hex(seed)
  } finally {
    seed.fill(0)
  }
}


/**
 * Derives a symmetric AES-256-GCM CryptoKey for the team channel.
 */
export async function deriveTeamChannelKey(teamId: number, teamPassphraseOrKey?: string): Promise<CryptoKey> {
  const payload = `msm:team:key:${teamId}${teamPassphraseOrKey ? `:${teamPassphraseOrKey}` : ''}`
  const seed = new TextEncoder().encode(payload)
  const keyBytes = await sha256Bytes(seed)
  seed.fill(0)
  const secureKey = SecureBuffer.fromBytes(keyBytes)
  keyBytes.fill(0)
  try {
    return await secureKey.useAsync(async (bytes) => {
      return await importAesGcmRawKey(bytes, ['encrypt', 'decrypt'])
    })
  } finally {
    secureKey.destroy()
  }
}

/**
 * Encrypts a team message using DIS AES-256-GCM with versioned team envelope.
 */
export async function encryptTeamE2eeMessage(
  message: string,
  teamId: number,
  teamPassphraseOrKey?: string
): Promise<string> {
  const aad = `msm:team:aad:${teamId}`
  const key = await deriveTeamChannelKey(teamId, teamPassphraseOrKey)
  const ciphertext = await encryptString(message, key, aad)
  return formatEnvelope(E2EE_TEAM_ENVELOPE_SPEC, ciphertext)
}

/**
 * Decrypts a versioned DIS envelope for a team channel.
 */
export async function decryptTeamE2eeMessage(
  envelopeString: string,
  teamId: number,
  teamPassphraseOrKey?: string
): Promise<string> {
  try {
    const parsed = parseEnvelope(E2EE_TEAM_ENVELOPE_SPEC, envelopeString)
    const aad = `msm:team:aad:${teamId}`
    const key = await deriveTeamChannelKey(teamId, teamPassphraseOrKey)
    return await decryptString(parsed.payload, key, aad)
  } catch {
    throw new DisDecryptionError('Team-E2EE-Entschlüsselung fehlgeschlagen')
  }
}

/**
 * Derives a symmetric AES-256-GCM CryptoKey for a chat group / community.
 */
export async function deriveGroupChannelKey(groupId: number, groupPassphraseOrKey?: string): Promise<CryptoKey> {
  const payload = `msm:group:key:${groupId}${groupPassphraseOrKey ? `:${groupPassphraseOrKey}` : ''}`
  const seed = new TextEncoder().encode(payload)
  const keyBytes = await sha256Bytes(seed)
  seed.fill(0)
  const secureKey = SecureBuffer.fromBytes(keyBytes)
  keyBytes.fill(0)
  try {
    return await secureKey.useAsync(async (bytes) => {
      return await importAesGcmRawKey(bytes, ['encrypt', 'decrypt'])
    })
  } finally {
    secureKey.destroy()
  }
}

/**
 * Encrypts a chat group message using DIS AES-256-GCM.
 */
export async function encryptGroupE2eeMessage(
  message: string,
  groupId: number,
  groupPassphraseOrKey?: string
): Promise<string> {
  const aad = `msm:group:aad:${groupId}`
  const key = await deriveGroupChannelKey(groupId, groupPassphraseOrKey)
  const ciphertext = await encryptString(message, key, aad)
  return formatEnvelope(E2EE_TEAM_ENVELOPE_SPEC, ciphertext)
}

/**
 * Decrypts a chat group message using DIS AES-256-GCM.
 */
export async function decryptGroupE2eeMessage(
  envelopeString: string,
  groupId: number,
  groupPassphraseOrKey?: string
): Promise<string> {
  try {
    const spec = envelopeString.startsWith('sv-e2ee-group-')
      ? E2EE_GROUP_ENVELOPE_SPEC
      : E2EE_TEAM_ENVELOPE_SPEC
    const parsed = parseEnvelope(spec, envelopeString)
    const aad = `msm:group:aad:${groupId}`
    const key = await deriveGroupChannelKey(groupId, groupPassphraseOrKey)
    return await decryptString(parsed.payload, key, aad)
  } catch {
    throw new DisDecryptionError('Gruppen-E2EE-Entschlüsselung fehlgeschlagen')
  }
}

// ==========================================
// 3. Asymmetric Hybrid Key Exchange (RSA-OAEP)
// ==========================================

export interface LocalE2eeKeyPair {
  publicKeyJwk: string
  privateKeyJwk: string
}

/**
 * Generates an RSA-OAEP 4096-bit key pair for asymmetric E2EE and exports as JWK strings.
 */
export async function generateLocalE2eeKeyPair(): Promise<LocalE2eeKeyPair> {
  const keyPair = await generateRsaOaepKeyPair()
  const pubJwk = await exportJwk(keyPair.publicKey)
  const privJwk = await exportJwk(keyPair.privateKey)
  return {
    publicKeyJwk: JSON.stringify(pubJwk),
    privateKeyJwk: JSON.stringify(privJwk),
  }
}

// Memory-hygiene KeyStore (in-memory cache backed by IndexedDB)
const memoryKeyStore = new Map<number, LocalE2eeKeyPair>()
const IDB_DB_NAME = 'msm_e2ee_keystore'
const IDB_STORE_NAME = 'keys'

function openKeyDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      return reject(new Error('IndexedDB not available'))
    }
    const req = indexedDB.open(IDB_DB_NAME, 1)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(IDB_STORE_NAME)) {
        db.createObjectStore(IDB_STORE_NAME, { keyPath: 'userId' })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

export async function storeLocalKeyPair(userId: number, keyPair: LocalE2eeKeyPair): Promise<void> {
  memoryKeyStore.set(userId, keyPair)
  try {
    localStorage.removeItem(`msm_e2ee_identity_${userId}_priv`)
  } catch {}

  try {
    const db = await openKeyDatabase()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(IDB_STORE_NAME, 'readwrite')
      const store = tx.objectStore(IDB_STORE_NAME)
      const req = store.put({ userId, ...keyPair })
      req.onsuccess = () => resolve()
      req.onerror = () => reject(req.error)
    })
  } catch {
    // Non-fatal fallback to in-memory store
  }
}

export async function getLocalKeyPair(userId: number): Promise<LocalE2eeKeyPair | null> {
  const cached = memoryKeyStore.get(userId)
  if (cached) return cached

  try {
    const db = await openKeyDatabase()
    const result = await new Promise<LocalE2eeKeyPair | null>((resolve, reject) => {
      const tx = db.transaction(IDB_STORE_NAME, 'readonly')
      const store = tx.objectStore(IDB_STORE_NAME)
      const req = store.get(userId)
      req.onsuccess = () => {
        if (req.result) {
          resolve({
            publicKeyJwk: req.result.publicKeyJwk,
            privateKeyJwk: req.result.privateKeyJwk,
          })
        } else {
          resolve(null)
        }
      }
      req.onerror = () => reject(req.error)
    })
    if (result) {
      memoryKeyStore.set(userId, result)
      return result
    }
  } catch {
    // IndexedDB unavailable
  }

  return null
}

export async function getOrGenerateLocalKeyPair(userId: number): Promise<LocalE2eeKeyPair> {
  const existing = await getLocalKeyPair(userId)
  if (existing) return existing

  const fresh = await generateLocalE2eeKeyPair()
  await storeLocalKeyPair(userId, fresh)
  return fresh
}

export function clearMemoryKeyStore(): void {
  memoryKeyStore.clear()
}

// Backward compatibility helpers (no plain private keys in localStorage)
export function getStoredLocalPrivateKey(userId: number): string | null {
  const cached = memoryKeyStore.get(userId)
  if (cached) return cached.privateKeyJwk
  try {
    const legacy = localStorage.getItem(`msm_e2ee_identity_${userId}_priv`)
    if (legacy) {
      localStorage.removeItem(`msm_e2ee_identity_${userId}_priv`)
      storeLocalPrivateKey(userId, legacy)
      return legacy
    }
  } catch {}
  return null
}

export function storeLocalPrivateKey(userId: number, privateKeyJwk: string): void {
  const existing = memoryKeyStore.get(userId)
  if (existing) {
    memoryKeyStore.set(userId, { ...existing, privateKeyJwk })
  } else {
    memoryKeyStore.set(userId, { publicKeyJwk: '', privateKeyJwk })
  }
  try {
    localStorage.removeItem(`msm_e2ee_identity_${userId}_priv`)
  } catch {}
}

/**
 * Hybrid-encrypts a message under a recipient's RSA-OAEP public key.
 * If senderPublicKeyJwk is provided, also wraps the ephemeral key for the sender,
 * allowing both parties to decrypt from the blind mailbox.
 *
 * Uses DIS SecureBuffer for memory hygiene of the ephemeral symmetric key.
 */
export async function encryptE2eeHybrid(
  message: string,
  recipientPublicKeyJwk: string,
  senderPublicKeyJwk?: string
): Promise<string> {
  const pubKeyObj: JsonWebKey = JSON.parse(recipientPublicKeyJwk)
  const rsaRecipientPubKey = await importRsaOaepPublicKey(pubKeyObj)

  const secureSymKey = SecureBuffer.random(32)
  try {
    return await secureSymKey.useAsync(async (rawSymKeyBytes) => {
      const symKeyString = bytesToBase64(rawSymKeyBytes)
      const wrappedRecipientKey = await rsaOaepEncrypt(
        symKeyString,
        rsaRecipientPubKey
      )

      let wrappedKeys = wrappedRecipientKey
      if (senderPublicKeyJwk) {
        const senderPubKeyObj: JsonWebKey = JSON.parse(senderPublicKeyJwk)
        const rsaSenderPubKey = await importRsaOaepPublicKey(senderPubKeyObj)
        const wrappedSenderKey = await rsaOaepEncrypt(
          symKeyString,
          rsaSenderPubKey
        )
        wrappedKeys = `${wrappedRecipientKey}:${wrappedSenderKey}`
      }

      const symKey = await importAesGcmRawKey(rawSymKeyBytes, ['encrypt'])
      const ciphertext = await encryptString(message, symKey, 'msm:hybrid:aad')
      const payload = `${wrappedKeys}.${ciphertext}`
      return formatEnvelope(E2EE_HYBRID_ENVELOPE_SPEC, payload)
    })
  } finally {
    secureSymKey.destroy()
  }
}

/**
 * Hybrid-decrypts a message using a participant's RSA-OAEP private key.
 * Supports both single-key envelopes and dual-wrapped (recipient:sender) envelopes.
 * Protects ephemeral symmetric key in DIS SecureBuffer before destroying.
 */
export async function decryptE2eeHybrid(
  envelopeString: string,
  participantPrivateKeyJwk: string
): Promise<string> {
  try {
    const parsed = parseEnvelope(E2EE_HYBRID_ENVELOPE_SPEC, envelopeString)
    const dotIdx = parsed.payload.indexOf('.')
    if (dotIdx === -1) {
      throw new DisDecryptionError('Ungültiges Hybrid-Payload-Format')
    }
    const wrappedKeysPart = parsed.payload.slice(0, dotIdx)
    const ciphertext = parsed.payload.slice(dotIdx + 1)

    const keyList = wrappedKeysPart.split(':')
    const privKeyObj: JsonWebKey = JSON.parse(participantPrivateKeyJwk)
    const rsaPrivKey = await importRsaOaepPrivateKey(privKeyObj)

    let rawBytes: Uint8Array | null = null
    for (const wrappedKey of keyList) {
      try {
        const rawKeyPlain = await rsaOaepDecrypt(wrappedKey, rsaPrivKey)
        if (rawKeyPlain.startsWith('[')) {
          // Backward-compatibility: JSON array format
          rawBytes = new Uint8Array(JSON.parse(rawKeyPlain))
        } else {
          // Standard DIS format: base64 encoded raw key bytes
          rawBytes = base64ToBytes(rawKeyPlain)
        }
        break
      } catch {
        // Try next key if dual-wrapped
      }
    }

    if (!rawBytes) {
      throw new DisDecryptionError('Kein passender RSA-Schlüssel im Hybrid-Umschlag')
    }

    const secureKey = SecureBuffer.fromBytes(rawBytes)
    rawBytes.fill(0)
    try {
      return await secureKey.useAsync(async (keyBytes) => {
        const symKey = await importAesGcmRawKey(keyBytes, ['decrypt'])
        return await decryptString(ciphertext, symKey, 'msm:hybrid:aad')
      })
    } finally {
      secureKey.destroy()
    }
  } catch {
    throw new DisDecryptionError('Hybrid-Entschlüsselung fehlgeschlagen')
  }
}
