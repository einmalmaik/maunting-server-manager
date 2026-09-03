import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import {
  getOfflineNotes,
  setOfflineNotes,
  getOfflineCalendarEvents,
  setOfflineCalendarEvents,
  getOutbox,
  setOutbox,
  clearMemoryStoreForTesting,
  saveNoteOffline,
  deleteNoteOffline,
  toggleNotePinOffline,
  toggleNoteArchiveOffline,
  toggleCheckItemOffline,
  loadCalendarEventsOfflineFirst,
  saveCalendarEventOffline,
  deleteCalendarEventOffline,
  replayOutbox,
  mergeNotesWithServer,
  mergeCalendarWithServer,
  initOfflineSync,
  handleIncomingSyncEvent,
  useEntitySync,
} from './offlineSync'
import * as client from '@/api/client'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
  apiStream: vi.fn(),
}))

describe('Offline Storage & Unified Real-Time SSE Sync Engine', () => {
  beforeEach(() => {
    clearMemoryStoreForTesting()
    vi.mocked(client.api).mockReset()
    vi.mocked(client.apiStream).mockReset()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  describe('R1. Offline-First Local Storage & Data Minimization', () => {
    it('saves and reads notes locally without network calls', () => {
      const notes = [
        {
          id: 1,
          note_uid: 'note-1',
          title: 'Offline Einkauf',
          content: '- [ ] Milch\n- [ ] Butter',
          category: 'shopping',
          color: 'emerald',
          is_pinned: false,
          is_archived: false,
          note_type: 'personal',
          user_id: 1,
          created_at: '2026-09-02T10:00:00Z',
          updated_at: '2026-09-02T10:00:00Z',
        },
      ]
      setOfflineNotes(notes)

      const read = getOfflineNotes()
      expect(read).toHaveLength(1)
      expect(read[0].title).toBe('Offline Einkauf')
      expect(read[0].content).toContain('Milch')
    })

    it('saves and reads calendar events locally without network calls', () => {
      const events = [
        {
          id: 1,
          event_id: 'evt-1',
          title: 'Wartung Node-1',
          start: '2026-09-02T14:00:00Z',
          end: '2026-09-02T15:00:00Z',
          event_type: 'server',
          color: 'purple',
        },
      ]
      setOfflineCalendarEvents(events)

      const read = getOfflineCalendarEvents()
      expect(read).toHaveLength(1)
      expect(read[0].title).toBe('Wartung Node-1')
      expect(read[0].color).toBe('purple')
    })

    it('creates, edits, toggles checklist items and deletes notes while offline without throwing', async () => {
      vi.mocked(client.api).mockRejectedValue(new TypeError('Failed to fetch'))

      // 1. Create note offline
      const { note: created } = await saveNoteOffline({
        title: 'Offline Notiz',
        content: '- [ ] Aufgabe 1\n- [ ] Aufgabe 2',
        category: 'todo',
      })
      expect(created.title).toBe('Offline Notiz')
      expect(getOfflineNotes()).toHaveLength(1)
      expect(getOutbox()).toHaveLength(1)
      expect(getOutbox()[0].action).toBe('create')

      // 2. Toggle checklist item offline
      const { note: toggled } = await toggleCheckItemOffline(created, 0)
      expect(toggled.content).toContain('- [x] Aufgabe 1')
      expect(getOfflineNotes()[0].content).toContain('- [x] Aufgabe 1')

      // 3. Edit note offline
      const { note: edited } = await saveNoteOffline(
        { title: 'Offline Notiz (Bearbeitet)', content: toggled.content },
        toggled
      )
      expect(edited.title).toBe('Offline Notiz (Bearbeitet)')
      expect(getOfflineNotes()[0].title).toBe('Offline Notiz (Bearbeitet)')

      // 4. Toggle pin & archive offline
      await toggleNotePinOffline(edited)
      expect(getOfflineNotes()[0].is_pinned).toBe(true)
      await toggleNoteArchiveOffline(edited)
      expect(getOfflineNotes()[0].is_archived).toBe(true)

      // 5. Delete note offline
      await deleteNoteOffline(edited)
      expect(getOfflineNotes()).toHaveLength(0)
    })

    it('creates, updates and deletes calendar events while offline without throwing', async () => {
      vi.mocked(client.api).mockRejectedValue(new TypeError('NetworkError when attempting to fetch resource.'))

      // 1. Create calendar event offline
      const { event: created } = await saveCalendarEventOffline({
        title: 'Offline Meeting',
        start_time: '2026-09-02T10:00:00Z',
        end_time: '2026-09-02T11:00:00Z',
        event_type: 'team',
      })
      expect(created.title).toBe('Offline Meeting')
      expect(getOfflineCalendarEvents()).toHaveLength(1)

      // 2. Load events offline in range
      const { events, isOffline } = await loadCalendarEventsOfflineFirst(
        '2026-09-02T00:00:00Z',
        '2026-09-02T23:59:59Z'
      )
      expect(isOffline).toBe(true)
      expect(events).toHaveLength(1)
      expect(events[0].title).toBe('Offline Meeting')

      // 3. Update event offline
      const { event: updated } = await saveCalendarEventOffline(
        {
          title: 'Offline Meeting (Verschoben)',
          start_time: '2026-09-02T11:00:00Z',
          end_time: '2026-09-02T12:00:00Z',
        },
        created.event_id
      )
      expect(updated.title).toBe('Offline Meeting (Verschoben)')
      expect(getOfflineCalendarEvents()[0].title).toBe('Offline Meeting (Verschoben)')

      // 4. Delete event offline
      await deleteCalendarEventOffline(created.event_id)
      expect(getOfflineCalendarEvents()).toHaveLength(0)
    })
  })

  describe('R2. Real-Time SSE Processing & Unified Event Stream', () => {
    it('dispatches msm:notes-updated event on incoming note sync SSE signal', () => {
      const listener = vi.fn()
      window.addEventListener('msm:notes-updated', listener)

      handleIncomingSyncEvent('sync', {
        entity: 'notes',
        action: 'updated',
        id: 'note-sse-1',
        data: { id: 1, note_uid: 'note-sse-1', title: 'SSE Note' },
      })

      expect(listener).toHaveBeenCalledTimes(1)
      window.removeEventListener('msm:notes-updated', listener)
    })

    it('dispatches msm:calendar-updated event on incoming calendar sync SSE signal', () => {
      const listener = vi.fn()
      window.addEventListener('msm:calendar-updated', listener)

      handleIncomingSyncEvent('sync', {
        entity: 'calendar',
        action: 'created',
        id: 'cal-sse-1',
        data: { id: 1, event_id: 'cal-sse-1', title: 'SSE Termin' },
      })

      expect(listener).toHaveBeenCalledTimes(1)
      window.removeEventListener('msm:calendar-updated', listener)
    })

    it('useEntitySync hook reacts to real-time events and online triggers', async () => {
      const onRefresh = vi.fn()
      const { result } = renderHook(() => useEntitySync('notes', onRefresh))

      expect(result.current.isOnline).toBe(true)

      // Trigger notes update event
      act(() => {
        window.dispatchEvent(new CustomEvent('msm:notes-updated', { detail: { id: 'n1' } }))
      })
      expect(onRefresh).toHaveBeenCalledTimes(1)
    })
  })

  describe('R3. Outbox Mutation Queue & Conflict Resolution (LWW)', () => {
    it('persists outbox mutations and replays them in chronological order when reconnected', async () => {
      // Setup offline creations
      vi.mocked(client.api).mockRejectedValue(new TypeError('Failed to fetch'))

      await saveNoteOffline({ title: 'Notiz 1' })
      await saveNoteOffline({ title: 'Notiz 2' })

      expect(getOutbox()).toHaveLength(2)
      expect(getOutbox()[0].payload.title).toBe('Notiz 1')
      expect(getOutbox()[1].payload.title).toBe('Notiz 2')

      // Now network is back online
      vi.mocked(client.api).mockImplementation(async (path: string, options?: any) => {
        if (path === '/notes' && options?.method === 'POST') {
          const body = JSON.parse(options.body)
          return {
            id: 101,
            note_uid: 'server-uid-' + body.title,
            title: body.title,
            content: '',
            category: 'personal',
            color: 'primary',
            is_pinned: false,
            is_archived: false,
            note_type: 'personal',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          }
        }
        return {}
      })

      const notesUpdatedListener = vi.fn()
      window.addEventListener('msm:notes-updated', notesUpdatedListener)

      const result = await replayOutbox()
      expect(result.processed).toBe(2)
      expect(result.remaining).toBe(0)
      expect(getOutbox()).toHaveLength(0)
      expect(notesUpdatedListener).toHaveBeenCalled()

      // Check that local cache was updated with server canonical note_uids
      const stored = getOfflineNotes()
      expect(stored[1].note_uid).toBe('server-uid-Notiz 1')
      expect(stored[0].note_uid).toBe('server-uid-Notiz 2')

      window.removeEventListener('msm:notes-updated', notesUpdatedListener)
    })

    it('resolves conflicts deterministically using Last-Write-Wins (LWW)', () => {
      const oldServerDate = '2026-09-01T10:00:00Z'
      const newLocalDate = '2026-09-02T12:00:00Z'

      // Server has older version
      const serverNotes = [
        {
          id: 10,
          note_uid: 'note-shared-1',
          title: 'Alter Titel vom Server',
          content: 'Alter Inhalt',
          category: 'personal',
          color: 'primary',
          is_pinned: false,
          is_archived: false,
          note_type: 'personal',
          user_id: 1,
          created_at: oldServerDate,
          updated_at: oldServerDate,
        },
      ]

      // Local has newer edit in outbox
      setOfflineNotes([
        {
          id: 10,
          note_uid: 'note-shared-1',
          title: 'Neuerer lokaler Titel',
          content: 'Neuer lokaler Inhalt',
          category: 'personal',
          color: 'primary',
          is_pinned: false,
          is_archived: false,
          note_type: 'personal',
          user_id: 1,
          created_at: oldServerDate,
          updated_at: newLocalDate,
        },
      ])

      setOutbox([
        {
          id: 'mut-1',
          entity: 'note',
          action: 'update',
          entityId: 'note-shared-1',
          payload: { title: 'Neuerer lokaler Titel' },
          timestamp: newLocalDate,
          retryCount: 0,
        },
      ])

      const merged = mergeNotesWithServer(serverNotes)
      expect(merged).toHaveLength(1)
      expect(merged[0].title).toBe('Neuerer lokaler Titel')
    })

    it('replays outbox automatically upon online event', async () => {
      const cleanup = initOfflineSync()

      // Add a mutation
      setOutbox([
        {
          id: 'mut-online-1',
          entity: 'note',
          action: 'create',
          entityId: 'local-test',
          payload: { title: 'Online Auto Sync Test' },
          timestamp: new Date().toISOString(),
          retryCount: 0,
        },
      ])

      vi.mocked(client.api).mockResolvedValue({
        id: 999,
        note_uid: 'server-note-auto',
        title: 'Online Auto Sync Test',
      })

      // Trigger online event
      window.dispatchEvent(new Event('online'))

      // Wait a tick for async handler
      await new Promise((resolve) => setTimeout(resolve, 50))

      expect(client.api).toHaveBeenCalledWith('/notes', expect.objectContaining({ method: 'POST' }))
      expect(getOutbox()).toHaveLength(0)

      cleanup()
    })

    it('preserves cached calendar events across multiple month and date range fetches', () => {
      // 1. Initial cached event in January
      setOfflineCalendarEvents([
        {
          id: 1,
          event_id: 'jan-evt-1',
          title: 'Januar Meeting',
          start: '2026-01-15T10:00:00Z',
          end: '2026-01-15T11:00:00Z',
          event_type: 'personal',
          color: 'primary',
          calendar: 'MSM Kalender',
          all_day: false,
          user_id: 1,
          can_edit: true,
        },
      ])

      // 2. Fetch February events from server
      const febServerEvents = [
        {
          id: 2,
          event_id: 'feb-evt-2',
          title: 'Februar Release',
          start: '2026-02-20T14:00:00Z',
          end: '2026-02-20T15:00:00Z',
          event_type: 'server',
          color: 'purple',
          calendar: 'MSM Kalender',
          all_day: false,
          user_id: 1,
          can_edit: true,
        },
      ]

      const merged = mergeCalendarWithServer(febServerEvents)

      // Both January and February events must be preserved in offline cache
      expect(merged).toHaveLength(2)
      const cached = getOfflineCalendarEvents()
      expect(cached).toHaveLength(2)
      expect(cached.find((e) => e.event_id === 'jan-evt-1')).toBeDefined()
      expect(cached.find((e) => e.event_id === 'feb-evt-2')).toBeDefined()
    })

    it('correctly rewrites temporary client UIDs in subsequent outbox mutations when replaying outbox', async () => {
      // Setup offline state: user created note offline, then toggled a checkbox on it
      vi.mocked(client.api).mockRejectedValue(new TypeError('Failed to fetch'))

      const { note: created } = await saveNoteOffline({
        title: 'Offline Einkaufsliste',
        content: '- [ ] Milch\n- [ ] Brot',
      })
      const tempUid = created.note_uid

      // User immediately checked off Milch while still offline
      await toggleCheckItemOffline(created, 0)

      expect(getOutbox()).toHaveLength(2)
      expect(getOutbox()[0].action).toBe('create')
      expect(getOutbox()[0].entityId).toBe(tempUid)
      expect(getOutbox()[1].action).toBe('update')
      expect(getOutbox()[1].entityId).toBe(tempUid)

      // Reconnect: Server creates note and returns canonical server UUID
      const serverUid = 'srv-canonical-uuid-123'
      const putUrls: string[] = []

      vi.mocked(client.api).mockImplementation(async (path: string, options?: any) => {
        if (path === '/notes' && options?.method === 'POST') {
          return {
            id: 888,
            note_uid: serverUid,
            title: 'Offline Einkaufsliste',
            content: '- [ ] Milch\n- [ ] Brot',
            updated_at: new Date().toISOString(),
          }
        }
        if (path.startsWith('/notes/') && options?.method === 'PUT') {
          putUrls.push(path)
          return {
            id: 888,
            note_uid: serverUid,
            title: 'Offline Einkaufsliste',
            content: '- [x] Milch\n- [ ] Brot',
            updated_at: new Date().toISOString(),
          }
        }
        return {}
      })

      const res = await replayOutbox()
      expect(res.processed).toBe(2)
      expect(res.failed).toBe(0)
      expect(res.remaining).toBe(0)

      // Verify that PUT call targeted the canonical server UUID, NOT the old tempUid
      expect(putUrls).toHaveLength(1)
      expect(putUrls[0]).toBe('/notes/' + serverUid)

      // Verify cached note has canonical server UID
      const finalNotes = getOfflineNotes()
      expect(finalNotes).toHaveLength(1)
      expect(finalNotes[0].note_uid).toBe(serverUid)
    })

    it('instantly updates local storage cache when handleIncomingSyncEvent receives notes/calendar payloads', () => {
      // 1. Incoming note create
      handleIncomingSyncEvent('sync', {
        entity: 'notes',
        action: 'created',
        id: 'note-incoming-1',
        data: {
          id: 77,
          note_uid: 'note-incoming-1',
          title: 'Instant Note from Peer',
          content: '- [ ] Item 1',
        },
      })
      expect(getOfflineNotes()).toHaveLength(1)
      expect(getOfflineNotes()[0].title).toBe('Instant Note from Peer')

      // 2. Incoming note update (checkbox checked by peer)
      handleIncomingSyncEvent('sync', {
        entity: 'notes',
        action: 'updated',
        id: 'note-incoming-1',
        data: {
          id: 77,
          note_uid: 'note-incoming-1',
          title: 'Instant Note from Peer',
          content: '- [x] Item 1',
        },
      })
      expect(getOfflineNotes()[0].content).toBe('- [x] Item 1')

      // 3. Incoming note delete
      handleIncomingSyncEvent('sync', {
        entity: 'notes',
        action: 'deleted',
        id: 'note-incoming-1',
      })
      expect(getOfflineNotes()).toHaveLength(0)

      // 4. Incoming calendar create, update, delete
      handleIncomingSyncEvent('sync', {
        entity: 'calendar',
        action: 'created',
        id: 'cal-incoming-1',
        data: {
          id: 88,
          event_id: 'cal-incoming-1',
          title: 'Instant Meeting from Peer',
          start: '2026-09-02T10:00:00Z',
          end: '2026-09-02T11:00:00Z',
        },
      })
      expect(getOfflineCalendarEvents()).toHaveLength(1)
      expect(getOfflineCalendarEvents()[0].title).toBe('Instant Meeting from Peer')

      handleIncomingSyncEvent('sync', {
        entity: 'calendar',
        action: 'updated',
        id: 'cal-incoming-1',
        data: {
          id: 88,
          event_id: 'cal-incoming-1',
          title: 'Instant Meeting (Updated)',
        },
      })
      expect(getOfflineCalendarEvents()[0].title).toBe('Instant Meeting (Updated)')

      handleIncomingSyncEvent('sync', {
        entity: 'calendar',
        action: 'deleted',
        id: 'cal-incoming-1',
      })
      expect(getOfflineCalendarEvents()).toHaveLength(0)
    })

    it('handles tricky checklist lines with extra brackets and indentation correctly', async () => {
      vi.mocked(client.api).mockRejectedValue(new TypeError('Offline'))

      const note = {
        id: 1,
        note_uid: 'chk-1',
        title: 'Checklist',
        content: '  - [ ] Buy [1] pack of eggs\n\t- [ ] Prepare [ ] box',
        category: 'shopping',
        color: 'emerald',
        is_pinned: false,
        is_archived: false,
        note_type: 'personal',
        user_id: 1,
        created_at: '2026-09-02T10:00:00Z',
        updated_at: '2026-09-02T10:00:00Z',
      }
      setOfflineNotes([note])

      // Toggle first item (with leading spaces and [1] inside description)
      const res1 = await toggleCheckItemOffline(note, 0)
      expect(res1.updatedContent).toBe('  - [x] Buy [1] pack of eggs\n\t- [ ] Prepare [ ] box')

      // Toggle second item (with tab indent and [ ] inside description)
      const res2 = await toggleCheckItemOffline(res1.note, 1)
      expect(res2.updatedContent).toBe('  - [x] Buy [1] pack of eggs\n\t- [x] Prepare [ ] box')

      // Untoggle first item
      const res3 = await toggleCheckItemOffline(res2.note, 0)
      expect(res3.updatedContent).toBe('  - [ ] Buy [1] pack of eggs\n\t- [x] Prepare [ ] box')
    })

    it('loadCalendarEventsOfflineFirst correctly displays events with empty end timestamps', async () => {
      vi.mocked(client.api).mockRejectedValue(new TypeError('Offline'))

      setOfflineCalendarEvents([
        {
          id: 1,
          event_id: 'point-evt',
          title: 'Point-in-time Milestone',
          start: '2026-09-02T14:00:00Z',
          end: '', // empty end
          event_type: 'personal',
          color: 'primary',
          calendar: 'MSM Kalender',
          all_day: false,
          user_id: 1,
          can_edit: true,
        },
      ])

      const { events } = await loadCalendarEventsOfflineFirst(
        '2026-09-02T00:00:00Z',
        '2026-09-02T23:59:59Z'
      )
      expect(events).toHaveLength(1)
      expect(events[0].event_id).toBe('point-evt')
    })
  })
})
