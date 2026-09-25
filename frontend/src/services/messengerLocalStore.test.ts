import { generateAesGcmKey } from '@msdis/shield/aead'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import { setzeInhaltsSchluessel, setzeSiegelAktiv } from './lokaleVersiegelung'
import {
  entferneLokaleNachricht,
  loadLocalMessages,
  saveLocalMessages,
  schreibeNachrichtenBestandNeu,
  updateMessageInLocalStore,
  getLocalMailboxLastSyncedId,
  clearLocalMessengerStore,
  parseMessageTimestamp,
  sortMessagesChronologically,
  isOptimisticMessage,
  mischeVerlauf,
  sichereDauerhafteAblage,
  ladeUmschlagKlartexte,
  leseUmschlagKlartext,
  speichereUmschlagKlartext,
  speichereEntwurf,
  ladeEntwurf,
  ladeAlleEntwuerfe,
  type LocalStoredMessage,
} from './messengerLocalStore'

/** Ein localStorage im Arbeitsspeicher — der Siegelschalter liegt dort. */
function installiereLocalStorage() {
  const daten = new Map<string, string>()
  ;(globalThis as any).localStorage = {
    getItem: (k: string) => daten.get(k) ?? null,
    setItem: (k: string, v: string) => daten.set(k, v),
    removeItem: (k: string) => daten.delete(k),
    clear: () => daten.clear(),
  }
}

/**
 * Die Ablage überlebt die einzelnen Tests und wird nur geleert.
 *
 * `messengerLocalStore` merkt sich die geöffnete Datenbank in `dbPromise` —
 * einmal pro Modul, nicht einmal pro Test. Eine bei jedem `beforeEach` frisch
 * gebaute Map wäre deshalb ab dem zweiten Test eine andere als die, in die das
 * Modul schreibt: die Tests liefen weiter, aber wer hineinschaut, sähe nichts.
 */
const datenbanken = new Map<string, Map<string, Map<string, any>>>()
/** Jeder `open`-Aufruf mit seinem Datenbanknamen, in der Reihenfolge. */
const geoeffneteDatenbanken: string[] = []
/** Jeder `deleteDatabase`-Aufruf. */
const geloeschteDatenbanken: string[] = []

/**
 * Die Ablage **einer** Datenbank.
 *
 * Der Browser trennt Datenbanken nach Namen; diese Fälschung muss das
 * nachmachen, sonst wäre jede Aussage über die Trennung je Konto eine Aussage
 * über nichts. Die Map-Objekte selbst bleiben über alle Tests dieselben, siehe
 * den Hinweis oben.
 */
function ablageVon(name: string): Map<string, Map<string, any>> {
  let vorhanden = datenbanken.get(name)
  if (!vorhanden) {
    vorhanden = new Map()
    datenbanken.set(name, vorhanden)
  }
  return vorhanden
}

/** Das Konto, unter dem die Tests dieser Datei laufen, und seine Datenbank. */
const TEST_KONTO = 1
const HAUPT_DB = `msm_messenger_local:konto:${TEST_KONTO}`
const mockStores = ablageVon(HAUPT_DB)

function installMockIndexedDb() {
  datenbanken.forEach((db) => db.forEach((map) => map.clear()))
  geoeffneteDatenbanken.length = 0
  geloeschteDatenbanken.length = 0

  const makeObjectStore = (stores: Map<string, Map<string, any>>, storeName: string) => {
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
        } else if (storeName === 'envelope_plaintexts') {
          // Zusammengesetzter Schlüssel wie im Schema: `[blindMailboxId, envelopeId]`.
          key = `${item.blindMailboxId}:${item.envelopeId}`
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
      getAll: (range?: any) => {
        const mid = Array.isArray(range?.lower) ? range.lower[0] : range?.lower
        const matched = Array.from(map!.values()).filter((i) => !mid || i.blindMailboxId === mid)
        const req: any = { onsuccess: null, onerror: null, result: matched }
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

  const macheDb = (name: string): any => {
    const stores = ablageVon(name)
    return {
      // Schließen ist im Browser echt; hier reicht die Nachbildung, damit der
      // Kontowechsel dieselbe Bewegung macht wie in der Anwendung.
      close: () => {},
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
          objectStore: (n: string) => makeObjectStore(stores, n),
          oncomplete: null,
          onerror: null,
        }
        queueMicrotask(() => tx.oncomplete?.())
        return tx
      },
    }
  }

  ;(globalThis as any).IDBKeyRange = {
    only: (v: any) => v,
    bound: (lower: any, upper: any) => ({ lower, upper }),
  }

  ;(globalThis as any).indexedDB = {
    open: (name: string) => {
      geoeffneteDatenbanken.push(name)
      const req: any = {
        onsuccess: null,
        onerror: null,
        onupgradeneeded: null,
        result: macheDb(name),
      }
      queueMicrotask(() => {
        req.onupgradeneeded?.()
        req.onsuccess?.()
      })
      return req
    },
    deleteDatabase: (name: string) => {
      geloeschteDatenbanken.push(name)
      ablageVon(name).forEach((map) => map.clear())
      const req: any = { onsuccess: null, onerror: null }
      queueMicrotask(() => req.onsuccess?.())
      return req
    },
  }

  return { stores: ablageVon(HAUPT_DB), geoeffneteDatenbanken, geloeschteDatenbanken }
}

describe('messengerLocalStore (IndexedDB Chat Persistence & F5 Hydration)', () => {
  beforeEach(() => {
    // Die Ablage gehört einem Konto. Ohne dieses Blatt gäbe sie nichts heraus —
    // und das ist, seit der Trennung je Konto, richtig so.
    setzeAngemeldetesKonto(TEST_KONTO)
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

  it('legt Systemzeilen nicht ab und gibt gespeicherte nicht wieder heraus', async () => {
    // Eine Systemzeile ist eine Aussage über den Zustand dieses Geräts in
    // diesem Moment, kein Gesprächsinhalt. Abgelegt blieb sie für immer stehen,
    // und weil sie ihre Kennung aus der Uhr nimmt, sortierte sie sich hinter
    // jede später eintreffende Nachricht.
    const mid = 'box-systemzeilen'
    await saveLocalMessages(mid, [
      {
        blindMailboxId: mid,
        id: 500,
        senderId: 2,
        text: 'Echte Nachricht',
        createdAt: '2026-09-18T10:00:00.000Z',
        isSelf: false,
      },
      {
        blindMailboxId: mid,
        id: 1758196800000,
        senderId: 0,
        text: 'Die Sicherheitssitzung mit diesem Gerät wurde neu aufgebaut.',
        createdAt: '2026-09-18T10:00:01.000Z',
        isSelf: false,
        isSystem: true,
      },
    ])

    const geladen = await loadLocalMessages(mid)
    expect(geladen.map((m) => m.text)).toEqual(['Echte Nachricht'])

    // Was aus der Zeit davor schon in der Ablage liegt, kommt ebenfalls nicht
    // mehr zurück — sonst stünde der alte Stapel weiter unter jedem Gespräch.
    // Nur über diesen Weg lässt sich noch eine Systemzeile hineinschreiben.
    await updateMessageInLocalStore(mid, 500, { isSystem: true })
    expect(await loadLocalMessages(mid)).toEqual([])
  })

  it('gibt ein als Nachricht abgelegtes Steuerpaket nicht mehr heraus', async () => {
    // Am laufenden System gefunden: `pin_message` hatte keinen Zweig, fiel in
    // den gewöhnlichen Weg und wurde gespeichert. Der rohe JSON-Text stand
    // danach in beiden Testchats, auch nachdem der Zweig nachgereicht war.
    const mid = 'box-steuerpaket'
    await saveLocalMessages(mid, [
      {
        blindMailboxId: mid,
        id: 600,
        senderId: 2,
        text: 'Echte Nachricht',
        createdAt: '2026-09-20T10:00:00.000Z',
        isSelf: false,
      },
      {
        blindMailboxId: mid,
        id: 601,
        senderId: 0,
        text: '{"type":"pin_message","target_id":4111,"aktion":"anheften","actor_id":10}',
        createdAt: '2026-09-20T10:00:01.000Z',
        isSelf: false,
      },
    ])

    expect((await loadLocalMessages(mid)).map((m) => m.text)).toEqual(['Echte Nachricht'])
  })

  it('nimmt eine verworfene Nachricht wirklich aus der Ablage', async () => {
    // Am laufenden System gefunden: `saveLocalMessages` schreibt nur. Die aus
    // der Liste weggelassene Nachricht blieb in der Ablage stehen und kam beim
    // nächsten Abgleich über `loadLocalMessages` zurück — die Nachricht mit der
    // Uhr, die sich nicht löschen liess.
    const mid = 'box-verworfen'
    await saveLocalMessages(mid, [
      {
        blindMailboxId: mid,
        id: 700,
        clientUuid: 'bleibt',
        senderId: 2,
        text: 'Bleibt stehen',
        createdAt: '2026-09-19T10:00:00.000Z',
        isSelf: false,
      },
      {
        blindMailboxId: mid,
        id: 1758276000000,
        clientUuid: 'haengt-fest',
        senderId: 1,
        text: 'Ging nie raus',
        createdAt: '2026-09-19T10:01:00.000Z',
        isSelf: true,
        status: 'queued',
      },
    ])

    await entferneLokaleNachricht(mid, { clientUuid: 'haengt-fest', id: 1758276000000 })

    expect((await loadLocalMessages(mid)).map((m) => m.text)).toEqual(['Bleibt stehen'])

    // Ein zweiter Aufruf ist kein Fehler, und ohne Kennung passiert nichts.
    await entferneLokaleNachricht(mid, { clientUuid: 'haengt-fest' })
    await entferneLokaleNachricht(mid, {})
    expect((await loadLocalMessages(mid)).map((m) => m.text)).toEqual(['Bleibt stehen'])
  })

  it('findet einen abgelegten Umschlagklartext unter derselben Kennung wieder', async () => {
    // Die beiden Funktionen müssen sich über den zusammengesetzten Schlüssel
    // einig sein. Wären sie es nicht, sähe jeder Lesedurchlauf einen schon
    // geöffneten Umschlag für ungeöffnet an, liefe ein zweites Mal über einen
    // verbrauchten Nachrichtenschlüssel und meldete einen Sitzungsbruch.
    await speichereUmschlagKlartext('box-klartext', 42, 'Hallo')

    expect(await leseUmschlagKlartext('box-klartext', 42)).toBe('Hallo')
    expect(await leseUmschlagKlartext('box-klartext', 43)).toBeNull()
    expect(await leseUmschlagKlartext('box-anders', 42)).toBeNull()
    expect((await ladeUmschlagKlartexte('box-klartext')).get(42)).toBe('Hallo')
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

  it('gibt verbogene Zeilen gar nicht erst heraus', async () => {
    /*
     * Eine einzige Zeile ohne brauchbaren `text` riss den kompletten
     * Messenger in die Fehlergrenze (`msg.text.startsWith is not a
     * function`), und weil sie auf der Platte lag, half auch Neuladen nicht.
     * Eine defekte Zeile darf eine Nachricht kosten, nicht das Gespräch.
     */
    const mid = 'box-verbogen'
    const ablage = mockStores.get('messages') || new Map()
    mockStores.set('messages', ablage)
    const kaputt = [
      { blindMailboxId: mid, id: 900001, clientUuid: 'a', createdAt: '2026-09-21T10:00:00.000Z' },
      { blindMailboxId: mid, id: 900002, clientUuid: 'b', createdAt: null, text: 'ok' },
      { blindMailboxId: mid, id: 900003, clientUuid: 'c', createdAt: '2026-09-21T10:00:00.000Z', text: { nicht: 'string' } },
      { blindMailboxId: mid, id: 'keine zahl', clientUuid: 'd', createdAt: '2026-09-21T10:00:00.000Z', text: 'ok' },
    ]
    for (const z of kaputt) ablage.set(`${mid}:${z.id}`, z)

    await saveLocalMessages(mid, [
      {
        blindMailboxId: mid,
        id: 77,
        clientUuid: 'heil',
        senderId: 1,
        text: 'Die heile Zeile',
        createdAt: '2026-09-21T11:00:00.000Z',
        isSelf: true,
        status: 'sent',
      },
    ])

    const geladen = await loadLocalMessages(mid)
    expect(geladen).toHaveLength(1)
    expect(geladen[0].text).toBe('Die heile Zeile')
  })

  it('nimmt die optimistische Zeile aus der Ablage, sobald die bestätigte da ist', async () => {
    /*
     * Beim Laden zu entdoppeln reicht nicht. Die optimistische Zeile blieb in
     * der Ablage stehen und überlebte die bestätigte: rutscht der Umschlag aus
     * dem Hundert-Umschläge-Fenster, ist sie die einzige Fassung, die dieses
     * Gerät noch hat — mit `status: 'queued'`, ganz unten im Verlauf statt an
     * ihrem Platz, ohne Häkchen und ohne Reaktionen.
     */
    const mid = 'box-aufraeumen'
    const clientUuid = 'uuid-aufraeumen'
    const gemeinsam = {
      blindMailboxId: mid,
      clientUuid,
      senderId: 1,
      text: 'Erste Nachricht',
      createdAt: '2026-09-16T18:00:00.000Z',
      isSelf: true,
    }

    // Runde 1: nur die optimistische Zeile, wie direkt nach dem Absenden.
    await saveLocalMessages(mid, [{ ...gemeinsam, id: 1773680000000, status: 'queued' }])
    expect(mockStores.get('messages')?.has(`${mid}:1773680000000`)).toBe(true)

    // Runde 2: die Bestätigung kommt nach — ohne die optimistische Zeile.
    await saveLocalMessages(mid, [{ ...gemeinsam, id: 88, status: 'sent' }])

    const abgelegt = mockStores.get('messages') as Map<string, any>
    expect(abgelegt.has(`${mid}:88`)).toBe(true)
    expect(abgelegt.has(`${mid}:1773680000000`)).toBe(false)
  })

  it('räumt auch auf, wenn beide Fassungen in derselben Runde kommen', async () => {
    const mid = 'box-gleichzeitig'
    const clientUuid = 'uuid-gleichzeitig'
    const gemeinsam = {
      blindMailboxId: mid,
      clientUuid,
      senderId: 1,
      text: 'Zusammen',
      createdAt: '2026-09-16T18:00:00.000Z',
      isSelf: true,
    }
    await saveLocalMessages(mid, [
      { ...gemeinsam, id: 1773680000001, status: 'queued' },
      { ...gemeinsam, id: 89, status: 'sent' },
    ])

    const abgelegt = mockStores.get('messages') as Map<string, any>
    expect(abgelegt.has(`${mid}:89`)).toBe(true)
    expect(abgelegt.has(`${mid}:1773680000001`)).toBe(false)
  })

  it('lässt eine noch nicht bestätigte Nachricht in Ruhe', async () => {
    // Die Kehrseite: ohne bestätigte Fassung darf nichts verschwinden, sonst
    // wäre die Warteschlange nach dem ersten Abgleich leer.
    const mid = 'box-wartet'
    await saveLocalMessages(mid, [
      {
        blindMailboxId: mid,
        id: 1773680000002,
        clientUuid: 'uuid-wartet',
        senderId: 1,
        text: 'Noch unterwegs',
        createdAt: '2026-09-16T18:00:00.000Z',
        isSelf: true,
        status: 'queued',
      },
      {
        blindMailboxId: mid,
        id: 90,
        clientUuid: 'uuid-fremd',
        senderId: 2,
        text: 'Von der Gegenseite',
        createdAt: '2026-09-16T18:01:00.000Z',
        isSelf: false,
        status: 'sent',
      },
    ])

    const abgelegt = mockStores.get('messages') as Map<string, any>
    expect(abgelegt.has(`${mid}:1773680000002`)).toBe(true)
    expect(abgelegt.has(`${mid}:90`)).toBe(true)
  })
})

/**
 * Der Weg durch die echte Ablage, mit eingeschaltetem PIN.
 *
 * `lokaleVersiegelung.test.ts` prüft, dass ein Datensatz zu- und wieder
 * aufgeht. Hier geht es um die Stelle, an der das schiefgehen kann, ohne dass
 * es jemandem auffällt: Verschlüsseln ist asynchron, eine IndexedDB-Transaktion
 * überlebt kein `await`. Wer versiegelt, **während** die Transaktion offen ist,
 * verliert sie — und die Nachricht.
 */
describe('messengerLocalStore mit PIN', () => {
  let ablage: Map<string, Map<string, any>>

  const mid = 'box-versiegelt'
  const nachricht: LocalStoredMessage = {
    blindMailboxId: mid,
    id: 501,
    clientUuid: 'uuid-501',
    senderId: 9,
    senderName: 'Jules',
    text: 'Das hier darf niemand im Klartext finden',
    createdAt: '2026-09-18T09:00:00.000Z',
    isSelf: false,
    status: 'sent',
  }

  beforeEach(async () => {
    ablage = installMockIndexedDb().stores
    installiereLocalStorage()
    setzeAngemeldetesKonto(TEST_KONTO)
    setzeSiegelAktiv(true)
    setzeInhaltsSchluessel(await generateAesGcmKey())
  })

  afterEach(() => {
    setzeSiegelAktiv(false)
    setzeInhaltsSchluessel(null)
    setzeAngemeldetesKonto(null)
  })

  it('schreibt und liest eine Nachricht vollständig zurück', async () => {
    await saveLocalMessages(mid, [nachricht])
    const geladen = await loadLocalMessages(mid)

    expect(geladen).toHaveLength(1)
    expect(geladen[0].text).toBe(nachricht.text)
    expect(geladen[0].senderName).toBe('Jules')
    expect(geladen[0].status).toBe('sent')
  })

  it('legt in der Datenbank keinen lesbaren Text ab', async () => {
    await saveLocalMessages(mid, [nachricht])

    const zeilen = [...(ablage.get('messages')?.values() ?? [])]
    expect(zeilen).toHaveLength(1)
    expect(JSON.stringify(zeilen[0])).not.toContain('darf niemand')
    expect(JSON.stringify(zeilen[0])).not.toContain('Jules')

    // Die Schlüssel- und Indexfelder bleiben lesbar, sonst gäbe es den Index
    // nicht mehr, über den `loadLocalMessages` und `updateMessageInLocalStore`
    // ihre Zeilen finden.
    expect(zeilen[0].blindMailboxId).toBe(mid)
    expect(zeilen[0].id).toBe(501)
    expect(zeilen[0].clientUuid).toBe('uuid-501')
  })

  it('ändert eine Nachricht über den Index, ohne sie zu öffnen zu lassen', async () => {
    await saveLocalMessages(mid, [nachricht])
    await updateMessageInLocalStore(mid, 'uuid-501', { isRead: true, text: 'geändert' })

    const geladen = await loadLocalMessages(mid)
    expect(geladen[0].isRead).toBe(true)
    expect(geladen[0].text).toBe('geändert')

    const zeilen = [...(ablage.get('messages')?.values() ?? [])]
    expect(JSON.stringify(zeilen[0])).not.toContain('geändert')
  })

  it('hält auch Umschlag-Klartexte zu', async () => {
    await speichereUmschlagKlartext(mid, 77, 'der geöffnete Umschlag')
    expect(await leseUmschlagKlartext(mid, 77)).toBe('der geöffnete Umschlag')

    const zeilen = [...(ablage.get('envelope_plaintexts')?.values() ?? [])]
    expect(JSON.stringify(zeilen[0])).not.toContain('geöffnete Umschlag')
  })

  it('gibt gesperrt nichts heraus und schreibt nichts', async () => {
    await saveLocalMessages(mid, [nachricht])
    setzeInhaltsSchluessel(null)

    expect(await loadLocalMessages(mid)).toHaveLength(0)
    expect(await leseUmschlagKlartext(mid, 77)).toBeNull()

    // Der Umschlag-Klartext ist der eine Schreibweg, der Fehler nicht schluckt:
    // scheitert er, darf der Ratchet-Schlüssel nicht als verbraucht gelten.
    await expect(speichereUmschlagKlartext(mid, 78, 'darf nicht liegen bleiben')).rejects.toThrow()

    const zeilen = [...(ablage.get('envelope_plaintexts')?.values() ?? [])]
    expect(JSON.stringify(zeilen)).not.toContain('darf nicht liegen bleiben')
  })

  it('schreibt den Bestand um, ohne ihn zu verlieren', async () => {
    await saveLocalMessages(mid, [nachricht])
    await speichereUmschlagKlartext(mid, 77, 'der geöffnete Umschlag')

    // Abschalten: Schalter aus, Schlüssel bleibt, einmal durchlaufen.
    setzeSiegelAktiv(false)
    await schreibeNachrichtenBestandNeu()

    const geladen = await loadLocalMessages(mid)
    expect(geladen[0].text).toBe(nachricht.text)
    expect(await leseUmschlagKlartext(mid, 77)).toBe('der geöffnete Umschlag')

    const zeilen = [...(ablage.get('messages')?.values() ?? [])]
    expect(zeilen[0].v).toBeUndefined()
    expect(zeilen[0].text).toBe(nachricht.text)
  })

  it('verträgt einen Abbruch mitten in der Umstellung', async () => {
    // Nach einem Abbruch liegen beide Formen nebeneinander. Beide müssen
    // lesbar bleiben, sonst wäre ein geschlossener Reiter ein Datenverlust.
    await saveLocalMessages(mid, [nachricht])
    setzeSiegelAktiv(false)
    await saveLocalMessages(mid, [{ ...nachricht, id: 502, clientUuid: 'uuid-502', text: 'offen' }])
    setzeSiegelAktiv(true)

    const geladen = await loadLocalMessages(mid)
    expect(geladen).toHaveLength(2)
    expect(geladen.map((m) => m.text).sort()).toEqual([nachricht.text, 'offen'].sort())
  })
})

/**
 * Entwürfe.
 *
 * Ein Entwurf ist ungesendeter Klartext und damit vom Empfindlichsten, was
 * dieses Gerät hält: er steht noch nirgends sonst. Deshalb liegt er in der
 * versiegelten Ablage und nicht im localStorage neben den Stummschaltungen —
 * dort stünde offen, was jemand gerade schreibt und wem.
 */
describe('Entwürfe je Chat', () => {
  let ablage: Map<string, Map<string, any>>
  const mid = 'mailbox-entwurf'
  const anderer = 'mailbox-entwurf-2'

  beforeEach(async () => {
    ablage = installMockIndexedDb().stores
    installiereLocalStorage()
    setzeAngemeldetesKonto(TEST_KONTO)
    setzeSiegelAktiv(true)
    setzeInhaltsSchluessel(await generateAesGcmKey())
  })

  afterEach(() => {
    setzeSiegelAktiv(false)
    setzeInhaltsSchluessel(null)
    setzeAngemeldetesKonto(null)
  })

  it('legt einen Entwurf ab und gibt ihn zurück', async () => {
    await speichereEntwurf(mid, 'halb getippt')
    expect(await ladeEntwurf(mid)).toBe('halb getippt')
  })

  it('legt ihn versiegelt ab, nicht lesbar', async () => {
    await speichereEntwurf(mid, 'das soll niemand finden')
    const zeilen = [...(ablage.get('entwuerfe')?.values() ?? [])]
    expect(zeilen).toHaveLength(1)
    expect(JSON.stringify(zeilen[0])).not.toContain('niemand finden')
    // Der Schlüssel bleibt lesbar, sonst fände ihn niemand wieder.
    expect(zeilen[0].blindMailboxId).toBe(mid)
  })

  it('schreibt nichts in den localStorage', async () => {
    await speichereEntwurf(mid, 'nur in der Ablage')
    expect(JSON.stringify({ ...localStorage })).not.toContain('nur in der Ablage')
  })

  it('hält die Chats auseinander', async () => {
    await speichereEntwurf(mid, 'für den einen')
    await speichereEntwurf(anderer, 'für den anderen')
    expect(await ladeEntwurf(mid)).toBe('für den einen')
    expect(await ladeEntwurf(anderer)).toBe('für den anderen')
  })

  it('löscht den Entwurf, statt ihn leer zu speichern', async () => {
    // Eine Zeile, die nur sagt „hier wurde mal etwas getippt und verworfen",
    // ist ein Hinweis, den niemand braucht.
    await speichereEntwurf(mid, 'erst getippt')
    await speichereEntwurf(mid, '   ')
    expect(await ladeEntwurf(mid)).toBe('')
    expect([...(ablage.get('entwuerfe')?.values() ?? [])]).toHaveLength(0)
  })

  it('meldet für einen Chat ohne Entwurf einen leeren String', async () => {
    expect(await ladeEntwurf('nie beschrieben')).toBe('')
  })

  it('sammelt alle Entwürfe für die Vorschau in der Liste', async () => {
    await speichereEntwurf(mid, 'eins')
    await speichereEntwurf(anderer, 'zwei')
    expect(await ladeAlleEntwuerfe()).toEqual({ [mid]: 'eins', [anderer]: 'zwei' })
  })

  it('gibt gesperrt nichts heraus', async () => {
    // Ein unlesbarer Entwurf ist kein Fehler, er ist eben keiner: ohne
    // Schlüssel bleibt das Eingabefeld leer, statt eine Ausnahme zu werfen.
    await speichereEntwurf(mid, 'hinter dem PIN')
    setzeInhaltsSchluessel(null)
    expect(await ladeEntwurf(mid)).toBe('')
    expect(await ladeAlleEntwuerfe()).toEqual({})
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

  it('überschreibt keine Nachricht eines anderen Absenders mit gleicher clientUuid', () => {
    const lokal = [
      nachricht({
        id: 10,
        senderId: 1,
        clientUuid: 'kollision-uuid',
        text: 'Nachricht von Opfer',
        createdAt: '2026-09-17T12:00:00Z',
      }),
    ]
    const frisch = [
      nachricht({
        id: 11,
        senderId: 2,
        clientUuid: 'kollision-uuid',
        text: 'Nachricht von Angreifer',
        createdAt: '2026-09-17T12:01:00Z',
      }),
    ]

    const zusammen = mischeVerlauf(lokal, frisch)
    expect(zusammen).toHaveLength(2)
    expect(zusammen.find((m) => m.senderId === 1)?.text).toBe('Nachricht von Opfer')
    expect(zusammen.find((m) => m.senderId === 2)?.text).toBe('Nachricht von Angreifer')
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

/**
 * Eine Ablage je Konto.
 *
 * Bis 09/2026 hieß die Datenbank schlicht `msm_messenger_local` und gehörte
 * damit dem Browserprofil. Zwei Konten nacheinander auf demselben Rechner
 * hinterließen ihre Verläufe nebeneinander, und das zweite las die des ersten
 * beim Öffnen des Messengers mit. Diese Tests halten die Trennung fest — nicht
 * als Filter über einer gemeinsamen Ablage, sondern als getrennte Datenbanken.
 */
describe('Ablage je Konto', () => {
  const mid = 'box-getrennt'
  const zeile: LocalStoredMessage = {
    blindMailboxId: mid,
    id: 11,
    senderId: 1,
    text: 'Gehört Konto 1',
    createdAt: '2026-09-22T10:00:00.000Z',
    isSelf: true,
    status: 'sent',
  }

  beforeEach(() => {
    setzeAngemeldetesKonto(TEST_KONTO)
    installMockIndexedDb()
  })

  afterEach(() => {
    setzeAngemeldetesKonto(TEST_KONTO)
  })

  it('zeigt einem anderen Konto den Verlauf nicht', async () => {
    await saveLocalMessages(mid, [zeile])
    expect(await loadLocalMessages(mid)).toHaveLength(1)

    // Dasselbe Gerät, dasselbe Gespräch, ein anderer Mensch.
    setzeAngemeldetesKonto(7)
    expect(await loadLocalMessages(mid)).toEqual([])

    // Und es bleibt dabei: das fremde Konto sieht auch nichts, wenn es selbst
    // etwas ablegt. Zwei Verläufe, zwei Ablagen.
    await saveLocalMessages(mid, [{ ...zeile, id: 12, text: 'Gehört Konto 7' }])
    const beiSieben = await loadLocalMessages(mid)
    expect(beiSieben).toHaveLength(1)
    expect(beiSieben[0].text).toBe('Gehört Konto 7')

    setzeAngemeldetesKonto(TEST_KONTO)
    const beiEins = await loadLocalMessages(mid)
    expect(beiEins).toHaveLength(1)
    expect(beiEins[0].text).toBe('Gehört Konto 1')
  })

  it('öffnet für jedes Konto eine eigene Datenbank', async () => {
    // Jeder Kontowechsel erzwingt ein neues Öffnen — die gemerkte Verbindung
    // des vorigen Kontos darf nicht weiterverwendet werden.
    setzeAngemeldetesKonto(7)
    await loadLocalMessages(mid)
    setzeAngemeldetesKonto(TEST_KONTO)
    await loadLocalMessages(mid)

    // Genau zwei, mit Namen, und der alte kontolose ist keiner davon.
    expect(geoeffneteDatenbanken).toEqual([
      'msm_messenger_local:konto:7',
      'msm_messenger_local:konto:1',
    ])
  })

  it('schreibt und liest nichts, solange niemand angemeldet ist', async () => {
    setzeAngemeldetesKonto(null)

    // Kein Wurf nach außen, aber auch kein Ausweichen auf eine gemeinsame
    // Ablage: der Aufruf tut schlicht nichts.
    await saveLocalMessages(mid, [zeile])
    expect(await loadLocalMessages(mid)).toEqual([])
    expect(await getLocalMailboxLastSyncedId(mid)).toBe(0)
    expect(geoeffneteDatenbanken).toEqual([])

    // Nach der Anmeldung ist die eigene Ablage da — und leer, denn abgelegt
    // wurde vorhin nichts.
    setzeAngemeldetesKonto(TEST_KONTO)
    expect(await loadLocalMessages(mid)).toEqual([])
  })

  it('räumt die alte, kontolose Datenbank ab', async () => {
    // Frisches Modul: `raeumeAltbestand` läuft einmal je Sitzung, und die ist
    // in dieser Datei längst vorbei.
    vi.resetModules()
    const { setzeAngemeldetesKonto: setzeFrisch } = await import('@/lib/angemeldetesKonto')
    setzeFrisch(TEST_KONTO)
    const frisch = await import('./messengerLocalStore')

    await frisch.loadLocalMessages(mid)

    expect(geloeschteDatenbanken).toContain('msm_messenger_local')
  })
})
