/**
 * Offline-First Local Storage & Near Real-Time Background Synchronization
 * for Notes & Calendar in Maunting Server Manager (Web / APK / Desktop).
 *
 * Adheres strictly to the KISS principle and Data Minimization:
 * - Persistent local cache (localStorage) for Notes and Calendar entries.
 * - Persistent Outbox mutation queue surviving app restarts and reloads.
 * - Deterministic Last-Write-Wins (LWW) conflict resolution using ISO timestamps.
 * - Automatic outbox replay on network reconnection ('online' event) and polling triggers.
 * - Zero tracking, zero telemetry, zero superfluous metadata stored.
 */

import { api } from '@/api/client'
import type { NoteItem } from '@/pages/Notes'
import type { CalendarEventItem } from '@/pages/Calendar'

export const STORAGE_KEYS = {
  NOTES: 'msm_offline_notes',
  CALENDAR: 'msm_offline_calendar',
  OUTBOX: 'msm_offline_outbox',
  LAST_SYNC: 'msm_offline_last_sync',
} as const

export interface OutboxMutation {
  id: string
  entity: 'note' | 'calendar'
  action: 'create' | 'update' | 'delete' | 'toggle_pin' | 'toggle_archive'
  entityId: string
  payload?: any
  timestamp: string
  retryCount: number
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
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.removeItem(STORAGE_KEYS.NOTES)
      window.localStorage.removeItem(STORAGE_KEYS.CALENDAR)
      window.localStorage.removeItem(STORAGE_KEYS.OUTBOX)
      window.localStorage.removeItem(STORAGE_KEYS.LAST_SYNC)
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

export function enqueueMutation(mutation: Omit<OutboxMutation, 'id' | 'timestamp' | 'retryCount'>): OutboxMutation {
  const fullMutation: OutboxMutation = {
    ...mutation,
    id: 'mut-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7),
    timestamp: new Date().toISOString(),
    retryCount: 0,
  }
  const current = getOutbox()
  current.push(fullMutation)
  setOutbox(current)
  return fullMutation
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

  const eventMap = new Map<string, CalendarEventItem>()

  for (const se of serverEvents) {
    eventMap.set(se.event_id, se)
  }

  for (const le of localEvents) {
    const hasPendingMutation = outbox.some((m) => m.entityId === le.event_id)
    if (hasPendingMutation) {
      const serverEvent = eventMap.get(le.event_id)
      if (!serverEvent) {
        // Created locally and not yet synced
        eventMap.set(le.event_id, le)
      } else {
        // Local mutation wins for active edits
        eventMap.set(le.event_id, { ...serverEvent, ...le })
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

  const outbox = getOutbox()
  if (outbox.length === 0) {
    return { processed: 0, failed: 0, remaining: 0 }
  }

  isReplaying = true
  let processed = 0
  let failed = 0
  const remainingMutations: OutboxMutation[] = []

  try {
    for (let i = 0; i < outbox.length; i++) {
      const mutation = outbox[i]
      try {
        if (mutation.entity === 'note') {
          if (mutation.action === 'create') {
            const res = await api<NoteItem>('/notes', {
              method: 'POST',
              body: JSON.stringify(mutation.payload),
            })
            if (res && res.note_uid) {
              const notes = getOfflineNotes()
              const updated = notes.map((n) =>
                n.note_uid === mutation.entityId ? { ...n, ...res } : n
              )
              setOfflineNotes(updated)
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
              const events = getOfflineCalendarEvents()
              const updated = events.map((e) =>
                e.event_id === mutation.entityId ? { ...e, ...res } : e
              )
              setOfflineCalendarEvents(updated)
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
        }
        processed++
      } catch (err: any) {
        const isNetworkErr =
          err?.status === 0 ||
          err?.name === 'TypeError' ||
          err?.message?.includes('Failed to fetch') ||
          err?.message?.includes('NetworkError') ||
          (typeof navigator !== 'undefined' && !navigator.onLine)

        if (isNetworkErr) {
          remainingMutations.push(mutation, ...outbox.slice(i + 1))
          break
        } else if (err?.status === 404 || err?.status === 400) {
          failed++
        } else {
          mutation.retryCount = (mutation.retryCount || 0) + 1
          if (mutation.retryCount > 5) {
            failed++
          } else {
            remainingMutations.push(mutation)
          }
        }
      }
    }
  } finally {
    setOutbox(remainingMutations)
    isReplaying = false

    if (typeof window !== 'undefined') {
      window.dispatchEvent(
        new CustomEvent('msm:sync-status', {
          detail: { processed, remaining: remainingMutations.length },
        })
      )
    }
  }

  return { processed, failed, remaining: remainingMutations.length }
}

// ── Public Offline-First Notes API ──

export async function loadNotesOfflineFirst(options?: {
  includeArchived?: boolean
}): Promise<{ notes: NoteItem[]; isOffline: boolean }> {
  let localNotes = getOfflineNotes()
  let isOffline = false

  try {
    const data = await api<NoteItem[]>('/notes?include_archived=true')
    if (Array.isArray(data)) {
      localNotes = mergeNotesWithServer(data)
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
      payload,
    })
  } else {
    const tempUid = 'local-note-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7)
    resultNote = {
      id: Date.now(),
      note_uid: tempUid,
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
      entityId: tempUid,
      payload,
    })
  }

  if (typeof navigator === 'undefined' || navigator.onLine) {
    void replayOutbox()
  }

  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event('msm:notes-updated'))
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

  if (typeof navigator === 'undefined' || navigator.onLine) {
    void replayOutbox()
  }

  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event('msm:notes-updated'))
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

  if (typeof navigator === 'undefined' || navigator.onLine) {
    void replayOutbox()
  }

  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event('msm:notes-updated'))
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

  if (typeof navigator === 'undefined' || navigator.onLine) {
    void replayOutbox()
  }

  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event('msm:notes-updated'))
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
    const isUnchecked = line.trimStart().startsWith('- [ ]')
    const isChecked = line.trimStart().startsWith('- [x]') || line.trimStart().startsWith('- [X]')
    if (isUnchecked || isChecked) {
      if (currentCheckIdx === itemIndex) {
        currentCheckIdx++
        if (isUnchecked) {
          return line.replace('- [ ]', '- [x]')
        } else {
          return line.replace(/- \[[xX]\]/, '- [ ]')
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

  enqueueMutation({
    entity: 'note',
    action: 'update',
    entityId: note.note_uid,
    payload: { content: updatedContent },
  })

  if (typeof navigator === 'undefined' || navigator.onLine) {
    void replayOutbox()
  }

  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event('msm:notes-updated'))
  }

  return { note: updatedNote, updatedContent, queued: true }
}

// ── Public Offline-First Calendar API ──

export async function loadCalendarEventsOfflineFirst(
  rangeStart: string,
  rangeEnd: string,
  eventType?: string
): Promise<{ events: CalendarEventItem[]; isOffline: boolean }> {
  let localEvents = getOfflineCalendarEvents()
  let isOffline = false

  try {
    const catParam = eventType && eventType !== 'all' ? '&event_type=' + encodeURIComponent(eventType) : ''
    const data = await api<CalendarEventItem[]>(
      '/calendar/events?start=' + encodeURIComponent(rangeStart) + '&end=' + encodeURIComponent(rangeEnd) + catParam
    )
    if (Array.isArray(data)) {
      localEvents = mergeCalendarWithServer(data)
    }
  } catch {
    isOffline = true
  }

  const startDt = new Date(rangeStart).getTime()
  const endDt = new Date(rangeEnd).getTime()

  const filtered = localEvents.filter((ev) => {
    if (eventType && eventType !== 'all' && ev.event_type !== eventType) {
      return false
    }
    const evStart = new Date(ev.start).getTime()
    const evEnd = new Date(ev.end).getTime()
    return evStart <= endDt && evEnd >= startDt
  })

  if (!isOffline && getOutbox().length > 0) {
    void replayOutbox()
  }

  return { events: filtered, isOffline }
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
  },
  formEventId?: string | null
): Promise<{ event: CalendarEventItem; queued: boolean }> {
  const localEvents = getOfflineCalendarEvents()
  let resultEvent: CalendarEventItem

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
      payload,
    })
  } else {
    const tempUid = 'local-evt-' + Date.now() + '-' + Math.random().toString(36).substring(2, 7)
    resultEvent = {
      id: Date.now(),
      event_id: tempUid,
      title: payload.title,
      start: payload.start_time,
      end: payload.end_time,
      description: payload.description ?? '',
      location: payload.location ?? '',
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
      entityId: tempUid,
      payload,
    })
  }

  if (typeof navigator === 'undefined' || navigator.onLine) {
    void replayOutbox()
  }

  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event('msm:calendar-updated'))
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

  if (typeof navigator === 'undefined' || navigator.onLine) {
    void replayOutbox()
  }

  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event('msm:calendar-updated'))
  }

  return { queued: true }
}

// ── Global Network & Sync Lifecycle Initialization ──

let isInitialized = false

export function initOfflineSync(): () => void {
  if (typeof window === 'undefined' || isInitialized) {
    return () => {}
  }

  isInitialized = true

  const handleOnline = () => {
    void replayOutbox()
  }

  const handleVisibilityChange = () => {
    if (document.visibilityState === 'visible') {
      void replayOutbox()
    }
  }

  window.addEventListener('online', handleOnline)
  document.addEventListener('visibilitychange', handleVisibilityChange)

  if (navigator.onLine) {
    void replayOutbox()
  }

  return () => {
    window.removeEventListener('online', handleOnline)
    document.removeEventListener('visibilitychange', handleVisibilityChange)
    isInitialized = false
  }
}
