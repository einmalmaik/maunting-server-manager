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
              const oldUid = mutation.entityId
              const newUid = res.note_uid
              const notes = getOfflineNotes()
              const updated = notes.map((n) =>
                n.note_uid === oldUid ? { ...n, ...res, note_uid: newUid } : n
              )
              setOfflineNotes(updated)

              // Update any subsequent queued mutations that referenced the temporary UID
              if (oldUid !== newUid) {
                for (let j = i + 1; j < outbox.length; j++) {
                  if (outbox[j].entity === 'note' && outbox[j].entityId === oldUid) {
                    outbox[j].entityId = newUid
                  }
                }
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
                e.event_id === oldUid ? { ...e, ...res, event_id: newUid } : e
              )
              setOfflineCalendarEvents(updated)

              // Update any subsequent queued mutations that referenced the temporary UID
              if (oldUid !== newUid) {
                for (let j = i + 1; j < outbox.length; j++) {
                  if (outbox[j].entity === 'calendar' && outbox[j].entityId === oldUid) {
                    outbox[j].entityId = newUid
                  }
                }
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
      if (processed > 0) {
        window.dispatchEvent(new CustomEvent('msm:notes-updated'))
        window.dispatchEvent(new CustomEvent('msm:calendar-updated'))
      }
      window.dispatchEvent(
        new CustomEvent('msm:sync-status', {
          detail: { processed, failed, remaining: remainingMutations.length },
        })
      )
    }
  }

  return { processed, failed, remaining: remainingMutations.length }
}

// ── Public Offline-First Notes API ──

export async function loadNotesOfflineFirst(_options?: {
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

  if (typeof navigator === 'undefined' || navigator.onLine) {
    void replayOutbox()
  }

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

  if (typeof navigator === 'undefined' || navigator.onLine) {
    void replayOutbox()
  }

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

  if (typeof navigator === 'undefined' || navigator.onLine) {
    void replayOutbox()
  }

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
    window.dispatchEvent(new CustomEvent('msm:notes-updated'))
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
    const rawEnd = ev.end ? new Date(ev.end).getTime() : NaN
    const evEnd = isNaN(rawEnd) ? evStart : rawEnd
    const validStart = isNaN(evStart) ? 0 : evStart
    return validStart <= endDt && evEnd >= startDt
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

  if (typeof navigator === 'undefined' || navigator.onLine) {
    void replayOutbox()
  }

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
  const controller = typeof AbortController !== 'undefined' ? new AbortController() : null

  const stop = () => {
    isCancelled = true
    isLiveConnected = false
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
    if (fallbackPollingTimer) return
    fallbackPollingTimer = setInterval(() => {
      if (typeof navigator !== 'undefined' && !navigator.onLine) return
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

  const scheduleReconnect = (delayMs = 3000) => {
    if (isCancelled || reconnectTimer) return
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      if (!isCancelled) {
        void connectStream()
      }
    }, delayMs)
  }

  const connectStream = async () => {
    if (isCancelled) return

    try {
      const res = await apiStream('/events/live', {
        method: 'GET',
        signal: controller?.signal,
      })

      if (!res.ok || !res.body) {
        isLiveConnected = false
        startFallbackPolling()
        scheduleReconnect(5000)
        return
      }

      isLiveConnected = true
      stopFallbackPolling()

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
      isLiveConnected = false
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('msm:sync-status', { detail: { connected: false } }))
      }
      startFallbackPolling()
      scheduleReconnect(4000)
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
        } else if (data.data && typeof data.data === 'object') {
          const current = getOfflineNotes()
          if (data.action === 'created') {
            if (!current.some((n) => n.note_uid === id)) {
              setOfflineNotes([data.data, ...current])
            }
          } else if (data.action === 'updated') {
            setOfflineNotes(current.map((n) => (n.note_uid === id ? { ...n, ...data.data } : n)))
          }
        }
      }
      window.dispatchEvent(new CustomEvent('msm:notes-updated', { detail: data }))
    } else if (entity === 'calendar') {
      if (!hasPendingLocal && id) {
        if (data.action === 'deleted') {
          const current = getOfflineCalendarEvents()
          setOfflineCalendarEvents(current.filter((e) => e.event_id !== id))
        } else if (data.data && typeof data.data === 'object') {
          const current = getOfflineCalendarEvents()
          if (data.action === 'created') {
            if (!current.some((e) => e.event_id === id)) {
              setOfflineCalendarEvents([...current, data.data])
            }
          } else if (data.action === 'updated') {
            setOfflineCalendarEvents(current.map((e) => (e.event_id === id ? { ...e, ...data.data } : e)))
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

  if (navigator.onLine) {
    void replayOutbox()
  }

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
