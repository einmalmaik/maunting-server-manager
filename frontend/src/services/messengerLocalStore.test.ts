import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  loadLocalMessages,
  saveLocalMessages,
  updateMessageInLocalStore,
  getLocalMailboxLastSyncedId,
  clearLocalMessengerStore,
  parseMessageTimestamp,
  sortMessagesChronologically,
  isOptimisticMessage,
  mischeVerlauf,
  sichereDauerhafteAblage,
  type LocalStoredMessage,
} from './messengerLocalStore'

function installMockIndexedDb() {
  const stores = new Map<string, Map<string, any>>()

  const makeObjectStore = (storeName: string) => {
    let map = stores.get(storeName)
    if (!map) {
      map = new Map()
      stores.set(storeName, map)
    }

    return {
      put: (item: any) => {
        let key: string
        if (storeName === 'messages') {
          key = `${item.blindMailboxId}:${item.id}`
        } else {
          key = String(item.blindMailboxId)
        }
        map!.set(key, item)
        const req: any = { onsuccess: null, onerror: null, result: key }
        queueMicrotask(() => req.onsuccess?.())
        return req
      },
      get: (keyOrArray: any) => {
        let lookupKey: string
        if (Array.isArray(keyOrArray)) {
          lookupKey = `${keyOrArray[0]}:${keyOrArray[1]}`
        } else {
          lookupKey = String(keyOrArray)
        }
        const result = map!.get(lookupKey) || null
        const req: any = { onsuccess: null, onerror: null, result }
        queueMicrotask(() => req.onsuccess?.())
        return req
      },
      delete: (keyOrArray: any) => {
        let lookupKey: string
        if (Array.isArray(keyOrArray)) {
          lookupKey = `${keyOrArray[0]}:${keyOrArray[1]}`
        } else {
          lookupKey = String(keyOrArray)
        }
        map!.delete(lookupKey)
        const req: any = { onsuccess: null, onerror: null }
        queueMicrotask(() => req.onsuccess?.())
        return req
      },
      clear: () => {
        map!.clear()
        const req: any = { onsuccess: null, onerror: null }
        queueMicrotask(() => req.onsuccess?.())
        return req
      },
      index: (indexName: string) => ({
        getAll: (range?: any) => {
          const targetMid = range?.lower || range
          const allItems = Array.from(map!.values())
          const matched = allItems.filter((i) => !targetMid || i.blindMailboxId === targetMid)
          const req: any = { onsuccess: null, onerror: null, result: matched }
          queueMicrotask(() => req.onsuccess?.())
          return req
        },
        get: (val: any) => {
          const allItems = Array.from(map!.values())
          const found = allItems.find((i) => i.clientUuid === val)
          const req: any = { onsuccess: null, onerror: null, result: found || null }
          queueMicrotask(() => req.onsuccess?.())
          return req
        },
      }),
    }
  }

  const db: any = {
    objectStoreNames: {
      contains: (n: string) => stores.has(n),
    },
    createObjectStore: (n: string) => {
      stores.set(n, new Map())
      return {
        createIndex: () => {},
      }
    },
    transaction: (_storeNames: any) => {
      const tx: any = {
        objectStore: (n: string) => makeObjectStore(n),
        oncomplete: null,
        onerror: null,
      }
      queueMicrotask(() => tx.oncomplete?.())
      return tx
    },
  }

  ;(globalThis as any).IDBKeyRange = {
    only: (v: any) => v,
  }

  ;(globalThis as any).indexedDB = {
    open: () => {
      const req: any = { onsuccess: null, onerror: null, onupgradeneeded: null, result: db }
      queueMicrotask(() => {
        req.onupgradeneeded?.()
        req.onsuccess?.()
      })
      return req
    },
  }

  return { stores }
}

describe('messengerLocalStore (IndexedDB Chat Persistence & F5 Hydration)', () => {
  beforeEach(() => {
    installMockIndexedDb()
  })

  it('speichert und lädt Nachrichten für eine blindMailboxId chronologisch sortiert', async () => {
    const mid = 'box-alice-bob-123'
    const testMessages: LocalStoredMessage[] = [
      {
        blindMailboxId: mid,
        id: 102,
        senderId: 1,
        text: 'Zweite Nachricht',
        createdAt: '2026-09-16T12:05:00.000Z',
        isSelf: true,
        status: 'sent',
      },
      {
        blindMailboxId: mid,
        id: 101,
        senderId: 2,
        text: 'Erste Nachricht',
        createdAt: '2026-09-16T12:00:00.000Z',
        isSelf: false,
      },
    ]

    await saveLocalMessages(mid, testMessages)
    const loaded = await loadLocalMessages(mid)

    expect(loaded).toHaveLength(2)
    // Chronologische Sortierung: id 101 (12:00) vor id 102 (12:05)
    expect(loaded[0].id).toBe(101)
    expect(loaded[0].text).toBe('Erste Nachricht')
    expect(loaded[1].id).toBe(102)
    expect(loaded[1].text).toBe('Zweite Nachricht')
    expect(loaded[1].status).toBe('sent')
  })

  it('übersteht simulierten F5-Reload und stellt Verlauf ohne Keys neu einzugeben wieder her', async () => {
    const mid = 'box-offline-recovery-999'
    const history: LocalStoredMessage[] = [
      {
        blindMailboxId: mid,
        id: 400,
        senderId: 10,
        text: 'Wichtige Besprechungsnotiz',
        createdAt: '2026-09-16T08:00:00.000Z',
        isSelf: false,
        noteAttachment: { title: 'Agenda', content: '1. Architektur 2. Deployment' },
      },
      {
        blindMailboxId: mid,
        id: 401,
        senderId: 1,
        text: 'Bestätigt!',
        createdAt: '2026-09-16T08:01:00.000Z',
        isSelf: true,
        isDelivered: true,
        isRead: true,
        status: 'read',
      },
    ]

    // 1. Nachrichten im IndexedDB-Store ablegen
    await saveLocalMessages(mid, history)

    // 2. Simulierter F5 / Tab-Reload: Neuer Zugriff auf die DB
    const reloaded = await loadLocalMessages(mid)
    expect(reloaded).toHaveLength(2)
    expect(reloaded[0].text).toBe('Wichtige Besprechungsnotiz')
    expect(reloaded[0].noteAttachment?.title).toBe('Agenda')
    expect(reloaded[1].text).toBe('Bestätigt!')
    expect(reloaded[1].isRead).toBe(true)
    expect(reloaded[1].status).toBe('read')

    // 3. Mailbox-Meta verifiziert
    const lastId = await getLocalMailboxLastSyncedId(mid)
    expect(lastId).toBe(401)
  })

  it('aktualisiert optimistische Nachrichten nach Server-Quittung (Upgrade ID & Status)', async () => {
    const mid = 'box-optimistic-sync'
    const clientUuid = 'msg-opt-test-uuid-42'
    const optimisticTimestampId = 1773680000000

    const initial: LocalStoredMessage[] = [
      {
        blindMailboxId: mid,
        id: optimisticTimestampId,
        clientUuid,
        senderId: 1,
        text: 'Nachricht aus der Warteschlange',
        createdAt: new Date().toISOString(),
        isSelf: true,
        status: 'queued',
      },
    ]

    await saveLocalMessages(mid, initial)
    const beforeAck = await loadLocalMessages(mid)
    expect(beforeAck[0].status).toBe('queued')
    expect(beforeAck[0].id).toBe(optimisticTimestampId)

    // Server-Quittung trifft ein: Envelope-ID 555 erhalten
    await updateMessageInLocalStore(mid, clientUuid, {
      id: 555,
      status: 'sent',
    })

    const afterAck = await loadLocalMessages(mid)
    expect(afterAck).toHaveLength(1)
    expect(afterAck[0].id).toBe(555)
    expect(afterAck[0].status).toBe('sent')
  })

  it('löscht den lokalen Store vollständig bei clearLocalMessengerStore', async () => {
    const mid = 'box-to-clear'
    await saveLocalMessages(mid, [
      {
        blindMailboxId: mid,
        id: 1,
        senderId: 1,
        text: 'Lösch mich',
        createdAt: new Date().toISOString(),
        isSelf: true,
      },
    ])

    await clearLocalMessengerStore()
    const result = await loadLocalMessages(mid)
    expect(result).toHaveLength(0)
    const lastId = await getLocalMailboxLastSyncedId(mid)
    expect(lastId).toBe(0)
  })

  it('parseMessageTimestamp normalisiert Zeitzonen und Datumsformate robust nach UTC', () => {
    // Ohne Z (wird defensiv als UTC gewertet)
    const t1 = parseMessageTimestamp('2026-09-16 18:00:00')
    const t2 = parseMessageTimestamp('2026-09-16T18:00:00Z')
    expect(t1).toBe(t2)

    // Mit Offset
    const t3 = parseMessageTimestamp('2026-09-16T20:00:00+02:00')
    expect(t3).toBe(t2)

    // Leere Eingabe
    expect(parseMessageTimestamp('')).toBe(0)
    expect(parseMessageTimestamp(undefined)).toBe(0)
  })

  it('sortMessagesChronologically sortiert Antwort von User B strikt nach User A auch bei minimalen Zeitunterschieden', () => {
    const msgA = {
      id: 10,
      clientUuid: 'uuid-a',
      createdAt: '2026-09-16T18:00:00.500Z',
      text: 'Nachricht von User A',
      status: 'sent' as const,
    }
    const msgB = {
      id: 11,
      clientUuid: 'uuid-b',
      createdAt: '2026-09-16T18:00:00.600Z',
      text: 'Antwort von User B',
      status: 'sent' as const,
    }
    const optC = {
      id: 1773680000000,
      clientUuid: 'uuid-c',
      createdAt: '2026-09-16T18:00:01.000Z',
      text: 'Optimistische Nachricht',
      status: 'queued' as const,
    }

    // Durcheinander übergeben
    const sorted = sortMessagesChronologically([optC, msgB, msgA])
    expect(sorted.map((m) => m.id)).toEqual([10, 11, 1773680000000])
  })

  it('verhindert das Überschreiben von lastSyncedEnvelopeId durch temporäre Date.now() IDs', async () => {
    const mid = 'box-opt-id-guard'
    await saveLocalMessages(mid, [
      {
        blindMailboxId: mid,
        id: 105,
        senderId: 1,
        text: 'Bestätigt',
        createdAt: new Date().toISOString(),
        isSelf: false,
      },
      {
        blindMailboxId: mid,
        id: Date.now(), // z. B. 1773680000000
        clientUuid: 'opt-temp',
        senderId: 2,
        text: 'In Warteschlange',
        createdAt: new Date().toISOString(),
        isSelf: true,
        status: 'queued',
      },
    ])

    const lastId = await getLocalMailboxLastSyncedId(mid)
    expect(lastId).toBe(105)
  })

  it('dedupliziert optimistische Nachrichten beim Laden sobald die Server-Bestätigung existiert', async () => {
    const mid = 'box-dedup'
    const clientUuid = 'shared-uuid-123'
    await saveLocalMessages(mid, [
      {
        blindMailboxId: mid,
        id: 1773680000000,
        clientUuid,
        senderId: 1,
        text: 'Hallo Welt',
        createdAt: '2026-09-16T18:00:00.000Z',
        isSelf: true,
        status: 'queued',
      },
      {
        blindMailboxId: mid,
        id: 88,
        clientUuid,
        senderId: 1,
        text: 'Hallo Welt',
        createdAt: '2026-09-16T18:00:00.000Z',
        isSelf: true,
        status: 'sent',
      },
    ])

    const loaded = await loadLocalMessages(mid)
    expect(loaded).toHaveLength(1)
    expect(loaded[0].id).toBe(88)
    expect(loaded[0].status).toBe('sent')
  })
})

describe('mischeVerlauf', () => {
  const nachricht = (
    ueber: Partial<LocalStoredMessage> & { id: number }
  ): LocalStoredMessage => ({
    senderId: 1,
    text: '',
    createdAt: '2026-09-17T12:00:00Z',
    isSelf: false,
    ...ueber,
  })

  it('behält den eigenen Gesprächsanteil, den der Abruf nicht liefern kann', () => {
    // Der Absender kann seine eigene Ratchet-Nachricht nicht entschlüsseln. Sie
    // steht nur lokal, und ein Ersetzen statt Zusammenführen würde sie bei
    // jedem Abruf wegwischen — der teuerste Fehler dieses Umbaus.
    const lokal = [nachricht({ id: 10, clientUuid: 'a', text: 'von mir', isSelf: true })]
    const frisch = [nachricht({ id: 11, clientUuid: 'b', text: 'von dir' })]

    const zusammen = mischeVerlauf(lokal, frisch)
    expect(zusammen.map((m) => m.text)).toEqual(['von mir', 'von dir'])
  })

  it('lässt den bestätigten Eintrag den optimistischen verdrängen', () => {
    const lokal = [
      nachricht({ id: 1e11 + 5, clientUuid: 'a', text: 'unterwegs', status: 'queued' }),
    ]
    const frisch = [nachricht({ id: 42, clientUuid: 'a', text: 'unterwegs', status: 'sent' })]

    const zusammen = mischeVerlauf(lokal, frisch)
    expect(zusammen).toHaveLength(1)
    expect(zusammen[0].id).toBe(42)
    expect(zusammen[0].status).toBe('sent')
  })

  it('wirft einen optimistischen Eintrag nicht über einen bestätigten', () => {
    const lokal = [nachricht({ id: 42, clientUuid: 'a', text: 'fertig', status: 'sent' })]
    const frisch = [nachricht({ id: 0, clientUuid: 'a', text: 'fertig', status: 'queued' })]

    const zusammen = mischeVerlauf(lokal, frisch)
    expect(zusammen).toHaveLength(1)
    expect(zusammen[0].id).toBe(42)
    expect(zusammen[0].status).toBe('sent')
  })

  it('bewahrt Felder, die nur die ältere Fassung kennt', () => {
    // Ein Anhang, den dieser Durchlauf nicht mitgelesen hat, darf beim
    // Zusammenführen nicht verschwinden.
    const lokal = [
      nachricht({ id: 7, clientUuid: 'a', text: 'Bild', imageAttachment: { mediaId: 3 } }),
    ]
    const frisch = [nachricht({ id: 7, clientUuid: 'a', text: 'Bild', isRead: true })]

    const [zusammen] = mischeVerlauf(lokal, frisch)
    expect(zusammen.imageAttachment).toEqual({ mediaId: 3 })
    expect(zusammen.isRead).toBe(true)
  })

  it('führt über die Umschlagkennung zusammen, wenn keine Client-Kennung da ist', () => {
    const lokal = [nachricht({ id: 7, text: 'alt' })]
    const frisch = [nachricht({ id: 7, text: 'neu' })]

    const zusammen = mischeVerlauf(lokal, frisch)
    expect(zusammen).toHaveLength(1)
    expect(zusammen[0].text).toBe('neu')
  })
})

describe('sichereDauerhafteAblage', () => {
  const echt = Object.getOwnPropertyDescriptor(globalThis, 'navigator')

  function stelleSpeicher(storage: unknown) {
    Object.defineProperty(globalThis, 'navigator', {
      value: storage === undefined ? {} : { storage },
      configurable: true,
      writable: true,
    })
  }

  afterEach(() => {
    if (echt) Object.defineProperty(globalThis, 'navigator', echt)
    else delete (globalThis as { navigator?: unknown }).navigator
  })

  it('fragt nicht noch einmal, wenn die Ablage schon dauerhaft ist', async () => {
    // Firefox legt die Frage dem Menschen vor. Eine Frage, die bei jedem
    // Öffnen wiederkommt, beantwortet irgendwann jeder mit „nein" — deshalb
    // steht `persisted()` davor.
    const persist = vi.fn(async () => true)
    stelleSpeicher({ persisted: async () => true, persist })

    expect(await sichereDauerhafteAblage()).toBe(true)
    expect(persist).not.toHaveBeenCalled()
  })

  it('bittet um Dauerhaftigkeit, solange sie fehlt', async () => {
    const persist = vi.fn(async () => true)
    stelleSpeicher({ persisted: async () => false, persist })

    expect(await sichereDauerhafteAblage()).toBe(true)
    expect(persist).toHaveBeenCalledTimes(1)
  })

  it('meldet eine Absage als Absage, statt sie zu beschönigen', async () => {
    // `false` heißt: der Verlauf liegt da, darf aber jederzeit gehen. Wer das
    // in ein `true` verwandelt, nimmt dem Aufrufer die einzige Gelegenheit,
    // es sichtbar zu machen.
    stelleSpeicher({ persisted: async () => false, persist: async () => false })
    expect(await sichereDauerhafteAblage()).toBe(false)
  })

  it('startet auch ohne Speicher-API', async () => {
    // Ältere Browser und Umgebungen, in denen die API abgeschaltet ist. Der
    // Messenger darf daran nicht hängenbleiben.
    stelleSpeicher(undefined)
    expect(await sichereDauerhafteAblage()).toBe(false)

    stelleSpeicher({ persisted: async () => { throw new Error('verboten') }, persist: async () => true })
    expect(await sichereDauerhafteAblage()).toBe(false)
  })
})
