/**
 * Der Identitätsschlüssel des Kontos — eine Stelle, nicht drei.
 *
 * Vorher hing die E2EE-Identität am Gerät: jeder Browser, die Tauri-App und die
 * APK erzeugten sich in ihrer eigenen IndexedDB ein Schlüsselpaar und luden den
 * öffentlichen Teil hoch. Weil `users.social_e2ee_public_key` nur einen Eintrag
 * pro Konto hat, überschrieb das zweite Gerät den Schlüssel des ersten — und ab
 * da konnte keine Seite mehr lesen, was die andere geschrieben hatte. Der
 * gesamte Verlauf stand auf „Verschlüsselte Nachricht", obwohl alle Umschläge
 * unverändert auf dem Server lagen.
 *
 * Jetzt gehört der Schlüssel dem Konto. Sein privater Teil wird clientseitig mit
 * einem Argon2id-Schlüssel aus dem Wiederherstellungsschlüssel verpackt und als
 * Blob beim Server hinterlegt. Der Server bewahrt ihn auf und kann ihn nicht
 * öffnen. Ein neues Gerät holt den Blob, fragt einmal nach dem
 * Wiederherstellungsschlüssel und liest danach denselben Verlauf wie alle
 * anderen.
 *
 * Die tragende Invariante steht in `resolveIdentity`: liegt serverseitig ein
 * Schlüsselbund, wird **niemals** ein frischer Schlüssel erzeugt und
 * veröffentlicht. Genau dieses stille Überschreiben war der Fehler.
 */

import { argon2idRaw } from '@msdis/shield/kdf'
import { randomBytes, randomInt } from '@msdis/shield/random'
import { encryptString, decryptString, importAesGcmRawKey } from '@msdis/shield/aead'
import { SecureBuffer } from '@msdis/shield/secure-memory'
import { DisDecryptionError, base64ToBytes, bytesToBase64 } from '@msdis/shield/core'

import { generateLocalE2eeKeyPair, type LocalE2eeKeyPair } from './e2eeCrypto'
import {
  getE2eeKeyring,
  putE2eeKeyring,
  getE2eePublicKey,
  type E2eeKeyringPayload,
} from '@/api/social'

export const E2EE_KEYRING_PREFIX = 'sv-e2ee-keyring-v1:'

const KEYRING_AAD = 'msm:e2ee:keyring:v1'
const SALT_BYTES = 16

/** Identisch zu den Tresor-Parametern (`desktop/vault/vaultCrypto.ts`). */
const KDF_MEMORY_KIB = 65536
const KDF_ITERATIONS = 3
const KDF_PARALLELISM = 4

/** Ohne 0/1/I/O/l — der Schlüssel wird abgetippt, teils von einem Zettel. */
const RECOVERY_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
const RECOVERY_LENGTH = 20
const RECOVERY_GROUP = 5

/**
 * `loading` ist der Zustand vor der ersten Antwort und bewusst kein `locked`:
 * sonst zeigte jedes Öffnen des Messengers für einen Moment den Sperrhinweis
 * und eine tote Eingabeleiste, auch auf einem längst entsperrten Gerät.
 */
export type IdentityState = 'loading' | 'needs-setup' | 'locked' | 'ready'

export interface E2eeIdentity {
  state: IdentityState
  /** Das Paar, mit dem gesendet wird. Nur im Zustand `ready` gesetzt. */
  sendPair: LocalE2eeKeyPair | null
  /** Alle privaten Schlüssel zum Lesen: Kontoschlüssel zuerst, dann Altschlüssel. */
  decryptionKeys: string[]
}

/** Startwert für Komponenten, bevor `resolveIdentity` geantwortet hat. */
export const IDENTITY_LOADING: E2eeIdentity = Object.freeze({
  state: 'loading' as const,
  sendPair: null,
  decryptionKeys: [] as string[],
})

interface KeyringEntry {
  publicKeyJwk: string
  privateKeyJwk: string
  adoptedAt?: string
}

interface KeyringContent {
  v: 1
  account: KeyringEntry
  /** Gerätesschlüssel aus der Zeit vor dem Kontoschlüssel. Nur zum Lesen. */
  legacy: KeyringEntry[]
}

const LOCKED_IDENTITY: E2eeIdentity = Object.freeze({
  state: 'locked' as const,
  sendPair: null,
  decryptionKeys: [] as string[],
})

/** Signalisiert, dass ohne Wiederherstellungsschlüssel nichts zu holen ist. */
export class E2eeLockedError extends Error {
  constructor() {
    super('Der Schlüsselbund dieses Kontos ist auf diesem Gerät gesperrt')
    this.name = 'E2eeLockedError'
  }
}

/** Der Empfänger hat noch keinen Schlüssel veröffentlicht — es gibt nichts, wogegen verschlüsselt werden kann. */
export class E2eeRecipientKeyMissingError extends Error {
  constructor(public readonly userId: number) {
    super('Für diesen Empfänger liegt kein Schlüssel vor')
    this.name = 'E2eeRecipientKeyMissingError'
  }
}

// ==========================================
// Wiederherstellungsschlüssel
// ==========================================

/**
 * Erzeugt einen Wiederherstellungsschlüssel in Vierergruppen ("A2C4E-…").
 * `randomInt` aus dem Shield zieht per Rejection Sampling und ist damit frei
 * von Modulo-Bias — ein selbstgebautes `% length` wäre es nicht.
 */
export function generateRecoveryKey(): string {
  const chars: string[] = []
  for (let i = 0; i < RECOVERY_LENGTH; i++) {
    if (i > 0 && i % RECOVERY_GROUP === 0) chars.push('-')
    chars.push(RECOVERY_ALPHABET[randomInt(0, RECOVERY_ALPHABET.length - 1)])
  }
  return chars.join('')
}

/**
 * Vereinheitlicht die Eingabe, bevor daraus abgeleitet wird.
 * Ein Mensch tippt Bindestriche, Leerzeichen und Kleinbuchstaben unterschiedlich
 * ab; ohne diesen Schritt scheitert dieselbe Eingabe mal und funktioniert mal.
 */
export function normalizeRecoveryKey(input: string): string {
  return (input || '').toUpperCase().replace(/[^A-Z0-9]/g, '')
}

// ==========================================
// Umschlag: sv-e2ee-keyring-v1:<base64(salt(16) || iv(12) || ct||tag)>
// ==========================================

async function deriveKeyringKey(recoveryKey: string, salt: Uint8Array): Promise<CryptoKey> {
  const normalized = normalizeRecoveryKey(recoveryKey)
  if (!normalized) {
    throw new DisDecryptionError('Wiederherstellungsschlüssel darf nicht leer sein')
  }
  const rawBytes = await argon2idRaw({
    password: normalized,
    salt,
    memorySize: KDF_MEMORY_KIB,
    iterations: KDF_ITERATIONS,
    parallelism: KDF_PARALLELISM,
    hashLength: 32,
  })
  const secure = SecureBuffer.fromBytes(rawBytes)
  rawBytes.fill(0)
  try {
    return await secure.useAsync((bytes) => importAesGcmRawKey(bytes, ['encrypt', 'decrypt']))
  } finally {
    secure.destroy()
  }
}

export async function wrapKeyring(content: KeyringContent, recoveryKey: string): Promise<string> {
  const salt = randomBytes(SALT_BYTES)
  const key = await deriveKeyringKey(recoveryKey, salt)
  const ciphertext = await encryptString(JSON.stringify(content), key, KEYRING_AAD)
  const ctBytes = base64ToBytes(ciphertext)
  const combined = new Uint8Array(salt.length + ctBytes.length)
  combined.set(salt, 0)
  combined.set(ctBytes, salt.length)
  return `${E2EE_KEYRING_PREFIX}${bytesToBase64(combined)}`
}

export async function unwrapKeyring(envelope: string, recoveryKey: string): Promise<KeyringContent> {
  if (!envelope || !envelope.startsWith(E2EE_KEYRING_PREFIX)) {
    throw new DisDecryptionError('Ungültiges Schlüsselbund-Format')
  }
  let parsed: KeyringContent
  try {
    const combined = base64ToBytes(envelope.slice(E2EE_KEYRING_PREFIX.length))
    if (combined.length <= SALT_BYTES + 28) {
      throw new Error('zu kurz')
    }
    const salt = combined.slice(0, SALT_BYTES)
    const ciphertext = bytesToBase64(combined.slice(SALT_BYTES))
    const key = await deriveKeyringKey(recoveryKey, salt)
    const plain = await decryptString(ciphertext, key, KEYRING_AAD)
    parsed = JSON.parse(plain) as KeyringContent
  } catch {
    // Ein Fehlschlag sagt nur „passt nicht" — nie, an welcher Stelle.
    throw new DisDecryptionError('Schlüsselbund konnte nicht geöffnet werden')
  }
  if (!parsed || parsed.v !== 1 || !parsed.account?.privateKeyJwk || !parsed.account?.publicKeyJwk) {
    throw new DisDecryptionError('Schlüsselbund hat einen unbekannten Aufbau')
  }
  if (!Array.isArray(parsed.legacy)) {
    parsed.legacy = []
  }
  return parsed
}

// ==========================================
// Lokale Ablage des geöffneten Bunds
// ==========================================

const IDB_DB_NAME = 'msm_e2ee_keystore'
const IDB_KEYRING_STORE = 'keyring'
const IDB_LEGACY_STORE = 'keys'

interface StoredKeyring {
  content: KeyringContent
  /** Serverstand, aus dem dieser Bund stammt. Siehe `hasNewerRemoteKeyring`. */
  version: number
}

const memoryKeyring = new Map<number, StoredKeyring>()

function openKeyringDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      return reject(new Error('IndexedDB nicht verfügbar'))
    }
    // Version 2: Store `keys` stammt aus der Gerätesschlüssel-Zeit und bleibt,
    // weil dort das Material liegt, das den alten Verlauf rettet.
    const req = indexedDB.open(IDB_DB_NAME, 2)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(IDB_LEGACY_STORE)) {
        db.createObjectStore(IDB_LEGACY_STORE, { keyPath: 'userId' })
      }
      if (!db.objectStoreNames.contains(IDB_KEYRING_STORE)) {
        db.createObjectStore(IDB_KEYRING_STORE, { keyPath: 'userId' })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

async function readStoredKeyring(userId: number): Promise<StoredKeyring | null> {
  const cached = memoryKeyring.get(userId)
  if (cached) return cached
  try {
    const db = await openKeyringDatabase()
    const result = await new Promise<StoredKeyring | null>((resolve, reject) => {
      const tx = db.transaction(IDB_KEYRING_STORE, 'readonly')
      const req = tx.objectStore(IDB_KEYRING_STORE).get(userId)
      req.onsuccess = () => {
        const row = req.result
        resolve(row?.content ? { content: row.content, version: row.version ?? 0 } : null)
      }
      req.onerror = () => reject(req.error)
    })
    if (result) memoryKeyring.set(userId, result)
    return result
  } catch {
    return null
  }
}

async function writeStoredKeyring(
  userId: number,
  content: KeyringContent,
  version: number
): Promise<void> {
  memoryKeyring.set(userId, { content, version })
  try {
    const db = await openKeyringDatabase()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(IDB_KEYRING_STORE, 'readwrite')
      const req = tx.objectStore(IDB_KEYRING_STORE).put({ userId, content, version })
      req.onsuccess = () => resolve()
      req.onerror = () => reject(req.error)
    })
  } catch {
    // Ohne IndexedDB bleibt der Bund für diese Sitzung im RAM. Das Gerät fragt
    // beim nächsten Start erneut nach dem Wiederherstellungsschlüssel — lästig,
    // aber es erzeugt keinen zweiten Schlüssel und zerstört nichts.
  }
}

/** Leert den RAM-Cache. Die IndexedDB überlebt das Abmelden wie bisher. */
export function clearIdentityMemory(): void {
  memoryKeyring.clear()
  recipientKeyCache.clear()
}

// ==========================================
// Zustand auflösen
// ==========================================

function toIdentity(content: KeyringContent): E2eeIdentity {
  return {
    state: 'ready',
    sendPair: {
      publicKeyJwk: content.account.publicKeyJwk,
      privateKeyJwk: content.account.privateKeyJwk,
    },
    // Kontoschlüssel zuerst: er trifft im Normalfall beim ersten Versuch.
    decryptionKeys: [
      content.account.privateKeyJwk,
      ...content.legacy.map((e) => e.privateKeyJwk).filter(Boolean),
    ],
  }
}

/**
 * Ermittelt, woran dieses Gerät gerade ist.
 *
 * Erzeugt und veröffentlicht **nichts**. Fehlt der Bund lokal, obwohl der
 * Server einen hat, ist die Antwort `locked` — und nicht ein frisches
 * Schlüsselpaar, das den Verlauf des Kontos unlesbar machen würde.
 */
export async function resolveIdentity(userId: number): Promise<E2eeIdentity> {
  if (!userId) return LOCKED_IDENTITY

  const local = await readStoredKeyring(userId)
  if (local) return toIdentity(local)

  let remote: E2eeKeyringPayload
  try {
    remote = await getE2eeKeyring()
  } catch {
    // Netzwerkfehler ist kein „es gibt keinen Bund". Im Zweifel gesperrt:
    // ein Einrichtungsangebot an dieser Stelle würde den vorhandenen Bund
    // überschreiben, sobald der Benutzer darauf klickt.
    return LOCKED_IDENTITY
  }

  if (!remote?.wrapped_keyring) {
    return { state: 'needs-setup', sendPair: null, decryptionKeys: [] }
  }
  return LOCKED_IDENTITY
}

/**
 * Richtet den Kontoschlüssel erstmalig ein und gibt den
 * Wiederherstellungsschlüssel zurück — das einzige Mal, dass er existiert.
 *
 * Ein bereits vorhandener Gerätesschlüssel wird als Altschlüssel übernommen,
 * damit der bisherige Verlauf dieses Geräts danach überall lesbar ist.
 */
export async function createIdentity(userId: number): Promise<{ recoveryKey: string }> {
  const remote = await getE2eeKeyring()
  if (remote?.wrapped_keyring) {
    // Zwischen `resolveIdentity` und hier hat ein anderes Gerät eingerichtet.
    throw new E2eeLockedError()
  }

  const account = await generateLocalE2eeKeyPair()
  const legacy: KeyringEntry[] = []
  const existingDeviceKey = await readLegacyDeviceKey(userId).catch(() => null)
  if (existingDeviceKey?.privateKeyJwk && existingDeviceKey.publicKeyJwk) {
    legacy.push({ ...existingDeviceKey, adoptedAt: new Date().toISOString() })
  }

  const content: KeyringContent = { v: 1, account, legacy }
  const recoveryKey = generateRecoveryKey()
  const wrapped = await wrapKeyring(content, recoveryKey)

  await putE2eeKeyring({
    wrappedKeyring: wrapped,
    publicKey: account.publicKeyJwk,
    expectedVersion: remote?.version ?? 0,
  })

  // Der alte Store `keys` wird bewusst nicht überschrieben: er ist das Archiv
  // des Gerätesschlüssels aus der Zeit davor und die einzige Quelle für den
  // Verlauf, der damals entstanden ist. Der Kontoschlüssel lebt im Store
  // `keyring`, nicht dort.
  await writeStoredKeyring(userId, content)
  return { recoveryKey }
}

/**
 * Öffnet den Bund auf diesem Gerät.
 *
 * Direkt danach der Rettungsschritt: liegt hier noch ein Gerätesschlüssel, den
 * der Bund nicht kennt, wandert er als Altschlüssel hinein. Erst damit werden
 * die Nachrichten, die dieses Gerät vor der Umstellung geschrieben hat, auch
 * auf allen anderen Geräten lesbar.
 */
export async function unlockWithRecoveryKey(userId: number, recoveryKey: string): Promise<E2eeIdentity> {
  const remote = await getE2eeKeyring()
  if (!remote?.wrapped_keyring) {
    throw new E2eeLockedError()
  }

  const content = await unwrapKeyring(remote.wrapped_keyring, recoveryKey)
  // Erst adoptieren, dann ablegen — und den Store `keys` gar nicht anfassen.
  // Er hält den Gerätesschlüssel von vor der Umstellung; würde der
  // Kontoschlüssel dort hineingeschrieben, wäre das einzige Material, das den
  // alten Verlauf noch öffnen kann, unwiederbringlich überschrieben.
  const adopted = await adoptLocalDeviceKey(userId, content, recoveryKey, remote.version ?? 0)
  await writeStoredKeyring(userId, adopted)
  return toIdentity(adopted)
}

/**
 * Nimmt einen hier liegenden Gerätesschlüssel in den Bund auf, falls er fehlt.
 * Ohne Fund oder bei einem Schreibkonflikt bleibt es beim übergebenen Stand —
 * eine gescheiterte Adoption darf das Entsperren nicht kippen.
 */
async function adoptLocalDeviceKey(
  userId: number,
  content: KeyringContent,
  recoveryKey: string,
  version: number
): Promise<KeyringContent> {
  let deviceKey: LocalE2eeKeyPair | null = null
  try {
    deviceKey = await readLegacyDeviceKey(userId)
  } catch {
    return content
  }
  if (!deviceKey?.privateKeyJwk || !deviceKey.publicKeyJwk) return content

  const known = new Set([
    content.account.publicKeyJwk,
    ...content.legacy.map((e) => e.publicKeyJwk),
  ])
  if (known.has(deviceKey.publicKeyJwk)) return content

  const next: KeyringContent = {
    ...content,
    legacy: [...content.legacy, { ...deviceKey, adoptedAt: new Date().toISOString() }],
  }

  try {
    const wrapped = await wrapKeyring(next, recoveryKey)
    await putE2eeKeyring({
      wrappedKeyring: wrapped,
      publicKey: next.account.publicKeyJwk,
      expectedVersion: version,
    })
  } catch {
    // 409: ein anderes Gerät war schneller. Lokal gilt trotzdem der erweiterte
    // Bund, damit dieses Gerät seinen eigenen Altbestand lesen kann; beim
    // nächsten Entsperren wird erneut versucht, ihn abzulegen.
    await writeStoredKeyring(userId, next)
    return next
  }

  await writeStoredKeyring(userId, next)
  return next
}

/**
 * Der ursprüngliche Gerätesschlüssel aus dem alten Store `keys`.
 * Bewusst direkt gelesen und nicht über `getLocalKeyPair`: dort steht nach dem
 * Entsperren bereits der Kontoschlüssel.
 */
async function readLegacyDeviceKey(userId: number): Promise<LocalE2eeKeyPair | null> {
  const db = await openKeyringDatabase()
  return await new Promise<LocalE2eeKeyPair | null>((resolve, reject) => {
    const tx = db.transaction(IDB_LEGACY_STORE, 'readonly')
    const req = tx.objectStore(IDB_LEGACY_STORE).get(userId)
    req.onsuccess = () => {
      const row = req.result
      if (row?.privateKeyJwk && row?.publicKeyJwk) {
        resolve({ publicKeyJwk: row.publicKeyJwk, privateKeyJwk: row.privateKeyJwk })
      } else {
        resolve(null)
      }
    }
    req.onerror = () => reject(req.error)
  })
}

/**
 * Verpackt den bestehenden Bund unter einem neuen Wiederherstellungsschlüssel.
 * Die Schlüssel selbst bleiben unverändert — der Verlauf bleibt lesbar, nur das
 * Passwort davor wechselt.
 */
export async function rotateRecoveryKey(userId: number): Promise<{ recoveryKey: string }> {
  const content = await readStoredKeyring(userId)
  if (!content) throw new E2eeLockedError()

  const remote = await getE2eeKeyring()
  const recoveryKey = generateRecoveryKey()
  const wrapped = await wrapKeyring(content, recoveryKey)
  await putE2eeKeyring({
    wrappedKeyring: wrapped,
    publicKey: content.account.publicKeyJwk,
    expectedVersion: remote?.version ?? 0,
  })
  return { recoveryKey }
}

// ==========================================
// Empfängerschlüssel
// ==========================================

const RECIPIENT_CACHE_TTL_MS = 600000

const recipientKeyCache = new Map<number, { key: string | null; fetchedAt: number }>()

/**
 * Holt den veröffentlichten Schlüssel eines Empfängers, zehn Minuten gecacht.
 *
 * Die Frist ist der Grund für den Cache: ohne sie hielte ein lange offener Tab
 * einen gewechselten Schlüssel für immer fest und würde gegen einen toten
 * Schlüssel verschlüsseln. `forget` räumt denselben Eintrag, wenn sich beim
 * Entschlüsseln herausstellt, dass er nicht mehr stimmt.
 */
export async function getRecipientPublicKey(userId: number): Promise<string | null> {
  const cached = recipientKeyCache.get(userId)
  if (cached && Date.now() - cached.fetchedAt < RECIPIENT_CACHE_TTL_MS) {
    return cached.key
  }
  try {
    const info = await getE2eePublicKey(userId)
    const key = info?.public_key || null
    recipientKeyCache.set(userId, { key, fetchedAt: Date.now() })
    return key
  } catch {
    return cached?.key ?? null
  }
}

export function forgetRecipientPublicKey(userId: number): void {
  recipientKeyCache.delete(userId)
}

/**
 * Wie `getRecipientPublicKey`, wirft aber statt `null` zu liefern.
 *
 * Der Sendepfad braucht diese Variante: ohne Empfängerschlüssel gab es früher
 * einen symmetrischen Notweg, dessen Schlüssel sich allein aus den beiden
 * Benutzerkennungen ergab — für den Server also nachbaubar. Lieber nicht senden
 * und es sagen, als eine Zusage brechen, die der Benutzer nicht nachprüfen kann.
 */
export async function requireRecipientPublicKey(userId: number): Promise<string> {
  const key = await getRecipientPublicKey(userId)
  if (!key) throw new E2eeRecipientKeyMissingError(userId)
  return key
}
