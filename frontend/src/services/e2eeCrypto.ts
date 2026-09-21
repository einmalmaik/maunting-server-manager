/**
 * Das gemeinsame Unterfutter der E2EE: Mailbox-Kennungen, der Hybridumschlag
 * und die Schlüsselablage. Die Verfahren selbst stehen woanders.
 *
 * Diese Datei trug bis 09/2026 fünf Verschlüsselungsverfahren nebeneinander,
 * und zwei davon hielten ihr Versprechen nicht: `deriveDirectChannelKey` bildete
 * `sha256("msm:dm:key:<min>:<max>")`, `deriveGroupChannelKey` bildete
 * `sha256("msm:group:key:<id>")`. Beide Zutaten stehen in der Datenbank — das
 * Backend konnte jede so verschlüsselte Nachricht selbst öffnen. Dazu kamen ein
 * Team-Verfahren ohne Aufrufer, ein „Ratchet", der nur eine SHA-256-Kette ohne
 * Diffie-Hellman-Schritt war, und ein Medienverfahren auf derselben ableitbaren
 * Grundlage. Alles davon ist gelöscht, auch im Lesepfad: was man nicht mehr
 * entschlüsseln kann, kann man auch nicht versehentlich wieder verschlüsseln.
 *
 * Geblieben ist, was mehrere Verfahren gemeinsam brauchen:
 *
 * - **Mailbox-Kennungen.** `sha256("msm:dm:<min>:<max>")` benennt das Postfach.
 *   Das ist Absicht und keine Schwäche: die Kennung soll auf beiden Geräten
 *   gleich herauskommen, ohne dass jemand sie verabredet. Sie verschlüsselt
 *   nichts.
 * - **Der Hybridumschlag** (`sv-e2ee-hybrid-v1:`, RSA-OAEP über einem
 *   AES-256-GCM-Einmalschlüssel). Er trägt heute den Sitzungsaufbau des Double
 *   Ratchets, die Gruppenschlüssel, den Raumschlüssel der Anrufe und die
 *   Quittungen — überall dort, wo gegen einen veröffentlichten Geräteschlüssel
 *   versiegelt wird.
 * - **Die Schlüsselablage** des alten Kontoschlüssels. Sie wird nicht mehr
 *   befüllt; `altbestandUebernahme.ts` liest sie einmal aus und löscht sie.
 *
 * Wer verschlüsselt, findet es in `ratchetSitzung.ts` (1:1),
 * `gruppenSchluessel.ts` (Gruppen) oder `medienKrypto.ts` (Anhänge).
 *
 * Invarianten:
 * - Der Server bleibt blindes Relais: kein Klartext, keine Teilnehmerbindung,
 *   kein Kanalschlüssel.
 * - DIS-Regel: `subtle()` aus `@msdis/shield/core`, nie `crypto.subtle` direkt.
 * - Jeder Entschlüsselungsfehler wird zu `DisDecryptionError` eingeebnet, damit
 *   kein AEAD-Orakel entsteht.
 */

import { encryptString, decryptString, importAesGcmRawKey } from '@msdis/shield/aead'
import { sha256Hex } from '@msdis/shield/integrity'
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

/** Der Double Ratchet aus `ratchetSitzung.ts`. Hier steht nur das Präfix. */
export const E2EE_DR_PREFIX = 'sv-e2ee-dr-v1:'

// ==========================================
// 1. Mailbox-Kennungen
// ==========================================

const blindMailboxIdCache = new Map<string, string>()
const groupBlindMailboxIdCache = new Map<string, string>()
const deviceBlindMailboxIdCache = new Map<number, string>()

/**
 * Returns the synchronously cached blind mailbox identifier if previously computed.
 */
export function getCachedBlindMailboxId(userAId: number, userBId: number, salt: string = ''): string | undefined {
  const minId = Math.min(userAId, userBId)
  const maxId = Math.max(userAId, userBId)
  const cacheKey = `${minId}:${maxId}:${salt}`
  return blindMailboxIdCache.get(cacheKey)
}

/**
 * Returns the synchronously cached group blind mailbox identifier if previously computed.
 */
export function getCachedGroupBlindMailboxId(groupId: number, groupSalt: string = ''): string | undefined {
  const cacheKey = `${groupId}:${groupSalt}`
  return groupBlindMailboxIdCache.get(cacheKey)
}

/**
 * Clears the in-memory blind mailbox identifier cache.
 */
export function clearBlindMailboxIdCache(): void {
  blindMailboxIdCache.clear()
  groupBlindMailboxIdCache.clear()
  deviceBlindMailboxIdCache.clear()
}

/**
 * Derives the deterministic blind mailbox identifier for two participants.
 * Zero-Knowledge: This mailbox ID contains no user identifiers or relational data on the wire.
 */
export async function deriveBlindMailboxId(userAId: number, userBId: number, salt: string = ''): Promise<string> {
  const minId = Math.min(userAId, userBId)
  const maxId = Math.max(userAId, userBId)
  const cacheKey = `${minId}:${maxId}:${salt}`
  const cached = blindMailboxIdCache.get(cacheKey)
  if (cached) return cached

  const payload = `msm:dm:${minId}:${maxId}${salt ? `:${salt}` : ''}`
  const seed = new TextEncoder().encode(payload)
  try {
    const res = await sha256Hex(seed)
    blindMailboxIdCache.set(cacheKey, res)
    return res
  } finally {
    seed.fill(0)
  }
}

/**
 * Derives the deterministic blind mailbox identifier for all devices of a user.
 * Used for inter-device synchronization (e.g. E2EE notes/calendar key distribution).
 */
export async function deriveUserDeviceMailboxId(userId: number): Promise<string> {
  const cached = deviceBlindMailboxIdCache.get(userId)
  if (cached) return cached

  const payload = `msm:devices:${userId}`
  const seed = new TextEncoder().encode(payload)
  try {
    const res = await sha256Hex(seed)
    deviceBlindMailboxIdCache.set(userId, res)
    return res
  } finally {
    seed.fill(0)
  }
}

/**
 * Derives the deterministic blind mailbox identifier for a chat group / community.
 */
export async function deriveGroupBlindMailboxId(groupId: number, groupSalt: string = ''): Promise<string> {
  const cacheKey = `${groupId}:${groupSalt}`
  const cached = groupBlindMailboxIdCache.get(cacheKey)
  if (cached) return cached

  const payload = `msm:group:${groupId}${groupSalt ? `:${groupSalt}` : ''}`
  const seed = new TextEncoder().encode(payload)
  try {
    const res = await sha256Hex(seed)
    groupBlindMailboxIdCache.set(cacheKey, res)
    return res
  } finally {
    seed.fill(0)
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
    const req = indexedDB.open(IDB_DB_NAME, 2)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(IDB_STORE_NAME)) {
        db.createObjectStore(IDB_STORE_NAME, { keyPath: 'userId' })
      }
      if (!db.objectStoreNames.contains('keyring')) {
        db.createObjectStore('keyring', { keyPath: 'userId' })
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
  clearBlindMailboxIdCache()
  clearRsaPrivateKeyCache()
  clearEnvelopePlaintextCache()
  scrubPlaintextStorage()
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

/** Gerätekennung im Klartextkopf eines Double-Ratchet-Umschlags. */
const GERAETEKENNUNG = /^[A-Za-z0-9_-]{8,64}$/
/** Die Kennung des Gruppenschlüssels: die ersten 16 Hexzeichen seines Hashes. */
const GRUPPEN_KEY_ID = /^[0-9a-f]{16}$/

/**
 * Prüft Aufbau, Präfix und Nonce eines Umschlags.
 *
 * Die Präfixliste ist absichtlich dieselbe wie `VALID_E2EE_PREFIXES` in
 * `backend/schemas/social.py`, und die Zweige prüfen dasselbe. Nicht als
 * doppelte Absicherung — das Backend ist die durchsetzende Stelle, hier lässt
 * sich nichts erzwingen. Sondern damit ein Client gar nicht erst einen Umschlag
 * baut, den das Relais gleich darauf mit 422 zurückweist.
 *
 * `sv-e2ee-v1:`, `sv-e2ee-team-v1:` und `sv-e2ee-ratchet-v1:` stehen nicht mehr
 * darin. Sie sind damit ungültig, auch beim Lesen.
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
    E2EE_GROUP_ENVELOPE_SPEC.currentPrefix,
    E2EE_HYBRID_ENVELOPE_SPEC.currentPrefix,
    E2EE_DR_PREFIX,
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
  } else if (matchedPrefix === E2EE_DR_PREFIX) {
    // <vonKonto>.<vonGeraet>.<fuerGeraet>.<base64(sv-dr-msg-v1:…)>
    //
    // Der Zweig kehrt eigenständig zurück. Die gemeinsame Nonce-Prüfung unten
    // liest die ersten zwölf Bytes als IV und verwirft einen Null-IV; ein
    // Double-Ratchet-Umschlag führt aber gar keinen IV mit sich, seine Nonce
    // leitet sich aus dem Einmal-Nachrichtenschlüssel ab. Angewandt verwürfe
    // sie zufällig gültige Umschläge und sicherte nichts ab.
    const teile = payload.split('.')
    if (teile.length !== 4) {
      return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Malformed double ratchet envelope: expected account.sender.recipient.body' }
    }
    const [kontoRoh, vonGeraet, fuerGeraet, rumpf] = teile
    if (!/^[1-9][0-9]*$/.test(kontoRoh)) {
      return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Invalid sender account id in double ratchet envelope' }
    }
    if (!GERAETEKENNUNG.test(vonGeraet) || !GERAETEKENNUNG.test(fuerGeraet)) {
      return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Invalid device id in double ratchet envelope' }
    }
    try {
      const roh = base64ToBytes(rumpf)
      const kopf = new TextDecoder().decode(roh.slice(0, 13))
      if (kopf !== 'sv-dr-msg-v1:') {
        return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Double ratchet body carries no valid DIS message format' }
      }
    } catch {
      return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Invalid base64 encoding in double ratchet body' }
    }
    return { valid: true, prefix: matchedPrefix, payloadLength: payload.length }
  } else {
    // Gruppe: <keyId>.<ciphertext>. Die Kennung steht im Klartext, weil ein
    // Gerät mehrere Generationen hält und wissen muss, welche gemeint ist. Sie
    // hängt allein am Schlüssel, den der Server nie sieht.
    let ct = payload
    if (matchedPrefix === E2EE_GROUP_ENVELOPE_SPEC.currentPrefix) {
      const dotIdx = payload.indexOf('.')
      if (dotIdx === -1) {
        return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Malformed group envelope: key id before ciphertext is missing' }
      }
      if (!GRUPPEN_KEY_ID.test(payload.slice(0, dotIdx))) {
        return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Invalid key id in group envelope' }
      }
      ct = payload.slice(dotIdx + 1)
    }
    if (ct.length < 38) {
      return { valid: false, prefix: matchedPrefix, payloadLength: payload.length, error: 'Ciphertext payload too short for valid IV and AEAD tag' }
    }
    try {
      const bytes = base64ToBytes(ct)
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

export const envelopePlaintextCache = new Map<
  number,
  { plain: string; ok: boolean; vonKonto?: number; vonGeraet?: string }
>()

/**
 * Clears the in-memory decrypted envelope plaintext cache.
 */
export function clearEnvelopePlaintextCache(): void {
  envelopePlaintextCache.clear()
}

const rsaPrivateKeyCache = new Map<string, CryptoKey>()

/**
 * Clears the in-memory RSA private key cache.
 */
export function clearRsaPrivateKeyCache(): void {
  rsaPrivateKeyCache.clear()
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
    let rsaPrivKey = rsaPrivateKeyCache.get(participantPrivateKeyJwk)
    if (!rsaPrivKey) {
      const privKeyObj: JsonWebKey = JSON.parse(participantPrivateKeyJwk)
      rsaPrivKey = await importRsaOaepPrivateKey(privKeyObj)
      rsaPrivateKeyCache.set(participantPrivateKeyJwk, rsaPrivKey)
    }

    let rawBytes: Uint8Array | null = null
    let isLegacyJsonArray = false
    for (const wrappedKey of keyList) {
      try {
        const rawKeyPlain = await rsaOaepDecrypt(wrappedKey, rsaPrivKey)
        if (rawKeyPlain.startsWith('[')) {
          // Backward-compatibility: JSON array format
          rawBytes = new Uint8Array(JSON.parse(rawKeyPlain))
          isLegacyJsonArray = true
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
        if (isLegacyJsonArray) {
          return await decryptString(ciphertext, symKey, 'msm:hybrid:aad')
        }
        // Decrypt with wrappedKeys bound in AAD (anti-tamper / anti-session-swap)
        return await decryptString(ciphertext, symKey, `msm:hybrid:aad:${wrappedKeysPart}`)
      })
    } finally {
      secureKey.destroy()
    }
  } catch {
    throw new DisDecryptionError('Hybrid-Entschlüsselung fehlgeschlagen')
  }
}

/**
 * Entschlüsselt einen Hybrid-Umschlag gegen einen ganzen Schlüsselbund.
 *
 * Ein Konto kann mehrere private Schlüssel besitzen: den aktuellen und die
 * Gerätesschlüssel aus der Zeit, als jedes Endgerät noch seinen eigenen hatte.
 * Nachrichten aus dieser Zeit lassen sich nur mit dem damaligen Schlüssel
 * öffnen. Die Schleife steht hier und nicht in der Komponente, damit
 * ausnahmslos jeder Lesepfad denselben Bund durchprobiert.
 */
export async function decryptE2eeHybridWithKeyring(
  envelopeString: string,
  privateKeyJwks: readonly string[]
): Promise<string> {
  if (!privateKeyJwks || privateKeyJwks.length === 0) {
    throw new DisDecryptionError('Kein Schlüssel zum Entschlüsseln vorhanden')
  }
  for (const privateKeyJwk of privateKeyJwks) {
    if (!privateKeyJwk) continue
    try {
      return await decryptE2eeHybrid(envelopeString, privateKeyJwk)
    } catch {
      // Nächster Schlüssel im Bund
    }
  }
  throw new DisDecryptionError('Kein passender Schlüssel im Bund für diesen Umschlag')
}

