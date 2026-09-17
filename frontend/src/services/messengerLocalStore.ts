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
 */

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
}

export interface LocalMailboxMeta {
  blindMailboxId: string
  lastSyncedEnvelopeId: number
  updatedAt: string
}

const DB_NAME = 'msm_messenger_local'
const DB_VERSION = 2
const STORE_MESSAGES = 'messages'
const STORE_MAILBOXES = 'mailboxes'
const STORE_KLARTEXTE = 'envelope_plaintexts'

let dbPromise: Promise<IDBDatabase> | null = null

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
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE_KLARTEXTE, 'readwrite')
    tx.objectStore(STORE_KLARTEXTE).put({
      blindMailboxId,
      envelopeId,
      plain,
      gespeichertAm: new Date().toISOString(),
    })
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

/** Alle bereits geöffneten Umschläge einer Mailbox, nach Umschlagkennung. */
export async function ladeUmschlagKlartexte(
  blindMailboxId: string
): Promise<Map<number, string>> {
  const treffer = new Map<number, string>()
  if (!blindMailboxId) return treffer
  try {
    const db = await openLocalDatabase()
    return await new Promise<Map<number, string>>((resolve, reject) => {
      const tx = db.transaction(STORE_KLARTEXTE, 'readonly')
      const req = tx.objectStore(STORE_KLARTEXTE).getAll(
        IDBKeyRange.bound([blindMailboxId, -Infinity], [blindMailboxId, Infinity])
      )
      req.onsuccess = () => {
        for (const e of req.result || []) treffer.set(e.envelopeId, e.plain)
        resolve(treffer)
      }
      req.onerror = () => reject(req.error)
    })
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
    return await new Promise<LocalStoredMessage[]>((resolve, reject) => {
      const tx = db.transaction(STORE_MESSAGES, 'readonly')
      const store = tx.objectStore(STORE_MESSAGES)
      const index = store.index('by_mailbox')
      const range = IDBKeyRange.only(blindMailboxId)
      const req = index.getAll(range)

      req.onsuccess = () => {
        const rawMsgs: LocalStoredMessage[] = req.result || []
        // Deduplicate by clientUuid: if a confirmed server message exists with this clientUuid,
        // discard any optimistic leftover with the same clientUuid.
        const confirmedUuids = new Set<string>()
        for (const m of rawMsgs) {
          if (m.clientUuid && !isOptimisticMessage(m)) {
            confirmedUuids.add(m.clientUuid)
          }
        }
        const filtered = rawMsgs.filter((m) => {
          if (m.clientUuid && isOptimisticMessage(m) && confirmedUuids.has(m.clientUuid)) {
            return false
          }
          return true
        })
        resolve(sortMessagesChronologically(filtered))
      }
      req.onerror = () => reject(req.error)
    })
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
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction([STORE_MESSAGES, STORE_MAILBOXES], 'readwrite')
      const msgStore = tx.objectStore(STORE_MESSAGES)
      const mbStore = tx.objectStore(STORE_MAILBOXES)

      let maxEnvelopeId = 0
      for (const m of messages) {
        const record: LocalStoredMessage = {
          ...m,
          blindMailboxId,
        }
        msgStore.put(record)
        // Only real server envelope IDs (< 1e11) advance lastSyncedEnvelopeId
        if (typeof m.id === 'number' && m.id > maxEnvelopeId && m.id < 1e11) {
          maxEnvelopeId = m.id
        }
      }

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
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_MESSAGES, 'readwrite')
      const store = tx.objectStore(STORE_MESSAGES)

      if (typeof clientUuidOrId === 'number') {
        const getReq = store.get([blindMailboxId, clientUuidOrId])
        getReq.onsuccess = () => {
          if (getReq.result) {
            const updated = { ...getReq.result, ...patch }
            store.put(updated)
          }
        }
      } else {
        const index = store.index('by_client_uuid')
        const getReq = index.get(clientUuidOrId)
        getReq.onsuccess = () => {
          if (getReq.result && getReq.result.blindMailboxId === blindMailboxId) {
            const updated = { ...getReq.result, ...patch }
            // If ID was upgraded (e.g. optimistic timestamp to server id), delete old key
            if (patch.id && patch.id !== getReq.result.id) {
              store.delete([blindMailboxId, getReq.result.id])
            }
            store.put(updated)
          }
        }
      }

      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
  } catch {
    // Non-fatal
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
