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
  enqueueMessageMutation,
  startLiveSync,
  redecryptPendingOfflineNotesAndCalendar,
  loadNotesOfflineFirst,
} from './offlineSync'
import {
  NOTE_CIPHERTEXT_PREFIX,
  CALENDAR_CIPHERTEXT_PREFIX,
  decryptNoteTitle,
  decryptCalendarField,
  encryptNoteTitle,
  encryptNoteContent,
  encryptCalendarField,
  getOrCreateUserNotesKey,
  setUserNotesKey,
  exportUserNotesKey,
  clearNotesKeyCache,
} from '@/services/notesCalendarCrypto'
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
      expect(getOutbox()[0].payload.title.startsWith(NOTE_CIPHERTEXT_PREFIX)).toBe(true)
      expect(getOutbox()[1].payload.title.startsWith(NOTE_CIPHERTEXT_PREFIX)).toBe(true)
      expect(await decryptNoteTitle(getOutbox()[0].payload.title, getOutbox()[0].entityId)).toBe('Notiz 1')
      expect(await decryptNoteTitle(getOutbox()[1].payload.title, getOutbox()[1].entityId)).toBe('Notiz 2')

      // Now network is back online
      vi.mocked(client.api).mockImplementation(async (path: string, options?: any) => {
        if (path === '/notes' && options?.method === 'POST') {
          const body = JSON.parse(options.body)
          expect(body.title.startsWith(NOTE_CIPHERTEXT_PREFIX)).toBe(true)
          const decTitle = await decryptNoteTitle(body.title, body.note_uid || '')
          return {
            id: 101,
            note_uid: 'server-uid-' + decTitle,
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

      // Check that local cache was updated with server canonical note_uids while maintaining plaintext titles
      const stored = getOfflineNotes()
      expect(stored[1].note_uid).toBe('server-uid-Notiz 1')
      expect(stored[0].note_uid).toBe('server-uid-Notiz 2')
      expect(stored[1].title).toBe('Notiz 1')
      expect(stored[0].title).toBe('Notiz 2')

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

    it('preserves and replays mutations enqueued concurrently while replayOutbox is in flight without data loss', async () => {
      // 1. Initial mutation in outbox
      setOutbox([
        {
          id: 'mut-first',
          entity: 'note',
          action: 'create',
          entityId: 'note-first',
          payload: { title: 'First Note' },
          timestamp: new Date().toISOString(),
          retryCount: 0,
        },
      ])

      // 2. Mock API: When 'mut-first' is being handled, another mutation is concurrently added
      vi.mocked(client.api).mockImplementation(async (path: string, options?: any) => {
        if (path === '/notes') {
          // Concurrently enqueue a message while the first HTTP request is in-flight
          enqueueMessageMutation({
            blind_mailbox_id: 'mailbox-concurrent-1',
            ciphertext_envelope: 'cipher-concurrent-1',
            client_uuid: 'uuid-concurrent-1',
          })
          return {
            id: 101,
            note_uid: 'server-first',
            title: 'First Note',
          }
        }
        if (path === '/social/e2ee/relay') {
          return {
            id: 202,
            blind_mailbox_id: 'mailbox-concurrent-1',
          }
        }
        return {}
      })

      const res = await replayOutbox()
      // Both the first mutation and the concurrent second mutation must be processed
      expect(res.processed).toBe(2)
      expect(res.failed).toBe(0)
      expect(res.remaining).toBe(0)
      expect(getOutbox()).toHaveLength(0)
      expect(client.api).toHaveBeenCalledWith('/social/e2ee/relay', expect.anything())
    })

    it('replays offline message mutations and dispatches msm:message-confirmed', async () => {
      const confirmedListener = vi.fn()
      window.addEventListener('msm:message-confirmed', confirmedListener)

      enqueueMessageMutation({
        blind_mailbox_id: 'test-mailbox-msg',
        ciphertext_envelope: 'cipher-offline-envelope',
        recipient_id: 42,
        client_uuid: 'uuid-offline-msg-42',
      })

      expect(getOutbox()).toHaveLength(1)
      expect(getOutbox()[0].entity).toBe('message')

      vi.mocked(client.api).mockResolvedValueOnce({
        id: 777,
        blind_mailbox_id: 'test-mailbox-msg',
      })

      const res = await replayOutbox()
      expect(res.processed).toBe(1)
      expect(res.failed).toBe(0)
      expect(getOutbox()).toHaveLength(0)
      expect(confirmedListener).toHaveBeenCalledWith(
        expect.objectContaining({
          detail: expect.objectContaining({
            client_uuid: 'uuid-offline-msg-42',
            envelope_id: 777,
            blind_mailbox_id: 'test-mailbox-msg',
          }),
        })
      )

      window.removeEventListener('msm:message-confirmed', confirmedListener)
    })

    // Ein Ratenlimit sagt „später", nicht „geht nicht". Solange niemand die
    // Warteschlange nachfasste, war das harmlos; seit der Chat das im
    // Fünf-Sekunden-Takt tut, wären fünf gezählte Versuche in einer halben
    // Minute aufgebraucht — und die Nachricht des Benutzers stillschweigend
    // verworfen. Dasselbe gilt für einen Server, der gerade nicht kann.
    it('verbraucht bei Ratenlimit und Serverfehler keinen Versuch und wirft nichts weg', async () => {
      for (const status of [429, 503]) {
        setOutbox([])
        enqueueMessageMutation({
          blind_mailbox_id: 'mailbox-drossel',
          ciphertext_envelope: 'cipher-drossel',
          recipient_id: 7,
          client_uuid: 'uuid-drossel-' + status,
        })

        vi.mocked(client.api).mockRejectedValue(Object.assign(new Error('abgewiesen'), { status }))

        for (let runde = 0; runde < 8; runde++) {
          const res = await replayOutbox()
          expect(res.failed).toBe(0)
        }

        const uebrig = getOutbox()
        expect(uebrig).toHaveLength(1)
        expect(uebrig[0].retryCount).toBe(0)
      }

      // Sobald der Server wieder kann, geht derselbe Auftrag raus.
      vi.mocked(client.api).mockResolvedValueOnce({ id: 991, blind_mailbox_id: 'mailbox-drossel' })
      const res = await replayOutbox()
      expect(res.processed).toBe(1)
      expect(getOutbox()).toHaveLength(0)
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

  describe('R7. Offline Message Outbox & Replay Confirmation', () => {
    it('enqueues offline message mutations with deduplication via client_uuid', () => {
      const mut1 = enqueueMessageMutation({
        blind_mailbox_id: 'mailbox-abc',
        ciphertext_envelope: 'cipher-1',
        recipient_id: 42,
        client_uuid: 'uuid-msg-101',
      })

      expect(mut1.id).toBe('uuid-msg-101')
      expect(mut1.entity).toBe('message')
      expect(mut1.action).toBe('relay')
      expect(getOutbox()).toHaveLength(1)

      // Duplicate enqueue with same client_uuid should not duplicate
      const mut2 = enqueueMessageMutation({
        blind_mailbox_id: 'mailbox-abc',
        ciphertext_envelope: 'cipher-1',
        recipient_id: 42,
        client_uuid: 'uuid-msg-101',
      })

      expect(getOutbox()).toHaveLength(1)
      expect(mut2.id).toBe('uuid-msg-101')
    })

    it('bleibt nicht stehen, nur weil das System offline meldet', async () => {
      // Am laufenden System gefunden. `navigator.onLine` zählte mit in die
      // Erkennung eines Netzwerkfehlers. Meldete das Betriebssystem fälschlich
      // „offline" — unter Windows reicht dafür ein virtueller Netzadapter —,
      // wurde jeder beliebige Fehler zu einem Netzwerkfehler, die Schleife
      // brach ab, und der Auftrag blieb vorn in der Warteschlange liegen. Im
      // Messenger waren das die Nachrichten mit der Uhr, die nie wieder
      // losgingen: kein Versand, kein Aufgeben, kein Weiterkommen für alles
      // dahinter.
      vi.stubGlobal('navigator', { ...window.navigator, onLine: false })

      enqueueMessageMutation({
        blind_mailbox_id: 'mailbox-abc',
        ciphertext_envelope: 'cipher-kaputt',
        recipient_id: 42,
        client_uuid: 'uuid-haengt',
      })
      enqueueMessageMutation({
        blind_mailbox_id: 'mailbox-abc',
        ciphertext_envelope: 'cipher-danach',
        recipient_id: 42,
        client_uuid: 'uuid-danach',
      })

      // Kein Netzwerkfehler, sondern eine Absage des Servers.
      vi.mocked(client.api).mockRejectedValue(
        Object.assign(new Error('Unprocessable Entity'), { status: 422 }),
      )

      await replayOutbox()

      // Der Versuch hat stattgefunden und zählt: nach fünf Fehlschlägen fliegt
      // der Auftrag raus, statt die Warteschlange für immer zu verstopfen.
      expect(client.api).toHaveBeenCalled()
      expect(getOutbox()[0]?.retryCount).toBe(1)
    })

    it('replays queued message mutations in FIFO order and dispatches confirmation events', async () => {
      enqueueMessageMutation({
        blind_mailbox_id: 'mailbox-abc',
        ciphertext_envelope: 'cipher-1',
        recipient_id: 42,
        client_uuid: 'uuid-msg-1',
      })
      enqueueMessageMutation({
        blind_mailbox_id: 'mailbox-abc',
        ciphertext_envelope: 'cipher-2',
        recipient_id: 42,
        client_uuid: 'uuid-msg-2',
      })

      expect(getOutbox()).toHaveLength(2)

      const confirmedEvents: any[] = []
      const messagesUpdatedEvents: any[] = []

      const onConfirmed = (e: any) => confirmedEvents.push(e.detail)
      const onUpdated = () => messagesUpdatedEvents.push(true)

      window.addEventListener('msm:message-confirmed', onConfirmed)
      window.addEventListener('msm:messages-updated', onUpdated)

      vi.mocked(client.api)
        .mockResolvedValueOnce({ id: 1001, blind_mailbox_id: 'mailbox-abc' })
        .mockResolvedValueOnce({ id: 1002, blind_mailbox_id: 'mailbox-abc' })

      const result = await replayOutbox()

      expect(result.processed).toBe(2)
      expect(result.remaining).toBe(0)
      expect(getOutbox()).toHaveLength(0)

      expect(confirmedEvents).toHaveLength(2)
      expect(confirmedEvents[0]).toEqual({
        client_uuid: 'uuid-msg-1',
        envelope_id: 1001,
        blind_mailbox_id: 'mailbox-abc',
      })
      expect(confirmedEvents[1]).toEqual({
        client_uuid: 'uuid-msg-2',
        envelope_id: 1002,
        blind_mailbox_id: 'mailbox-abc',
      })

      expect(messagesUpdatedEvents.length).toBeGreaterThan(0)

      window.removeEventListener('msm:message-confirmed', onConfirmed)
      window.removeEventListener('msm:messages-updated', onUpdated)
    })
  })

  describe('R8. LiveSync Lifecycle, Flapping Protection & Timer Cleanups', () => {
    beforeEach(() => {
      vi.useFakeTimers()
    })

    afterEach(() => {
      vi.useRealTimers()
    })

    it('stops cleanly and does not trigger fallback polling or reconnect timers after cancellation', async () => {
      vi.mocked(client.apiStream).mockRejectedValue(new Error('Network offline'))

      const stop = startLiveSync()

      // Disconnect occurs immediately
      await vi.advanceTimersByTimeAsync(10)

      // Stop sync
      stop()

      // Clear any call counts
      vi.mocked(client.apiStream).mockClear()

      // Fast forward 30 seconds into the future
      await vi.advanceTimersByTimeAsync(30_000)

      // No additional reconnect calls or fallback polling calls should have fired
      expect(client.apiStream).not.toHaveBeenCalled()
    })

    it('reconnects with exponential backoff and jitter under repeated failures', async () => {
      vi.mocked(client.apiStream).mockRejectedValue(new Error('Connection dropped'))

      const stop = startLiveSync()

      // Initial connect call
      expect(client.apiStream).toHaveBeenCalledTimes(1)

      // 1st backoff base: 1000ms (jittered 850-1000ms)
      await vi.advanceTimersByTimeAsync(1100)
      expect(client.apiStream).toHaveBeenCalledTimes(2)

      // 2nd backoff base: 2000ms (jittered 1700-2000ms)
      await vi.advanceTimersByTimeAsync(2100)
      expect(client.apiStream).toHaveBeenCalledTimes(3)

      stop()
    })
  })

  describe('R7. Automatic Multi-Device E2EE Key Synchronization & Re-decryption', () => {
    it('re-decrypts pending offline notes and calendar items and dispatches update events', async () => {
      const userId = 1
      clearNotesKeyCache()
      const key = await getOrCreateUserNotesKey(userId)

      const noteUid = 'offline-pending-note-101'
      const plainNoteTitle = 'Geheimer Offline-Einkauf'
      const plainNoteContent = 'Sichere Liste vor Synchronisation'
      const encNoteTitle = await encryptNoteTitle(plainNoteTitle, noteUid, key, userId)
      const encNoteContent = await encryptNoteContent(plainNoteContent, noteUid, key, userId)

      setOfflineNotes([
        {
          id: 101,
          note_uid: noteUid,
          title: encNoteTitle,
          content: encNoteContent,
          category: 'personal',
          color: 'primary',
          is_pinned: false,
          is_archived: false,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          user_id: userId,
        },
      ])

      const eventUid = 'offline-pending-event-202'
      const plainCalTitle = 'Geheimes Meeting'
      const plainCalDesc = 'Sicherer Kalendertermin'
      const plainCalLoc = 'Zimmer 42'
      const encCalTitle = await encryptCalendarField(plainCalTitle, eventUid, 'title', key, userId)
      const encCalDesc = await encryptCalendarField(plainCalDesc, eventUid, 'description', key, userId)
      const encCalLoc = await encryptCalendarField(plainCalLoc, eventUid, 'location', key, userId)

      setOfflineCalendarEvents([
        {
          id: 202,
          event_id: eventUid,
          title: encCalTitle,
          start: new Date().toISOString(),
          end: new Date().toISOString(),
          description: encCalDesc,
          location: encCalLoc,
          all_day: false,
          color: 'primary',
          calendar: 'MSM Kalender',
          event_type: 'personal',
          user_id: userId,
          can_edit: true,
        },
      ])

      const notesUpdatedSpy = vi.fn()
      const calUpdatedSpy = vi.fn()
      window.addEventListener('msm:notes-updated', notesUpdatedSpy)
      window.addEventListener('msm:calendar-updated', calUpdatedSpy)

      const result = await redecryptPendingOfflineNotesAndCalendar(userId)
      expect(result.decryptedNotesCount).toBe(1)
      expect(result.decryptedEventsCount).toBe(1)

      const decryptedNotes = getOfflineNotes()
      expect(decryptedNotes[0].title).toBe(plainNoteTitle)
      expect(decryptedNotes[0].content).toBe(plainNoteContent)
      expect(decryptedNotes[0].title.startsWith(NOTE_CIPHERTEXT_PREFIX)).toBe(false)

      const decryptedEvents = getOfflineCalendarEvents()
      expect(decryptedEvents[0].title).toBe(plainCalTitle)
      expect(decryptedEvents[0].description).toBe(plainCalDesc)
      expect(decryptedEvents[0].location).toBe(plainCalLoc)
      expect(decryptedEvents[0].title.startsWith(CALENDAR_CIPHERTEXT_PREFIX)).toBe(false)

      expect(notesUpdatedSpy).toHaveBeenCalled()
      expect(calUpdatedSpy).toHaveBeenCalled()

      window.removeEventListener('msm:notes-updated', notesUpdatedSpy)
      window.removeEventListener('msm:calendar-updated', calUpdatedSpy)
    })

    it('automatically invokes re-decryption when msm:notes-key-updated is fired', async () => {
      const userId = 1
      clearNotesKeyCache()
      const key = await getOrCreateUserNotesKey(userId)

      const noteUid = 'offline-event-note-303'
      const plainTitle = 'Automatisch nach-entschlüsselt'
      const encTitle = await encryptNoteTitle(plainTitle, noteUid, key, userId)

      setOfflineNotes([
        {
          id: 303,
          note_uid: noteUid,
          title: encTitle,
          content: '',
          category: 'personal',
          color: 'primary',
          is_pinned: false,
          is_archived: false,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          user_id: userId,
        },
      ])

      window.dispatchEvent(
        new CustomEvent('msm:notes-key-updated', { detail: { userId } })
      )

      // Wait a tick for async event handler
      await new Promise((resolve) => setTimeout(resolve, 50))

      const current = getOfflineNotes()
      expect(current[0].title).toBe(plainTitle)
    })

    it('handles incoming notes_key_sync and notes_key_request SSE events without crash', () => {
      expect(() => {
        handleIncomingSyncEvent('sync', {
          type: 'e2ee_blind_message',
          control_type: 'notes_key_sync',
          recipient_id: 1,
        } as any)
      }).not.toThrow()

      expect(() => {
        handleIncomingSyncEvent('sync', {
          type: 'e2ee_blind_message',
          control_type: 'notes_key_request',
          sender_user_id: 1,
        } as any)
      }).not.toThrow()
    })

    it('preserves encrypted notes from server when key is absent and re-decrypts on key sync', async () => {
      const userId = 205
      // 1. Primärgerät verschlüsselt Note mit Schlüssel
      const keyDevA = await getOrCreateUserNotesKey(userId)
      const rawKeyDevA = exportUserNotesKey(userId)!
      const noteUid = 'multi-dev-note-888'
      const plainTitle = 'Vom Primärgerät erstellte Notiz'
      const encTitle = await encryptNoteTitle(plainTitle, noteUid, keyDevA, userId)

      // 2. Sekundärgerät hat NOCH KEINEN Schlüssel (frisch gekoppelt oder vor Update)
      clearNotesKeyCache()
      if (typeof localStorage !== 'undefined') {
        localStorage.removeItem(`msm_e2ee_notes_key_${userId}`)
      }

      // 3. Sekundärgerät ruft Notizen vom Server ab (Server liefert verschlüsselte Note)
      vi.mocked(client.api).mockImplementation(async (url: string) => {
        if (typeof url === 'string' && url.includes('/notes')) {
          return [
            {
              id: 888,
              note_uid: noteUid,
              title: encTitle,
              content: '',
              category: 'work',
              color: 'default',
              is_pinned: false,
              is_archived: false,
              created_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
              user_id: userId,
            },
          ]
        }
        return []
      })

      const { notes, isOffline } = await loadNotesOfflineFirst({ userId })
      expect(isOffline).toBe(false)
      // Note muss im Offline-Speicher mit Ciphertext vorliegen, NICHT verworfen worden sein
      expect(notes.length).toBe(1)
      expect(notes[0].title).toBe(encTitle)
      expect(notes[0].title.startsWith(NOTE_CIPHERTEXT_PREFIX)).toBe(true)

      // 4. Jetzt trifft der Schlüssel vom Primärgerät ein (z. B. via SSE oder Mailbox)
      await setUserNotesKey(userId, rawKeyDevA)

      // Kurz warten auf asynchrone msm:notes-key-updated Ereignisverarbeitung
      await new Promise((resolve) => setTimeout(resolve, 60))

      // 5. Die Notiz ist nun im Offline-Cache nahtlos entschlüsselt!
      const afterSyncNotes = getOfflineNotes()
      const decryptedNote = afterSyncNotes.find((n) => n.note_uid === noteUid)
      expect(decryptedNote).toBeDefined()
      expect(decryptedNote?.title).toBe(plainTitle)
      expect(decryptedNote?.title.startsWith(NOTE_CIPHERTEXT_PREFIX)).toBe(false)
    })
  })
})
