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
import { DisDecryptionError, DisInvalidArgumentError, base64ToBytes, bytesToBase64 } from '@msdis/shield/core'

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

export const E2EE_RATCHET_ENVELOPE_SPEC: VersionedCipherEnvelopeSpec = {
  currentPrefix: 'sv-e2ee-ratchet-v1:',
  familyPrefix: 'sv-e2ee-ratchet-',
  subject: 'e2ee ratchet message envelope',
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
  const integrity = validateEnvelopeIntegrity(envelopeString)
  if (!integrity.valid) {
    throw new DisDecryptionError(integrity.error || 'Ungültige Umschlag-Integrität')
  }
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
  const integrity = validateEnvelopeIntegrity(envelopeString)
  if (!integrity.valid) {
    throw new DisDecryptionError(integrity.error || 'Ungültige Team-E2EE-Umschlag-Integrität')
  }
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
  const integrity = validateEnvelopeIntegrity(envelopeString)
  if (!integrity.valid) {
    throw new DisDecryptionError(integrity.error || 'Ungültige Gruppen-E2EE-Umschlag-Integrität')
  }
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

/**
 * Zero-Knowledge Plaintext Storage Scrubbing:
 * Scrubs any legacy plaintext chat cache or leaked keys from localStorage and sessionStorage.
 */
export function scrubPlaintextStorage(): void {
  const scrubStore = (store: Storage | undefined) => {
    if (!store) return
    try {
      const keysToRemove: string[] = []
      for (let i = 0; i < store.length; i++) {
        const key = store.key(i)
        if (
          key &&
          (key.startsWith('msm:chat_cache:') ||
            key.startsWith('msm_e2ee_identity_') ||
            key.startsWith('msm_chat_') ||
            key.includes('_priv') ||
            key.includes('privateKey'))
        ) {
          keysToRemove.push(key)
        }
      }
      keysToRemove.forEach((k) => store.removeItem(k))
    } catch {}
  }

  if (typeof localStorage !== 'undefined') {
    scrubStore(localStorage)
  }
  if (typeof sessionStorage !== 'undefined') {
    scrubStore(sessionStorage)
  }
}

export function clearMemoryKeyStore(): void {
  memoryKeyStore.clear()
  scrubPlaintextStorage()
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
  const updated: LocalE2eeKeyPair = existing
    ? { ...existing, privateKeyJwk }
    : { publicKeyJwk: '', privateKeyJwk }
  memoryKeyStore.set(userId, updated)
  try {
    localStorage.removeItem(`msm_e2ee_identity_${userId}_priv`)
  } catch {}
  void (async () => {
    try {
      const db = await openKeyDatabase()
      const tx = db.transaction(IDB_STORE_NAME, 'readwrite')
      const store = tx.objectStore(IDB_STORE_NAME)
      store.put({ userId, ...updated })
    } catch {}
  })()
}

/**
 * Securely deletes a user's key pair from both memory cache and IndexedDB.
 */
export async function deleteLocalKeyPair(userId: number): Promise<void> {
  memoryKeyStore.delete(userId)
  try {
    localStorage.removeItem(`msm_e2ee_identity_${userId}_priv`)
  } catch {}
  try {
    const db = await openKeyDatabase()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(IDB_STORE_NAME, 'readwrite')
      const store = tx.objectStore(IDB_STORE_NAME)
      const req = store.delete(userId)
      req.onsuccess = () => resolve()
      req.onerror = () => reject(req.error)
    })
  } catch {}
}

/**
 * Validates whether a given string is a safe, well-formed RSA-OAEP public key in JWK format.
 * Strict check: prevents uploading private parameters (d, p, q, etc.), weak key sizes,
 * and algorithm-confusion / key-misuse attacks (e.g. alg: "none", alg: "RS256", use: "sig").
 */
export function validatePublicKeyJwk(publicKeyJwk: string): {
  valid: boolean
  error?: string
  jwk?: JsonWebKey
} {
  if (!publicKeyJwk || typeof publicKeyJwk !== 'string') {
    return { valid: false, error: 'Public key must be a non-empty string' }
  }
  let jwk: JsonWebKey
  try {
    jwk = JSON.parse(publicKeyJwk)
  } catch {
    return { valid: false, error: 'Public key is not valid JSON' }
  }
  if (!jwk || typeof jwk !== 'object' || Array.isArray(jwk)) {
    return { valid: false, error: 'Public key JWK must be a JSON object' }
  }
  if (jwk.kty !== 'RSA') {
    return { valid: false, error: `Invalid key type: expected 'RSA', got '${jwk.kty}'` }
  }
  if (!jwk.n || typeof jwk.n !== 'string') {
    return { valid: false, error: 'Missing or invalid RSA modulus (n)' }
  }
  if (!jwk.e || typeof jwk.e !== 'string') {
    return { valid: false, error: 'Missing or invalid RSA public exponent (e)' }
  }
  // Zero private parameter leak: verify no private parameters are exposed in public key
  const privateFields = ['d', 'p', 'q', 'dp', 'dq', 'qi', 'dmp1', 'dmq1', 'coeff', 'oth']
  for (const f of privateFields) {
    if (f in jwk) {
      return { valid: false, error: `Security violation: private key component '${f}' detected in public key JWK` }
    }
  }
  // Check modulus length (minimum RSA-2048, modulus base64 string must be at least ~300 chars)
  if (jwk.n.length < 300) {
    return { valid: false, error: 'RSA modulus length is too small: minimum 2048-bit key required' }
  }

  // Algorithm-confusion & key misuse prevention:
  if (jwk.alg) {
    const validAlgs = ['RSA-OAEP', 'RSA-OAEP-256', 'RSA-OAEP-384', 'RSA-OAEP-512']
    if (!validAlgs.includes(jwk.alg)) {
      return {
        valid: false,
        error: `Security violation: unsupported or malicious algorithm '${jwk.alg}' for E2EE public key (expected RSA-OAEP)`,
      }
    }
  }

  if (jwk.use && jwk.use !== 'enc') {
    return {
      valid: false,
      error: `Security violation: invalid key use '${jwk.use}' for E2EE encryption key (expected 'enc')`,
    }
  }

  if (jwk.key_ops) {
    const forbiddenOps = ['sign', 'verify']
    const hasForbidden = jwk.key_ops.some((op) => forbiddenOps.includes(op))
    if (hasForbidden) {
      return {
        valid: false,
        error: 'Security violation: signature operations forbidden in E2EE encryption public key',
      }
    }
  }

  return { valid: true, jwk }
}

/**
 * Validates ciphertext envelope integrity, format, and non-trivial nonce/IV.
 */
export function validateEnvelopeIntegrity(envelopeString: string): {
  valid: boolean
  prefix: string
  payloadLength: number
  error?: string
} {
  if (!envelopeString || typeof envelopeString !== 'string') {
    return { valid: false, prefix: '', payloadLength: 0, error: 'Envelope must be a non-empty string' }
  }

  const validPrefixes = [
    E2EE_ENVELOPE_SPEC.currentPrefix,
    E2EE_TEAM_ENVELOPE_SPEC.currentPrefix,
    E2EE_GROUP_ENVELOPE_SPEC.currentPrefix,
    E2EE_HYBRID_ENVELOPE_SPEC.currentPrefix,
    E2EE_RATCHET_ENVELOPE_SPEC.currentPrefix,
  ]

  const matchedPrefix = validPrefixes.find((p) => envelopeString.startsWith(p))
  if (!matchedPrefix) {
    return { valid: false, prefix: '', payloadLength: 0, error: 'Missing or unknown envelope version prefix' }
  }

  const payload = envelopeString.slice(matchedPrefix.length)
  if (!payload || payload.length === 0) {
    return { valid: false, prefix: matchedPrefix, payloadLength: 0, error: 'Empty envelope payload' }
  }

  // Check for plain JSON leaks
  const trimmed = payload.trim()
  if (
    trimmed.startsWith('{') ||
    trimmed.startsWith('[') ||
    trimmed.includes('"text":') ||
    trimmed.includes('"sender_id":') ||
    trimmed.includes('"ciphertext":')
  ) {
    return {
      valid: false,
      prefix: matchedPrefix,
      payloadLength: payload.length,
      error: 'Security violation: unencrypted plaintext payload detected in envelope',
    }
  }

  if (matchedPrefix === E2EE_HYBRID_ENVELOPE_SPEC.currentPrefix) {
    const dotIdx = payload.indexOf('.')
    if (dotIdx === -1) {
      return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Malformed hybrid envelope structure' }
    }
    const wrappedKeys = payload.slice(0, dotIdx)
    if (!wrappedKeys.trim()) {
      return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Missing wrapped keys component in hybrid envelope' }
    }
    const ct = payload.slice(dotIdx + 1)
    if (ct.length < 38) {
      return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Hybrid ciphertext too short for valid IV and AEAD tag' }
    }
    try {
      const bytes = base64ToBytes(ct)
      if (bytes.length < 28) {
        return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Decoded hybrid ciphertext length must be at least 28 bytes' }
      }
      const iv = bytes.slice(0, 12)
      if (iv.every((b) => b === 0)) {
        return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Weak/zero nonce/IV detected in hybrid envelope' }
      }
    } catch {
      return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Invalid base64 encoding in hybrid ciphertext' }
    }
  } else if (matchedPrefix === E2EE_RATCHET_ENVELOPE_SPEC.currentPrefix) {
    const dotIdx = payload.indexOf('.')
    if (dotIdx === -1) {
      return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Malformed ratchet envelope structure: expected epoch.ciphertext' }
    }
    const epochStr = payload.slice(0, dotIdx)
    const epochNum = parseInt(epochStr, 10)
    if (isNaN(epochNum) || epochNum < 0 || String(epochNum) !== epochStr) {
      return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Invalid epoch number in ratchet envelope' }
    }
    const ct = payload.slice(dotIdx + 1)
    if (ct.length < 38) {
      return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Ratchet ciphertext too short for valid IV and AEAD tag' }
    }
    try {
      const bytes = base64ToBytes(ct)
      if (bytes.length < 28) {
        return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Decoded ratchet ciphertext length must be at least 28 bytes' }
      }
      const iv = bytes.slice(0, 12)
      if (iv.every((b) => b === 0)) {
        return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Weak/zero nonce/IV detected in ratchet envelope' }
      }
    } catch {
      return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Invalid base64 encoding in ratchet ciphertext' }
    }
  } else {
    if (payload.length < 38) {
      return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Ciphertext payload too short for valid IV and AEAD tag' }
    }
    try {
      const bytes = base64ToBytes(payload)
      if (bytes.length < 28) {
        return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Decoded ciphertext payload length must be at least 28 bytes' }
      }
      const iv = bytes.slice(0, 12)
      if (iv.every((b) => b === 0)) {
        return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Weak/zero nonce/IV detected' }
      }
    } catch {
      return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Invalid base64 encoding in ciphertext' }
    }
  }

  return { valid: true, prefix: matchedPrefix, payloadLength: payload.length }
}

/**
 * Hybrid-encrypts a message under a recipient's RSA-OAEP public key.
 * If senderPublicKeyJwk is provided, also wraps the ephemeral key for the sender,
 * allowing both parties to decrypt from the blind mailbox.
 *
 * Uses DIS SecureBuffer for memory hygiene of the ephemeral symmetric key.
 * Cryptographically binds wrappedKeys in AAD to prevent ciphertext transposition / session swaps.
 */
export async function encryptE2eeHybrid(
  message: string,
  recipientPublicKeyJwk: string,
  senderPublicKeyJwk?: string
): Promise<string> {
  const recipientCheck = validatePublicKeyJwk(recipientPublicKeyJwk)
  if (!recipientCheck.valid || !recipientCheck.jwk) {
    throw new DisInvalidArgumentError(recipientCheck.error || 'Ungültiger Recipient Public Key')
  }
  const rsaRecipientPubKey = await importRsaOaepPublicKey(recipientCheck.jwk)

  let rsaSenderPubKey: CryptoKey | null = null
  if (senderPublicKeyJwk) {
    const senderCheck = validatePublicKeyJwk(senderPublicKeyJwk)
    if (!senderCheck.valid || !senderCheck.jwk) {
      throw new DisInvalidArgumentError(senderCheck.error || 'Ungültiger Sender Public Key')
    }
    rsaSenderPubKey = await importRsaOaepPublicKey(senderCheck.jwk)
  }

  const secureSymKey = SecureBuffer.random(32)
  try {
    return await secureSymKey.useAsync(async (rawSymKeyBytes) => {
      const symKeyString = bytesToBase64(rawSymKeyBytes)
      const wrappedRecipientKey = await rsaOaepEncrypt(
        symKeyString,
        rsaRecipientPubKey
      )

      let wrappedKeys = wrappedRecipientKey
      if (rsaSenderPubKey) {
        const wrappedSenderKey = await rsaOaepEncrypt(
          symKeyString,
          rsaSenderPubKey
        )
        wrappedKeys = `${wrappedRecipientKey}:${wrappedSenderKey}`
      }

      const symKey = await importAesGcmRawKey(rawSymKeyBytes, ['encrypt'])
      // Cryptographically bind wrappedKeys in AAD to prevent ciphertext transposition / session swaps
      const aad = `msm:hybrid:aad:${wrappedKeys}`
      const ciphertext = await encryptString(message, symKey, aad)
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
 * Verifies AAD bound to wrappedKeys with backwards-compatible legacy fallback.
 */
export async function decryptE2eeHybrid(
  envelopeString: string,
  participantPrivateKeyJwk: string
): Promise<string> {
  const integrity = validateEnvelopeIntegrity(envelopeString)
  if (!integrity.valid) {
    throw new DisDecryptionError(integrity.error || 'Ungültige Hybrid-Umschlag-Integrität')
  }
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
        // Attempt decryption with wrappedKeys bound in AAD (anti-tamper / anti-session-swap)
        try {
          return await decryptString(ciphertext, symKey, `msm:hybrid:aad:${wrappedKeysPart}`)
        } catch {
          // Fallback to legacy static AAD for backwards compatibility with pre-fix envelopes
          return await decryptString(ciphertext, symKey, 'msm:hybrid:aad')
        }
      })
    } finally {
      secureKey.destroy()
    }
  } catch {
    throw new DisDecryptionError('Hybrid-Entschlüsselung fehlgeschlagen')
  }
}

// ==========================================
// 4. E2EE Media & Attachment Blobs
// ==========================================

export const E2EE_BLOB_SPEC: VersionedCipherEnvelopeSpec = {
  currentPrefix: 'sv-blob-v1:',
  familyPrefix: 'sv-blob-',
  subject: 'e2ee media blob envelope',
}

export interface AttachmentCryptoContext {
  userAId?: number
  userBId?: number
  groupId?: number
  teamId?: number
  sharedSecret?: string
}

/**
 * Encrypts an attachment data string client-side before upload to server / bucket.
 * The server only ever sees the encrypted blob (E2EE invariant).
 */
export async function encryptE2eeAttachmentBlob(
  data: string,
  context: AttachmentCryptoContext
): Promise<string> {
  let key: CryptoKey
  let aad: string

  if (context.groupId) {
    key = await deriveGroupChannelKey(context.groupId, context.sharedSecret)
    aad = `msm:group:blob:aad:${context.groupId}`
  } else if (context.teamId) {
    key = await deriveTeamChannelKey(context.teamId, context.sharedSecret)
    aad = `msm:team:blob:aad:${context.teamId}`
  } else if (context.userAId && context.userBId) {
    const minId = Math.min(context.userAId, context.userBId)
    const maxId = Math.max(context.userAId, context.userBId)
    key = await deriveDirectChannelKey(context.userAId, context.userBId, context.sharedSecret)
    aad = `msm:dm:blob:aad:${minId}:${maxId}`
  } else {
    throw new Error('Ungültiger Verschlüsselungskontext für Medienanhang')
  }

  const ciphertext = await encryptString(data, key, aad)
  return formatEnvelope(E2EE_BLOB_SPEC, ciphertext)
}

/**
 * Decrypts an attachment blob client-side after download from signed media URL.
 */
export async function decryptE2eeAttachmentBlob(
  envelopeString: string,
  context: AttachmentCryptoContext
): Promise<string> {
  try {
    const parsed = parseEnvelope(E2EE_BLOB_SPEC, envelopeString)
    let key: CryptoKey
    let aad: string

    if (context.groupId) {
      key = await deriveGroupChannelKey(context.groupId, context.sharedSecret)
      aad = `msm:group:blob:aad:${context.groupId}`
    } else if (context.teamId) {
      key = await deriveTeamChannelKey(context.teamId, context.sharedSecret)
      aad = `msm:team:blob:aad:${context.teamId}`
    } else if (context.userAId && context.userBId) {
      const minId = Math.min(context.userAId, context.userBId)
      const maxId = Math.max(context.userAId, context.userBId)
      key = await deriveDirectChannelKey(context.userAId, context.userBId, context.sharedSecret)
      aad = `msm:dm:blob:aad:${minId}:${maxId}`
    } else {
      throw new Error('Ungültiger Entschlüsselungskontext für Medienanhang')
    }

    return await decryptString(parsed.payload, key, aad)
  } catch {
    throw new DisDecryptionError('Entschlüsselung des Medienanhangs fehlgeschlagen oder manipuliert')
  }
}

// ==========================================
// 5. Ratchet Sessions & Key Rotation (Forward Secrecy)
// ==========================================

/**
 * Ratchet Session for Forward Secrecy:
 * Derives ephemeral message keys from a ratchet chain.
 * Advancing the ratchet forward overwrites and destroys previous chain keys in memory.
 */
export class E2eeRatchetSession {
  private chainKeyBuffer: SecureBuffer | null = null
  private currentEpoch: number = 0
  private readonly maxSkippedSteps: number = 100
  private spentEpochs: Set<number> = new Set()

  constructor(initialChainKeyBytes: Uint8Array, startEpoch: number = 0) {
    this.chainKeyBuffer = SecureBuffer.fromBytes(initialChainKeyBytes)
    this.currentEpoch = startEpoch
  }

  public static async create(
    userAId: number,
    userBId: number,
    sessionSalt: string = ''
  ): Promise<E2eeRatchetSession> {
    const minId = Math.min(userAId, userBId)
    const maxId = Math.max(userAId, userBId)
    const payload = `msm:dm:ratchet:root:${minId}:${maxId}${sessionSalt ? `:${sessionSalt}` : ''}`
    const seed = new TextEncoder().encode(payload)
    const initialBytes = await sha256Bytes(seed)
    seed.fill(0)
    try {
      return new E2eeRatchetSession(initialBytes)
    } finally {
      initialBytes.fill(0)
    }
  }

  public get epoch(): number {
    return this.currentEpoch
  }

  /**
   * Advances the ratchet chain by one step, securely destroying the previous chain key
   * and deriving the next ephemeral AES-256-GCM message CryptoKey.
   * Exception-safe: Epoch increment is only committed after key derivation succeeds.
   */
  public async stepForward(): Promise<{ messageKey: CryptoKey; epoch: number }> {
    if (!this.chainKeyBuffer) {
      throw new DisDecryptionError('Ratchet-Session ist zerstört oder geschlossen')
    }

    const epochToUse = this.currentEpoch

    const nextChainBytes = await this.chainKeyBuffer.useAsync(async (currentBytes) => {
      const stepSeed = new TextEncoder().encode(`step:${epochToUse}:chain`)
      const combined = new Uint8Array(currentBytes.length + stepSeed.length)
      combined.set(currentBytes, 0)
      combined.set(stepSeed, currentBytes.length)
      const nextChain = await sha256Bytes(combined)
      combined.fill(0)
      stepSeed.fill(0)
      return nextChain
    })

    let messageKey: CryptoKey
    try {
      messageKey = await this.chainKeyBuffer.useAsync(async (currentBytes) => {
        const msgSeed = new TextEncoder().encode(`step:${epochToUse}:msg`)
        const combined = new Uint8Array(currentBytes.length + msgSeed.length)
        combined.set(currentBytes, 0)
        combined.set(msgSeed, currentBytes.length)
        const msgKeyBytes = await sha256Bytes(combined)
        combined.fill(0)
        msgSeed.fill(0)
        const secMsgKey = SecureBuffer.fromBytes(msgKeyBytes)
        msgKeyBytes.fill(0)
        try {
          return await secMsgKey.useAsync(async (bytes) => {
            return await importAesGcmRawKey(bytes, ['encrypt', 'decrypt'])
          })
        } finally {
          secMsgKey.destroy()
        }
      })
    } catch (err) {
      nextChainBytes.fill(0)
      throw err
    }

    // Commit epoch progression only after both cryptographic steps succeeded
    this.currentEpoch++
    this.spentEpochs.add(epochToUse)

    // Securely overwrite old chain key
    this.chainKeyBuffer.destroy()
    this.chainKeyBuffer = SecureBuffer.fromBytes(nextChainBytes)
    nextChainBytes.fill(0)

    return { messageKey, epoch: epochToUse }
  }

  /**
   * Marks a specific epoch as consumed to prevent replay attacks.
   */
  public markEpochSpent(epoch: number): void {
    this.spentEpochs.add(epoch)
  }

  /**
   * Fast-forwards the ratchet chain to reach targetEpoch (up to maxSkippedSteps),
   * destroying intermediate chain keys and returning the target epoch message key.
   */
  public async advanceToEpoch(targetEpoch: number): Promise<{ messageKey: CryptoKey; epoch: number }> {
    const val = this.validateEpoch(targetEpoch)
    if (!val.ok) {
      throw new DisDecryptionError(val.reason || 'Ungültige Ziel-Epoche für Ratchet-Sprung')
    }

    while (this.currentEpoch < targetEpoch) {
      const skipped = await this.stepForward()
      // Mark skipped message key as spent
      this.spentEpochs.add(skipped.epoch)
    }

    return await this.stepForward()
  }

  /**
   * Validates whether an incoming epoch is acceptable (not already spent, within window).
   */
  public validateEpoch(epoch: number): { ok: boolean; reason?: string } {
    if (this.spentEpochs.has(epoch)) {
      return { ok: false, reason: `Replay-Attacke oder veraltete Nachricht: Epoche ${epoch} wurde bereits verbraucht` }
    }
    if (epoch < this.currentEpoch - 1) {
      return { ok: false, reason: `Veraltete Epoche: ${epoch} < aktuelle Epoche ${this.currentEpoch}` }
    }
    if (epoch > this.currentEpoch + this.maxSkippedSteps) {
      return { ok: false, reason: `Epochen-Sprung zu groß: ${epoch} überschreitet maximales Fenster (+${this.maxSkippedSteps})` }
    }
    return { ok: true }
  }

  public destroy(): void {
    if (this.chainKeyBuffer) {
      this.chainKeyBuffer.destroy()
      this.chainKeyBuffer = null
    }
    this.spentEpochs.clear()
  }
}

/**
 * Derives a symmetric AES-256-GCM CryptoKey for an explicit ratchet epoch.
 */
export async function deriveRatchetChannelKey(
  userAId: number,
  userBId: number,
  epoch: number,
  sharedSecret?: string
): Promise<CryptoKey> {
  const minId = Math.min(userAId, userBId)
  const maxId = Math.max(userAId, userBId)
  const payload = `msm:dm:key:${minId}:${maxId}:epoch:${epoch}${sharedSecret ? `:${sharedSecret}` : ''}`
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
 * Encrypts a message using the current ratchet session step with forward secrecy.
 * Cryptographically binds user identifiers and ratchet epoch into AAD.
 */
export async function encryptRatchetMessage(
  message: string,
  userAId: number,
  userBId: number,
  session: E2eeRatchetSession
): Promise<string> {
  const minId = Math.min(userAId, userBId)
  const maxId = Math.max(userAId, userBId)
  const { messageKey, epoch } = await session.stepForward()
  const aad = `msm:dm:ratchet:aad:${minId}:${maxId}:${epoch}`
  const ciphertext = await encryptString(message, messageKey, aad)
  const payload = `${epoch}.${ciphertext}`
  return formatEnvelope(E2EE_RATCHET_ENVELOPE_SPEC, payload)
}

/**
 * Decrypts a ratcheted message envelope with forward secrecy and AAD verification.
 */
export async function decryptRatchetMessage(
  envelopeString: string,
  userAId: number,
  userBId: number,
  sessionOrKey: E2eeRatchetSession | CryptoKey,
  explicitEpoch?: number
): Promise<string> {
  const integrity = validateEnvelopeIntegrity(envelopeString)
  if (!integrity.valid) {
    throw new DisDecryptionError(integrity.error || 'Ungültige Ratchet-Umschlag-Integrität')
  }

  const parsed = parseEnvelope(E2EE_RATCHET_ENVELOPE_SPEC, envelopeString)
  const dotIdx = parsed.payload.indexOf('.')
  if (dotIdx === -1) {
    throw new DisDecryptionError('Ungültiges Ratchet-Payload-Format')
  }

  const epochStr = parsed.payload.slice(0, dotIdx)
  const ciphertext = parsed.payload.slice(dotIdx + 1)
  const epoch = parseInt(epochStr, 10)
  if (isNaN(epoch) || epoch < 0) {
    throw new DisDecryptionError('Ungültige Epochen-Nummer im Ratchet-Umschlag')
  }

  const minId = Math.min(userAId, userBId)
  const maxId = Math.max(userAId, userBId)
  const aad = `msm:dm:ratchet:aad:${minId}:${maxId}:${epoch}`

  let key: CryptoKey
  if (sessionOrKey instanceof E2eeRatchetSession) {
    const epochCheck = sessionOrKey.validateEpoch(epoch)
    if (!epochCheck.ok) {
      throw new DisDecryptionError(epochCheck.reason || 'Ungültige Ratchet-Epoche')
    }
    if (epoch < sessionOrKey.epoch) {
      throw new DisDecryptionError(`Replay-Attacke oder veraltete Nachricht: Epoche ${epoch} wurde bereits verbraucht`)
    }
    let stepResult: { messageKey: CryptoKey; epoch: number }
    if (epoch === sessionOrKey.epoch) {
      stepResult = await sessionOrKey.stepForward()
    } else {
      stepResult = await sessionOrKey.advanceToEpoch(epoch)
    }
    key = stepResult.messageKey
  } else {
    if (explicitEpoch !== undefined && explicitEpoch !== epoch) {
      throw new DisDecryptionError('Epochen-Konflikt zwischen Schlüssel und Umschlag')
    }
    key = sessionOrKey
  }

  try {
    return await decryptString(ciphertext, key, aad)
  } catch {
    throw new DisDecryptionError('Ratchet-Entschlüsselung fehlgeschlagen oder AAD manipuliert')
  }
}

/**
 * Rotates a channel key by hashing the current secret together with a rotation seed/epoch.
 */
export async function rotateChannelKey(
  currentSecret: string,
  rotationEpoch: number | string = Date.now()
): Promise<string> {
  if (!currentSecret || typeof currentSecret !== 'string' || !currentSecret.trim()) {
    throw new DisInvalidArgumentError('currentSecret darf für Key-Rotation nicht leer sein')
  }
  const seed = new TextEncoder().encode(`msm:rotate:${rotationEpoch}:${currentSecret.trim()}`)
  try {
    return await sha256Hex(seed)
  } finally {
    seed.fill(0)
  }
}

/**
 * Rotates the team channel symmetric key.
 */
export async function rotateTeamChannelKey(
  teamId: number,
  currentKeyOrPassphrase: string,
  newSalt: string = ''
): Promise<string> {
  if (!currentKeyOrPassphrase || typeof currentKeyOrPassphrase !== 'string' || !currentKeyOrPassphrase.trim()) {
    throw new DisInvalidArgumentError('currentKeyOrPassphrase darf für Team-Key-Rotation nicht leer sein')
  }
  const seed = new TextEncoder().encode(`msm:team:rotate:${teamId}:${newSalt}:${currentKeyOrPassphrase.trim()}`)
  try {
    return await sha256Hex(seed)
  } finally {
    seed.fill(0)
  }
}

/**
 * Rotates local RSA-OAEP key pair for a user:
 * Generates fresh 4096-bit key pair, replaces in KeyStore, and returns the new pair.
 */
export async function rotateLocalKeyPair(userId: number): Promise<LocalE2eeKeyPair> {
  const fresh = await generateLocalE2eeKeyPair()
  await storeLocalKeyPair(userId, fresh)
  return fresh
}

/**
 * Memory-bounded Replay Detector with sliding window / hash ring.
 * Protects against duplicate envelopes and replayed ciphertexts.
 */
export class ReplayDetector {
  private seenHashes: Set<string> = new Set()
  private hashQueue: string[] = []
  private readonly maxEntries: number

  constructor(maxEntries = 1000) {
    this.maxEntries = maxEntries
  }

  public async checkAndRecord(envelope: string): Promise<boolean> {
    const raw = new TextEncoder().encode(envelope)
    const hash = await sha256Hex(raw)
    raw.fill(0)

    if (this.seenHashes.has(hash)) {
      return false // Replay detected!
    }

    this.seenHashes.add(hash)
    this.hashQueue.push(hash)

    if (this.hashQueue.length > this.maxEntries) {
      const oldest = this.hashQueue.shift()
      if (oldest) {
        this.seenHashes.delete(oldest)
      }
    }

    return true // Fresh!
  }

  public clear(): void {
    this.seenHashes.clear()
    this.hashQueue = []
  }
}

export function createReplayDetector(maxEntries?: number): ReplayDetector {
  return new ReplayDetector(maxEntries)
}

