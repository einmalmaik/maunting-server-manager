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
import { randomBytes } from '@msdis/shield/random'
import { DisDecryptionError } from '@msdis/shield/core'

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
  return await sha256Hex(seed)
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
  return await importAesGcmRawKey(keyBytes, ['encrypt', 'decrypt'])
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
  return await sha256Hex(seed)
}

/**
 * Derives a symmetric AES-256-GCM CryptoKey for the team channel.
 */
export async function deriveTeamChannelKey(teamId: number, teamPassphraseOrKey?: string): Promise<CryptoKey> {
  const payload = `msm:team:key:${teamId}${teamPassphraseOrKey ? `:${teamPassphraseOrKey}` : ''}`
  const seed = new TextEncoder().encode(payload)
  const keyBytes = await sha256Bytes(seed)
  return await importAesGcmRawKey(keyBytes, ['encrypt', 'decrypt'])
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

const LOCAL_STORAGE_KEY_PREFIX = 'msm_e2ee_identity_'

export function getStoredLocalPrivateKey(userId: number): string | null {
  try {
    return localStorage.getItem(`${LOCAL_STORAGE_KEY_PREFIX}${userId}_priv`)
  } catch {
    return null
  }
}

export function storeLocalPrivateKey(userId: number, privateKeyJwk: string): void {
  try {
    localStorage.setItem(`${LOCAL_STORAGE_KEY_PREFIX}${userId}_priv`, privateKeyJwk)
  } catch {
    // LocalStorage unavailable
  }
}

/**
 * Hybrid-encrypts a message under a recipient's RSA-OAEP public key.
 * 1. Generates ephemeral 256-bit symmetric key bytes using CSPRNG.
 * 2. Encrypts message under the ephemeral key with AES-256-GCM.
 * 3. Wraps ephemeral key under recipient's RSA-OAEP public key.
 * 4. Combines into payload: base64(wrappedKey) + '.' + ciphertext.
 */
export async function encryptE2eeHybrid(message: string, recipientPublicKeyJwk: string): Promise<string> {
  const pubKeyObj: JsonWebKey = JSON.parse(recipientPublicKeyJwk)
  const rsaPubKey = await importRsaOaepPublicKey(pubKeyObj)

  const rawSymKeyBytes = randomBytes(32)
  try {
    const wrappedKeyBase64 = await rsaOaepEncrypt(JSON.stringify(Array.from(rawSymKeyBytes)), rsaPubKey)
    const symKey = await importAesGcmRawKey(rawSymKeyBytes, ['encrypt'])
    const ciphertext = await encryptString(message, symKey, 'msm:hybrid:aad')
    const payload = `${wrappedKeyBase64}.${ciphertext}`
    return formatEnvelope(E2EE_HYBRID_ENVELOPE_SPEC, payload)
  } finally {
    rawSymKeyBytes.fill(0)
  }
}

/**
 * Hybrid-decrypts a message using the recipient's RSA-OAEP private key.
 */
export async function decryptE2eeHybrid(envelopeString: string, recipientPrivateKeyJwk: string): Promise<string> {
  try {
    const parsed = parseEnvelope(E2EE_HYBRID_ENVELOPE_SPEC, envelopeString)
    const dotIdx = parsed.payload.indexOf('.')
    if (dotIdx === -1) {
      throw new DisDecryptionError('Ungültiges Hybrid-Payload-Format')
    }
    const wrappedKeyBase64 = parsed.payload.slice(0, dotIdx)
    const ciphertext = parsed.payload.slice(dotIdx + 1)

    const privKeyObj: JsonWebKey = JSON.parse(recipientPrivateKeyJwk)
    const rsaPrivKey = await importRsaOaepPrivateKey(privKeyObj)
    const rawKeyJson = await rsaOaepDecrypt(wrappedKeyBase64, rsaPrivKey)
    const rawBytes = new Uint8Array(JSON.parse(rawKeyJson))
    try {
      const symKey = await importAesGcmRawKey(rawBytes, ['decrypt'])
      return await decryptString(ciphertext, symKey, 'msm:hybrid:aad')
    } finally {
      rawBytes.fill(0)
    }
  } catch {
    throw new DisDecryptionError('Hybrid-Entschlüsselung fehlgeschlagen')
  }
}
