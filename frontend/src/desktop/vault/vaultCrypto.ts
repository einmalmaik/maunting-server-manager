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

import i18n from '@/i18n'
import { argon2idRaw } from '@msdis/shield/kdf'
import { SecureBuffer } from '@msdis/shield/secure-memory'
import { sha256Hex } from '@msdis/shield/integrity'
import {
  biometrieSpeicherVerfuegbar,
  pruefeBiometrieVerfuegbar,
  verifiziereBiometrie,
} from '../tauri'

export { SecureBuffer }

export const VAULT_ENVELOPE_V1_PREFIX = 'sv-vault-v1:'
const AES_GCM_IV_LENGTH = 12 // 96-Bit IV

/**
 * Mindestlänge des Master-Passworts.
 *
 * Diese Zahl ist keine Bedienfreundlichkeits-Frage, sondern die einzige
 * Verteidigung gegen den einen Angriff, den Zero-Knowledge prinzipbedingt
 * offenlässt: der Server hält den KDF-Salt (`/api/vault/salt`) **und** die
 * Ciphertexte. Wer beides hat — der Betreiber, ein Datenbank-Leak — kann offline
 * Kandidaten durchprobieren: Argon2id rechnen, AES-GCM-Tag prüfen, fertig. Kein
 * Rate-Limit greift dort, denn es findet auf seiner Hardware statt.
 *
 * Argon2id mit 64 MiB und 3 Durchgängen macht jeden Versuch teuer, aber nicht
 * beliebig teuer. Acht Zeichen (der Wert bis zum Audit vom 22.09.2026) sind, so
 * wie Menschen sie wählen, ungefähr 25–30 Bit — das ist in Tagen durch, nicht in
 * Jahren. Zwölf verschieben die Rechnung um Größenordnungen.
 */
export const MASTER_PASSWORT_MINDESTLAENGE = 12

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
    throw new Error(i18n.t('mss.vault.errors.emptyPassword'))
  }
  if (!saltBytes || saltBytes.byteLength < 16) {
    throw new Error(i18n.t('mss.vault.errors.invalidSalt'))
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
    throw new Error(i18n.t('mss.vault.errors.invalidEnvelopeFormat', { prefix: VAULT_ENVELOPE_V1_PREFIX }))
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
    throw new Error(i18n.t('mss.vault.errors.decryptionFailed'))
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
  // Ein Schnelleinstieg braucht zweierlei: eine Bestätigung **und** einen Platz
  // für das Geheimnis. Bis 09/2026 wurde hier nur das erste geprüft. Auf
  // Android sagte `checkAndroidBiometric()` deshalb „ja", der Tresor bot den
  // Schnelleinstieg an — und `biometrieSpeichern` scheiterte beim Einrichten am
  // fehlenden Speicher. Fragen ohne Verwahren nützt niemandem.
  if (!(await biometrieSpeicherVerfuegbar())) return false

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
 * Zieht eine gleichverteilte Zahl aus [0, obergrenze) — ohne Modulo-Schiefe.
 *
 * `zufall % n` bevorzugt die niedrigen Werte, wann immer n kein Teiler von 2^32
 * ist. Verworfen wird deshalb alles oberhalb des größten durch n teilbaren
 * Vielfachen; erwartet kostet das weniger als einen zusätzlichen Zug.
 */
function zufallsZahl(obergrenze: number): number {
  if (obergrenze <= 0) throw new Error('obergrenze muss positiv sein')
  const grenze = Math.floor(0x100000000 / obergrenze) * obergrenze
  const puffer = new Uint32Array(1)
  for (;;) {
    window.crypto.getRandomValues(puffer)
    if (puffer[0] < grenze) return puffer[0] % obergrenze
  }
}

/**
 * Kryptographischer Zufalls-Passwortgenerator (stark & vorkonfiguriert).
 *
 * Zwei Dinge waren hier bis zum Audit vom 22.09.2026 falsch:
 *
 * 1. Das Mischen griff auf **dieselben** Zufallswerte zurück, aus denen kurz
 *    zuvor die Zeichen gewählt worden waren (`array[i]` doppelt genutzt). Damit
 *    hing die Zielposition eines Zeichens von dem Zeichen selbst ab — der
 *    Shuffle war keine unabhängige Permutation mehr.
 * 2. Bei `length < 4` und Symbolen war `array[3]` undefined; `symbols[NaN]`
 *    ergibt `undefined`, das als Zeichenkette „undefined" im Passwort landete.
 *
 * Beides ist hier beseitigt: jeder Zug kommt frisch aus `zufallsZahl`, und die
 * Länge wird auf das erzwungen, was die Kategorien überhaupt zulassen.
 */
export function generateSecurePassword(length = 20, useSymbols = true): string {
  const lowercase = 'abcdefghjkmnpqrstuvwxyz' // ohne verwirrende Zeichen l, i, o
  const uppercase = 'ABCDEFGHJKLMNPQRSTUVWXYZ' // ohne I, O
  const digits = '23456789' // ohne 0, 1
  const symbols = '!@#$%^&*()_+-=[]{}|;:,.?'

  const kategorien = useSymbols
    ? [lowercase, uppercase, digits, symbols]
    : [lowercase, uppercase, digits]
  const charset = kategorien.join('')

  // Kürzer als die Zahl der Kategorien kann kein Passwort sein, das aus jeder
  // Kategorie ein Zeichen enthalten soll.
  const laenge = Math.max(kategorien.length, Math.floor(length) || 0)

  // Garantiere mindestens 1 Zeichen jeder Kategorie
  const chars = kategorien.map((satz) => satz[zufallsZahl(satz.length)])
  for (let i = kategorien.length; i < laenge; i++) {
    chars.push(charset[zufallsZahl(charset.length)])
  }

  // Fisher-Yates mit unabhängigem Zufall
  for (let i = chars.length - 1; i > 0; i--) {
    const j = zufallsZahl(i + 1)
    const tmp = chars[i]
    chars[i] = chars[j]
    chars[j] = tmp
  }

  return chars.join('')
}
