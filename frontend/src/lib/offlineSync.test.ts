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
  grundbestandZuruecksetzen,
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
import { deriveUserDeviceMailboxId } from '@/services/e2eeCrypto'
import { useAuthStore } from '@/stores/authStore'

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

    it('liest Schlüsselumschläge unter dem angemeldeten Konto, nicht unter dem des Einwerfers', async () => {
      // Auf dem Mailbox-Weg steht in `recipient_id` nichts, und `sender_user_id`
      // ist, wer den Umschlag eingeworfen hat. Unter dessen Kennung zu lesen
      // hiesse, seine Mailbox nach einem Schlüssel zu durchsuchen, den er selbst
      // hineingelegt hat.
      useAuthStore.setState({ user: { id: 31, username: 'ich' } as any })
      try {
        clearNotesKeyCache()
        localStorage.removeItem('msm_e2ee_notes_key_31')
        localStorage.removeItem('msm_e2ee_notes_key_77')
        vi.mocked(client.api).mockResolvedValue([] as any)
        const eigene = await deriveUserDeviceMailboxId(31)
        const fremde = await deriveUserDeviceMailboxId(77)
        const abgerufen = () => vi.mocked(client.api).mock.calls.map(([pfad]) => String(pfad))

        handleIncomingSyncEvent('sync', {
          type: 'e2ee_blind_message',
          control_type: 'notes_key_sync',
          recipient_id: null,
          sender_user_id: 77,
        } as any)

        await vi.waitFor(() => expect(abgerufen().some((p) => p.includes(eigene))).toBe(true))
        expect(abgerufen().some((p) => p.includes(fremde))).toBe(false)
      } finally {
        useAuthStore.setState({ user: null })
      }
    })

    it('beantwortet eine gemeldete Anfrage aus der Mailbox, statt den Schlüssel an alle zu schicken', async () => {
      // Bis 09/2026 schickte jede solche Meldung den Schlüssel an jedes eigene
      // Gerät — auch an die, die ihn längst hatten. Gefragt hat ein Gerät, und
      // dessen Anfrage liegt in der Mailbox: die wird beantwortet, einmal.
      // Geprüft wird hier der Weg — die Meldung führt in die eigene Mailbox,
      // die Verteilung an alle hätte sie nie geöffnet. Dass dabei nichts
      // hinausgeht, beweist dieser Test nicht: der Messenger ist hier
      // gesperrt, und ohne Gerät sendet keiner der beiden Wege.
      useAuthStore.setState({ user: { id: 32, username: 'ich' } as any })
      try {
        clearNotesKeyCache()
        await setUserNotesKey(32, btoa(String.fromCharCode(...new Uint8Array(32).fill(3))))
        vi.mocked(client.api).mockResolvedValue([] as any)
        const eigene = await deriveUserDeviceMailboxId(32)
        const abgerufen = () => vi.mocked(client.api).mock.calls.map(([pfad]) => String(pfad))

        handleIncomingSyncEvent('sync', {
          type: 'e2ee_blind_message',
          control_type: 'notes_key_request',
          recipient_id: null,
          sender_user_id: 77,
        } as any)

        await vi.waitFor(() => expect(abgerufen().some((p) => p.includes(eigene))).toBe(true))
      } finally {
        localStorage.removeItem('msm_e2ee_notes_key_32')
        useAuthStore.setState({ user: null })
      }
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

  describe('Serientermine', () => {
    beforeEach(() => {
      grundbestandZuruecksetzen()
    })

    /** Antwortet auf den Grundbestand-Abruf und auf Bereichsabrufe getrennt. */
    function antworteMit(termine: any[], optionen: { bereichFaellt?: boolean } = {}) {
      vi.mocked(client.api).mockImplementation(async (pfad: string) => {
        if (pfad === '/calendar/events') return termine as any
        if (pfad.startsWith('/calendar/events?')) {
          if (optionen.bereichFaellt) throw new TypeError('NetworkError')
          // Der Server filtert nach Zeitraum — ein Geburtstag von 1995 fällt
          // aus einer Abfrage für 2026 heraus. Genau deshalb gibt es den
          // Grundbestand.
          return [] as any
        }
        return [] as any
      })
    }

    const geburtstag1995 = {
      id: 1,
      event_id: 'geb-1',
      title: 'Geburtstag Lisa',
      start: '1995-03-13T23:00:00.000Z',
      end: '1995-03-14T23:00:00.000Z',
      all_day: true,
      recurrence: '{"rrule":"FREQ=YEARLY"}',
      user_id: 1,
    }

    it('zeigt den Geburtstag von 1995 im Fenster von 2026', async () => {
      // Der Fall, der die ganze Übung nötig macht: der Serienkopf liegt
      // außerhalb jeder Bereichsabfrage, das Vorkommen liegt darin.
      antworteMit([geburtstag1995])

      const { events } = await loadCalendarEventsOfflineFirst(
        '2026-03-01T00:00:00Z',
        '2026-04-01T00:00:00Z',
        undefined,
        1,
        'Europe/Berlin',
      )

      expect(events).toHaveLength(1)
      expect(events[0].title).toBe('Geburtstag Lisa')
      expect(events[0].vorkommen).toBe('2026-03-14')
      expect(events[0].istSerie).toBe(true)
      // `event_id` ist bei allen Vorkommen dasselbe; nur der zusammengesetzte
      // Schlüssel taugt als React-Key.
      expect(events[0].schluessel).toBe('geb-1#2026-03-14')
    })

    it('holt den Grundbestand nur einmal je Sitzung', async () => {
      antworteMit([geburtstag1995])

      await loadCalendarEventsOfflineFirst('2026-03-01T00:00:00Z', '2026-04-01T00:00:00Z', undefined, 1, 'UTC')
      await loadCalendarEventsOfflineFirst('2026-04-01T00:00:00Z', '2026-05-01T00:00:00Z', undefined, 1, 'UTC')

      const grundabrufe = vi.mocked(client.api).mock.calls.filter((c) => c[0] === '/calendar/events')
      expect(grundabrufe).toHaveLength(1)
    })

    it('behält die Serien, wenn danach der Bereichsabruf scheitert', async () => {
      // Der Grundbestand hat seinen eigenen Versuch: zöge er den Bereichsabruf
      // mit — oder umgekehrt —, stünde bei einem einzigen Fehlschlag der ganze
      // Kalender auf dem Stand von vorher.
      antworteMit([geburtstag1995], { bereichFaellt: true })

      const { events, isOffline } = await loadCalendarEventsOfflineFirst(
        '2026-03-01T00:00:00Z',
        '2026-04-01T00:00:00Z',
        undefined,
        1,
        'Europe/Berlin',
      )

      expect(isOffline).toBe(true)
      expect(events).toHaveLength(1)
      expect(events[0].vorkommen).toBe('2026-03-14')
    })

    it('breitet eine Serie über mehrere Vorkommen im Fenster aus', async () => {
      antworteMit([
        {
          ...geburtstag1995,
          event_id: 'standup',
          title: 'Standup',
          start: '2026-01-05T08:00:00.000Z',
          end: '2026-01-05T08:30:00.000Z',
          all_day: false,
          recurrence: '{"rrule":"FREQ=WEEKLY;BYDAY=MO,TH"}',
        },
      ])

      const { events } = await loadCalendarEventsOfflineFirst(
        '2026-01-01T00:00:00Z',
        '2026-01-20T00:00:00Z',
        undefined,
        1,
        'Europe/Berlin',
      )
      expect(events.map((e) => e.vorkommen)).toEqual([
        '2026-01-05', '2026-01-08', '2026-01-12', '2026-01-15', '2026-01-19',
      ])
    })

    it('lässt ausgenommene Vorkommen weg und nimmt verschobene mit', async () => {
      antworteMit([
        {
          ...geburtstag1995,
          recurrence: JSON.stringify({
            rrule: 'FREQ=YEARLY',
            ausnahmen: ['2027-03-14'],
            abweichungen: {
              '2028-03-14': { start: '2028-03-15T09:00:00Z', ende: '2028-03-15T10:00:00Z', titel: 'Nachgefeiert' },
            },
          }),
        },
      ])

      const { events } = await loadCalendarEventsOfflineFirst(
        '2026-01-01T00:00:00Z',
        '2029-01-01T00:00:00Z',
        undefined,
        1,
        'Europe/Berlin',
      )
      expect(events.map((e) => e.vorkommen)).toEqual(['2026-03-14', '2028-03-14'])
      expect(events[1].title).toBe('Nachgefeiert')
    })

    it('speichert auch bei einem Einzeltermin ein Wiederholungsdokument', async () => {
      // Der Kern der Metadaten-Entscheidung: ein leeres Feld neben lauter
      // gefüllten wäre in der Datenbank selbst eine Auskunft darüber, welche
      // Termine Serien sind.
      vi.mocked(client.api).mockRejectedValue(new TypeError('NetworkError'))

      await saveCalendarEventOffline({
        title: 'Zahnarzt',
        start_time: '2026-09-02T10:00:00Z',
        end_time: '2026-09-02T11:00:00Z',
      })

      const auslauf = getOutbox().find((m) => m.entity === 'calendar')
      expect(auslauf?.payload.recurrence).toBeTruthy()
      expect(auslauf?.payload.recurrence.startsWith(CALENDAR_CIPHERTEXT_PREFIX)).toBe(true)
    })

    it('verschlüsselt die Regel, bevor sie den Rechner verlässt', async () => {
      vi.mocked(client.api).mockRejectedValue(new TypeError('NetworkError'))

      await saveCalendarEventOffline({
        title: 'Geburtstag',
        start_time: '2026-03-14T00:00:00Z',
        end_time: '2026-03-15T00:00:00Z',
        all_day: true,
        recurrence: '{"rrule":"FREQ=YEARLY"}',
      })

      const auslauf = getOutbox().find((m) => m.entity === 'calendar')
      expect(auslauf?.payload.recurrence.startsWith(CALENDAR_CIPHERTEXT_PREFIX)).toBe(true)
      expect(auslauf?.payload.recurrence).not.toContain('FREQ')
      expect(auslauf?.payload.recurrence).not.toContain('YEARLY')
    })
  })

  describe('Schlüssel unter der echten Kennung', () => {
    // Bis zum 22.09.2026 liess der Schreibpfad den `userId`-Parameter weg.
    // Jeder Schluessel landete unter der Kennung 1, der Lesepfad suchte unter
    // der echten — fuer jedes Konto ausser dem ersten blieb alles Chiffretext,
    // und eine Serie, deren Regel unlesbar ist, erscheint nur noch einmal.
    const KONTO = 18

    beforeEach(() => {
      clearNotesKeyCache()
      localStorage.clear()
      grundbestandZuruecksetzen()
      useAuthStore.setState({ user: { id: KONTO, username: 'pruefung' } as any })
    })

    afterEach(() => {
      useAuthStore.setState({ user: null })
      localStorage.clear()
      clearNotesKeyCache()
    })

    function antworteMit(termine: any[]) {
      vi.mocked(client.api).mockImplementation(async (pfad: string) => {
        if (pfad.startsWith('/calendar/events')) return termine as any
        return [] as any
      })
    }

    it('legt den Schluessel beim Speichern unter der echten Kennung ab', async () => {
      vi.mocked(client.api).mockResolvedValue({
        id: 1,
        event_id: 'neu-1',
        title: 'x',
        start: '2026-09-14T09:00:00.000Z',
        end: '2026-09-14T10:00:00.000Z',
      } as any)

      await saveCalendarEventOffline({
        title: 'Geburtstag Lisa',
        start_time: '2026-09-14 09:00',
        end_time: '2026-09-14 10:00',
        recurrence: '{"rrule":"FREQ=YEARLY"}',
      })

      expect(exportUserNotesKey(KONTO)).toBeTruthy()
      expect(exportUserNotesKey(1)).toBeNull()
    })

    it('oeffnet den Altbestand und uebernimmt den Schluessel — die Serie kommt zurueck', async () => {
      // Aufbau wie nach dem Schreibfehler: der Schluessel liegt unter 1,
      // angemeldet ist Konto 18, unter 18 liegt nichts.
      const alt = await getOrCreateUserNotesKey(1)
      const altRoh = exportUserNotesKey(1)
      const uid = 'geb-alt-1'
      const verTitel = await encryptCalendarField('Geburtstag Lisa', uid, 'title', alt, 1)
      const verRegel = await encryptCalendarField('{"rrule":"FREQ=YEARLY"}', uid, 'recurrence', alt, 1)
      clearNotesKeyCache()
      localStorage.removeItem('msm_e2ee_notes_key_18')

      antworteMit([
        {
          id: 1,
          event_id: uid,
          title: verTitel,
          start: '1995-03-13T23:00:00.000Z',
          end: '1995-03-14T23:00:00.000Z',
          all_day: true,
          recurrence: verRegel,
          user_id: KONTO,
        },
      ])

      const { events } = await loadCalendarEventsOfflineFirst(
        '2026-03-01T00:00:00Z',
        '2026-04-01T00:00:00Z',
        undefined,
        KONTO,
        'Europe/Berlin',
      )

      // Der Titel ist lesbar …
      expect(events).toHaveLength(1)
      expect(events[0].title).toBe('Geburtstag Lisa')
      // … und die Regel auch, sonst stuende hier kein Vorkommen von 2026.
      expect(events[0].istSerie).toBe(true)
      expect(events[0].vorkommen).toBe('2026-03-14')
      // Der Beleg ist erbracht, also wandert der Schluessel auf die echte Kennung.
      expect(exportUserNotesKey(KONTO)).toBe(altRoh)
    })

    it('uebernimmt nichts, was sich nicht oeffnen laesst', async () => {
      // Derselbe Aufbau, aber der Schluessel unter 1 gehoert jemand anderem:
      // er oeffnet keine Zeile dieses Kontos, also bleibt er liegen.
      const fremd = await getOrCreateUserNotesKey(77)
      const uid = 'fremd-1'
      const verTitel = await encryptCalendarField('Nicht fuer dich', uid, 'title', fremd, 77)
      clearNotesKeyCache()
      localStorage.clear()
      await getOrCreateUserNotesKey(1) // ein anderer, unbeteiligter Altbestand

      antworteMit([
        {
          id: 1,
          event_id: uid,
          title: verTitel,
          start: '2026-03-14T09:00:00.000Z',
          end: '2026-03-14T10:00:00.000Z',
          recurrence: '{"rrule":null}',
          user_id: KONTO,
        },
      ])

      const { events } = await loadCalendarEventsOfflineFirst(
        '2026-03-01T00:00:00Z',
        '2026-04-01T00:00:00Z',
        undefined,
        KONTO,
        'Europe/Berlin',
      )

      expect(events[0].title.startsWith(CALENDAR_CIPHERTEXT_PREFIX)).toBe(true)
      expect(exportUserNotesKey(KONTO)).toBeNull()
    })
  })

  describe('Team-Elemente (Klartext fuer DIS-Backend-Schutz)', () => {
    const KONTO = 2001

    beforeEach(() => {
      useAuthStore.setState({ user: { id: KONTO, username: 'testuser' } as any })
      vi.mocked(client.api).mockRejectedValue(new TypeError('Failed to fetch'))
    })

    it('speichert Team-Notizen im Klartext und persoenliche Notizen verschluesselt', async () => {
      // 1. Team-Notiz
      await saveNoteOffline({
        title: 'Team Roadmap',
        content: 'Alle arbeiten am Release',
        note_type: 'team',
        team_id: 42,
      })

      const outbox = getOutbox()
      const teamMutation = outbox.find((m) => (m.payload as any)?.note_type === 'team')
      expect(teamMutation).toBeDefined()
      expect((teamMutation!.payload as any).title).toBe('Team Roadmap')
      expect((teamMutation!.payload as any).content).toBe('Alle arbeiten am Release')
      expect((teamMutation!.payload as any).title.startsWith(NOTE_CIPHERTEXT_PREFIX)).toBe(false)

      // 2. Persoenliche Notiz
      await saveNoteOffline({
        title: 'Mein Geheimnis',
        content: 'Streng vertraulich',
        note_type: 'personal',
      })

      const personalMutation = getOutbox().find((m) => (m.payload as any)?.note_type === 'personal')
      expect(personalMutation).toBeDefined()
      expect((personalMutation!.payload as any).title.startsWith(NOTE_CIPHERTEXT_PREFIX)).toBe(true)
      expect((personalMutation!.payload as any).content.startsWith(NOTE_CIPHERTEXT_PREFIX)).toBe(true)
    })

    it('speichert Team-Termine im Klartext und persoenliche Termine verschluesselt', async () => {
      // 1. Team-Termin
      await saveCalendarEventOffline({
        title: 'Sprint Planning',
        description: 'Planung fuer Q3',
        location: 'Raum 101',
        start_time: '2026-06-01T10:00:00.000Z',
        end_time: '2026-06-01T11:00:00.000Z',
        event_type: 'team',
        team_id: 42,
        recurrence: '{"rrule":"FREQ=WEEKLY"}',
      })

      const outbox = getOutbox()
      const teamMutation = outbox.find((m) => (m.payload as any)?.event_type === 'team')
      expect(teamMutation).toBeDefined()
      expect((teamMutation!.payload as any).title).toBe('Sprint Planning')
      expect((teamMutation!.payload as any).description).toBe('Planung fuer Q3')
      expect((teamMutation!.payload as any).location).toBe('Raum 101')
      expect((teamMutation!.payload as any).recurrence).toBe('{"rrule":"FREQ=WEEKLY"}')
      expect((teamMutation!.payload as any).title.startsWith(CALENDAR_CIPHERTEXT_PREFIX)).toBe(false)

      // 2. Persoenlicher Termin
      await saveCalendarEventOffline({
        title: 'Zahnarzt',
        description: 'Kontrolle',
        location: 'Praxis',
        start_time: '2026-06-02T10:00:00.000Z',
        end_time: '2026-06-02T11:00:00.000Z',
        event_type: 'personal',
      })

      const personalMutation = getOutbox().find((m) => (m.payload as any)?.event_type === 'personal')
      expect(personalMutation).toBeDefined()
      expect((personalMutation!.payload as any).title.startsWith(CALENDAR_CIPHERTEXT_PREFIX)).toBe(true)
    })
  })
})

