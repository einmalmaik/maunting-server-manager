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

import { argon2idRaw } from '@msdis/shield/kdf'
import { SecureBuffer } from '@msdis/shield/secure-memory'
import { sha256Hex } from '@msdis/shield/integrity'
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

export function concatBytes(a: Uint8Array, b: Uint8Array): Uint8Array {
  const c = new Uint8Array(a.length + b.length)
  c.set(a, 0)
  c.set(b, a.length)
  return c
}

const PAD_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'

/**
 * Normalisiert Nutzlasten vor der Verschlüsselung auf ein Vielfaches von targetBlockSize (Standard 4.096 Bytes),
 * um Traffic-Analyse und Ciphertext-Längen-Lecks im Netzwerk zu verhindern.
 */
export function padPayload(payload: string, targetBlockSize = 4096): string {
  const safePayload = payload ?? ''
  const prefix = `${safePayload.length}:`
  const minLength = prefix.length + safePayload.length
  const blockSize = Math.max(1, targetBlockSize || 4096)
  const targetLength = Math.max(
    blockSize,
    Math.ceil(minLength / blockSize) * blockSize,
  )
  const paddingNeeded = targetLength - minLength

  if (paddingNeeded <= 0) {
    return `${prefix}${safePayload}`
  }

  const randomBytes = new Uint8Array(paddingNeeded)
  if (typeof window !== 'undefined' && window.crypto?.getRandomValues) {
    window.crypto.getRandomValues(randomBytes)
  } else if (typeof globalThis !== 'undefined' && globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(randomBytes)
  } else {
    for (let i = 0; i < paddingNeeded; i++) {
      randomBytes[i] = Math.floor(Math.random() * 256)
    }
  }

  let padding = ''
  for (let i = 0; i < paddingNeeded; i++) {
    padding += PAD_CHARS[randomBytes[i] % PAD_CHARS.length]
  }

  return `${prefix}${safePayload}${padding}`
}

/**
 * Entfernt das Padding einer entschlüsselten Nutzlast.
 * Falls die Nutzlast unpadded ist (z. B. bestehende Altdaten mit reinem JSON), wird sie direkt zurückgegeben.
 */
export function unpadPayload(padded: string): string {
  if (!padded) return ''
  const match = padded.match(/^(\d+):/)
  if (match) {
    const lenStr = match[1]
    const len = parseInt(lenStr, 10)
    const start = lenStr.length + 1
    if (Number.isSafeInteger(len) && len >= 0 && padded.length >= start + len) {
      return padded.slice(start, start + len)
    }
  }
  return padded
}

/**
 * Leitet den Inhaltsschlüssel (UserKey), die blinde Bucket-ID und den blinden Besitznachweis aus dem Master-Passwort ab.
 * Nutzt speicherhartes Argon2id via `@msdis/shield` und schützt Schlüsselmaterial im RAM per `SecureBuffer`.
 */
export async function deriveVaultKeys(
  masterPassword: string,
  saltBytes: Uint8Array,
): Promise<{ userKey: CryptoKey; bucketId: string; bucketAuthToken: string }> {
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

      // 4. Blinder Besitznachweis (bucketAuthToken): Deterministisch aus bucketSeed abgeleitet
      const authSuffix = new TextEncoder().encode(':msm-vault-auth-v1')
      const authMaterial = concatBytes(bucketSeed, authSuffix)
      const bucketAuthToken = await sha256Hex(authMaterial)

      keyBytes.fill(0)
      authMaterial.fill(0)
      bucketSeed.fill(0)

      return { userKey, bucketId, bucketAuthToken }
    })
  } finally {
    secureBuf.destroy()
  }
}

/**
 * Verschlüsselt eine Tresor-Nutzlast gebunden an `entryId` als AAD.
 * Verwendet padPayload zur Normalisierung der Ciphertext-Länge gegen Traffic-Analyse.
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
  const rawJson = JSON.stringify(data)
  const padded = padPayload(rawJson)
  const plaintext = encoder.encode(padded)
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
 * Unterstützt sowohl gepaddete als auch historische ungepaddete Datensätze (Zero-Breakage).
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
    const rawStr = decoder.decode(decryptedBuffer)
    const jsonStr = unpadPayload(rawStr)
    return JSON.parse(jsonStr) as Record<string, unknown>
  } catch {
    throw new Error('Tresor-Eintrag konnte nicht entschlüsselt werden (Authentifizierungsfehler oder falscher Schlüssel)')
  }
}

async function checkAndroidBiometric(): Promise<boolean> {
  try {
    const { checkStatus } = await import('@tauri-apps/plugin-biometric')
    const status = await checkStatus()
    return !!status.isAvailable
  } catch {
    return false
  }
}

async function promptAndroidBiometric(title?: string): Promise<boolean> {
  try {
    const { authenticate } = await import('@tauri-apps/plugin-biometric')
    await authenticate(title || 'Tresor entsperren', {
      allowDeviceCredential: true,
    })
    return true
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err)
    if (
      msg.includes('cancel') ||
      msg.includes('Cancel') ||
      msg.includes('abgebrochen') ||
      msg.includes('User canceled') ||
      msg.includes('NegativeButton')
    ) {
      throw new Error('Biometrische Authentifizierung abgebrochen.')
    }
    return false
  }
}

export async function isBiometricsAvailable(): Promise<boolean> {
  // 1. In Tauri / Desktop: Prüfe native Windows Hello / OS Biometrie über Rust
  try {
    const nativeAvailable = await pruefeBiometrieVerfuegbar()
    if (nativeAvailable) {
      return true
    }
  } catch {}

  // 2. In Tauri / Mobile (Android): Prüfe Biometrie über tauri-plugin-biometric
  try {
    const androidAvailable = await checkAndroidBiometric()
    if (androidAvailable) {
      return true
    }
  } catch {}

  // 3. Im Web-Browser existiert kein hardware-geschützter OS-Keyring zur sicheren Passwort-Verwahrung.
  // WebAuthn ohne hardwaregestützten Credential Store wird abgewiesen (SEC-CRIT-01).
  return false
}

/**
 * Fordert Benutzer-Verifikation über den nativen Plattform-Authenticator an (Windows Hello, BiometricPrompt).
 * Schlägt bei Nicht-Verifikation oder unzureichenden Rechten fehl (Fail-Closed).
 */
export async function promptBiometricVerification(title?: string): Promise<boolean> {
  // 1. In Tauri / Desktop: Nutze native Windows Hello API falls auf diesem System verfügbar
  try {
    const isWindowsHelloAvailable = await pruefeBiometrieVerfuegbar()
    if (isWindowsHelloAvailable) {
      const verified = await verifiziereBiometrie(title || 'Tresor entsperren')
      return verified
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err)
    if (msg.includes('abgebrochen') || msg.includes('Canceled') || msg.includes('Fehler')) {
      throw new Error('Biometrische Authentifizierung abgebrochen.')
    }
    return false
  }

  // 2. In Tauri / Mobile (Android): Nutze BiometricPrompt falls auf Android verfügbar
  try {
    const isAndroidAvailable = await checkAndroidBiometric()
    if (isAndroidAvailable) {
      return await promptAndroidBiometric(title)
    }
  } catch (err: unknown) {
    throw err
  }

  // 3. WebAuthn Plattform-Authenticator: Fail-Closed (SEC-CRIT-01)
  if (typeof window === 'undefined' || !window.PublicKeyCredential || !navigator.credentials) {
    return false
  }

  try {
    const challenge = new Uint8Array(32)
    window.crypto.getRandomValues(challenge)

    const credential = await navigator.credentials.get({
      publicKey: {
        challenge,
        timeout: 60000,
        userVerification: 'required',
        rpId: window.location.hostname || undefined,
      },
    })
    return !!credential
  } catch (err) {
    const errorName = (err && typeof err === 'object' && 'name' in err) ? String(err.name) : ''
    const errorMsg = err instanceof Error ? err.message : String(err)
    if (
      errorName === 'NotAllowedError' ||
      errorName === 'AbortError' ||
      errorMsg.includes('NotAllowedError') ||
      errorMsg.toLowerCase().includes('cancel') ||
      errorMsg.toLowerCase().includes('abort')
    ) {
      throw new Error('Biometrische Authentifizierung abgebrochen.')
    }
    return false
  }
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
