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
import {
  deriveUserDeviceMailboxId,
  encryptE2eeHybrid,
  decryptE2eeHybrid,
} from './e2eeCrypto'
import {
  eigenesGeraet,
  geraeteVon,
} from './e2eeGeraet'
import {
  relayE2eeEnvelope,
  fetchE2eeEnvelopes,
} from '@/api/social'

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
const syncedInSession = new Set<number>()

/**
 * Die Kennung, unter der bis zum 22.09.2026 **jeder** Schlüssel landete.
 *
 * Der Schreibpfad in `offlineSync` reichte den `userId`-Parameter nicht durch,
 * also griff hier der Vorgabewert 1 — unabhängig davon, wer angemeldet war.
 * Der Lesepfad gab die echte Kennung mit und fand nichts. Für jedes Konto mit
 * einer anderen Kennung als 1 blieb damit alles Chiffretext.
 *
 * Der Schreibfehler ist behoben. Was unter dieser Kennung liegt, muss aber
 * weiter erreichbar bleiben, sonst wäre der gesamte Bestand verloren.
 */
const ALTSCHLUESSEL_KENNUNG = 1

/**
 * Leert den In-Memory-Schlüsselcache (z. B. bei Session-Wipe oder Tests).
 */
export function clearNotesKeyCache(): void {
  keyCache.clear()
  rawKeyMemoryStore.clear()
  syncedInSession.clear()
}

/**
 * Prüft synchron, ob für diesen Benutzer bereits ein lokaler Notizenschlüssel vorliegt.
 */
export function hasUserNotesKey(userId: number = 1): boolean {
  if (keyCache.has(userId) || rawKeyMemoryStore.has(userId)) {
    return true
  }
  const storageKey = `${STORAGE_KEY_PREFIX}${userId}`
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      return !!window.localStorage.getItem(storageKey)
    }
  } catch {}
  return false
}

/**
 * Gibt den bestehenden Schlüssel des Nutzers zurück, falls vorhanden.
 * Versucht bei Fehlen einen Mailbox-Abruf, erzeugt aber NIEMALS selbstständig einen neuen Zufallsschlüssel.
 */
export async function getUserNotesKey(userId: number = 1): Promise<CryptoKey | null> {
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
  } catch {}

  if (!rawBase64) {
    rawBase64 = rawKeyMemoryStore.get(userId) ?? null
  }

  if (!rawBase64) {
    try {
      const received = await checkAndReceiveDeviceNotesKey(userId)
      if (received) {
        rawBase64 = exportUserNotesKey(userId)
      }
    } catch {}
  }

  if (!rawBase64) {
    return null
  }

  const rawBytes = base64ToBytes(rawBase64)
  const importedKey = await importAesGcmRawKey(rawBytes, ['encrypt', 'decrypt'])
  keyCache.set(userId, importedKey)
  return importedKey
}

/**
 * Ermittelt oder erzeugt den symmetrischen AES-256-GCM E2EE-Schlüssel für Notizen & Kalender.
 * Bleibt rein auf dem Client und wird NIEMALS an den Server übertragen.
 */
export async function getOrCreateUserNotesKey(userId: number = 1): Promise<CryptoKey> {
  const cached = keyCache.get(userId)
  if (cached) {
    if (!syncedInSession.has(userId)) {
      syncedInSession.add(userId)
      void syncNotesKeyToPairedDevices(userId).catch(() => {})
    }
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

  // Falls der Schlüssel lokal noch fehlt, versuchen wir ihn aus der Geräte-Mailbox zu empfangen
  if (!rawBase64) {
    try {
      const received = await checkAndReceiveDeviceNotesKey(userId)
      if (received) {
        const afterReceive = exportUserNotesKey(userId)
        if (afterReceive) {
          rawBase64 = afterReceive
        }
      }
    } catch {
      // Fehler beim Mailbox-Abruf ignorieren
    }
  }

  // Hier wird der Altbestand unter der Kennung 1 **nicht** übernommen, obwohl
  // er oft genau der gesuchte Schlüssel wäre. Er ist mehrdeutig: auf einem
  // geteilten Gerät gehört er dem Konto 1, und eine Übernahme ohne Nachweis
  // gäbe dem zweiten Konto den Schlüssel des ersten. Die Übernahme steht in
  // `altschluesselUebernehmen` und verlangt einen Beleg.
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

    syncedInSession.add(userId)
    // Automatische Verteilung an andere bereits gekoppelte Geräte des Benutzers im Hintergrund
    void syncNotesKeyToPairedDevices(userId).catch(() => {})
  } else if (!syncedInSession.has(userId)) {
    syncedInSession.add(userId)
    void syncNotesKeyToPairedDevices(userId).catch(() => {})
  }

  const rawBytes = base64ToBytes(rawBase64)
  const importedKey = await importAesGcmRawKey(rawBytes, ['encrypt', 'decrypt'])
  keyCache.set(userId, importedKey)
  return importedKey
}

/**
 * Setzt oder importiert einen Schlüssel (z. B. nach Geräte-Kopplung oder Übernahme).
 * Triggert bei erfolgreichem Setzen ein globales 'msm:notes-key-updated' Event zur Nach-Entschlüsselung.
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

  if (typeof window !== 'undefined') {
    window.dispatchEvent(
      new CustomEvent('msm:notes-key-updated', { detail: { userId, rawKey: rawBase64 } })
    )
  }

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

/**
 * Liest den Altschlüssel (Kennung 1) — rein lokal, ohne Netz, ohne Erzeugung.
 *
 * Absichtlich getrennt von `getUserNotesKey`: dort hängt ein Mailbox-Abruf
 * daran, und dort gilt die Zusage „ein Schlüssel gehört genau einem Konto".
 * Diese Funktion bricht die Zusage nicht, sie reicht nur einen Kandidaten
 * heraus. Wer ihn benutzt, muss selbst belegen, dass er ihm gehört — siehe
 * `altschluesselUebernehmen`.
 */
export async function altschluessel(): Promise<CryptoKey | null> {
  const zwischengespeichert = keyCache.get(ALTSCHLUESSEL_KENNUNG)
  if (zwischengespeichert) return zwischengespeichert

  const roh = exportUserNotesKey(ALTSCHLUESSEL_KENNUNG)
  if (!roh) return null

  try {
    const schluessel = await importAesGcmRawKey(base64ToBytes(roh), ['encrypt', 'decrypt'])
    keyCache.set(ALTSCHLUESSEL_KENNUNG, schluessel)
    return schluessel
  } catch {
    return null
  }
}

/**
 * Schreibt den Altschlüssel unter die echte Kennung um.
 *
 * Nur aufrufen, wenn erwiesen ist, dass er diesem Konto gehört — und der
 * einzige brauchbare Beweis ist, dass er eine Zeile entschlüsselt, die der
 * Server diesem Konto ausgeliefert hat. Ein Konto bekommt nur die eigenen
 * Zeilen; was sich damit öffnen lässt, war nie fremd.
 *
 * Überschreibt nie einen vorhandenen Schlüssel: liegt unter der echten Kennung
 * schon einer, ist er die Wahrheit und der Altbestand nur noch Geschichte.
 */
export async function altschluesselUebernehmen(userId: number): Promise<boolean> {
  if (userId === ALTSCHLUESSEL_KENNUNG) return false
  if (hasUserNotesKey(userId)) return false

  const roh = exportUserNotesKey(ALTSCHLUESSEL_KENNUNG)
  if (!roh) return false

  // `setUserNotesKey` legt ab, cached, und meldet `msm:notes-key-updated` —
  // daraufhin holt `redecryptPendingOfflineNotesAndCalendar` den Spiegel nach,
  // der bis eben nur Chiffretext hielt.
  await setUserNotesKey(userId, roh)
  return true
}

/**
 * Synchronisiert den Notizen-/Kalenderschlüssel automatisch an alle anderen gekoppelten
 * Geräte desselben Benutzers über die blinde Geräte-Mailbox.
 */
export async function syncNotesKeyToPairedDevices(userId: number = 1): Promise<number> {
  const rawKey = exportUserNotesKey(userId)
  if (!rawKey) return 0

  let self: any = null
  try {
    self = await eigenesGeraet()
  } catch {
    return 0
  }
  if (!self) return 0

  let pairedDevices: any[] = []
  try {
    pairedDevices = await geraeteVon(userId)
  } catch {
    return 0
  }

  const targetDevices = pairedDevices
    .map((d) => ({
      deviceId: d.device_id || (d as any).deviceId,
      publicKey: d.public_key || (d as any).publicKey,
    }))
    .filter((d) => d.deviceId && d.deviceId !== self.kennung && d.publicKey)

  if (targetDevices.length === 0) return 0

  const mailboxId = await deriveUserDeviceMailboxId(userId)
  let sentCount = 0

  for (const target of targetDevices) {
    try {
      const payload = JSON.stringify({
        type: 'notes_key_sync',
        version: 1,
        userId,
        notesKey: rawKey,
        targetDeviceId: target.deviceId,
        senderDeviceId: self.kennung,
        timestamp: Date.now(),
      })
      const ciphertextEnvelope = await encryptE2eeHybrid(payload, target.publicKey)
      await relayE2eeEnvelope({
        blind_mailbox_id: mailboxId,
        ciphertext_envelope: ciphertextEnvelope,
        client_uuid: `noteskeysync:${self.kennung}:${target.deviceId}:${Date.now()}`,
        is_control: true,
        control_type: 'notes_key_sync',
      })
      sentCount++
    } catch {
      // Best-Effort für das jeweilige Zielgerät
    }
  }

  return sentCount
}

/**
 * Fordert den Notizenschlüssel von anderen gekoppelten Geräten des Benutzers an,
 * falls dieses Gerät noch keinen Schlüssel besitzt.
 */
export async function requestNotesKeyFromPairedDevices(userId: number = 1): Promise<boolean> {
  if (hasUserNotesKey(userId)) {
    return false
  }

  let self: any = null
  try {
    self = await eigenesGeraet()
  } catch {
    return false
  }
  if (!self) return false

  let pairedDevices: any[] = []
  try {
    pairedDevices = await geraeteVon(userId)
  } catch {
    return false
  }

  const targetDevices = pairedDevices
    .map((d) => ({
      deviceId: d.device_id || (d as any).deviceId,
      publicKey: d.public_key || (d as any).publicKey,
    }))
    .filter((d) => d.deviceId && d.deviceId !== self.kennung && d.publicKey)

  if (targetDevices.length === 0) return false

  const mailboxId = await deriveUserDeviceMailboxId(userId)
  let sentCount = 0

  for (const target of targetDevices) {
    try {
      const payload = JSON.stringify({
        type: 'notes_key_request',
        version: 1,
        userId,
        requesterDeviceId: self.kennung,
        requesterPublicKey: self.paar.publicKeyJwk,
        timestamp: Date.now(),
      })
      const ciphertextEnvelope = await encryptE2eeHybrid(payload, target.publicKey)
      await relayE2eeEnvelope({
        blind_mailbox_id: mailboxId,
        ciphertext_envelope: ciphertextEnvelope,
        client_uuid: `noteskeyreq:${self.kennung}:${target.deviceId}:${Date.now()}`,
        is_control: true,
        control_type: 'notes_key_request',
      })
      sentCount++
    } catch {
      // Best-Effort
    }
  }

  return sentCount > 0
}

/**
 * Verarbeitet einen verschlüsselten Steuerumschlag aus der Geräte-Mailbox.
 * Erkennt 'notes_key_sync' (Schlüsselimport) und 'notes_key_request' (Beantwortung mit Schlüssel).
 */
export async function processNotesKeyControlEnvelope(
  ciphertextEnvelope: string,
  userId: number = 1
): Promise<boolean> {
  let self: any = null
  try {
    self = await eigenesGeraet()
  } catch {
    return false
  }
  if (!self?.paar?.privateKeyJwk) return false

  let decrypted: string
  try {
    decrypted = await decryptE2eeHybrid(ciphertextEnvelope, self.paar.privateKeyJwk)
  } catch {
    // Umschlag ist für ein anderes Gerät bestimmt oder ungültig
    return false
  }

  let data: any
  try {
    data = JSON.parse(decrypted)
  } catch {
    return false
  }

  if (data?.type === 'notes_key_sync') {
    if (data.notesKey && typeof data.notesKey === 'string') {
      const targetUserId = typeof data.userId === 'number' ? data.userId : userId
      if (!hasUserNotesKey(targetUserId)) {
        await setUserNotesKey(targetUserId, data.notesKey)
        return true
      } else {
        const existing = exportUserNotesKey(targetUserId)
        if (existing && existing !== data.notesKey) {
          // Bereits ein Schlüssel vorhanden, der vom eingegangenen abweicht.
          // Wir verteidigen unseren bestehenden Schlüssel und senden ihn zurück.
          void syncNotesKeyToPairedDevices(targetUserId).catch(() => {})
        }
      }
      return true
    }
  } else if (data?.type === 'notes_key_request') {
    const targetUserId = typeof data.userId === 'number' ? data.userId : userId
    const existingRawKey = exportUserNotesKey(targetUserId)
    if (existingRawKey && data.requesterPublicKey && data.requesterDeviceId) {
      try {
        const payload = JSON.stringify({
          type: 'notes_key_sync',
          version: 1,
          userId: targetUserId,
          notesKey: existingRawKey,
          targetDeviceId: data.requesterDeviceId,
          senderDeviceId: self.kennung,
          timestamp: Date.now(),
        })
        const replyEnv = await encryptE2eeHybrid(payload, data.requesterPublicKey)
        const mailboxId = await deriveUserDeviceMailboxId(targetUserId)
        await relayE2eeEnvelope({
          blind_mailbox_id: mailboxId,
          ciphertext_envelope: replyEnv,
          client_uuid: `noteskeysync:${self.kennung}:${data.requesterDeviceId}:${Date.now()}`,
          is_control: true,
          control_type: 'notes_key_sync',
        })
        return true
      } catch {
        return false
      }
    }
  }

  return false
}

/**
 * Prüft die blinde Geräte-Mailbox auf bereitliegende Schlüsselumschläge.
 * Falls noch kein Schlüssel vorhanden ist, wird nach dem Scan ggf. eine Anforderung gesendet.
 */
export async function checkAndReceiveDeviceNotesKey(userId: number = 1): Promise<boolean> {
  if (hasUserNotesKey(userId)) {
    return true
  }

  try {
    const mailboxId = await deriveUserDeviceMailboxId(userId)
    const envelopes = await fetchE2eeEnvelopes(mailboxId, 0)
    if (Array.isArray(envelopes)) {
      for (let i = envelopes.length - 1; i >= 0; i--) {
        const item = envelopes[i]
        if (item?.ciphertext_envelope) {
          await processNotesKeyControlEnvelope(item.ciphertext_envelope, userId)
          if (hasUserNotesKey(userId)) {
            return true
          }
        }
      }
    }
  } catch {
    // Mailbox-Abruf nicht möglich oder leer
  }

  if (!hasUserNotesKey(userId)) {
    void requestNotesKeyFromPairedDevices(userId).catch(() => {})
  }

  return hasUserNotesKey(userId)
}

/**
 * Prüft die Geräte-Mailbox auf anstehende 'notes_key_request'-Anfragen anderer gekoppelter Geräte,
 * wenn dieses Gerät bereits über den Notizenschlüssel verfügt.
 */
export async function checkAndRespondToDeviceKeyRequests(userId: number = 1): Promise<number> {
  if (!hasUserNotesKey(userId)) {
    return 0
  }
  let responded = 0
  try {
    const mailboxId = await deriveUserDeviceMailboxId(userId)
    const envelopes = await fetchE2eeEnvelopes(mailboxId, 0)
    if (Array.isArray(envelopes)) {
      for (let i = envelopes.length - 1; i >= 0; i--) {
        const item = envelopes[i]
        if (item?.ciphertext_envelope) {
          const handled = await processNotesKeyControlEnvelope(item.ciphertext_envelope, userId)
          if (handled) responded++
        }
      }
    }
  } catch {}
  return responded
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

  const resolvedKey = key ?? (await getUserNotesKey(userId ?? 1))
  if (!resolvedKey) {
    throw new DisDecryptionError(`Kein Notizenschlüssel vorhanden für Entschlüsselung von ${noteUid}.`)
  }

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

  const resolvedKey = key ?? (await getUserNotesKey(userId ?? 1))
  if (!resolvedKey) {
    throw new DisDecryptionError(`Kein Notizenschlüssel vorhanden für Entschlüsselung von ${noteUid}.`)
  }

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
  fieldName: 'title' | 'description' | 'location' | 'recurrence',
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
  fieldName: 'title' | 'description' | 'location' | 'recurrence',
  key?: CryptoKey,
  userId?: number,
): Promise<string> {
  if (!ciphertext) return ''
  if (!ciphertext.startsWith(CALENDAR_CIPHERTEXT_PREFIX)) {
    return ciphertext
  }

  const resolvedKey = key ?? (await getUserNotesKey(userId ?? 1))
  if (!resolvedKey) {
    throw new DisDecryptionError(`Kein Kalenderschlüssel vorhanden für Entschlüsselung von ${fieldName} (${eventUid}).`)
  }

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
