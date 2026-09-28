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
 * - Übergabe zwischen eigenen Geräten: nur an Geräte, die das Verzeichnis unter **diesem** Konto
 *   führt, versiegelt gegen deren Schlüssel von dort, und nur angenommen mit der Unterschrift eines
 *   solchen Geräts (`uebergabeDaten`). In die eigene Geräte-Mailbox darf jeder Freund, jedes
 *   Gruppenmitglied und jedes Gegenüber eines Direktchats Steuerumschläge legen; dass sich ein
 *   Umschlag öffnen lässt, sagt deshalb nur, *für* wen er ist — nie, von wem.
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
import { signiere } from './absenderSignatur'
import {
  eigenesGeraet,
  geraeteVon,
  pruefeGeraeteBeleg,
  verzeichnisVon,
  type EigenesGeraet,
} from './e2eeGeraet'
import {
  relayE2eeEnvelope,
  fetchE2eeEnvelopes,
  type E2eeGeraetItem,
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
/** Der Abgleich unter eigenen Geräten, wo es keine Ablage gibt — siehe `leseAbgleich`. */
const abgleichOhneAblage = new Map<number, Abgleich>()
/** Wann dieses Gerät zuletzt nach dem Schlüssel gefragt hat — siehe `requestNotesKeyFromPairedDevices`. */
const letzteAnfrage = new Map<number, number>()
/** Wann dieses Gerät einem fragenden zuletzt geantwortet hat, je `konto:gerät` — siehe `verarbeiteSteuerumschlag`. */
const letzteAntwort = new Map<string, number>()

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

const bestaetigteGeraeteFuerNotizen = new Set<string>()

export function bestaetigeGeraetFuerNotizenSchluessel(userId: number, deviceId: string): void {
  bestaetigteGeraeteFuerNotizen.add(`${userId}:${deviceId}`)
  try {
    if (typeof localStorage !== 'undefined') {
      const raw = localStorage.getItem(`msm_notes_approved_devices:${userId}`)
      const list: string[] = raw ? JSON.parse(raw) : []
      if (!list.includes(deviceId)) {
        list.push(deviceId)
        localStorage.setItem(`msm_notes_approved_devices:${userId}`, JSON.stringify(list))
      }
    }
  } catch {}
}

export function istGeraetBestaetigtFuerNotizenSchluessel(userId: number, deviceId: string): boolean {
  if (bestaetigteGeraeteFuerNotizen.has(`${userId}:${deviceId}`)) return true
  try {
    if (typeof localStorage !== 'undefined') {
      const raw = localStorage.getItem(`msm_notes_approved_devices:${userId}`)
      if (!raw) return false
      const list: string[] = JSON.parse(raw)
      return Array.isArray(list) && list.includes(deviceId)
    }
  } catch {
    return false
  }
  return false
}

/**
 * Leert den In-Memory-Schlüsselcache (z. B. bei Session-Wipe oder Tests).
 */
export function clearNotesKeyCache(): void {
  keyCache.clear()
  rawKeyMemoryStore.clear()
  syncedInSession.clear()
  abgleichOhneAblage.clear()
  letzteAnfrage.clear()
  letzteAntwort.clear()
  laufendeVerteilung.clear()
  laufenderDurchgang.clear()
  bestaetigteGeraeteFuerNotizen.clear()
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
 * Die Zeichenkette, über die eine Übergabe des Notizschlüssels unterschrieben wird.
 *
 * Konto, sendendes und empfangendes Gerät und der Schlüssel selbst. Das
 * Zielgerät gehört hinein, sonst ließe sich eine Übergabe, die einem Gerät
 * galt, einem anderen vorlegen. Der Vorsatz hält diese Unterschrift von denen
 * über Nutzlasten und Sitzungsaufbauten fern, die mit demselben Schlüssel
 * entstehen.
 */
function uebergabeDaten(userId: number, vonGeraet: string, anGeraet: string, notesKey: string): string {
  return JSON.stringify(['msm:notes-key-sync:v1', userId, vonGeraet, anGeraet, notesKey])
}

/**
 * Die Unterschrift dieses Geräts unter die Übergabe an `anGeraet`, oder `null`,
 * wenn es nicht unterschreiben kann.
 *
 * Für beide Wege, auf denen der Schlüssel wandert: den Steuerumschlag in der
 * Geräte-Mailbox und das Paket beim Koppeln (`verlaufsUebergabe`). Dieselbe
 * Aussage auf beiden — „dieses Gerät gibt jenem den Schlüssel dieses Kontos" —,
 * deshalb dieselbe Zeichenkette.
 */
export async function unterschreibeNotizUebergabe(
  self: EigenesGeraet,
  userId: number,
  anGeraet: string,
  notesKey: string,
): Promise<string | null> {
  try {
    return await signiere(
      uebergabeDaten(userId, self.kennung, anGeraet, notesKey),
      self.signaturPaar.privateKeyJwk,
    )
  } catch {
    return null
  }
}

/**
 * Belegt `sig`, dass `vonGeraet` — laut Verzeichnis ein Gerät dieses Kontos —
 * den Schlüssel an `anGeraet` übergeben hat?
 *
 * `offen` und `unbekannt` zählen hier als Nein. Wer später noch einmal fragen
 * kann, wie der Lesepfad der Geräte-Mailbox, prüft mit `pruefeGeraeteBeleg`
 * selbst.
 */
export async function pruefeNotizUebergabe(
  userId: number,
  vonGeraet: string,
  anGeraet: string,
  notesKey: string,
  sig: unknown,
): Promise<boolean> {
  const beleg = await pruefeGeraeteBeleg(
    userId,
    vonGeraet,
    uebergabeDaten(userId, vonGeraet, anGeraet, notesKey),
    sig,
  )
  return beleg === 'echt' || beleg === 'altbestand'
}

/**
 * Die unterschriebene Übergabe an ein eigenes Gerät, oder `null`.
 *
 * `null`, wenn dieses Gerät nicht unterschreiben kann. Dann wird gar nicht erst
 * gesendet: das Zielgerät nähme eine Übergabe ohne Unterschrift nicht an.
 */
async function unterschriebeneUebergabe(
  self: EigenesGeraet,
  userId: number,
  anGeraet: string,
  notesKey: string,
): Promise<string | null> {
  const sig = await unterschreibeNotizUebergabe(self, userId, anGeraet, notesKey)
  if (!sig) return null
  return JSON.stringify({
    type: 'notes_key_sync',
    version: 1,
    userId,
    notesKey,
    targetDeviceId: anGeraet,
    senderDeviceId: self.kennung,
    timestamp: Date.now(),
    sig,
  })
}

/**
 * Die Umschlagkennung eines Steuerumschlags zwischen zwei eigenen Geräten.
 *
 * Das Relais nimmt höchstens 64 Zeichen (`client_uuid` in
 * `backend/schemas/social.py`). Bis 09/2026 standen hier beide
 * Gerätekennungen in voller Länge, zusammen 91 Zeichen: jede Anfrage und jede
 * Übergabe scheiterte mit 422, und der Schlüssel wanderte nie zwischen den
 * Geräten. Zwölf Zeichen je Gerät unterscheiden sie genauso gut — derselbe
 * Schnitt wie beim Auffächern im Ratchet (`KENNUNG_KURZ`).
 */
function umschlagKennung(art: 'noteskeyreq' | 'noteskeysync', von: string, an: string): string {
  return `${art}:${von.slice(0, 12)}:${an.slice(0, 12)}:${Date.now()}`
}

// ── Abgleich unter eigenen Geräten ──
//
// Die Geräte-Mailbox teilen sich alle Geräte eines Kontos, und jeder Abruf
// liefert die neuesten hundert Umschläge — auch die, mit denen dieses Gerät
// längst fertig ist. Bis 09/2026 beantwortete jeder Abruf jede Anfrage darin
// aufs Neue, und jedes Laden von Notizen oder Kalender schickte den Schlüssel
// noch einmal an jedes eigene Gerät. Aufgefallen ist das erst, als die
// Umschläge das Relais überhaupt erreichten (`umschlagKennung`): in der
// Laufzeitprobe über sechzig in der Minute, und weil das Relais je Adresse
// zählt, bekamen danach auch echte Nachrichten ein 429.
//
// Deshalb merkt sich jedes Gerät zweierlei, über das Neuladen hinaus: mit
// welchen Umschlägen es fertig ist, und welchen Geräten es welchen Schlüssel
// gegeben hat. Vom Schlüssel steht dort nur ein Fingerabdruck.

const ABGLEICH_PREFIX = 'msm_notes_abgleich_'
/** Ein Abruf liefert höchstens 100 Umschläge; mehr muss sich kein Gerät merken. */
const ERLEDIGT_HOECHSTENS = 300
/** So lange fragt ein Gerät ohne Schlüssel nicht noch einmal nach. */
const ANFRAGE_PAUSE_MS = 10 * 60 * 1000

type Abgleich = {
  /** Das Gerät, für das der Rest gilt. Mit einer neuen Kennung beginnt es von vorn. */
  geraet: string
  /** Fingerabdruck des Schlüssels, den die Geräte in `an` von hier bekommen haben. */
  abdruck: string
  an: string[]
  /** Umschläge der Geräte-Mailbox, mit denen dieses Gerät fertig ist. */
  erledigt: number[]
}

function leseAbgleich(userId: number, geraet: string): Abgleich {
  let stand: Partial<Abgleich> | undefined = abgleichOhneAblage.get(userId)
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      const roh = window.localStorage.getItem(`${ABGLEICH_PREFIX}${userId}`)
      if (roh) stand = JSON.parse(roh)
    }
  } catch {}
  if (!stand || stand.geraet !== geraet) return { geraet, abdruck: '', an: [], erledigt: [] }
  return {
    geraet,
    abdruck: typeof stand.abdruck === 'string' ? stand.abdruck : '',
    an: Array.isArray(stand.an) ? stand.an.filter((x): x is string => typeof x === 'string') : [],
    erledigt: Array.isArray(stand.erledigt)
      ? stand.erledigt.filter((x): x is number => typeof x === 'number')
      : [],
  }
}

function schreibeAbgleich(userId: number, stand: Abgleich): void {
  const knapp = { ...stand, erledigt: stand.erledigt.slice(-ERLEDIGT_HOECHSTENS) }
  abgleichOhneAblage.set(userId, knapp)
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem(`${ABGLEICH_PREFIX}${userId}`, JSON.stringify(knapp))
    }
  } catch {}
}

// Lesen und Schreiben jeweils ohne `await` dazwischen: zwei Abrufe, die sich
// überlappen, überschreiben so keinen Eintrag des anderen.

function istErledigt(userId: number, geraet: string, umschlag: number): boolean {
  return leseAbgleich(userId, geraet).erledigt.includes(umschlag)
}

function merkeErledigt(userId: number, geraet: string, umschlag: number): void {
  const stand = leseAbgleich(userId, geraet)
  if (stand.erledigt.includes(umschlag)) return
  stand.erledigt.push(umschlag)
  schreibeAbgleich(userId, stand)
}

function schonGegeben(userId: number, geraet: string, abdruck: string, an: string): boolean {
  const stand = leseAbgleich(userId, geraet)
  return stand.abdruck === abdruck && stand.an.includes(an)
}

function merkeGegeben(userId: number, geraet: string, abdruck: string, an: string): void {
  const stand = leseAbgleich(userId, geraet)
  if (stand.abdruck !== abdruck) {
    stand.abdruck = abdruck
    stand.an = []
  }
  if (stand.an.includes(an)) return
  stand.an.push(an)
  schreibeAbgleich(userId, stand)
}

/**
 * Ein Fingerabdruck des Schlüssels: genug, um einen neuen zu erkennen, und zu
 * wenig, um auf den alten zu schließen.
 */
async function abdruckVon(rawKey: string): Promise<string> {
  const hash = await globalThis.crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(`msm:notes-key-abdruck:v1:${rawKey}`),
  )
  return bytesToBase64(new Uint8Array(hash)).slice(0, 22)
}

/**
 * Was aus einem Steuerumschlag der Geräte-Mailbox wurde.
 *
 * - `erledigt`: angenommen, beantwortet oder schon bekannt.
 * - `verworfen`: nicht für dieses Gerät, oder ein endgültiges Nein.
 * - `offen`: noch nicht zu entscheiden — auch, wenn das andere Gerät nicht im
 *   Verzeichnis steht; der nächste Abruf versucht es wieder.
 *
 * Gemerkt werden nur die ersten beiden — ein Nein, das nur „jetzt nicht"
 * hieß, darf nicht hängen bleiben.
 */
type Ausgang = 'erledigt' | 'verworfen' | 'offen'

/**
 * Das Gerät dieses Kontos mit dieser Kennung aus dem Verzeichnis, oder `null`.
 *
 * Ein Gerät, das sich eben erst gemeldet hat, steht in der zehn Minuten alten
 * Liste noch nicht — deshalb vor dem Nein einmal frisch nachsehen. Antwortet
 * das Verzeichnis nicht, wirft sie: das ist kein Nein, sondern noch keine
 * Antwort, und die Anfrage wird beim nächsten Abruf wieder gelesen.
 */
async function eigenesVerzeichnisGeraet(
  userId: number,
  deviceId: string,
): Promise<E2eeGeraetItem | null> {
  const treffer = (await verzeichnisVon(userId)).find((g) => g.device_id === deviceId)
  if (treffer?.public_key) return treffer
  const frisch = (await verzeichnisVon(userId, { frisch: true })).find(
    (g) => g.device_id === deviceId,
  )
  return frisch?.public_key ? frisch : null
}

/**
 * Ein Lauf, der für dieses Konto schon unterwegs ist, oder ein neuer.
 *
 * Erinnerungen, Notizen und Messenger laden gemeinsam, und jeder Ladevorgang
 * gleicht ab. Zwei Läufe zugleich sähen dieselben Geräte als „noch nicht
 * gegeben" — vermerkt wird erst nach dem Senden — und schickten doppelt; in der
 * Laufzeitprobe vom 23.09. bekam jedes Gerät den Schlüssel zweimal in derselben
 * Sekunde. Der zweite Aufruf wartet deshalb auf den ersten.
 */
function einLaufZugleich<K>(
  laufend: Map<K, Lauf>,
  schluessel: K,
  lauf: () => Promise<number>,
): Promise<number> {
  const schon = laufend.get(schluessel)
  if (schon) {
    // Wer dazukommt, bekommt einen Nachlauf: sein Anlass — eine neue Anfrage,
    // ein neues Gerät — kam womöglich erst nach dem Abruf des laufenden an.
    // Ein Nachlauf genügt für alle, die bis dahin dazukommen.
    schon.nochmal = true
    return schon.ergebnis
  }
  const eintrag: Lauf = { ergebnis: Promise.resolve(0), nochmal: false }
  eintrag.ergebnis = (async () => {
    let summe = 0
    try {
      do {
        eintrag.nochmal = false
        summe += await lauf()
      } while (eintrag.nochmal)
    } finally {
      laufend.delete(schluessel)
    }
    return summe
  })()
  laufend.set(schluessel, eintrag)
  return eintrag.ergebnis
}

type Lauf = { ergebnis: Promise<number>; nochmal: boolean }

const laufendeVerteilung = new Map<number, Lauf>()
const laufenderDurchgang = new Map<string, Lauf>()

/**
 * Synchronisiert den Notizen-/Kalenderschlüssel automatisch an alle anderen gekoppelten
 * Geräte desselben Benutzers über die blinde Geräte-Mailbox.
 */
export function syncNotesKeyToPairedDevices(userId: number = 1): Promise<number> {
  return einLaufZugleich(laufendeVerteilung, userId, () => verteileSchluessel(userId))
}

async function verteileSchluessel(userId: number): Promise<number> {
  const rawKey = exportUserNotesKey(userId)
  if (!rawKey) return 0

  let self: EigenesGeraet | null = null
  try {
    self = await eigenesGeraet()
  } catch {
    return 0
  }
  if (!self) return 0
  const ich = self

  let pairedDevices: any[] = []
  try {
    pairedDevices = await geraeteVon(userId)
  } catch {
    return 0
  }

  // Jedem Gerät einmal je Schlüssel — siehe „Abgleich unter eigenen Geräten".
  // Wer ihn verloren hat, fragt; beantwortet wird jede Anfrage.
  const abdruck = await abdruckVon(rawKey)
  const targetDevices = pairedDevices
    .map((d) => ({
      deviceId: d.device_id || (d as any).deviceId,
      publicKey: d.public_key || (d as any).publicKey,
      isApproved: d.is_approved,
      approvedBy: d.approved_by,
    }))
    .filter((d) => d.deviceId && d.deviceId !== ich.kennung && d.publicKey)
    .filter((d) => d.isApproved !== false)
    .filter(
      (d) =>
        d.isApproved !== true ||
        d.approvedBy === ich.kennung ||
        istGeraetBestaetigtFuerNotizenSchluessel(userId, d.deviceId),
    )
    .filter((d) => !schonGegeben(userId, ich.kennung, abdruck, d.deviceId))

  if (targetDevices.length === 0) return 0

  const mailboxId = await deriveUserDeviceMailboxId(userId)
  let sentCount = 0

  for (const target of targetDevices) {
    try {
      const payload = await unterschriebeneUebergabe(ich, userId, target.deviceId, rawKey)
      if (!payload) continue
      const ciphertextEnvelope = await encryptE2eeHybrid(payload, target.publicKey)
      await relayE2eeEnvelope({
        blind_mailbox_id: mailboxId,
        ciphertext_envelope: ciphertextEnvelope,
        client_uuid: umschlagKennung('noteskeysync', ich.kennung, target.deviceId),
        is_control: true,
        control_type: 'notes_key_sync',
      })
      merkeGegeben(userId, ich.kennung, abdruck, target.deviceId)
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

  // Höchstens eine Runde je Pause. Die Marke steht, bevor das erste `await`
  // fällt: Notizen, Kalender und Erinnerungen laden gleichzeitig, und jeder
  // Aufruf sähe sonst noch keine Runde. Öfter zu fragen bringt nichts — eine
  // gestellte Anfrage bleibt in der Mailbox, und ein Gerät, das gerade nicht
  // läuft, beantwortet sie beim nächsten Start. Solange noch nichts gesendet
  // ist, fällt die Marke wieder.
  const jetzt = Date.now()
  const zuletzt = letzteAnfrage.get(userId)
  if (zuletzt !== undefined && jetzt - zuletzt < ANFRAGE_PAUSE_MS) return false
  letzteAnfrage.set(userId, jetzt)

  let self: any = null
  try {
    self = await eigenesGeraet()
  } catch {
    letzteAnfrage.delete(userId)
    return false
  }
  if (!self) {
    letzteAnfrage.delete(userId)
    return false
  }

  let pairedDevices: any[] = []
  try {
    pairedDevices = await geraeteVon(userId)
  } catch {
    letzteAnfrage.delete(userId)
    return false
  }

  const targetDevices = pairedDevices
    .map((d) => ({
      deviceId: d.device_id || (d as any).deviceId,
      publicKey: d.public_key || (d as any).publicKey,
    }))
    .filter((d) => d.deviceId && d.deviceId !== self.kennung && d.publicKey)

  if (targetDevices.length === 0) {
    letzteAnfrage.delete(userId)
    return false
  }

  let mailboxId: string
  try {
    mailboxId = await deriveUserDeviceMailboxId(userId)
  } catch {
    letzteAnfrage.delete(userId)
    return false
  }
  let sentCount = 0

  for (const target of targetDevices) {
    try {
      const payload = JSON.stringify({
        type: 'notes_key_request',
        version: 1,
        userId,
        requesterDeviceId: self.kennung,
        // Kein eigener öffentlicher Schlüssel mehr: versiegelt wird gegen den
        // aus dem Verzeichnis. Gegenstellen von vor 09/2026, die noch den aus
        // der Anfrage nahmen, antworten unsigniert — und das nimmt dieses
        // Gerät ohnehin nicht mehr an.
        timestamp: Date.now(),
      })
      const ciphertextEnvelope = await encryptE2eeHybrid(payload, target.publicKey)
      await relayE2eeEnvelope({
        blind_mailbox_id: mailboxId,
        ciphertext_envelope: ciphertextEnvelope,
        client_uuid: umschlagKennung('noteskeyreq', self.kennung, target.deviceId),
        is_control: true,
        control_type: 'notes_key_request',
      })
      sentCount++
    } catch {
      // Best-Effort
    }
  }

  // Ging nichts hinaus — jede Anfrage etwa am 429 des Relais gescheitert —,
  // hält die Pause nichts auf, was gefragt wurde.
  if (sentCount === 0) letzteAnfrage.delete(userId)
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
  return (await verarbeiteSteuerumschlag(ciphertextEnvelope, userId)) === 'erledigt'
}

/** Wie `processNotesKeyControlEnvelope`, sagt aber auch, ob ein neuer Versuch lohnt. */
async function verarbeiteSteuerumschlag(
  ciphertextEnvelope: string,
  userId: number,
): Promise<Ausgang> {
  let self: EigenesGeraet | null = null
  try {
    self = await eigenesGeraet()
  } catch {
    return 'offen'
  }
  if (!self?.paar?.privateKeyJwk) return 'offen'

  let decrypted: string
  try {
    decrypted = await decryptE2eeHybrid(ciphertextEnvelope, self.paar.privateKeyJwk)
  } catch {
    // Umschlag ist für ein anderes Gerät bestimmt oder ungültig
    return 'verworfen'
  }

  let data: any
  try {
    data = JSON.parse(decrypted)
  } catch {
    return 'verworfen'
  }

  if (data?.type === 'notes_key_sync') {
    if (typeof data.notesKey !== 'string' || !data.notesKey) return 'verworfen'
    // Nur für das eigene Konto und nur für dieses Gerät. Bis 09/2026 nahm
    // dieses Gerät jeden Schlüssel an, der sich öffnen ließ, und legte ihn unter
    // dem Konto ab, das der Umschlag nannte. Ein untergeschobener Schlüssel
    // hätte jede Notiz, die dieses Gerät danach schreibt, für den lesbar
    // gemacht, der ihn geschickt hat.
    if (data.userId !== userId || data.targetDeviceId !== self.kennung) return 'verworfen'
    const vonGeraet = typeof data.senderDeviceId === 'string' ? data.senderDeviceId : ''
    if (!vonGeraet || vonGeraet === self.kennung) return 'verworfen'
    const beleg = await pruefeGeraeteBeleg(
      userId,
      vonGeraet,
      uebergabeDaten(userId, vonGeraet, self.kennung, data.notesKey),
      data.sig,
    )
    // 'offen' und 'unbekannt' sind kein Nein: der Umschlag bleibt in der
    // Mailbox, und der nächste Abruf prüft ihn wieder.
    if (beleg === 'offen' || beleg === 'unbekannt') return 'offen'
    if (beleg !== 'echt' && beleg !== 'altbestand') return 'verworfen'

    if (!hasUserNotesKey(userId)) {
      await setUserNotesKey(userId, data.notesKey)
      return 'erledigt'
    }
    const existing = exportUserNotesKey(userId)
    if (existing && existing !== data.notesKey) {
      // Bereits ein Schlüssel vorhanden, der vom eingegangenen abweicht.
      // Wir verteidigen unseren bestehenden Schlüssel und senden ihn zurück —
      // jedem Gerät einmal. Bis 09/2026 ging er bei jedem solchen Umschlag an
      // alle, und das andere Gerät hielt es genauso: ein Pingpong ohne Ende.
      await syncNotesKeyToPairedDevices(userId).catch(() => 0)
    }
    return 'erledigt'
  } else if (data?.type === 'notes_key_request') {
    // Nur für das eigene Konto: auf einem geteilten Gerät liegt womöglich auch
    // der Schlüssel eines anderen, und die Anfrage bestimmt nicht, welcher.
    if (data.userId !== userId) return 'verworfen'
    const anGeraet = typeof data.requesterDeviceId === 'string' ? data.requesterDeviceId : ''
    if (!anGeraet || anGeraet === self.kennung) return 'verworfen'
    const existingRawKey = exportUserNotesKey(userId)
    // Ohne Schlüssel ist nichts zu geben — noch nicht. Kommt er später, wird
    // dieselbe Anfrage dann beantwortet.
    if (!existingRawKey) return 'offen'

    // Jedem fragenden Gerät höchstens eine Antwort je Pause. In die eigene
    // Geräte-Mailbox darf jeder Freund und jedes Gruppenmitglied einwerfen, und
    // eine Anfrage, die ein echtes eigenes Gerät nennt, bekam bis zur
    // Durchsicht vom 23.09. jedes Mal ihre Antwort: hundert gefälschte in der
    // Minute, und das Relais wies danach auch die echten Nachrichten dieses
    // Kontos mit 429 ab. Verraten hat die Antwort nie etwas — sie ist gegen
    // das Verzeichnis versiegelt —, es ging um die Last. Kam die letzte Antwort
    // nicht an, fragt das Gerät nach seiner eigenen Pause wieder.
    const beantwortet = letzteAntwort.get(`${userId}:${anGeraet}`)
    if (beantwortet !== undefined && Date.now() - beantwortet < ANFRAGE_PAUSE_MS) {
      return 'verworfen'
    }

    // Versiegelt wird gegen den Schlüssel, den das Verzeichnis für dieses Gerät
    // unter **diesem** Konto führt — nie gegen den aus der Anfrage. Bis 09/2026
    // stand hier `data.requesterPublicKey`: wer einen Umschlag in die eigene
    // Geräte-Mailbox legen durfte, bekam den Notizschlüssel gegen seinen
    // eigenen Schlüssel versiegelt zurück.
    let ziel: E2eeGeraetItem | null
    try {
      ziel = await eigenesVerzeichnisGeraet(userId, anGeraet)
    } catch {
      return 'offen'
    }
    // Auch „nicht im Verzeichnis" bleibt offen, genau wie `unbekannt` bei der
    // Unterschrift: ein Gerät, das sich eben angemeldet hat, fehlt in einer
    // Liste, die jünger als `FRISCH_MS` ist, auch nach dem frischen Abruf.
    // Endgültig vermerkt, bekäme es nie eine Antwort — so geschehen in der
    // Laufzeitprobe vom 23.09. Gesendet wird ohne Eintrag ohnehin nichts.
    if (!ziel) return 'offen'

    // Schutz gegen unterschobene Geräte (Vorfall 7):
    // Ein Gerät, das der Server als freigegeben meldet (`is_approved === true`),
    // darf den Notizenschlüssel nur erhalten, wenn dieses Gerät die Freigabe
    // selbst unterschrieben hat (`approved_by === self.kennung`) oder der Benutzer
    // das Gerät interaktiv für den Notizenschlüssel bestätigt hat.
    // Ein nicht freigegebenes Gerät (`is_approved === false`) erhält den Schlüssel nicht.
    if (ziel.is_approved === false) {
      return 'offen'
    }
    if (
      ziel.is_approved === true &&
      ziel.approved_by !== self.kennung &&
      !istGeraetBestaetigtFuerNotizenSchluessel(userId, anGeraet)
    ) {
      if (typeof window !== 'undefined') {
        window.dispatchEvent(
          new CustomEvent('msm:notes-key-request-pending', {
            detail: { userId, deviceId: anGeraet, label: ziel.label },
          }),
        )
      }
      return 'offen'
    }

    try {
      const payload = await unterschriebeneUebergabe(self, userId, anGeraet, existingRawKey)
      if (!payload) return 'offen'
      const replyEnv = await encryptE2eeHybrid(payload, ziel.public_key)
      const mailboxId = await deriveUserDeviceMailboxId(userId)
      const abdruck = await abdruckVon(existingRawKey)
      await relayE2eeEnvelope({
        blind_mailbox_id: mailboxId,
        ciphertext_envelope: replyEnv,
        client_uuid: umschlagKennung('noteskeysync', self.kennung, anGeraet),
        is_control: true,
        control_type: 'notes_key_sync',
      })
      merkeGegeben(userId, self.kennung, abdruck, anGeraet)
      letzteAntwort.set(`${userId}:${anGeraet}`, Date.now())
      return 'erledigt'
    } catch {
      // Etwa ein 429 des Relais: die Anfrage bleibt offen.
      return 'offen'
    }
  }

  return 'verworfen'
}

/**
 * Geht die Geräte-Mailbox durch, neueste zuerst, und lässt aus, womit dieses
 * Gerät schon fertig ist. Liefert, wie viele Umschläge jetzt erledigt wurden.
 *
 * `bisSchluessel`: aufhören, sobald ein Schlüssel da ist — der Weg eines
 * Geräts, das noch keinen hat.
 */
function geraeteMailboxDurchgehen(userId: number, bisSchluessel: boolean): Promise<number> {
  return einLaufZugleich(laufenderDurchgang, `${userId}:${bisSchluessel}`, () =>
    mailboxDurchgang(userId, bisSchluessel),
  )
}

async function mailboxDurchgang(userId: number, bisSchluessel: boolean): Promise<number> {
  const mailboxId = await deriveUserDeviceMailboxId(userId)
  const envelopes = await fetchE2eeEnvelopes(mailboxId, 0)
  if (!Array.isArray(envelopes) || envelopes.length === 0) return 0
  const ich = await eigenesGeraet()
  if (!ich) return 0

  let erledigt = 0
  for (let i = envelopes.length - 1; i >= 0; i--) {
    const item = envelopes[i]
    if (!item?.ciphertext_envelope) continue
    const nummer = typeof item.id === 'number' ? item.id : null
    if (nummer !== null && istErledigt(userId, ich.kennung, nummer)) continue
    const ausgang = await verarbeiteSteuerumschlag(item.ciphertext_envelope, userId)
    if (ausgang !== 'offen' && nummer !== null) merkeErledigt(userId, ich.kennung, nummer)
    if (ausgang === 'erledigt') erledigt++
    if (bisSchluessel && hasUserNotesKey(userId)) break
  }
  return erledigt
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
    await geraeteMailboxDurchgehen(userId, true)
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
  try {
    return await geraeteMailboxDurchgehen(userId, false)
  } catch {
    return 0
  }
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
