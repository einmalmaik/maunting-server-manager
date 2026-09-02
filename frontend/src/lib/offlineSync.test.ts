import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  getOfflineNotes,
  setOfflineNotes,
  getOfflineCalendarEvents,
  setOfflineCalendarEvents,
  getOutbox,
  setOutbox,
  clearMemoryStoreForTesting,
  loadNotesOfflineFirst,
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
} from './offlineSync'
import * as client from '@/api/client'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

describe('Offline Storage & Sync Engine', () => {
  beforeEach(() => {
    clearMemoryStoreForTesting()
    vi.mocked(client.api).mockReset()
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

  describe('R2. Outbox Mutation Queue & Conflict Resolution (LWW)', () => {
    it('persists outbox mutations and replays them in chronological order when reconnected', async () => {
      // Setup offline creations
      vi.mocked(client.api).mockRejectedValue(new TypeError('Failed to fetch'))

      const { note: note1 } = await saveNoteOffline({ title: 'Notiz 1' })
      const { note: note2 } = await saveNoteOffline({ title: 'Notiz 2' })

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

      const result = await replayOutbox()
      expect(result.processed).toBe(2)
      expect(result.remaining).toBe(0)
      expect(getOutbox()).toHaveLength(0)

      // Check that local cache was updated with server canonical note_uids
      const stored = getOfflineNotes()
      expect(stored[1].note_uid).toBe('server-uid-Notiz 1')
      expect(stored[0].note_uid).toBe('server-uid-Notiz 2')
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
  })
})
