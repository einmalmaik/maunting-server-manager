/**
 * Unified Offline-First Local Storage & Real-Time SSE Synchronization
 * for Notes & Calendar in Maunting Server Manager (Web / APK / Desktop).
 *
 * Adheres strictly to KISS and Data Minimization:
 * - Persistent local cache (localStorage) for Notes and Calendar entries.
 * - Persistent Outbox mutation queue surviving app restarts and reloads.
 * - Deterministic Last-Write-Wins (LWW) conflict resolution using ISO timestamps.
 * - Real-Time Server-Sent-Events (SSE) stream (/api/events/live) updating views in < 1s.
 * - Adaptive fallback polling (10s) during disconnections with exponential reconnect.
 * - Immediate Outbox replay and UI event dispatching upon network reconnection.
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { api, apiStream } from '@/api/client'
import type { NoteItem } from '@/pages/Notes'
import type { CalendarEventItem, KalenderVorkommen } from '@/pages/Calendar'
import { LEERES_DOKUMENT, ausbreiten, serieLesen } from '@/services/kalenderSerie'
import { useAuthStore } from '@/stores/authStore'
import {
  NOTE_CIPHERTEXT_PREFIX,
  CALENDAR_CIPHERTEXT_PREFIX,
  generateClientEntityId,
  encryptNoteTitle,
  encryptNoteContent,
  decryptNoteTitle,
  decryptNoteContent,
  encryptCalendarField,
  decryptCalendarField,
  hasUserNotesKey,
  checkAndReceiveDeviceNotesKey,
  syncNotesKeyToPairedDevices,
  checkAndRespondToDeviceKeyRequests,
  altschluessel,
  altschluesselUebernehmen,
} from '@/services/notesCalendarCrypto'

export const STORAGE_KEYS = {
  NOTES: 'msm_offline_notes',
  CALENDAR: 'msm_offline_calendar',
  OUTBOX: 'msm_offline_outbox',
  LAST_SYNC: 'msm_offline_last_sync',
} as const

export interface OutboxMutation {
  id: string
  entity: 'note' | 'calendar' | 'message'
  action: 'create' | 'update' | 'delete' | 'toggle_pin' | 'toggle_archive' | 'relay'
  entityId: string
  payload?: any
  timestamp: string
  retryCount: number
}

export interface SyncEventPayload {
  entity: 'notes' | 'note' | 'calendar'
  action: 'created' | 'updated' | 'deleted' | string
  id: string
  timestamp?: string
  team_id?: number | null
  user_id?: number
  data?: any
}

// In-memory fallback if localStorage is unavailable
let memoryStore: Record<string, string> = {}

function getStorageItem(key: string): string | null {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      return window.localStorage.getItem(key)
    }
  } catch {
    // fallback
  }
  return memoryStore[key] ?? null
}

function setStorageItem(key: string, value: string): void {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem(key, value)
      return
    }
  } catch {
    // fallback
  }
  memoryStore[key] = value
}

export function clearMemoryStoreForTesting(): void {
  memoryStore = {}
  isReplaying = false
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.removeItem(STORAGE_KEYS.NOTES)
      window.localStorage.removeItem(STORAGE_KEYS.CALENDAR)
      window.localStorage.removeItem(STORAGE_KEYS.OUTBOX)
      window.localStorage.removeItem(STORAGE_KEYS.LAST_SYNC)
      window.localStorage.removeItem('msm_outbox_replay_lease')
    }
  } catch {
    // ignore
  }
}

// ── Cache Accessors (Data Minimization) ──

export function getOfflineNotes(): NoteItem[] {
  try {
    const raw = getStorageItem(STORAGE_KEYS.NOTES)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export function setOfflineNotes(notes: NoteItem[]): void {
  try {
    const sanitized = notes.map((n) => ({
      id: n.id,
      note_uid: n.note_uid,
      title: n.title,
      content: n.content || '',
      category: n.category || 'personal',
      color: n.color || 'primary',
      is_pinned: Boolean(n.is_pinned),
      is_archived: Boolean(n.is_archived),
      note_type: n.note_type || 'personal',
      user_id: n.user_id || 0,
      team_id: n.team_id ?? null,
      team_name: n.team_name ?? null,
      creator_name: n.creator_name ?? null,
      can_edit: n.can_edit ?? true,
      created_at: n.created_at || new Date().toISOString(),
      updated_at: n.updated_at || new Date().toISOString(),
    }))
    setStorageItem(STORAGE_KEYS.NOTES, JSON.stringify(sanitized))
  } catch {
    // ignore
  }
}

export function getOfflineCalendarEvents(): CalendarEventItem[] {
  try {
    const raw = getStorageItem(STORAGE_KEYS.CALENDAR)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export function setOfflineCalendarEvents(events: CalendarEventItem[]): void {
  try {
    const sanitized = events.map((ev) => ({
      id: ev.id,
      event_id: ev.event_id,
      title: ev.title,
      start: ev.start,
      end: ev.end,
      description: ev.description ?? '',
      location: ev.location ?? '',
      // Das Wiederholungsdokument gehört in den lokalen Spiegel, sonst
      // verliert ein Serientermin beim nächsten Offline-Start seine Regel und
      // erscheint als Einzeltermin.
      recurrence: ev.recurrence ?? '',
      all_day: Boolean(ev.all_day),
      color: ev.color || 'primary',
      calendar: ev.calendar || 'MSM Kalender',
      event_type: ev.event_type || 'personal',
      team_id: ev.team_id ?? null,
      team_name: ev.team_name ?? null,
      server_id: ev.server_id ?? null,
      server_name: ev.server_name ?? null,
      creator_name: ev.creator_name ?? null,
      user_id: ev.user_id ?? 0,
      can_edit: ev.can_edit ?? true,
    }))
    setStorageItem(STORAGE_KEYS.CALENDAR, JSON.stringify(sanitized))
  } catch {
    // ignore
  }
}

export function getOutbox(): OutboxMutation[] {
  try {
    const raw = getStorageItem(STORAGE_KEYS.OUTBOX)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export function setOutbox(mutations: OutboxMutation[]): void {
  try {
    setStorageItem(STORAGE_KEYS.OUTBOX, JSON.stringify(mutations))
  } catch {
    // ignore
  }
}

export function enqueueMutation(
  mutation: Omit<OutboxMutation, 'id' | 'timestamp' | 'retryCount'> & { id?: string }
): OutboxMutation {
  const fullMutation: OutboxMutation = {
    ...mutation,
    id: mutation.id || ('mut-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7)),
    timestamp: new Date().toISOString(),
    retryCount: 0,
  }
  const current = getOutbox()
  // Deduplizierung in der lokalen Warteschlange nach Mutation-ID
  if (!current.some((m) => m.id === fullMutation.id)) {
    current.push(fullMutation)
    setOutbox(current)
  }
  return fullMutation
}

/**
 * Reiht eine E2EE-Nachricht in die persistente Offline-Outbox ein.
 * Garantiert strikte FIFO-Reihenfolge und Idempotenz via Client-UUID nach Reconnect.
 */
export function enqueueMessageMutation(payload: {
  blind_mailbox_id: string
  ciphertext_envelope: string
  recipient_id?: number | null
  client_uuid: string
  /**
   * Steuerumschläge müssen ihre Kennzeichnung auch über die Outbox behalten.
   * Ein Sitzungsaufbau (`dr-init`), der sie unterwegs verliert, landet beim
   * Empfänger als unlesbare Nachricht im Verlauf statt als das, was er ist.
   * Die FIFO-Zusage der Outbox sorgt dafür, dass er vor der Nachricht ankommt,
   * für die er gebraucht wird.
   */
  is_control?: boolean
  control_type?: string
}): OutboxMutation {
  return enqueueMutation({
    id: payload.client_uuid,
    entity: 'message',
    action: 'relay',
    entityId: payload.blind_mailbox_id,
    payload,
  })
}

// ── Last-Write-Wins (LWW) Merge Functions ──

export function mergeNotesWithServer(serverNotes: NoteItem[]): NoteItem[] {
  const localNotes = getOfflineNotes()
  const outbox = getOutbox().filter((m) => m.entity === 'note')

  const noteMap = new Map<string, NoteItem>()

  // 1. Index server notes
  for (const sn of serverNotes) {
    noteMap.set(sn.note_uid, sn)
  }

  // 2. Overlay pending local mutations using LWW
  for (const ln of localNotes) {
    const hasPendingMutation = outbox.some((m) => m.entityId === ln.note_uid)
    if (hasPendingMutation) {
      const serverNote = noteMap.get(ln.note_uid)
      if (!serverNote) {
        // Created locally and not yet on server
        noteMap.set(ln.note_uid, ln)
      } else {
        // Compare timestamps
        const localTime = new Date(ln.updated_at || 0).getTime()
        const serverTime = new Date(serverNote.updated_at || 0).getTime()
        if (localTime >= serverTime) {
          noteMap.set(ln.note_uid, { ...serverNote, ...ln })
        }
      }
    }
  }

  // Filter out any locally deleted notes that have a pending delete mutation
  const pendingDeletes = new Set(
    outbox.filter((m) => m.action === 'delete').map((m) => m.entityId)
  )
  const result = Array.from(noteMap.values()).filter((n) => !pendingDeletes.has(n.note_uid))

  setOfflineNotes(result)
  return result
}

export function mergeCalendarWithServer(serverEvents: CalendarEventItem[]): CalendarEventItem[] {
  const localEvents = getOfflineCalendarEvents()
  const outbox = getOutbox().filter((m) => m.entity === 'calendar')

  // Start with existing cached local events so events outside current view range aren't lost
  const eventMap = new Map<string, CalendarEventItem>()
  for (const le of localEvents) {
    eventMap.set(le.event_id, le)
  }

  // Update or insert server events
  for (const se of serverEvents) {
    const hasPendingMutation = outbox.some((m) => m.entityId === se.event_id)
    if (!hasPendingMutation) {
      eventMap.set(se.event_id, se)
    } else {
      const le = eventMap.get(se.event_id)
      if (le) {
        eventMap.set(se.event_id, { ...se, ...le })
      } else {
        eventMap.set(se.event_id, se)
      }
    }
  }

  const pendingDeletes = new Set(
    outbox.filter((m) => m.action === 'delete').map((m) => m.entityId)
  )
  const result = Array.from(eventMap.values()).filter((e) => !pendingDeletes.has(e.event_id))

  setOfflineCalendarEvents(result)
  return result
}

// ── Outbox Replay & Synchronization ──

let isReplaying = false

export async function replayOutbox(): Promise<{ processed: number; failed: number; remaining: number }> {
  if (isReplaying) {
    return { processed: 0, failed: 0, remaining: getOutbox().length }
  }

  const initialOutbox = getOutbox()
  if (initialOutbox.length === 0) {
    return { processed: 0, failed: 0, remaining: 0 }
  }

  isReplaying = true
  let processed = 0
  let failed = 0

  try {
    while (true) {
      const currentOutbox = getOutbox()
      if (currentOutbox.length === 0) break

      const mutation = currentOutbox[0]
      try {
        if (mutation.entity === 'note') {
          if (mutation.action === 'create') {
            const res = await api<NoteItem>('/notes', {
              method: 'POST',
              body: JSON.stringify(mutation.payload),
            })
            if (res && res.note_uid) {
              const oldUid = mutation.entityId
              const newUid = res.note_uid
              const notes = getOfflineNotes()
              const updated = notes.map((n) =>
                n.note_uid === oldUid ? { ...n, ...res, title: n.title, content: n.content, note_uid: newUid } : n
              )
              setOfflineNotes(updated)

              // Update any subsequent queued mutations that referenced the temporary UID
              if (oldUid !== newUid) {
                const liveOutbox = getOutbox()
                for (const m of liveOutbox) {
                  if (m.entity === 'note' && m.entityId === oldUid) {
                    m.entityId = newUid
                  }
                }
                setOutbox(liveOutbox)
              }
            }
          } else if (mutation.action === 'update') {
            await api('/notes/' + encodeURIComponent(mutation.entityId), {
              method: 'PUT',
              body: JSON.stringify(mutation.payload),
            })
          } else if (mutation.action === 'delete') {
            await api('/notes/' + encodeURIComponent(mutation.entityId), {
              method: 'DELETE',
            })
          } else if (mutation.action === 'toggle_pin') {
            await api('/notes/' + encodeURIComponent(mutation.entityId) + '/pin', {
              method: 'POST',
            })
          } else if (mutation.action === 'toggle_archive') {
            await api('/notes/' + encodeURIComponent(mutation.entityId) + '/archive', {
              method: 'POST',
            })
          }
        } else if (mutation.entity === 'calendar') {
          if (mutation.action === 'create') {
            const res = await api<any>('/calendar/events', {
              method: 'POST',
              body: JSON.stringify(mutation.payload),
            })
            if (res && res.event_id) {
              const oldUid = mutation.entityId
              const newUid = res.event_id
              const events = getOfflineCalendarEvents()
              const updated = events.map((e) =>
                e.event_id === oldUid ? { ...e, ...res, title: e.title, description: e.description, location: e.location, event_id: newUid } : e
              )
              setOfflineCalendarEvents(updated)

              // Update any subsequent queued mutations that referenced the temporary UID
              if (oldUid !== newUid) {
                const liveOutbox = getOutbox()
                for (const m of liveOutbox) {
                  if (m.entity === 'calendar' && m.entityId === oldUid) {
                    m.entityId = newUid
                  }
                }
                setOutbox(liveOutbox)
              }
            }
          } else if (mutation.action === 'update') {
            await api('/calendar/events/' + encodeURIComponent(mutation.entityId), {
              method: 'PUT',
              body: JSON.stringify(mutation.payload),
            })
          } else if (mutation.action === 'delete') {
            await api('/calendar/events/' + encodeURIComponent(mutation.entityId), {
              method: 'DELETE',
            })
          }
        } else if (mutation.entity === 'message') {
          if (mutation.action === 'relay' || mutation.action === 'create') {
            const res = await api<any>('/social/e2ee/relay', {
              method: 'POST',
              body: JSON.stringify(mutation.payload),
            })
            if (res && res.id) {
              if (typeof window !== 'undefined') {
                window.dispatchEvent(
                  new CustomEvent('msm:message-confirmed', {
                    detail: {
                      client_uuid: mutation.payload?.client_uuid || mutation.id,
                      envelope_id: res.id,
                      blind_mailbox_id: res.blind_mailbox_id,
                    },
                  })
                )
              }
            }
          }
        }

        // Successfully processed: remove from outbox atomically
        const afterSuccessOutbox = getOutbox()
        setOutbox(afterSuccessOutbox.filter((m) => m.id !== mutation.id))
        processed++
      } catch (err: any) {
        // `navigator.onLine` stand hier bis 09/2026 mit in der Bedingung. Das
        // machte aus jedem beliebigen Fehler einen Netzwerkfehler, sobald das
        // Betriebssystem „offline" meldete — und dann bricht die Schleife ab
        // und der Auftrag bleibt vorn liegen. Am laufenden System hiess das:
        // Nachrichten mit der Uhr, die nie wieder losgingen, weil ein
        // virtueller Netzadapter die Auskunft verfälschte. Was wirklich schief
        // ging, steht im Fehler selbst.
        const isNetworkErr =
          err?.status === 0 ||
          err?.name === 'TypeError' ||
          err?.message?.includes('Failed to fetch') ||
          err?.message?.includes('NetworkError')

        // Ein Ratenlimit ist kein Fehlschlag, sondern ein „später". Es unter
        // die gezählten Versuche zu nehmen war harmlos, solange niemand die
        // Warteschlange nachfasste; sobald das im Takt geschieht, wären die
        // fünf Versuche in einer halben Minute aufgebraucht und die Nachricht
        // des Benutzers stillschweigend weg. Dasselbe gilt für einen Server,
        // der gerade nicht kann: 5xx sagt nichts über den Auftrag aus.
        if (isNetworkErr || err?.status === 429 || (err?.status >= 500 && err?.status < 600)) {
          break
        } else if (err?.status === 404 || err?.status === 400) {
          const afterErrOutbox = getOutbox()
          setOutbox(afterErrOutbox.filter((m) => m.id !== mutation.id))
          failed++
        } else {
          const afterErrOutbox = getOutbox()
          const idx = afterErrOutbox.findIndex((m) => m.id === mutation.id)
          if (idx >= 0) {
            const retryCount = (afterErrOutbox[idx].retryCount || 0) + 1
            if (retryCount > 5) {
              afterErrOutbox.splice(idx, 1)
              failed++
            } else {
              afterErrOutbox[idx].retryCount = retryCount
            }
            setOutbox(afterErrOutbox)
          }
          break
        }
      }
    }
  } finally {
    isReplaying = false

    const remaining = getOutbox().length
    if (typeof window !== 'undefined') {
      if (processed > 0) {
        window.dispatchEvent(new CustomEvent('msm:notes-updated'))
        window.dispatchEvent(new CustomEvent('msm:calendar-updated'))
        window.dispatchEvent(new CustomEvent('msm:messages-updated'))
      }
      window.dispatchEvent(
        new CustomEvent('msm:sync-status', {
          detail: { processed, failed, remaining },
        })
      )
    }
  }

  return { processed, failed, remaining: getOutbox().length }
}

/**
 * Entschlüsselt alle im Offline-Cache verbliebenen Notizen und Termine nach,
 * sobald ein neuer oder synchronisierter Notizenschlüssel eingegangen ist.
 */
export async function redecryptPendingOfflineNotesAndCalendar(userId: number = 1): Promise<{
  decryptedNotesCount: number
  decryptedEventsCount: number
}> {
  let decryptedNotesCount = 0
  let decryptedEventsCount = 0

  // 1. Lokale Notizen auf verschlüsselte Altbestände prüfen
  const notes = getOfflineNotes()
  let notesChanged = false
  const updatedNotes: NoteItem[] = []

  for (const n of notes) {
    const titleIsEnc = typeof n.title === 'string' && n.title.startsWith(NOTE_CIPHERTEXT_PREFIX)
    const contentIsEnc = typeof n.content === 'string' && n.content.startsWith(NOTE_CIPHERTEXT_PREFIX)
    if (titleIsEnc || contentIsEnc) {
      try {
        const decryptedTitle = titleIsEnc
          ? await decryptNoteTitle(n.title, n.note_uid, undefined, n.user_id || userId)
          : n.title
        const decryptedContent = contentIsEnc
          ? await decryptNoteContent(n.content, n.note_uid, undefined, n.user_id || userId)
          : n.content
        updatedNotes.push({
          ...n,
          title: decryptedTitle,
          content: decryptedContent,
        })
        notesChanged = true
        decryptedNotesCount++
      } catch {
        updatedNotes.push(n)
      }
    } else {
      updatedNotes.push(n)
    }
  }

  if (notesChanged) {
    setOfflineNotes(updatedNotes)
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('msm:notes-updated'))
    }
  }

  // 2. Lokale Kalendereinträge auf verschlüsselte Altbestände prüfen
  const events = getOfflineCalendarEvents()
  let eventsChanged = false
  const updatedEvents: CalendarEventItem[] = []

  for (const ev of events) {
    const titleIsEnc = typeof ev.title === 'string' && ev.title.startsWith(CALENDAR_CIPHERTEXT_PREFIX)
    const descIsEnc = typeof ev.description === 'string' && ev.description.startsWith(CALENDAR_CIPHERTEXT_PREFIX)
    const locIsEnc = typeof ev.location === 'string' && ev.location.startsWith(CALENDAR_CIPHERTEXT_PREFIX)
    const recIsEnc = typeof ev.recurrence === 'string' && ev.recurrence.startsWith(CALENDAR_CIPHERTEXT_PREFIX)

    if (titleIsEnc || descIsEnc || locIsEnc || recIsEnc) {
      try {
        const decryptedTitle = titleIsEnc
          ? await decryptCalendarField(ev.title, ev.event_id, 'title', undefined, userId)
          : ev.title
        const decryptedDesc = descIsEnc
          ? await decryptCalendarField(ev.description, ev.event_id, 'description', undefined, userId)
          : ev.description
        const decryptedLoc = locIsEnc
          ? await decryptCalendarField(ev.location, ev.event_id, 'location', undefined, userId)
          : ev.location
        const decryptedRec = recIsEnc
          ? await decryptCalendarField(ev.recurrence, ev.event_id, 'recurrence', undefined, userId)
          : ev.recurrence

        updatedEvents.push({
          ...ev,
          title: decryptedTitle,
          description: decryptedDesc,
          location: decryptedLoc,
          recurrence: decryptedRec,
        })
        eventsChanged = true
        decryptedEventsCount++
      } catch {
        updatedEvents.push(ev)
      }
    } else {
      updatedEvents.push(ev)
    }
  }

  if (eventsChanged) {
    setOfflineCalendarEvents(updatedEvents)
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('msm:calendar-updated'))
    }
  }

  return { decryptedNotesCount, decryptedEventsCount }
}

function getEffectiveUserId(explicitUserId?: number): number {
  if (typeof explicitUserId === 'number' && explicitUserId > 0) {
    return explicitUserId
  }
  try {
    const authId = useAuthStore.getState().user?.id
    if (typeof authId === 'number' && authId > 0) {
      return authId
    }
  } catch {}
  return 1
}

/**
 * Ein Entschlüsselungsversuch, der den Altbestand mitnimmt.
 *
 * Bis zum 22.09.2026 schrieb diese Datei jeden Schlüssel unter der Kennung 1
 * fort, weil der `userId`-Parameter fehlte. Der Schreibfehler ist behoben — was
 * damals entstand, liegt aber weiter dort, und ohne diesen Weg bliebe es für
 * immer Chiffretext.
 *
 * Der Altschlüssel ist mehrdeutig: er kann dem Konto 1 gehören oder falsch
 * abgelegt worden sein. Deshalb wird er **nicht** einfach mitprobiert, sondern
 * nur hier, an einer Zeile, die der Server diesem Konto ausgeliefert hat. Ein
 * Konto bekommt nur die eigenen Zeilen; öffnet der Schlüssel eine davon, ist
 * er belegt dieses Kontos Schlüssel, und genau dann wird er übernommen.
 *
 * Die Zusage der Primitiven bleibt davon unberührt: `decryptNoteTitle` und
 * `decryptCalendarField` greifen weiterhin ausschließlich den Schlüssel der
 * übergebenen Kennung (siehe `notesCalendarCrypto.test.ts`, „fails decryption
 * if encrypted with a different user key").
 */
async function mitAltbestand<T>(
  kennung: number,
  versuch: (schluessel: CryptoKey | undefined) => Promise<T>,
): Promise<T> {
  try {
    return await versuch(undefined)
  } catch (fehler) {
    const alt = await altschluessel()
    if (!alt) throw fehler
    const ergebnis = await versuch(alt)
    void altschluesselUebernehmen(kennung).catch(() => {})
    return ergebnis
  }
}

// Globaler Event-Listener für neu eingegangene oder synchronisierte Notizenschlüssel
if (typeof window !== 'undefined') {
  window.addEventListener('msm:notes-key-updated', (e: any) => {
    const uid = e?.detail?.userId || getEffectiveUserId()
    void redecryptPendingOfflineNotesAndCalendar(uid)
  })
}

// ── Public Offline-First Notes API ──

export async function loadNotesOfflineFirst(_options?: {
  includeArchived?: boolean
  userId?: number
}): Promise<{ notes: NoteItem[]; isOffline: boolean }> {
  const effectiveUid = getEffectiveUserId(_options?.userId)
  if (!hasUserNotesKey(effectiveUid)) {
    await checkAndReceiveDeviceNotesKey(effectiveUid).catch(() => false)
  } else {
    void syncNotesKeyToPairedDevices(effectiveUid).catch(() => {})
    void checkAndRespondToDeviceKeyRequests(effectiveUid).catch(() => {})
  }

  let localNotes = getOfflineNotes()
  let isOffline = false

  try {
    const data = await api<NoteItem[]>('/notes?include_archived=true')
    if (Array.isArray(data)) {
      const decryptedData: NoteItem[] = await Promise.all(
        data.map(async (n) => {
          const itemUid = n.user_id || effectiveUid
          let title = n.title
          let content = n.content
          try {
            title = await mitAltbestand(itemUid, (k) =>
              decryptNoteTitle(n.title, n.note_uid, k, itemUid),
            )
          } catch {
            // Bei fehlendem oder falschem Schlüssel Ciphertext im Offline-Cache belassen,
            // damit nach Key-Sync redecryptPendingOfflineNotesAndCalendar greift
          }
          try {
            content = await mitAltbestand(itemUid, (k) =>
              decryptNoteContent(n.content, n.note_uid, k, itemUid),
            )
          } catch {
            // Ciphertext belassen
          }
          return {
            ...n,
            title,
            content,
          }
        })
      )
      localNotes = mergeNotesWithServer(decryptedData)
    }
  } catch {
    isOffline = true
  }

  if (!isOffline && getOutbox().length > 0) {
    void replayOutbox()
  }

  return { notes: localNotes, isOffline }
}

export async function saveNoteOffline(
  payload: {
    title: string
    content?: string
    category?: string
    color?: string
    is_pinned?: boolean
    note_type?: string
    team_id?: number | null
  },
  editingNote?: NoteItem | null
): Promise<{ note: NoteItem; queued: boolean }> {
  const now = new Date().toISOString()
  const localNotes = getOfflineNotes()
  let resultNote: NoteItem

  const targetUid = editingNote ? editingNote.note_uid : generateClientEntityId()
  // `targetUid` ist die Notiz-Kennung, nicht die des Benutzers — die gehört
  // getrennt mitgegeben, sonst greift der Vorgabewert 1 und der Schlüssel
  // landet unter dem falschen Konto. Siehe ALTSCHLUESSEL_KENNUNG.
  const kennung = getEffectiveUserId()
  const encryptedTitle = await encryptNoteTitle(payload.title, targetUid, undefined, kennung)
  const encryptedContent =
    payload.content !== undefined
      ? await encryptNoteContent(payload.content, targetUid, undefined, kennung)
      : ''

  const wirePayload = {
    ...payload,
    note_uid: targetUid,
    title: encryptedTitle,
    content: encryptedContent,
  }

  if (editingNote) {
    resultNote = {
      ...editingNote,
      ...payload,
      content: payload.content ?? editingNote.content,
      category: payload.category ?? editingNote.category,
      color: payload.color ?? editingNote.color,
      is_pinned: payload.is_pinned ?? editingNote.is_pinned,
      note_type: payload.note_type ?? editingNote.note_type,
      team_id: payload.team_id !== undefined ? payload.team_id : editingNote.team_id,
      updated_at: now,
    }
    const updated = localNotes.map((n) =>
      n.note_uid === editingNote.note_uid ? resultNote : n
    )
    setOfflineNotes(updated)

    enqueueMutation({
      entity: 'note',
      action: 'update',
      entityId: editingNote.note_uid,
      payload: wirePayload,
    })
  } else {
    resultNote = {
      id: Date.now(),
      note_uid: targetUid,
      title: payload.title,
      content: payload.content || '',
      category: payload.category || 'personal',
      color: payload.color || 'primary',
      is_pinned: Boolean(payload.is_pinned),
      is_archived: false,
      note_type: payload.note_type || 'personal',
      user_id: 1,
      team_id: payload.team_id ?? null,
      created_at: now,
      updated_at: now,
      can_edit: true,
    }
    localNotes.unshift(resultNote)
    setOfflineNotes(localNotes)

    enqueueMutation({
      entity: 'note',
      action: 'create',
      entityId: targetUid,
      payload: wirePayload,
    })
  }

  void replayOutbox()

  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('msm:notes-updated'))
  }

  return { note: resultNote, queued: true }
}

export async function deleteNoteOffline(note: NoteItem): Promise<{ queued: boolean }> {
  const localNotes = getOfflineNotes()
  const filtered = localNotes.filter((n) => n.note_uid !== note.note_uid)
  setOfflineNotes(filtered)

  const outbox = getOutbox()
  const isPendingLocalCreate = outbox.some(
    (m) => m.entity === 'note' && m.action === 'create' && m.entityId === note.note_uid
  )

  if (isPendingLocalCreate) {
    setOutbox(outbox.filter((m) => m.entityId !== note.note_uid))
  } else {
    enqueueMutation({
      entity: 'note',
      action: 'delete',
      entityId: note.note_uid,
    })
  }

  void replayOutbox()

  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('msm:notes-updated'))
  }

  return { queued: true }
}

export async function toggleNotePinOffline(note: NoteItem): Promise<{ note: NoteItem; queued: boolean }> {
  const now = new Date().toISOString()
  const localNotes = getOfflineNotes()
  const updatedNote: NoteItem = {
    ...note,
    is_pinned: !note.is_pinned,
    updated_at: now,
  }
  const updated = localNotes.map((n) => (n.note_uid === note.note_uid ? updatedNote : n))
  setOfflineNotes(updated)

  enqueueMutation({
    entity: 'note',
    action: 'toggle_pin',
    entityId: note.note_uid,
  })

  void replayOutbox()

  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('msm:notes-updated'))
  }

  return { note: updatedNote, queued: true }
}

export async function toggleNoteArchiveOffline(note: NoteItem): Promise<{ note: NoteItem; queued: boolean }> {
  const now = new Date().toISOString()
  const localNotes = getOfflineNotes()
  const updatedNote: NoteItem = {
    ...note,
    is_archived: !note.is_archived,
    updated_at: now,
  }
  const updated = localNotes.map((n) => (n.note_uid === note.note_uid ? updatedNote : n))
  setOfflineNotes(updated)

  enqueueMutation({
    entity: 'note',
    action: 'toggle_archive',
    entityId: note.note_uid,
  })

  void replayOutbox()

  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('msm:notes-updated'))
  }

  return { note: updatedNote, queued: true }
}

export async function toggleCheckItemOffline(
  note: NoteItem,
  itemIndex: number
): Promise<{ note: NoteItem; updatedContent: string; queued: boolean }> {
  const lines = (note.content || '').split('\n')
  let currentCheckIdx = 0
  const newLines = lines.map((line) => {
    const isUnchecked = /^[ \t]*- \[[ ]\]/.test(line)
    const isChecked = /^[ \t]*- \[[xX]\]/.test(line)
    if (isUnchecked || isChecked) {
      if (currentCheckIdx === itemIndex) {
        currentCheckIdx++
        if (isUnchecked) {
          return line.replace(/^([ \t]*- )\[ \]/, '$1[x]')
        } else {
          return line.replace(/^([ \t]*- )\[[xX]\]/, '$1[ ]')
        }
      }
      currentCheckIdx++
    }
    return line
  })

  const updatedContent = newLines.join('\n')
  const now = new Date().toISOString()
  const localNotes = getOfflineNotes()
  const updatedNote: NoteItem = {
    ...note,
    content: updatedContent,
    updated_at: now,
  }
  const updated = localNotes.map((n) => (n.note_uid === note.note_uid ? updatedNote : n))
  setOfflineNotes(updated)

  const encContent = await encryptNoteContent(
    updatedContent,
    note.note_uid,
    undefined,
    note.user_id || getEffectiveUserId(),
  )
  enqueueMutation({
    entity: 'note',
    action: 'update',
    entityId: note.note_uid,
    payload: { content: encContent },
  })

  void replayOutbox()

  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('msm:notes-updated'))
  }

  return { note: updatedNote, updatedContent, queued: true }
}

// ── Public Offline-First Calendar API ──

/**
 * Entschlüsselt die vier Textfelder eines Termins, jedes für sich.
 *
 * Jedes Feld in seinem eigenen `try`: schlägt eines fehl, sollen die übrigen
 * trotzdem lesbar sein. Ein Termin, dessen Ort sich nicht entschlüsseln lässt,
 * ist immer noch ein Termin.
 */
async function entschluesselterTermin(
  ev: CalendarEventItem,
  itemUid: number,
): Promise<CalendarEventItem> {
  let title = ev.title
  let description = ev.description
  let location = ev.location
  let recurrence = ev.recurrence
  try {
    title = await mitAltbestand(itemUid, (k) =>
      decryptCalendarField(ev.title, ev.event_id, 'title', k, itemUid),
    )
  } catch {}
  try {
    description = ev.description
      ? await mitAltbestand(itemUid, (k) =>
          decryptCalendarField(ev.description, ev.event_id, 'description', k, itemUid),
        )
      : ''
  } catch {}
  try {
    location = ev.location
      ? await mitAltbestand(itemUid, (k) =>
          decryptCalendarField(ev.location, ev.event_id, 'location', k, itemUid),
        )
      : ''
  } catch {}
  try {
    recurrence = ev.recurrence
      ? await mitAltbestand(itemUid, (k) =>
          decryptCalendarField(ev.recurrence, ev.event_id, 'recurrence', k, itemUid),
        )
      : ''
  } catch {}
  return { ...ev, title, description, location, recurrence }
}

/**
 * Der Grundbestand ist einmal je Sitzung zu holen.
 *
 * Der Server kann nicht wissen, welche Zeile eine Serie ist — das Feld
 * `recurrence` ist verschlüsselt, und bei E2EE-Terminen bleibt es das auch für
 * ihn (Betreiberentscheid 22.09.2026). Eine Bereichsabfrage für 2026 liefert
 * deshalb keinen Geburtstag, der 1995 angelegt wurde: sein `start_time` liegt
 * außerhalb.
 *
 * Also holt der Client einmal alles und hält es im Spiegel. Danach reichen die
 * gewohnten Bereichsabfragen plus die Echtzeitmeldungen.
 */
let grundbestandGeholt = false

export function grundbestandZuruecksetzen(): void {
  grundbestandGeholt = false
}

async function holeGrundbestand(effectiveUid: number): Promise<CalendarEventItem[] | null> {
  if (grundbestandGeholt) return null
  const data = await api<CalendarEventItem[]>('/calendar/events')
  if (!Array.isArray(data)) return null
  const entschluesselt = await Promise.all(
    data.map((ev) => entschluesselterTermin(ev, ev.user_id || effectiveUid)),
  )
  const zusammengefuehrt = mergeCalendarWithServer(entschluesselt)
  grundbestandGeholt = true
  return zusammengefuehrt
}

export async function loadCalendarEventsOfflineFirst(
  rangeStart: string,
  rangeEnd: string,
  eventType?: string,
  userId?: number,
  zeitzone?: string | null
): Promise<{ events: KalenderVorkommen[]; isOffline: boolean }> {
  const effectiveUid = getEffectiveUserId(userId)
  if (!hasUserNotesKey(effectiveUid)) {
    await checkAndReceiveDeviceNotesKey(effectiveUid).catch(() => false)
  } else {
    void syncNotesKeyToPairedDevices(effectiveUid).catch(() => {})
    void checkAndRespondToDeviceKeyRequests(effectiveUid).catch(() => {})
  }

  let localEvents = getOfflineCalendarEvents()
  let isOffline = false

  // Der Grundbestand hat seinen **eigenen** Versuch. Zöge er den
  // Bereichsabruf mit, stünde bei einem einzigen Fehlschlag der ganze Kalender
  // auf dem lokalen Spiegel — nur weil ein zusätzlicher Abruf nicht klappte,
  // den es vorher gar nicht gab.
  try {
    // Das Ergebnis übernehmen, nicht nur ablegen: scheitert gleich darauf der
    // Bereichsabruf, wäre `localEvents` sonst der Stand von **vor** dem
    // Grundbestand — und die Serien fehlten in genau dem Fall, für den er da
    // ist.
    const grundbestand = await holeGrundbestand(effectiveUid)
    if (grundbestand) localEvents = grundbestand
  } catch {
    // Beim nächsten Aufruf noch einmal: ein misslungener Grundbestand darf
    // nicht für den Rest der Sitzung als erledigt gelten, sonst fehlen die
    // Serien bis zum Neuladen der Seite.
    grundbestandGeholt = false
  }

  try {
    const catParam = eventType && eventType !== 'all' ? '&event_type=' + encodeURIComponent(eventType) : ''
    const data = await api<CalendarEventItem[]>(
      '/calendar/events?start=' + encodeURIComponent(rangeStart) + '&end=' + encodeURIComponent(rangeEnd) + catParam
    )
    if (Array.isArray(data)) {
      const decryptedData: CalendarEventItem[] = await Promise.all(
        data.map((ev) => entschluesselterTermin(ev, ev.user_id || effectiveUid)),
      )
      localEvents = mergeCalendarWithServer(decryptedData)
    }
  } catch {
    isOffline = true
  }

  const von = new Date(rangeStart)
  const bis = new Date(rangeEnd)

  const vorkommen: KalenderVorkommen[] = []
  for (const ev of localEvents) {
    if (eventType && eventType !== 'all' && ev.event_type !== eventType) continue

    const serie = serieLesen(ev.recurrence)
    const start = new Date(ev.start)
    if (isNaN(start.getTime())) continue
    const rohEnde = ev.end ? new Date(ev.end) : start
    const ende = isNaN(rohEnde.getTime()) ? start : rohEnde

    for (const v of ausbreiten(serie, start, ende, {
      ganztaegig: Boolean(ev.all_day),
      zeitzone,
      fensterVon: von,
      fensterBis: bis,
    })) {
      vorkommen.push({
        ...ev,
        title: v.titel || ev.title,
        start: v.start.toISOString(),
        end: v.ende.toISOString(),
        vorkommen: serie.rrule ? v.schluessel : '',
        istSerie: Boolean(serie.rrule),
        // `event_id` ist bei einer Serie für alle Vorkommen dasselbe. Als
        // React-Schlüssel oder zum Wiederfinden taugt nur beides zusammen.
        schluessel: serie.rrule ? `${ev.event_id}#${v.schluessel}` : ev.event_id,
      })
    }
  }
  vorkommen.sort((a, b) => new Date(a.start).getTime() - new Date(b.start).getTime())

  if (!isOffline && getOutbox().length > 0) {
    void replayOutbox()
  }

  return { events: vorkommen, isOffline }
}

export async function saveCalendarEventOffline(
  payload: {
    title: string
    start_time: string
    end_time: string
    description?: string | null
    location?: string | null
    all_day?: boolean
    color?: string
    event_type?: string
    team_id?: number | null
    server_id?: number | null
    recurrence?: string
  },
  formEventId?: string | null
): Promise<{ event: CalendarEventItem; queued: boolean }> {
  const localEvents = getOfflineCalendarEvents()
  let resultEvent: CalendarEventItem

  const targetUid = formEventId || generateClientEntityId()
  // `targetUid` ist die Termin-Kennung, nicht die des Benutzers — die gehört
  // getrennt mitgegeben, sonst greift der Vorgabewert 1 und der Schlüssel
  // landet unter dem falschen Konto. Siehe ALTSCHLUESSEL_KENNUNG.
  const kennung = getEffectiveUserId()
  const encryptedTitle = await encryptCalendarField(payload.title, targetUid, 'title', undefined, kennung)
  const encryptedDesc = payload.description ? await encryptCalendarField(payload.description, targetUid, 'description', undefined, kennung) : (payload.description ?? '')
  const encryptedLoc = payload.location ? await encryptCalendarField(payload.location, targetUid, 'location', undefined, kennung) : (payload.location ?? '')

  // Das Wiederholungsdokument geht **immer** mit, auch bei Einzelterminen —
  // dann eben als verschlüsseltes "keine Wiederholung". Ein leeres Feld neben
  // lauter gefüllten wäre in der Datenbank selbst eine Auskunft.
  const klartextSerie = payload.recurrence || LEERES_DOKUMENT
  const encryptedRec = await encryptCalendarField(klartextSerie, targetUid, 'recurrence', undefined, kennung)

  const wirePayload = {
    ...payload,
    event_uid: targetUid,
    title: encryptedTitle,
    description: encryptedDesc,
    location: encryptedLoc,
    recurrence: encryptedRec,
  }

  if (formEventId) {
    const existing = localEvents.find((e) => e.event_id === formEventId)
    resultEvent = {
      ...(existing || {
        id: Date.now(),
        event_id: formEventId,
        title: payload.title,
        start: payload.start_time,
        end: payload.end_time,
      }),
      title: payload.title,
      start: payload.start_time,
      end: payload.end_time,
      description: payload.description ?? '',
      location: payload.location ?? '',
      recurrence: klartextSerie,
      all_day: Boolean(payload.all_day),
      color: payload.color || 'primary',
      event_type: payload.event_type || 'personal',
      team_id: payload.team_id ?? null,
      server_id: payload.server_id ?? null,
      can_edit: true,
    }
    const updated = localEvents.map((e) => (e.event_id === formEventId ? resultEvent : e))
    setOfflineCalendarEvents(updated)

    enqueueMutation({
      entity: 'calendar',
      action: 'update',
      entityId: formEventId,
      payload: wirePayload,
    })
  } else {
    resultEvent = {
      id: Date.now(),
      event_id: targetUid,
      title: payload.title,
      start: payload.start_time,
      end: payload.end_time,
      description: payload.description ?? '',
      location: payload.location ?? '',
      recurrence: klartextSerie,
      all_day: Boolean(payload.all_day),
      color: payload.color || 'primary',
      calendar: 'MSM Kalender',
      event_type: payload.event_type || 'personal',
      team_id: payload.team_id ?? null,
      server_id: payload.server_id ?? null,
      can_edit: true,
    }
    localEvents.push(resultEvent)
    setOfflineCalendarEvents(localEvents)

    enqueueMutation({
      entity: 'calendar',
      action: 'create',
      entityId: targetUid,
      payload: wirePayload,
    })
  }

  void replayOutbox()

  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('msm:calendar-updated'))
  }

  return { event: resultEvent, queued: true }
}

export async function deleteCalendarEventOffline(eventId: string): Promise<{ queued: boolean }> {
  const localEvents = getOfflineCalendarEvents()
  const filtered = localEvents.filter((e) => e.event_id !== eventId)
  setOfflineCalendarEvents(filtered)

  const outbox = getOutbox()
  const isPendingLocalCreate = outbox.some(
    (m) => m.entity === 'calendar' && m.action === 'create' && m.entityId === eventId
  )

  if (isPendingLocalCreate) {
    setOutbox(outbox.filter((m) => m.entityId !== eventId))
  } else {
    enqueueMutation({
      entity: 'calendar',
      action: 'delete',
      entityId: eventId,
    })
  }

  void replayOutbox()

  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('msm:calendar-updated'))
  }

  return { queued: true }
}

// ── Real-Time SSE Stream & Adaptive Polling Manager ──

let isLiveConnected = false
let abortLiveSync: (() => void) | null = null
let reconnectTimer: any = null
let fallbackPollingTimer: any = null
let isInitialized = false

export function getIsLiveConnected(): boolean {
  return isLiveConnected
}

/**
 * Startet den langlebigen SSE-Echtzeitkanal (/api/events/live).
 * Bei Verbindungsabbruch schaltet das Subsystem automatisch auf adaptives Polling (10s) um
 * und verbindet sich im Hintergrund per Exponential Backoff wieder neu.
 */
export function startLiveSync(): () => void {
  if (abortLiveSync) {
    return abortLiveSync
  }

  let isCancelled = false
  let stableTimer: ReturnType<typeof setTimeout> | null = null
  let reconnectAttempts = 0
  const RECONNECT_DELAYS = [1000, 2000, 5000, 10000, 20000]
  const controller = typeof AbortController !== 'undefined' ? new AbortController() : null

  const stop = () => {
    isCancelled = true
    isLiveConnected = false
    if (stableTimer) {
      clearTimeout(stableTimer)
      stableTimer = null
    }
    if (controller) {
      try {
        controller.abort()
      } catch {}
    }
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (fallbackPollingTimer) {
      clearInterval(fallbackPollingTimer)
      fallbackPollingTimer = null
    }
    abortLiveSync = null
  }

  abortLiveSync = stop

  const startFallbackPolling = () => {
    if (isCancelled || fallbackPollingTimer) return
    fallbackPollingTimer = setInterval(() => {
      if (isCancelled) {
        stopFallbackPolling()
        return
      }
      // Kein Deckel auf `navigator.onLine`: das war die letzte Stelle, an der
      // eine falsche Auskunft des Betriebssystems die Warteschlange stehen
      // liess. `replayOutbox` bricht bei einem echten Netzwerkfehler von selbst
      // ab, ein Versuch alle zehn Sekunden kostet dann nichts.
      void replayOutbox()
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('msm:notes-updated'))
        window.dispatchEvent(new CustomEvent('msm:calendar-updated'))
      }
    }, 10_000)
  }

  const stopFallbackPolling = () => {
    if (fallbackPollingTimer) {
      clearInterval(fallbackPollingTimer)
      fallbackPollingTimer = null
    }
  }

  const scheduleReconnect = () => {
    if (isCancelled || reconnectTimer) return
    const baseDelay = RECONNECT_DELAYS[Math.min(reconnectAttempts, RECONNECT_DELAYS.length - 1)]
    reconnectAttempts++
    // Jitter: Streuung zwischen 85% und 100% des Base-Delays
    const minDelay = Math.floor(baseDelay * 0.85)
    const jitteredDelay = minDelay + Math.floor(Math.random() * (baseDelay - minDelay + 1))
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      if (!isCancelled) {
        void connectStream()
      }
    }, jitteredDelay)
  }

  const connectStream = async () => {
    if (isCancelled) return

    try {
      const res = await apiStream('/events/live', {
        method: 'GET',
        signal: controller?.signal,
      })

      if (!res.ok || !res.body) {
        return
      }

      isLiveConnected = true
      stopFallbackPolling()

      // Flapping-Schutz: reconnectAttempts erst nach 2000ms stabiler Verbindung zurücksetzen
      if (stableTimer) clearTimeout(stableTimer)
      stableTimer = setTimeout(() => {
        if (!isCancelled && isLiveConnected) {
          reconnectAttempts = 0
        }
        stableTimer = null
      }, 2000)

      // Bei gelungener Verbindung sofort Outbox abspielen
      void replayOutbox()

      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('msm:sync-status', { detail: { connected: true } }))
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      let currentEvent = 'message'

      while (!isCancelled) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          const trimmed = line.trim()
          if (!trimmed) {
            currentEvent = 'message'
            continue
          }
          if (trimmed.startsWith('event:')) {
            currentEvent = trimmed.slice(6).trim()
          } else if (trimmed.startsWith('data:')) {
            const dataStr = trimmed.slice(5).trim()
            try {
              const data = JSON.parse(dataStr) as SyncEventPayload
              handleIncomingSyncEvent(currentEvent, data)
            } catch {
              // Non-JSON or keepalive
            }
          }
        }
      }
    } catch {
      // Stream error or disconnection
    } finally {
      if (stableTimer) {
        clearTimeout(stableTimer)
        stableTimer = null
      }
      isLiveConnected = false
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('msm:sync-status', { detail: { connected: false } }))
      }
      if (!isCancelled) {
        startFallbackPolling()
        scheduleReconnect()
      }
    }
  }

  void connectStream()
  return stop
}

/**
 * Verarbeitet eingehende SSE-Ereignisse in unter 1s und aktualisiert das UI.
 */
export function handleIncomingSyncEvent(eventName: string, data: SyncEventPayload): void {
  if (typeof window === 'undefined') return

  if ((data as any)?.type === 'e2ee_blind_message' && (data as any)?.control_type?.startsWith('notes_key_')) {
    const cType = (data as any).control_type
    const targetUid = (data as any).recipient_id || (data as any).sender_user_id || getEffectiveUserId()
    if (cType === 'notes_key_sync') {
      void checkAndReceiveDeviceNotesKey(targetUid)
    } else if (cType === 'notes_key_request') {
      if (hasUserNotesKey(targetUid)) {
        void syncNotesKeyToPairedDevices(targetUid)
      }
    }
    return
  }

  if (eventName === 'sync' || data.entity) {
    const entity = data.entity
    const id = data.id || (data as any).note_uid || (data as any).event_id
    const outbox = getOutbox()
    const hasPendingLocal = id ? outbox.some((m) => m.entityId === id) : false

    if (entity === 'notes' || entity === 'note') {
      if (!hasPendingLocal && id) {
        if (data.action === 'deleted') {
          const current = getOfflineNotes()
          setOfflineNotes(current.filter((n) => n.note_uid !== id))
          window.dispatchEvent(new CustomEvent('msm:notes-updated', { detail: data }))
        } else if (data.data && typeof data.data === 'object') {
          const raw = data.data
          const isEncrypted = typeof raw.title === 'string' && raw.title.startsWith(NOTE_CIPHERTEXT_PREFIX)
          if (isEncrypted) {
            void (async () => {
              let title = raw.title
              let content = raw.content
              try {
                title = await decryptNoteTitle(raw.title, id, undefined, raw.user_id)
              } catch {}
              try {
                content = await decryptNoteContent(raw.content, id, undefined, raw.user_id)
              } catch {}
              const decryptedData: NoteItem = {
                ...raw,
                title,
                content,
              }
              const current = getOfflineNotes()
              if (data.action === 'created') {
                if (!current.some((n) => n.note_uid === id)) {
                  setOfflineNotes([...current, decryptedData])
                }
              } else if (data.action === 'updated') {
                setOfflineNotes(current.map((n) => (n.note_uid === id ? { ...n, ...decryptedData } : n)))
              }
              window.dispatchEvent(new CustomEvent('msm:notes-updated', { detail: { ...data, data: decryptedData } }))
              window.dispatchEvent(new CustomEvent('msm:sync-event', { detail: { ...data, data: decryptedData } }))
            })()
            return
          } else {
            const current = getOfflineNotes()
            if (data.action === 'created') {
              if (!current.some((n) => n.note_uid === id)) {
                setOfflineNotes([raw, ...current])
              }
            } else if (data.action === 'updated') {
              setOfflineNotes(current.map((n) => (n.note_uid === id ? { ...n, ...raw } : n)))
            }
            window.dispatchEvent(new CustomEvent('msm:notes-updated', { detail: data }))
            window.dispatchEvent(new CustomEvent('msm:sync-event', { detail: data }))
            return
          }
        }
      }
      window.dispatchEvent(new CustomEvent('msm:notes-updated', { detail: data }))
    } else if (entity === 'calendar') {
      if (!hasPendingLocal && id) {
        if (data.action === 'deleted') {
          const current = getOfflineCalendarEvents()
          setOfflineCalendarEvents(current.filter((e) => e.event_id !== id))
          window.dispatchEvent(new CustomEvent('msm:calendar-updated', { detail: data }))
        } else if (data.data && typeof data.data === 'object') {
          const raw = data.data
          const isEncrypted =
            (typeof raw.title === 'string' && raw.title.startsWith(CALENDAR_CIPHERTEXT_PREFIX)) ||
            (typeof raw.recurrence === 'string' && raw.recurrence.startsWith(CALENDAR_CIPHERTEXT_PREFIX))
          if (isEncrypted) {
            void (async () => {
              let title = raw.title
              let description = raw.description || ''
              let location = raw.location || ''
              let recurrence = raw.recurrence || ''
              try {
                title = await decryptCalendarField(raw.title, id, 'title', undefined, raw.user_id)
              } catch {}
              try {
                if (raw.description) {
                  description = await decryptCalendarField(raw.description, id, 'description', undefined, raw.user_id)
                }
              } catch {}
              try {
                if (raw.location) {
                  location = await decryptCalendarField(raw.location, id, 'location', undefined, raw.user_id)
                }
              } catch {}
              try {
                if (raw.recurrence) {
                  recurrence = await decryptCalendarField(raw.recurrence, id, 'recurrence', undefined, raw.user_id)
                }
              } catch {}
              const decryptedData: CalendarEventItem = {
                ...raw,
                title,
                description,
                location,
                recurrence,
              }
              const current = getOfflineCalendarEvents()
              if (data.action === 'created') {
                if (!current.some((e) => e.event_id === id)) {
                  setOfflineCalendarEvents([...current, decryptedData])
                }
              } else if (data.action === 'updated') {
                setOfflineCalendarEvents(current.map((e) => (e.event_id === id ? { ...e, ...decryptedData } : e)))
              }
              window.dispatchEvent(new CustomEvent('msm:calendar-updated', { detail: { ...data, data: decryptedData } }))
              window.dispatchEvent(new CustomEvent('msm:sync-event', { detail: { ...data, data: decryptedData } }))
            })()
            return
          } else {
            const current = getOfflineCalendarEvents()
            if (data.action === 'created') {
              if (!current.some((e) => e.event_id === id)) {
                setOfflineCalendarEvents([...current, raw])
              }
            } else if (data.action === 'updated') {
              setOfflineCalendarEvents(current.map((e) => (e.event_id === id ? { ...e, ...raw } : e)))
            }
            window.dispatchEvent(new CustomEvent('msm:calendar-updated', { detail: data }))
            window.dispatchEvent(new CustomEvent('msm:sync-event', { detail: data }))
            return
          }
        }
      }
      window.dispatchEvent(new CustomEvent('msm:calendar-updated', { detail: data }))
    }
    window.dispatchEvent(new CustomEvent('msm:sync-event', { detail: data }))
  }
}

export function reconnectLiveSyncNow(): void {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  if (!isLiveConnected) {
    if (abortLiveSync) {
      abortLiveSync()
    }
    void startLiveSync()
  }
}

export function ensureLiveSyncRunning(): () => void {
  if (typeof window === 'undefined') return () => {}
  return startLiveSync()
}

// ── Global Network & Sync Lifecycle Initialization ──

export function initOfflineSync(): () => void {
  if (typeof window === 'undefined' || isInitialized) {
    return () => {}
  }

  isInitialized = true
  const stopStream = startLiveSync()

  const handleOnline = () => {
    void replayOutbox()
    reconnectLiveSyncNow()
  }

  const handleVisibilityChange = () => {
    if (document.visibilityState === 'visible') {
      void replayOutbox()
      reconnectLiveSyncNow()
    }
  }

  window.addEventListener('online', handleOnline)
  document.addEventListener('visibilitychange', handleVisibilityChange)

  void replayOutbox()

  return () => {
    window.removeEventListener('online', handleOnline)
    document.removeEventListener('visibilitychange', handleVisibilityChange)
    stopStream()
    isInitialized = false
  }
}

// ── Unified React Hook for Entities (Notes / Calendar) ──

export function useEntitySync(
  entity: 'notes' | 'calendar' | 'all' = 'all',
  onRefresh?: (eventData?: any) => void
) {
  const [isOnline, setIsOnline] = useState(() => (typeof navigator !== 'undefined' ? navigator.onLine : true))
  const [isLive, setIsLive] = useState(() => getIsLiveConnected())
  const [outboxCount, setOutboxCount] = useState(() => getOutbox().length)
  const refreshCallbackRef = useRef(onRefresh)
  refreshCallbackRef.current = onRefresh

  useEffect(() => {
    const triggerRefresh = (detail?: any) => {
      refreshCallbackRef.current?.(detail)
    }

    const handleNotes = (e: any) => {
      if (entity === 'notes' || entity === 'all') {
        triggerRefresh(e?.detail)
      }
    }

    const handleCalendar = (e: any) => {
      if (entity === 'calendar' || entity === 'all') {
        triggerRefresh(e?.detail)
      }
    }

    const handleSyncStatus = () => {
      setOutboxCount(getOutbox().length)
      setIsLive(getIsLiveConnected())
      if (typeof navigator !== 'undefined') {
        setIsOnline(navigator.onLine)
      }
    }

    const handleOnlineEvent = () => {
      setIsOnline(true)
      reconnectLiveSyncNow()
      void replayOutbox().then(() => {
        triggerRefresh()
      })
    }

    const handleOfflineEvent = () => {
      setIsOnline(false)
      setIsLive(false)
    }

    window.addEventListener('msm:notes-updated', handleNotes)
    window.addEventListener('msm:calendar-updated', handleCalendar)
    window.addEventListener('msm:sync-status', handleSyncStatus)
    window.addEventListener('online', handleOnlineEvent)
    window.addEventListener('offline', handleOfflineEvent)

    // Ensure SSE is active
    ensureLiveSyncRunning()

    return () => {
      window.removeEventListener('msm:notes-updated', handleNotes)
      window.removeEventListener('msm:calendar-updated', handleCalendar)
      window.removeEventListener('msm:sync-status', handleSyncStatus)
      window.removeEventListener('online', handleOnlineEvent)
      window.removeEventListener('offline', handleOfflineEvent)
    }
  }, [entity])

  const syncNow = useCallback(async () => {
    await replayOutbox()
    refreshCallbackRef.current?.()
  }, [])

  return {
    isOnline,
    isLive,
    outboxCount,
    syncNow,
  }
}
