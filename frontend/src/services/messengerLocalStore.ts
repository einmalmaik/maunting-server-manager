/**
 * MSM Messenger Local Store (IndexedDB)
 *
 * Persists decrypted chat messages locally so conversation histories survive
 * browser refreshes (F5) and app restarts (Web, Desktop, Mobile) without
 * re-entering recovery keys or waiting for network round-trips.
 *
 * Database: `msm_messenger_local` (v1)
 * Object Stores:
 *  - `messages`: keyed by `[blindMailboxId, id]`, indexed by `blindMailboxId`, `createdAt`, `clientUuid`
 *  - `mailboxes`: keyed by `blindMailboxId`, tracks `lastSyncedEnvelopeId` and `updatedAt`
 *
 * Seit 09/2026 liegt der Inhalt hier nicht mehr offen, sobald ein Messenger-PIN
 * eingerichtet ist: jede Zeile geht durch `lokaleVersiegelung`, nur die
 * Schlüssel- und Indexfelder bleiben lesbar. Ohne PIN ändert sich nichts.
 *
 * Daraus folgt eine Regel für jeden Schreibweg in dieser Datei: **erst
 * versiegeln, dann die Transaktion öffnen.** Verschlüsseln ist asynchron, und
 * eine IndexedDB-Transaktion schließt sich, sobald der Ereignisumlauf
 * leerläuft — ein `await` zwischen `transaction()` und `put()` bricht sie ab.
 * Gelesen wird umgekehrt: erst die Rohzeilen holen, dann außerhalb der
 * Transaktion entsiegeln.
 */

import { entsiegleZeile, entsiegleZeilen, versiegleZeile } from './lokaleVersiegelung'
import { istSteuerzeile } from './nachrichtBezug'

export interface LocalStoredMessage {
  blindMailboxId?: string
  id: number
  clientUuid?: string
  senderId: number
  senderName?: string
  text: string
  createdAt: string
  isSelf: boolean
  isDelivered?: boolean
  isRead?: boolean
  isEdited?: boolean
  editedAt?: string
  isDeleted?: boolean
  deletedAt?: string
  originalText?: string
  /**
   * Eine Systemzeile im Verlauf, etwa die Meldung über einen Sitzungsbruch.
   *
   * Sie steht hier nur, damit `saveLocalMessages` und `loadLocalMessages` sie
   * aussortieren können: eine solche Zeile ist eine Aussage über den Zustand
   * **dieses** Geräts in **diesem** Moment, kein Gesprächsinhalt. Abgelegt
   * bliebe sie für immer stehen — und weil sie ihre Kennung aus der Uhr nimmt,
   * sortiert sie sich hinter jede später eintreffende Nachricht. Nach ein paar
   * Tagen stünde am Ende jedes Gesprächs ein Stapel alter Meldungen.
   */
  isSystem?: boolean
  noteAttachment?: any
  calendarAttachment?: any
  imageAttachment?: any
  audioAttachment?: any
  fileAttachment?: any
  stickerAttachment?: any
  storyReply?: any
  videoNoteAttachment?: any
  videoUrl?: string
  status?: 'queued' | 'sent' | 'delivered' | 'read'

  /**
   * Wirkungen, die aus Steuerumschlägen kommen und deshalb **hier** landen
   * müssen, nicht nur im Umschlag.
   *
   * Die Mailbox gibt die letzten hundert Umschläge her. Eine Reaktion, die
   * nur dort lebt, verschwindet, sobald hundert neue darüber gelaufen sind —
   * die Nachricht bliebe stehen, die Reaktion wäre weg. Näheres in
   * `nachrichtBezug.ts`. Alle Felder gehen durch dieselbe Versiegelung wie
   * der Text; `NACHRICHT_KLARFELDER` bleibt unverändert.
   */
  reaktionen?: Record<string, number[]>
  antwortAuf?: { clientUuid: string; absenderId: number; absenderName?: string; auszug: string }
  erwaehnungen?: number[]
  erwaehntAlle?: boolean
  weitergeleitet?: boolean
  /** Sternchen. Rein lokal — welche Nachricht mir wichtig ist, geht niemanden an. */
  istMarkiert?: boolean
  /** Ab wann diese Zeile von selbst verschwindet (ISO). */
  verfaelltAm?: string
}

export interface LocalMailboxMeta {
  blindMailboxId: string
  lastSyncedEnvelopeId: number
  updatedAt: string
}

const DB_NAME = 'msm_messenger_local'
const DB_VERSION = 3
const STORE_MESSAGES = 'messages'
const STORE_MAILBOXES = 'mailboxes'
const STORE_KLARTEXTE = 'envelope_plaintexts'
const STORE_ENTWUERFE = 'entwuerfe'

/**
 * Die Felder einer Nachricht, die im Klartext liegen bleiben müssen.
 *
 * `blindMailboxId` und `id` bilden den Schlüssel, `clientUuid` trägt den Index,
 * über den `updateMessageInLocalStore` eine Zeile wiederfindet, `createdAt` den
 * Zeitindex. Was IndexedDB als Schlüssel braucht, kann nicht verschlüsselt
 * sein — sonst gibt es den Index nicht mehr.
 *
 * Das ist der Preis des Siegels und er steht so auch in der Dokumentation: wer
 * eine kopierte Platte hat, sieht weiter, welche Mailbox wann wie viele
 * Nachrichten bekommen hat. Den Inhalt sieht er nicht. `blindMailboxId` ist
 * bereits eine blinde Kennung, `clientUuid` eine Zufallskennung ohne Bezug zum
 * Text.
 */
const NACHRICHT_KLARFELDER = ['blindMailboxId', 'id', 'createdAt', 'clientUuid'] as const

/** Bindet eine Nachrichtenzeile an ihren Platz. Siehe `versiegleZeile`. */
function nachrichtAad(blindMailboxId: string, id: number | undefined): string {
  return `msm-nachricht:${blindMailboxId}:${id ?? ''}`
}

/** Bindet einen Umschlag-Klartext an seinen Platz. */
function klartextAad(blindMailboxId: string, envelopeId: number): string {
  return `msm-umschlag:${blindMailboxId}:${envelopeId}`
}

let dbPromise: Promise<IDBDatabase> | null = null

/**
 * Bittet den Browser, diese Ablage zu behalten.
 *
 * Ohne das gilt eine IndexedDB als *best effort*: der Browser darf sie bei
 * Speicherdruck verwerfen oder beim Aufräumen selten besuchter Seiten
 * mitnehmen, ohne zu fragen und ohne es zu melden. Seit der Umstellung auf den
 * Double Ratchet wiegt das schwerer als früher, denn der **eigene**
 * Gesprächsanteil steht nirgendwo sonst: wer eine Ratchet-Nachricht
 * verschlüsselt, kann sie selbst nicht wieder öffnen, und der Server hat nur
 * den Umschlag. Was hier verschwindet, ist verschwunden.
 *
 * Erst `persisted()`, dann `persist()`: Firefox fragt den Menschen, und eine
 * Frage, die bei jedem Öffnen wiederkommt, beantwortet irgendwann jeder mit
 * „nein". Chrome und Safari entscheiden still anhand ihrer eigenen Kriterien.
 *
 * Die Antwort ist eine Auskunft, keine Zusage: `false` heißt, der Verlauf
 * liegt weiter da, darf aber gehen. Der Aufrufer bekommt sie zurück, damit
 * sich das später sichtbar machen lässt, statt es zu verschweigen.
 */
export async function sichereDauerhafteAblage(): Promise<boolean> {
  try {
    if (typeof navigator === 'undefined' || !navigator.storage?.persist) return false
    if (await navigator.storage.persisted()) return true
    return await navigator.storage.persist()
  } catch {
    // Ältere Browser und abgeschaltete Speicher-APIs. Kein Grund, den
    // Messenger nicht zu starten.
    return false
  }
}

function openLocalDatabase(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise
  dbPromise = new Promise<IDBDatabase>((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      return reject(new Error('IndexedDB not available in current environment'))
    }
    const req = indexedDB.open(DB_NAME, DB_VERSION)

    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE_MESSAGES)) {
        const msgStore = db.createObjectStore(STORE_MESSAGES, {
          keyPath: ['blindMailboxId', 'id'],
        })
        msgStore.createIndex('by_mailbox', 'blindMailboxId', { unique: false })
        msgStore.createIndex('by_created_at', 'createdAt', { unique: false })
        msgStore.createIndex('by_client_uuid', 'clientUuid', { unique: false })
      }
      if (!db.objectStoreNames.contains(STORE_MAILBOXES)) {
        db.createObjectStore(STORE_MAILBOXES, { keyPath: 'blindMailboxId' })
      }
      // Version 2: der Klartext je Umschlag.
      //
      // Ein Double-Ratchet-Nachrichtenschlüssel wird beim Entschlüsseln
      // verbraucht — derselbe Umschlag geht kein zweites Mal auf. Ohne diese
      // Ablage wäre jeder Abruf ein Versuch, bereits verbrauchte Schlüssel
      // erneut zu benutzen, und jede Nachricht nur so lange lesbar wie die
      // Browsersitzung, in der sie ankam.
      if (!db.objectStoreNames.contains(STORE_KLARTEXTE)) {
        db.createObjectStore(STORE_KLARTEXTE, { keyPath: ['blindMailboxId', 'envelopeId'] })
      }
      // Version 3: ungesendete Entwürfe.
      //
      // Ein Entwurf ist Klartext, den noch niemand gesehen hat — das
      // Empfindlichste, was der Messenger hält. Er gehört deshalb hierher und
      // nicht in den localStorage neben die Stummschaltungen: hier greift
      // dieselbe Versiegelung wie beim Verlauf.
      if (!db.objectStoreNames.contains(STORE_ENTWUERFE)) {
        db.createObjectStore(STORE_ENTWUERFE, { keyPath: 'blindMailboxId' })
      }
    }

    req.onsuccess = () => resolve(req.result)
    req.onerror = () => {
      dbPromise = null
      reject(req.error)
    }
  })
  return dbPromise
}

/**
 * Robust ISO timestamp parser.
 * Always normalizes missing timezone offsets to UTC ('Z') so timestamps
 * are never mistakenly parsed as local time by the browser.
 */
export function parseMessageTimestamp(isoString: string | undefined | null): number {
  if (!isoString) return 0
  let s = String(isoString).trim()
  s = s.replace(' ', 'T')
  if (!s.endsWith('Z') && !/[+-]\d{2}(?::?\d{2})?$/.test(s)) {
    s += 'Z'
  }
  const t = new Date(s).getTime()
  return isNaN(t) ? 0 : t
}

/**
 * Checks if a message is an unconfirmed / optimistic local message.
 */
export function isOptimisticMessage(m: { id?: number; status?: string }): boolean {
  return !m.id || m.id >= 1e11 || m.status === 'queued'
}

/**
 * Sorts messages strictly and monotonically:
 * 1. Server-confirmed messages always come before unconfirmed/optimistic messages.
 * 2. Confirmed messages are ordered primarily by monotonic server ID and normalized timestamps.
 * 3. Optimistic messages are ordered by their local creation timestamp at the end of the timeline.
 */
export function sortMessagesChronologically<T extends { id: number; createdAt: string; clientUuid?: string; status?: string }>(
  msgs: T[]
): T[] {
  return [...msgs].sort((a, b) => {
    const isAOpt = isOptimisticMessage(a)
    const isBOpt = isOptimisticMessage(b)

    // Confirmed messages before optimistic messages
    if (isAOpt !== isBOpt) {
      return isAOpt ? 1 : -1
    }

    if (!isAOpt) {
      const tA = parseMessageTimestamp(a.createdAt)
      const tB = parseMessageTimestamp(b.createdAt)
      // If timestamps differ by more than 60 seconds, use timestamp
      if (Math.abs(tA - tB) > 60_000) {
        return tA - tB
      }
      // Within the same minute / conversational flow, server monotonic ID is the authoritative order
      if (a.id && b.id && a.id !== b.id) {
        return a.id - b.id
      }
      return tA - tB
    }

    // Both optimistic: sort by local timestamp
    const tA = parseMessageTimestamp(a.createdAt) || (a.id || 0)
    const tB = parseMessageTimestamp(b.createdAt) || (b.id || 0)
    return tA - tB
  })
}

/**
 * Legt den Klartext eines Umschlags ab — und **wirft**, wenn das misslingt.
 *
 * Der einzige Schreibweg dieser Datei, der Fehler nicht schluckt, und das mit
 * Absicht: der Aufrufer schreibt hier, bevor er den fortgeschriebenen
 * Ratchet-Zustand speichert. Schlägt es fehl, muss der ganze Schritt scheitern,
 * damit derselbe Umschlag beim nächsten Durchlauf erneut aufgeht. Ein stilles
 * `catch` an dieser Stelle verlöre Nachrichten, ohne dass es jemand merkt.
 */
export async function speichereUmschlagKlartext(
  blindMailboxId: string,
  envelopeId: number,
  plain: string
): Promise<void> {
  const db = await openLocalDatabase()
  // Versiegeln vor der Transaktion — und ein gesperrter Messenger wirft hier,
  // wie er soll: scheitert dieser Schritt, darf der Ratchet-Schlüssel nicht
  // als verbraucht gelten.
  const zeile = await versiegleZeile(
    {
      blindMailboxId,
      envelopeId,
      plain,
      gespeichertAm: new Date().toISOString(),
    },
    ['blindMailboxId', 'envelopeId'],
    klartextAad(blindMailboxId, envelopeId),
  )
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE_KLARTEXTE, 'readwrite')
    tx.objectStore(STORE_KLARTEXTE).put(zeile)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

/** Bindet einen Entwurf an seinen Chat. */
function entwurfAad(blindMailboxId: string): string {
  return `msm-entwurf:${blindMailboxId}`
}

/**
 * Legt den ungesendeten Text eines Chats ab — versiegelt wie alles andere.
 *
 * Ein leerer Entwurf wird gelöscht statt leer gespeichert: eine Zeile, die nur
 * sagt „hier wurde mal etwas getippt und wieder verworfen", ist ein Hinweis,
 * den niemand braucht.
 */
export async function speichereEntwurf(blindMailboxId: string, text: string): Promise<void> {
  if (!blindMailboxId) return
  const db = await openLocalDatabase()
  if (!text.trim()) {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_ENTWUERFE, 'readwrite')
      tx.objectStore(STORE_ENTWUERFE).delete(blindMailboxId)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
    return
  }
  const zeile = await versiegleZeile(
    { blindMailboxId, text, gespeichertAm: new Date().toISOString() },
    ['blindMailboxId'],
    entwurfAad(blindMailboxId),
  )
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE_ENTWUERFE, 'readwrite')
    tx.objectStore(STORE_ENTWUERFE).put(zeile)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

/** Holt den Entwurf eines Chats. Leerer String heißt „keiner". */
export async function ladeEntwurf(blindMailboxId: string): Promise<string> {
  if (!blindMailboxId) return ''
  const db = await openLocalDatabase()
  const roh = await new Promise<any>((resolve, reject) => {
    const tx = db.transaction(STORE_ENTWUERFE, 'readonly')
    const req = tx.objectStore(STORE_ENTWUERFE).get(blindMailboxId)
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
  if (!roh) return ''
  try {
    const offen = await entsiegleZeile(roh, entwurfAad(blindMailboxId))
    return typeof offen?.text === 'string' ? offen.text : ''
  } catch {
    // Gesperrt oder mit einem anderen Schlüssel abgelegt. Ein unlesbarer
    // Entwurf ist kein Fehler, er ist eben keiner.
    return ''
  }
}

/** Alle Chats mit Entwurf, für die Vorschau in der Liste. */
export async function ladeAlleEntwuerfe(): Promise<Record<string, string>> {
  const db = await openLocalDatabase()
  const rohe = await new Promise<any[]>((resolve, reject) => {
    const tx = db.transaction(STORE_ENTWUERFE, 'readonly')
    const req = tx.objectStore(STORE_ENTWUERFE).getAll()
    req.onsuccess = () => resolve(req.result || [])
    req.onerror = () => reject(req.error)
  })
  const ergebnis: Record<string, string> = {}
  for (const roh of rohe) {
    const mid = roh?.blindMailboxId
    if (!mid) continue
    try {
      const offen = await entsiegleZeile(roh, entwurfAad(mid))
      if (typeof offen?.text === 'string' && offen.text.trim()) ergebnis[mid] = offen.text
    } catch {
      // siehe `ladeEntwurf`
    }
  }
  return ergebnis
}

/**
 * Nimmt den Inhalt eines geöffneten Umschlags heraus, ohne die Zeile zu löschen.
 *
 * Gebraucht beim Löschen einer Nachricht: Hier liegt die Fassung, aus der ein
 * späterer Abruf sie wieder aufbaut. Bliebe sie stehen, käme die gelöschte
 * Nachricht beim nächsten Öffnen des Gesprächs zurück.
 *
 * Die Zeile selbst muss bleiben. Sie ist zugleich die Marke „dieser Umschlag
 * ist geöffnet", und ein Nachrichtenschlüssel des Double Ratchet ist nach dem
 * ersten Öffnen verbraucht: ohne die Marke liefe der Ratchet ein zweites Mal
 * über denselben Umschlag und bräche die Sitzung. Der leere Text ist dafür
 * ausreichend, die Prüfung fragt auf `null` (`ratchetSitzung.ts`).
 *
 * Gab es zu diesem Umschlag noch keine Zeile — etwa beim Absender, der seine
 * eigene Ratchet-Nachricht nie geöffnet hat —, entsteht hier eine leere. Das
 * schadet nicht: sie markiert einen Umschlag, der ohnehin verschwindet.
 */
export async function leereUmschlagKlartext(
  blindMailboxId: string,
  envelopeId: number
): Promise<void> {
  await speichereUmschlagKlartext(blindMailboxId, envelopeId, '')
}

/**
 * Der Klartext **eines** Umschlags — und der Fehlerfall bleibt ein Fehler.
 *
 * `ladeUmschlagKlartexte` darf eine leere Map liefern, wenn die Ablage streikt:
 * dort ist das Ergebnis eine Abkürzung, und wer sie nicht bekommt, entschlüsselt
 * eben noch einmal. Hier ist es umgekehrt. Diese Abfrage läuft im
 * Sitzungsschloss und entscheidet, ob ein Nachrichtenschlüssel verbraucht wird.
 * Ein verschluckter Fehler sähe aus wie „noch nicht geöffnet", der Ratchet liefe
 * ein zweites Mal über denselben Umschlag, und das Ergebnis wäre genau der
 * Sitzungsbruch, den diese Abfrage verhindern soll.
 */
export async function leseUmschlagKlartext(
  blindMailboxId: string,
  envelopeId: number
): Promise<string | null> {
  if (!blindMailboxId) return null
  const db = await openLocalDatabase()
  const roh = await new Promise<Record<string, any> | null>((resolve, reject) => {
    const tx = db.transaction(STORE_KLARTEXTE, 'readonly')
    const req = tx.objectStore(STORE_KLARTEXTE).get([blindMailboxId, envelopeId])
    req.onsuccess = () => resolve(req.result ?? null)
    req.onerror = () => reject(req.error)
  })
  const zeile = await entsiegleZeile<{ plain?: unknown }>(
    roh,
    klartextAad(blindMailboxId, envelopeId),
  )
  return typeof zeile?.plain === 'string' ? zeile.plain : null
}

/** Alle bereits geöffneten Umschläge einer Mailbox, nach Umschlagkennung. */
export async function ladeUmschlagKlartexte(
  blindMailboxId: string
): Promise<Map<number, string>> {
  const treffer = new Map<number, string>()
  if (!blindMailboxId) return treffer
  try {
    const db = await openLocalDatabase()
    const rohzeilen = await new Promise<Record<string, any>[]>((resolve, reject) => {
      const tx = db.transaction(STORE_KLARTEXTE, 'readonly')
      const req = tx.objectStore(STORE_KLARTEXTE).getAll(
        IDBKeyRange.bound([blindMailboxId, -Infinity], [blindMailboxId, Infinity])
      )
      req.onsuccess = () => resolve(req.result || [])
      req.onerror = () => reject(req.error)
    })
    const offen = await entsiegleZeilen<{ envelopeId: number; plain: string }>(
      rohzeilen,
      (z) => klartextAad(blindMailboxId, z.envelopeId),
    )
    for (const e of offen) {
      if (typeof e.plain === 'string') treffer.set(e.envelopeId, e.plain)
    }
    return treffer
  } catch {
    return treffer
  }
}

/**
 * Führt den lokalen Verlauf mit dem zusammen, was dieser Durchlauf entschlüsselt
 * hat.
 *
 * Seit dem Umstieg auf den Double Ratchet ist dieser Speicher nicht mehr nur ein
 * Zwischenspeicher, sondern die einzige Quelle des eigenen Gesprächsanteils: der
 * Absender kann seine eigene Ratchet-Nachricht nicht entschlüsseln — das ist der
 * Sinn der Sache. Würde ein Abruf den Verlauf wie früher **ersetzen**, wäre alles
 * selbst Geschriebene beim nächsten Abruf verschwunden.
 *
 * Zusammengeführt wird über die logische Kennung (`clientUuid`), ersatzweise über
 * die Umschlagkennung. Bei einem Treffer gewinnt der frischere Eintrag: ein vom
 * Server bestätigter schlägt einen optimistischen, sonst der zuletzt gelesene.
 * Felder, die nur die alte Fassung kennt — etwa ein Anhang, den dieser Durchlauf
 * nicht mitgelesen hat —, bleiben erhalten.
 */
export function mischeVerlauf(
  lokal: readonly LocalStoredMessage[],
  frisch: readonly LocalStoredMessage[]
): LocalStoredMessage[] {
  const schluessel = (m: LocalStoredMessage) => m.clientUuid || `#${m.id}`
  const zusammen = new Map<string, LocalStoredMessage>()

  for (const m of lokal) zusammen.set(schluessel(m), m)

  for (const m of frisch) {
    const k = schluessel(m)
    const alt = zusammen.get(k)
    if (!alt) {
      zusammen.set(k, m)
      continue
    }
    // Ein bestätigter Eintrag verdrängt einen optimistischen, nie umgekehrt.
    const nimmNeu = !isOptimisticMessage(m) || isOptimisticMessage(alt)
    zusammen.set(k, nimmNeu ? { ...alt, ...m } : { ...m, ...alt })
  }

  return sortMessagesChronologically([...zusammen.values()])
}

/**
 * Loads cached decrypted messages for a given mailbox ID from IndexedDB.
 * Returns messages sorted chronologically (oldest to newest).
 */
export async function loadLocalMessages(blindMailboxId: string): Promise<LocalStoredMessage[]> {
  if (!blindMailboxId) return []
  try {
    const db = await openLocalDatabase()
    const rohzeilen = await new Promise<Record<string, any>[]>((resolve, reject) => {
      const tx = db.transaction(STORE_MESSAGES, 'readonly')
      const store = tx.objectStore(STORE_MESSAGES)
      const index = store.index('by_mailbox')
      const range = IDBKeyRange.only(blindMailboxId)
      const req = index.getAll(range)
      req.onsuccess = () => resolve(req.result || [])
      req.onerror = () => reject(req.error)
    })

    // Erst öffnen, dann filtern: `isSystem` und `status` liegen im versiegelten
    // Teil und stehen vor dem Entsiegeln nicht zur Verfügung.
    const rawMsgs = await entsiegleZeilen<LocalStoredMessage>(
      rohzeilen,
      (z) => nachrichtAad(blindMailboxId, z.id),
    )

    // Deduplicate by clientUuid: if a confirmed server message exists with this clientUuid,
    // discard any optimistic leftover with the same clientUuid.
    const confirmedUuids = new Set<string>()
    for (const m of rawMsgs) {
      if (m.clientUuid && !isOptimisticMessage(m)) {
        confirmedUuids.add(m.clientUuid)
      }
    }
    const filtered = rawMsgs.filter((m) => {
      // Systemzeilen gehören nicht in den Verlauf. Gespeicherte gibt es
      // trotzdem: bis 09/2026 schrieb der Messenger sie mit, und bei jedem
      // Ladevorgang kamen sie zurück.
      if (m.isSystem) return false
      // Dasselbe für ein Steuerpaket, das keinen Zweig fand und als Nachricht
      // abgelegt wurde: der rohe JSON-Text bliebe sonst für immer stehen.
      if (istSteuerzeile(m.text)) return false
      if (m.clientUuid && isOptimisticMessage(m) && confirmedUuids.has(m.clientUuid)) {
        return false
      }
      return true
    })
    return sortMessagesChronologically(filtered)
  } catch {
    return []
  }
}

/**
 * Saves a list of decrypted messages to the local IndexedDB store.
 * Existing entries with the same `[blindMailboxId, id]` are updated idempotently.
 */
export async function saveLocalMessages(
  blindMailboxId: string,
  messages: LocalStoredMessage[]
): Promise<void> {
  if (!blindMailboxId || !messages || messages.length === 0) return
  try {
    const db = await openLocalDatabase()

    // Versiegeln passiert vollständig vor der Transaktion. Siehe Dateikopf.
    let maxEnvelopeId = 0
    const zeilen: Record<string, unknown>[] = []
    for (const m of messages) {
      // Siehe `isSystem`: eine Meldung über die Sitzung dieses Geräts ist
      // kein Gesprächsinhalt und hat in der Ablage nichts verloren.
      if (m.isSystem) continue
      zeilen.push(
        await versiegleZeile(
          { ...m, blindMailboxId },
          NACHRICHT_KLARFELDER,
          nachrichtAad(blindMailboxId, m.id),
        ),
      )
      // Only real server envelope IDs (< 1e11) advance lastSyncedEnvelopeId
      if (typeof m.id === 'number' && m.id > maxEnvelopeId && m.id < 1e11) {
        maxEnvelopeId = m.id
      }
    }

    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction([STORE_MESSAGES, STORE_MAILBOXES], 'readwrite')
      const msgStore = tx.objectStore(STORE_MESSAGES)
      const mbStore = tx.objectStore(STORE_MAILBOXES)

      for (const zeile of zeilen) msgStore.put(zeile)

      if (maxEnvelopeId > 0) {
        const mbMeta: LocalMailboxMeta = {
          blindMailboxId,
          lastSyncedEnvelopeId: maxEnvelopeId,
          updatedAt: new Date().toISOString(),
        }
        mbStore.put(mbMeta)
      }

      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
  } catch {
    // Non-fatal if IndexedDB write fails
  }
}

/**
 * Nimmt eine Nachricht endgültig aus der lokalen Ablage.
 *
 * `saveLocalMessages` schreibt nur. Was in der übergebenen Liste fehlt, bleibt
 * in der Ablage stehen — eine verworfene Nachricht verschwand deshalb nur aus
 * der Ansicht und kam beim nächsten Abgleich über `loadLocalMessages` und
 * `mischeVerlauf` zurück. Am laufenden System waren das die Nachrichten mit der
 * Uhr, die sich nicht löschen liessen: weg, wieder da, weg, wieder da.
 *
 * Beide Kennungen zählen: die Zeile einer noch nicht gesendeten Nachricht trägt
 * eine aus der Uhr erfundene Umschlagkennung, die einer bestätigten die echte.
 */
export async function entferneLokaleNachricht(
  blindMailboxId: string,
  kennung: { clientUuid?: string; id?: number }
): Promise<void> {
  if (!blindMailboxId) return
  if (!kennung.clientUuid && typeof kennung.id !== 'number') return
  try {
    const db = await openLocalDatabase()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_MESSAGES, 'readwrite')
      const store = tx.objectStore(STORE_MESSAGES)
      // Über den Mailbox-Index, nicht über `by_client_uuid`: dieselbe logische
      // Kennung kann in der Ablage mehrfach liegen (optimistische Zeile und
      // bestätigte Fassung unter verschiedenen Umschlagkennungen), und ein
      // `get` auf dem Index fände davon nur eine.
      const req = store.index('by_mailbox').getAll(IDBKeyRange.only(blindMailboxId))
      req.onsuccess = () => {
        for (const m of (req.result || []) as LocalStoredMessage[]) {
          const trifft =
            (kennung.clientUuid !== undefined && m.clientUuid === kennung.clientUuid) ||
            (typeof kennung.id === 'number' && m.id === kennung.id)
          if (trifft) store.delete([blindMailboxId, m.id])
        }
      }
      req.onerror = () => reject(req.error)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
  } catch {
    // Non-fatal if IndexedDB write fails
  }
}

/**
 * Updates a single message in the local store by clientUuid or id.
 */
export async function updateMessageInLocalStore(
  blindMailboxId: string,
  clientUuidOrId: string | number,
  patch: Partial<LocalStoredMessage>
): Promise<void> {
  if (!blindMailboxId) return
  try {
    const db = await openLocalDatabase()

    // Lesen, öffnen, ändern, wieder zumachen, schreiben. Das sind zwei
    // Transaktionen statt einer, weil Ent- und Verschlüsseln asynchron sind
    // und eine IndexedDB-Transaktion kein `await` überlebt.
    const roh = await new Promise<Record<string, any> | null>((resolve, reject) => {
      const tx = db.transaction(STORE_MESSAGES, 'readonly')
      const store = tx.objectStore(STORE_MESSAGES)
      const getReq =
        typeof clientUuidOrId === 'number'
          ? store.get([blindMailboxId, clientUuidOrId])
          : store.index('by_client_uuid').get(clientUuidOrId)
      getReq.onsuccess = () => resolve(getReq.result ?? null)
      getReq.onerror = () => reject(getReq.error)
    })

    if (!roh || roh.blindMailboxId !== blindMailboxId) return

    const alt = await entsiegleZeile<LocalStoredMessage>(
      roh,
      nachrichtAad(blindMailboxId, roh.id),
    )
    if (!alt) return

    const alteId = roh.id as number
    const neu = { ...alt, ...patch, blindMailboxId }
    const zeile = await versiegleZeile(
      neu,
      NACHRICHT_KLARFELDER,
      nachrichtAad(blindMailboxId, neu.id),
    )

    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_MESSAGES, 'readwrite')
      const store = tx.objectStore(STORE_MESSAGES)
      // If ID was upgraded (e.g. optimistic timestamp to server id), delete old key
      if (patch.id && patch.id !== alteId) {
        store.delete([blindMailboxId, alteId])
      }
      store.put(zeile)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
  } catch {
    // Non-fatal
  }
}

/**
 * Alle Mailboxen, zu denen hier Nachrichten liegen.
 *
 * Gelesen wird über den Index auf `blindMailboxId`, nicht über den
 * Mailbox-Store: dessen Zeile entsteht erst, wenn eine Nachricht eine echte
 * Umschlagkennung vom Server hat. Ein Gespräch, in dem bisher nur die eigene
 * optimistische Zeile steht, hätte dort keinen Eintrag — und genau die soll
 * der Erstabgleich mitnehmen.
 */
export async function listeLokaleMailboxen(): Promise<string[]> {
  try {
    const db = await openLocalDatabase()
    return await new Promise<string[]>((resolve, reject) => {
      const tx = db.transaction(STORE_MESSAGES, 'readonly')
      const index = tx.objectStore(STORE_MESSAGES).index('by_mailbox')
      const gefunden: string[] = []
      const req = index.openKeyCursor(null, 'nextunique')
      req.onsuccess = () => {
        const cursor = req.result
        if (!cursor) return resolve(gefunden)
        if (typeof cursor.key === 'string' && cursor.key) gefunden.push(cursor.key)
        cursor.continue()
      }
      req.onerror = () => reject(req.error)
    })
  } catch {
    return []
  }
}

/**
 * Retrieves the last synced envelope ID for a mailbox.
 */
export async function getLocalMailboxLastSyncedId(blindMailboxId: string): Promise<number> {
  if (!blindMailboxId) return 0
  try {
    const db = await openLocalDatabase()
    return await new Promise<number>((resolve) => {
      const tx = db.transaction(STORE_MAILBOXES, 'readonly')
      const req = tx.objectStore(STORE_MAILBOXES).get(blindMailboxId)
      req.onsuccess = () => resolve(req.result?.lastSyncedEnvelopeId ?? 0)
      req.onerror = () => resolve(0)
    })
  } catch {
    return 0
  }
}

/**
 * Schreibt jede Zeile dieser Ablage einmal neu.
 *
 * Das ist der Umstellungsdurchlauf beim Ein- und beim Ausschalten des PIN, und
 * es ist derselbe Code für beide Richtungen: `versiegleZeile` richtet sich nach
 * dem Schalter, der vorher gesetzt wurde. Einschalten heißt Schalter an und
 * einmal durchlaufen, Ausschalten heißt Schalter aus und einmal durchlaufen.
 *
 * Bricht der Durchlauf in der Mitte ab — geschlossener Reiter, leerer Akku —,
 * liegen beide Formen nebeneinander. Beide sind lesbar, solange der Schlüssel
 * da ist, und ein zweiter Durchlauf räumt den Rest ab. Deshalb wird hier Zeile
 * für Zeile geschrieben und nicht alles in einer großen Transaktion: eine
 * abgebrochene große Transaktion macht die halbe Arbeit rückgängig, hier bleibt
 * jede fertige Zeile fertig.
 */
export async function schreibeNachrichtenBestandNeu(): Promise<number> {
  const db = await openLocalDatabase()

  const rohNachrichten = await new Promise<Record<string, any>[]>((resolve, reject) => {
    const tx = db.transaction(STORE_MESSAGES, 'readonly')
    const req = tx.objectStore(STORE_MESSAGES).getAll()
    req.onsuccess = () => resolve(req.result || [])
    req.onerror = () => reject(req.error)
  })

  let geschrieben = 0
  for (const roh of rohNachrichten) {
    const mailbox = String(roh.blindMailboxId || '')
    if (!mailbox) continue
    const aad = nachrichtAad(mailbox, roh.id)
    const offen = await entsiegleZeile<LocalStoredMessage>(roh, aad)
    if (!offen) continue
    const zeile = await versiegleZeile({ ...offen, blindMailboxId: mailbox }, NACHRICHT_KLARFELDER, aad)
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_MESSAGES, 'readwrite')
      tx.objectStore(STORE_MESSAGES).put(zeile)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
    geschrieben++
  }

  const rohKlartexte = await new Promise<Record<string, any>[]>((resolve, reject) => {
    const tx = db.transaction(STORE_KLARTEXTE, 'readonly')
    const req = tx.objectStore(STORE_KLARTEXTE).getAll()
    req.onsuccess = () => resolve(req.result || [])
    req.onerror = () => reject(req.error)
  })

  for (const roh of rohKlartexte) {
    const mailbox = String(roh.blindMailboxId || '')
    const umschlag = Number(roh.envelopeId)
    if (!mailbox || !Number.isFinite(umschlag)) continue
    const aad = klartextAad(mailbox, umschlag)
    const offen = await entsiegleZeile<Record<string, any>>(roh, aad)
    if (!offen) continue
    const zeile = await versiegleZeile(
      { ...offen, blindMailboxId: mailbox, envelopeId: umschlag },
      ['blindMailboxId', 'envelopeId'],
      aad,
    )
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_KLARTEXTE, 'readwrite')
      tx.objectStore(STORE_KLARTEXTE).put(zeile)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
    geschrieben++
  }

  return geschrieben
}

/**
 * Clears all local stored messenger data.
 */
export async function clearLocalMessengerStore(): Promise<void> {
  try {
    const db = await openLocalDatabase()
    await new Promise<void>((resolve, reject) => {
      // Die Klartextablage gehört zwingend dazu: dort liegen die gelesenen
      // Nachrichten im Klartext, und nach einem Abmelden darf davon nichts
      // zurückbleiben.
      const tx = db.transaction(
        [STORE_MESSAGES, STORE_MAILBOXES, STORE_KLARTEXTE],
        'readwrite'
      )
      tx.objectStore(STORE_MESSAGES).clear()
      tx.objectStore(STORE_MAILBOXES).clear()
      tx.objectStore(STORE_KLARTEXTE).clear()
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
  } catch {
    // Non-fatal
  }
}
