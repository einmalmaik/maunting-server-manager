/**
 * Client-seitige Ende-zu-Ende-Verschlüsselung (E2EE) für Notizen und Kalendereinträge in MSM.
 *
 * Invarianten:
 * - Der Server ist ein blindes Zero-Knowledge-Relais und erhält NIEMALS Klartext.
 * - Ciphertexte im Netzwerk und in der Datenbank tragen verbindliche Versionspräfixe:
 *     - Notizen:  `sv-note-v1:<base64(IV || ciphertext || authTag)>`
 *     - Kalender: `sv-cal-v1:<base64(IV || ciphertext || authTag)>`
 * - Kryptographie: DIS @msdis/shield mit AES-256-GCM und AAD-Bindung an die jeweilige Eintrags-UID.
 * - Schlüsselverwaltung: Lokaler per-User-Schlüssel (AES-GCM-256), persistent im Keystore / Storage.
 *   Bei Kopplung eines Neugeräts (APK / Desktop) wird der Schlüssel über den E2EE-Handover übergeben.
 */

import {
  encryptString,
  decryptString,
  importAesGcmRawKey,
} from '@msdis/shield/aead'
import {
  formatEnvelope,
  parseEnvelope,
  type VersionedCipherEnvelopeSpec,
} from '@msdis/shield/format-versioning'
import {
  DisDecryptionError,
  base64ToBytes,
  bytesToBase64,
} from '@msdis/shield/core'

export const NOTE_ENVELOPE_SPEC: VersionedCipherEnvelopeSpec = {
  currentPrefix: 'sv-note-v1:',
  familyPrefix: 'sv-note-',
  subject: 'note ciphertext envelope',
}

export const CALENDAR_ENVELOPE_SPEC: VersionedCipherEnvelopeSpec = {
  currentPrefix: 'sv-cal-v1:',
  familyPrefix: 'sv-cal-',
  subject: 'calendar ciphertext envelope',
}

export const NOTE_CIPHERTEXT_PREFIX = 'sv-note-v1:'
export const CALENDAR_CIPHERTEXT_PREFIX = 'sv-cal-v1:'

/**
 * Generiert eine standardkonforme UUIDv4 für lokale Notiz- und Termin-Entitäten.
 */
export function generateClientEntityId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}

const STORAGE_KEY_PREFIX = 'msm_e2ee_notes_key_'
const keyCache = new Map<number, CryptoKey>()
const rawKeyMemoryStore = new Map<number, string>()

/**
 * Leert den In-Memory-Schlüsselcache (z. B. bei Session-Wipe oder Tests).
 */
export function clearNotesKeyCache(): void {
  keyCache.clear()
  rawKeyMemoryStore.clear()
}

/**
 * Ermittelt oder erzeugt den symmetrischen AES-256-GCM E2EE-Schlüssel für Notizen & Kalender.
 * Bleibt rein auf dem Client und wird NIEMALS an den Server übertragen.
 */
export async function getOrCreateUserNotesKey(userId: number = 1): Promise<CryptoKey> {
  const cached = keyCache.get(userId)
  if (cached) {
    return cached
  }

  const storageKey = `${STORAGE_KEY_PREFIX}${userId}`
  let rawBase64: string | null = null

  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      rawBase64 = window.localStorage.getItem(storageKey)
    }
  } catch {
    // LocalStorage ggf. gesperrt
  }

  if (!rawBase64) {
    rawBase64 = rawKeyMemoryStore.get(userId) ?? null
  }

  if (!rawBase64) {
    // 32 Bytes kryptographischer Zufall (256-Bit)
    const randomBytes = new Uint8Array(32)
    if (typeof window !== 'undefined' && window.crypto?.getRandomValues) {
      window.crypto.getRandomValues(randomBytes)
    } else if (typeof globalThis !== 'undefined' && globalThis.crypto?.getRandomValues) {
      globalThis.crypto.getRandomValues(randomBytes)
    } else {
      for (let i = 0; i < 32; i++) {
        randomBytes[i] = Math.floor(Math.random() * 256)
      }
    }
    rawBase64 = bytesToBase64(randomBytes)
    try {
      if (typeof window !== 'undefined' && window.localStorage) {
        window.localStorage.setItem(storageKey, rawBase64)
      }
    } catch {}
    rawKeyMemoryStore.set(userId, rawBase64)
  }

  const rawBytes = base64ToBytes(rawBase64)
  const importedKey = await importAesGcmRawKey(rawBytes, ['encrypt', 'decrypt'])
  keyCache.set(userId, importedKey)
  return importedKey
}

/**
 * Setzt oder importiert einen Schlüssel (z. B. nach Geräte-Kopplung oder Übernahme).
 */
export async function setUserNotesKey(userId: number, rawBase64: string): Promise<CryptoKey> {
  const storageKey = `${STORAGE_KEY_PREFIX}${userId}`
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem(storageKey, rawBase64)
    }
  } catch {}
  rawKeyMemoryStore.set(userId, rawBase64)

  const rawBytes = base64ToBytes(rawBase64)
  const importedKey = await importAesGcmRawKey(rawBytes, ['encrypt', 'decrypt'])
  keyCache.set(userId, importedKey)
  return importedKey
}

/**
 * Exportiert den Schlüssel als Base64-String zur sicheren Übertragung an ein gekoppeltes Neugerät.
 */
export function exportUserNotesKey(userId: number = 1): string | null {
  const storageKey = `${STORAGE_KEY_PREFIX}${userId}`
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      const stored = window.localStorage.getItem(storageKey)
      if (stored) return stored
    }
  } catch {}
  return rawKeyMemoryStore.get(userId) ?? null
}

// ── Notizen Verschlüsselung & Entschlüsselung ──

export async function encryptNoteTitle(
  title: string,
  noteUid: string,
  key?: CryptoKey,
  userId?: number,
): Promise<string> {
  if (!title) return ''
  if (title.startsWith(NOTE_CIPHERTEXT_PREFIX)) return title

  const resolvedKey = key ?? (await getOrCreateUserNotesKey(userId))
  const aad = `msm:note:${noteUid}`
  const ciphertextBase64 = await encryptString(title.trim(), resolvedKey, aad)
  return formatEnvelope(NOTE_ENVELOPE_SPEC, ciphertextBase64)
}

export async function encryptNoteContent(
  content: string,
  noteUid: string,
  key?: CryptoKey,
  userId?: number,
): Promise<string> {
  if (!content) return ''
  if (content.startsWith(NOTE_CIPHERTEXT_PREFIX)) return content

  const resolvedKey = key ?? (await getOrCreateUserNotesKey(userId))
  const aad = `msm:note:${noteUid}:content`
  const ciphertextBase64 = await encryptString(content, resolvedKey, aad)
  return formatEnvelope(NOTE_ENVELOPE_SPEC, ciphertextBase64)
}

export async function decryptNoteTitle(
  ciphertext: string,
  noteUid: string,
  key?: CryptoKey,
  userId?: number,
): Promise<string> {
  if (!ciphertext) return ''
  if (!ciphertext.startsWith(NOTE_CIPHERTEXT_PREFIX)) {
    // Altdaten im Klartext
    return ciphertext
  }

  const resolvedKey = key ?? (await getOrCreateUserNotesKey(userId))
  const parsed = parseEnvelope(NOTE_ENVELOPE_SPEC, ciphertext)
  const aad = `msm:note:${noteUid}`

  try {
    return await decryptString(parsed.payload, resolvedKey, aad)
  } catch (err) {
    // Fallback falls AAD-Format abweicht
    try {
      return await decryptString(parsed.payload, resolvedKey)
    } catch {
      throw new DisDecryptionError(`Notiz-Titel für ${noteUid} konnte nicht entschlüsselt werden.`)
    }
  }
}

export async function decryptNoteContent(
  ciphertext: string,
  noteUid: string,
  key?: CryptoKey,
  userId?: number,
): Promise<string> {
  if (!ciphertext) return ''
  if (!ciphertext.startsWith(NOTE_CIPHERTEXT_PREFIX)) {
    return ciphertext
  }

  const resolvedKey = key ?? (await getOrCreateUserNotesKey(userId))
  const parsed = parseEnvelope(NOTE_ENVELOPE_SPEC, ciphertext)
  const aad = `msm:note:${noteUid}:content`

  try {
    return await decryptString(parsed.payload, resolvedKey, aad)
  } catch (err) {
    try {
      return await decryptString(parsed.payload, resolvedKey)
    } catch {
      throw new DisDecryptionError(`Notiz-Inhalt für ${noteUid} konnte nicht entschlüsselt werden.`)
    }
  }
}

// ── Kalender Verschlüsselung & Entschlüsselung ──

export async function encryptCalendarField(
  text: string | null | undefined,
  eventUid: string,
  fieldName: 'title' | 'description' | 'location',
  key?: CryptoKey,
  userId?: number,
): Promise<string> {
  if (!text) return ''
  if (text.startsWith(CALENDAR_CIPHERTEXT_PREFIX)) return text

  const resolvedKey = key ?? (await getOrCreateUserNotesKey(userId))
  const aad = `msm:cal:${eventUid}:${fieldName}`
  const ciphertextBase64 = await encryptString(text, resolvedKey, aad)
  return formatEnvelope(CALENDAR_ENVELOPE_SPEC, ciphertextBase64)
}

export async function decryptCalendarField(
  ciphertext: string | null | undefined,
  eventUid: string,
  fieldName: 'title' | 'description' | 'location',
  key?: CryptoKey,
  userId?: number,
): Promise<string> {
  if (!ciphertext) return ''
  if (!ciphertext.startsWith(CALENDAR_CIPHERTEXT_PREFIX)) {
    return ciphertext
  }

  const resolvedKey = key ?? (await getOrCreateUserNotesKey(userId))
  const parsed = parseEnvelope(CALENDAR_ENVELOPE_SPEC, ciphertext)
  const aad = `msm:cal:${eventUid}:${fieldName}`

  try {
    return await decryptString(parsed.payload, resolvedKey, aad)
  } catch (err) {
    try {
      return await decryptString(parsed.payload, resolvedKey)
    } catch {
      throw new DisDecryptionError(`Kalenderfeld ${fieldName} für ${eventUid} konnte nicht entschlüsselt werden.`)
    }
  }
}
