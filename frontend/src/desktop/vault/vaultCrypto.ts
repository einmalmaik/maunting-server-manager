/**
 * DIS-kompatible Kryptographie für den integrierten Zero-Knowledge Passwort-Manager.
 *
 * Spezifikation:
 * - Primitiv: AES-256-GCM via WebCrypto.
 * - Envelope: `sv-vault-v1:<base64(IV || ciphertext || authTag)>`.
 * - AAD: Kryptographisch an die `entryId` gebunden (Schutz vor Ciphertext-Swap).
 * - Blindes Bucket: SHA-256 / HKDF-Ableitung für `bucket_id` (64-char Hex).
 * - Memory Hygiene: `SecureBuffer` mit `.use()` und `.destroy()`.
 */

export const VAULT_ENVELOPE_V1_PREFIX = 'sv-vault-v1:'
const AES_GCM_IV_LENGTH = 12 // 96-Bit IV

export class SecureBuffer {
  private _data: Uint8Array | null
  private _destroyed = false

  constructor(size: number) {
    this._data = new Uint8Array(size)
  }

  get size(): number {
    return this._data ? this._data.byteLength : 0
  }

  get isDestroyed(): boolean {
    return this._destroyed
  }

  use<T>(fn: (bytes: Uint8Array) => T): T {
    if (this._destroyed || !this._data) {
      throw new Error('SecureBuffer has already been destroyed')
    }
    return fn(this._data)
  }

  destroy(): void {
    if (!this._destroyed && this._data) {
      this._data.fill(0)
      this._data = null
      this._destroyed = true
    }
  }
}

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i])
  }
  return btoa(binary)
}

export function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes
}

export function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

/**
 * Leitet den Inhaltsschlüssel (UserKey) und die blinde Bucket-ID aus dem Master-Passwort ab.
 */
export async function deriveVaultKeys(
  masterPassword: string,
  saltBytes: Uint8Array,
): Promise<{ userKey: CryptoKey; bucketId: string }> {
  const encoder = new TextEncoder()
  const pwBytes = encoder.encode(masterPassword)

  // 1. Base-Key via PBKDF2 importieren
  const baseKey = await window.crypto.subtle.importKey(
    'raw',
    pwBytes,
    'PBKDF2',
    false,
    ['deriveBits', 'deriveKey'],
  )

  // 2. 256-Bit Master-Key Bits ableiten (100.000 Runden SHA-256)
  const derivedBits = await window.crypto.subtle.deriveBits(
    {
      name: 'PBKDF2',
      salt: saltBytes as BufferSource,
      iterations: 100000,
      hash: 'SHA-256',
    },
    baseKey,
    512, // 256 Bit für AES-GCM + 256 Bit für Sync-Bucket
  )

  const derivedArray = new Uint8Array(derivedBits)
  const keyBytes = derivedArray.slice(0, 32)
  const bucketSeed = derivedArray.slice(32, 64)

  // 3. UserKey als AES-GCM importieren
  const userKey = await window.crypto.subtle.importKey(
    'raw',
    keyBytes,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  )

  // 4. Blinde Bucket-ID: SHA-256 Hash der zweiten 256 Bits
  const bucketHash = await window.crypto.subtle.digest('SHA-256', bucketSeed)
  const bucketId = bytesToHex(new Uint8Array(bucketHash))

  // Säubern der temporären Byte-Arrays
  keyBytes.fill(0)
  bucketSeed.fill(0)
  derivedArray.fill(0)

  return { userKey, bucketId }
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
