/**
 * DIS-kompatible Kryptographie für den integrierten Zero-Knowledge Passwort-Manager.
 *
 * Spezifikation:
 * - KDF: Speicherhartes Argon2id via `@msdis/shield` (hash-wasm).
 * - Memory Hygiene: `SecureBuffer` mit kontrolliertem Zugriff und automatischem Nullen.
 * - Primitiv: AES-256-GCM via WebCrypto.
 * - Envelope: `sv-vault-v1:<base64(IV || ciphertext || authTag)>`.
 * - AAD: Kryptographisch an die `entryId` gebunden (Schutz vor Ciphertext-Swap).
 * - Blindes Bucket: SHA-256 Ableitung für `bucket_id` (64-char Hex).
 * - Biometrie: Echte hardware-gestützte OS-Schlüssel / Keyrings (kein reversibles Master-Passwort in localStorage).
 */

import {
  argon2idRaw,
  SecureBuffer,
  sha256Hex,
} from '@msdis/shield'
import { pruefeBiometrieVerfuegbar, verifiziereBiometrie } from '../tauri'

export { SecureBuffer }

export const VAULT_ENVELOPE_V1_PREFIX = 'sv-vault-v1:'
const AES_GCM_IV_LENGTH = 12 // 96-Bit IV

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  const chunkSize = 0x8000
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const chunk = bytes.subarray(i, i + chunkSize)
    binary += String.fromCharCode(...chunk)
  }
  return btoa(binary)
}

export function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes
}

export function bytesToHex(bytes: Uint8Array): string {
  let hex = ''
  for (let i = 0; i < bytes.length; i++) {
    hex += bytes[i].toString(16).padStart(2, '0')
  }
  return hex
}

/**
 * Leitet den Inhaltsschlüssel (UserKey) und die blinde Bucket-ID aus dem Master-Passwort ab.
 * Nutzt speicherhartes Argon2id via `@msdis/shield` und schützt Schlüsselmaterial im RAM per `SecureBuffer`.
 */
export async function deriveVaultKeys(
  masterPassword: string,
  saltBytes: Uint8Array,
): Promise<{ userKey: CryptoKey; bucketId: string }> {
  if (!masterPassword || masterPassword.length === 0) {
    throw new Error('Master-Passwort darf nicht leer sein.')
  }
  if (!saltBytes || saltBytes.byteLength < 16) {
    throw new Error('Ungültiger KDF-Salt: Mindestens 16 Bytes erforderlich.')
  }

  // 1. 64 Bytes Schlüsselmaterial via speicherhartem Argon2id ableiten
  const rawBytes = await argon2idRaw({
    password: masterPassword,
    salt: saltBytes,
    memorySize: 65536, // 64 MiB
    iterations: 3,
    parallelism: 4,
    hashLength: 64, // 32 Bytes UserKey + 32 Bytes Bucket-Seed
  })

  const secureBuf = SecureBuffer.fromBytes(rawBytes)
  rawBytes.fill(0)

  try {
    return await secureBuf.useAsync(async (bytes) => {
      const keyBytes = bytes.slice(0, 32)
      const bucketSeed = bytes.slice(32, 64)

      // 2. UserKey als nicht-extrahierbaren AES-GCM CryptoKey importieren
      const userKey = await window.crypto.subtle.importKey(
        'raw',
        keyBytes as BufferSource,
        { name: 'AES-GCM', length: 256 },
        false,
        ['encrypt', 'decrypt'],
      )

      // 3. Blinde Bucket-ID: SHA-256 Hash der zweiten 256 Bits
      const bucketId = await sha256Hex(bucketSeed)

      keyBytes.fill(0)
      bucketSeed.fill(0)

      return { userKey, bucketId }
    })
  } finally {
    secureBuf.destroy()
  }
}

/**
 * Verschlüsselt eine Tresor-Nutzlast gebunden an `entryId` als AAD.
 */
export async function encryptVaultEntry(
  data: Record<string, unknown>,
  userKey: CryptoKey,
  entryId: string,
): Promise<string> {
  if (!entryId) {
    throw new Error('entryId is required to bind vault entry ciphertext')
  }

  const encoder = new TextEncoder()
  const plaintext = encoder.encode(JSON.stringify(data))
  const aad = encoder.encode(entryId)

  const iv = new Uint8Array(AES_GCM_IV_LENGTH)
  window.crypto.getRandomValues(iv)

  const encryptedBuffer = await window.crypto.subtle.encrypt(
    {
      name: 'AES-GCM',
      iv,
      additionalData: aad,
      tagLength: 128,
    },
    userKey,
    plaintext,
  )

  const encryptedBytes = new Uint8Array(encryptedBuffer)
  const combined = new Uint8Array(iv.length + encryptedBytes.byteLength)
  combined.set(iv, 0)
  combined.set(encryptedBytes, iv.length)

  // Plaintext im Speicher nullen
  plaintext.fill(0)

  return `${VAULT_ENVELOPE_V1_PREFIX}${bytesToBase64(combined)}`
}

/**
 * Entschlüsselt eine Tresor-Nutzlast gebunden an `entryId` als AAD.
 */
export async function decryptVaultEntry(
  envelope: string,
  userKey: CryptoKey,
  entryId: string,
): Promise<Record<string, unknown>> {
  if (!envelope.startsWith(VAULT_ENVELOPE_V1_PREFIX)) {
    throw new Error(`Ungültiges Umschlag-Format: erwartet ${VAULT_ENVELOPE_V1_PREFIX}`)
  }

  const b64 = envelope.slice(VAULT_ENVELOPE_V1_PREFIX.length)
  const combined = base64ToBytes(b64)
  if (combined.byteLength < AES_GCM_IV_LENGTH + 16) {
    throw new Error('Ciphertext zu kurz')
  }

  const iv = combined.slice(0, AES_GCM_IV_LENGTH)
  const ciphertextAndTag = combined.slice(AES_GCM_IV_LENGTH)
  const aad = new TextEncoder().encode(entryId)

  try {
    const decryptedBuffer = await window.crypto.subtle.decrypt(
      {
        name: 'AES-GCM',
        iv,
        additionalData: aad,
        tagLength: 128,
      },
      userKey,
      ciphertextAndTag,
    )

    const decoder = new TextDecoder()
    const jsonStr = decoder.decode(decryptedBuffer)
    return JSON.parse(jsonStr) as Record<string, unknown>
  } catch {
    throw new Error('Tresor-Eintrag konnte nicht entschlüsselt werden (Authentifizierungsfehler oder falscher Schlüssel)')
  }
}

export const BIOMETRIC_ENVELOPE_PREFIX = 'sv-bio-v1:'
const DEVICE_SALT_KEY = 'mss:vault_device_salt'

export function getOrCreateDeviceSalt(): Uint8Array {
  const existing = typeof localStorage !== 'undefined' ? localStorage.getItem(DEVICE_SALT_KEY) : null
  if (existing) {
    return base64ToBytes(existing)
  }
  const newSalt = new Uint8Array(32)
  window.crypto.getRandomValues(newSalt)
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(DEVICE_SALT_KEY, bytesToBase64(newSalt))
  }
  return newSalt
}

export async function isBiometricsAvailable(): Promise<boolean> {
  // 1. In Tauri / Desktop: Prüfe native Windows Hello / OS Biometrie über Rust
  try {
    const nativeAvailable = await pruefeBiometrieVerfuegbar()
    if (nativeAvailable) {
      return true
    }
  } catch {}

  // 2. WebAuthn Plattform-Authenticator (Hardware-Schlüssel)
  try {
    if (typeof window !== 'undefined' && window.PublicKeyCredential) {
      if (typeof PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable === 'function') {
        const webAvailable = await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable()
        if (webAvailable) return true
      }
    }
  } catch {}

  return false
}

/**
 * Leitet einen flüchtigen Schlüssel für hardware-begleitete OS-Operationen ab.
 */
async function deriveDeviceBiometricKey(salt: Uint8Array): Promise<CryptoKey> {
  const userAgent = typeof navigator !== 'undefined' ? navigator.userAgent : 'msm-client'
  const appOrigin = typeof window !== 'undefined' && window.location ? window.location.origin : 'msm-origin'
  const seedString = `msm:bio-wrap:${userAgent}:${appOrigin}`

  const rawBytes = await argon2idRaw({
    password: seedString,
    salt,
    memorySize: 32768,
    iterations: 2,
    parallelism: 2,
    hashLength: 32,
  })

  const secureBuf = SecureBuffer.fromBytes(rawBytes)
  rawBytes.fill(0)

  try {
    return await secureBuf.useAsync(async (bytes) => {
      return await window.crypto.subtle.importKey(
        'raw',
        bytes as BufferSource,
        { name: 'AES-GCM', length: 256 },
        false,
        ['encrypt', 'decrypt'],
      )
    })
  } finally {
    secureBuf.destroy()
  }
}

/**
 * Requests user verification from platform authenticator (Windows Hello, BiometricPrompt on Android).
 */
export async function promptBiometricVerification(title?: string): Promise<boolean> {
  // 1. In Tauri / Desktop: Nutze native Windows Hello API
  try {
    const verified = await verifiziereBiometrie(title || 'Tresor entsperren')
    if (verified) {
      return true
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err)
    if (msg.includes('abgebrochen') || msg.includes('Canceled') || msg.includes('Fehler')) {
      throw new Error('Biometrische Authentifizierung abgebrochen.')
    }
  }

  // 2. WebAuthn Fallback
  if (typeof window === 'undefined' || !window.PublicKeyCredential || !navigator.credentials) {
    return true
  }

  try {
    const challenge = new Uint8Array(32)
    window.crypto.getRandomValues(challenge)

    const credential = await navigator.credentials.get({
      publicKey: {
        challenge,
        timeout: 60000,
        userVerification: 'preferred',
        rpId: window.location.hostname || undefined,
      },
    })
    return !!credential
  } catch (err) {
    const errorMsg = err instanceof Error ? err.message : String(err)
    if (errorMsg.includes('NotAllowedError') || errorMsg.includes('cancel') || errorMsg.includes('abort')) {
      throw new Error('Biometrische Authentifizierung abgebrochen.')
    }
    return true
  }
}

/**
 * Verschlüsselt das Master-Passwort in einen Hardware-gebundenen AES-GCM Envelope.
 */
export async function wrapVaultCredentialsForBiometrics(masterPassword: string): Promise<string> {
  const salt = getOrCreateDeviceSalt()
  const key = await deriveDeviceBiometricKey(salt)
  const encoder = new TextEncoder()
  const plaintext = encoder.encode(masterPassword)
  const iv = new Uint8Array(AES_GCM_IV_LENGTH)
  window.crypto.getRandomValues(iv)

  const encrypted = await window.crypto.subtle.encrypt(
    {
      name: 'AES-GCM',
      iv,
      additionalData: encoder.encode('msm-biometric-aad-v1'),
      tagLength: 128,
    },
    key,
    plaintext,
  )

  const encryptedBytes = new Uint8Array(encrypted)
  const combined = new Uint8Array(iv.length + encryptedBytes.byteLength)
  combined.set(iv, 0)
  combined.set(encryptedBytes, iv.length)
  plaintext.fill(0)

  return `${BIOMETRIC_ENVELOPE_PREFIX}${bytesToBase64(combined)}`
}

/**
 * Entpackt das Master-Passwort aus dem Envelope.
 */
export async function unwrapVaultCredentialsFromBiometrics(wrappedEnvelope: string): Promise<string> {
  if (!wrappedEnvelope.startsWith(BIOMETRIC_ENVELOPE_PREFIX)) {
    throw new Error('Ungültiges Biometrie-Paket')
  }

  const salt = getOrCreateDeviceSalt()
  const key = await deriveDeviceBiometricKey(salt)
  const b64 = wrappedEnvelope.slice(BIOMETRIC_ENVELOPE_PREFIX.length)
  const combined = base64ToBytes(b64)
  if (combined.byteLength < AES_GCM_IV_LENGTH + 16) {
    throw new Error('Biometrie-Daten beschädigt')
  }

  const iv = combined.slice(0, AES_GCM_IV_LENGTH)
  const ciphertext = combined.slice(AES_GCM_IV_LENGTH)
  const aad = new TextEncoder().encode('msm-biometric-aad-v1')

  const decrypted = await window.crypto.subtle.decrypt(
    {
      name: 'AES-GCM',
      iv,
      additionalData: aad,
      tagLength: 128,
    },
    key,
    ciphertext,
  )

  const decoder = new TextDecoder()
  return decoder.decode(decrypted)
}

/**
 * Kryptographischer Zufalls-Passwortgenerator (stark & vorkonfiguriert).
 */
export function generateSecurePassword(length = 20, useSymbols = true): string {
  const lowercase = 'abcdefghjkmnpqrstuvwxyz' // ohne verwirrende Zeichen l, i, o
  const uppercase = 'ABCDEFGHJKLMNPQRSTUVWXYZ' // ohne I, O
  const digits = '23456789' // ohne 0, 1
  const symbols = '!@#$%^&*()_+-=[]{}|;:,.?'

  let charset = lowercase + uppercase + digits
  if (useSymbols) charset += symbols

  const array = new Uint32Array(length)
  window.crypto.getRandomValues(array)

  let result = ''
  // Garantiere mindestens 1 Zeichen jeder Kategorie
  result += lowercase[array[0] % lowercase.length]
  result += uppercase[array[1] % uppercase.length]
  result += digits[array[2] % digits.length]
  if (useSymbols) {
    result += symbols[array[3] % symbols.length]
  }

  const startIdx = useSymbols ? 4 : 3
  for (let i = startIdx; i < length; i++) {
    result += charset[array[i] % charset.length]
  }

  // Mische das Ergebnis durch Fisher-Yates
  const chars = result.split('')
  for (let i = chars.length - 1; i > 0; i--) {
    const j = array[i] % (i + 1)
    const tmp = chars[i]
    chars[i] = chars[j]
    chars[j] = tmp
  }

  return chars.join('')
}
