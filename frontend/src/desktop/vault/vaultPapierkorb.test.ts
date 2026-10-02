/**
 * Papierkorb, Archiv und Verträglichkeit zwischen App-Versionen.
 *
 * Seit 09/2026 legt Löschen einen Eintrag in den Papierkorb; endgültig
 * gelöscht wird nach 30 Tagen oder von Hand. Dazu kommen zwei Regeln, die
 * über diese Funktion hinaus gelten:
 *
 *   - Eine App speichert Felder mit, die sie nicht kennt. Vorher baute jede
 *     Speicherung die Nutzlast aus einer festen Liste neu, und eine ältere App
 *     löschte still, was eine neuere hineingeschrieben hatte.
 *   - Endgültig gelöscht wird nur die Fassung, die das Gerät zuletzt vom Server
 *     kannte. Hat ein anderes Gerät den Eintrag inzwischen wiederhergestellt,
 *     lehnt der Server ab, und dessen Fassung bleibt.
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import i18n from '@/i18n'
import {
  PAPIERKORB_TAGE,
  useVaultStore,
  VAULT_TOMBSTONE_MARKER,
  type VaultBlindSyncPayload,
} from './vaultStore'
import { decryptVaultEntry, encryptVaultEntry } from './vaultCrypto'
import { gepolsterteGroesse } from './tresorDatei'

vi.mock('../tauri', () => ({
  FACH_TRESOR: 'vault_biometric_key',
  biometrieSpeichern: vi.fn().mockResolvedValue(undefined),
  biometrieEntsperren: vi.fn().mockResolvedValue(''),
  biometrieLoeschen: vi.fn().mockResolvedValue(undefined),
  pruefeBiometrieVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherFragtSelbst: vi.fn().mockResolvedValue(false),
  verifiziereBiometrie: vi.fn().mockResolvedValue(false),
  setzeTresorSchutz: vi.fn().mockResolvedValue(undefined),
}))

const BUCKET = 'a'.repeat(64)
const EINTRAG = 'eintrag-1'
const TAG = 24 * 60 * 60 * 1000

async function userKeyAnlegen(): Promise<CryptoKey> {
  return window.crypto.subtle.importKey(
    'raw',
    new Uint8Array(32).fill(7),
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  )
}

function warteschlange() {
  return JSON.parse(localStorage.getItem(`mss:vault_pending_${BUCKET}`) || '[]') as {
    id: string
    ciphertext: string
    is_deleted: boolean
    expected_revision?: number
  }[]
}

/**
 * Ein entsperrter Tresor, dessen einziger Eintrag über den Sync hereinkommt:
 * so steht er wie im echten Betrieb mit Serverrevision im Cache.
 */
async function tresorVomServer(userKey: CryptoKey, nutzlast: Record<string, unknown>, serverRev = 10) {
  const ciphertext = await encryptVaultEntry(nutzlast, userKey, EINTRAG)
  useVaultStore.setState({
    userKey,
    bucketId: BUCKET,
    bucketAuthToken: 'b'.repeat(64),
    isUnlocked: true,
    syncStatus: 'synced',
    items: [],
  })
  const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => ({
      server_revision: serverRev,
      entries: [{ id: EINTRAG, ciphertext, revision: serverRev, is_deleted: false, updated_at: '2026-09-30T00:00:00Z' }],
    }),
  } as Response)
  await useVaultStore.getState().syncWithServer()
  fetchSpy.mockRestore()
  return ciphertext
}

/** Ein Server, der jede Mutation annimmt und sie mit fortlaufender Revision zurückgibt. */
function echoServer() {
  let revision = 10
  const koerper: VaultBlindSyncPayload[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => {
    const body = JSON.parse(String(init?.body)) as VaultBlindSyncPayload
    koerper.push(body)
    const entries = body.mutations.map((m) => ({ ...m, revision: ++revision, updated_at: '2026-09-30T00:00:00Z' }))
    return {
      ok: true,
      status: 200,
      json: async () => ({ server_revision: revision, entries }),
    } as Response
  })
  return koerper
}

describe('Tresor: Verträglichkeit zwischen App-Versionen', () => {
  let userKey: CryptoKey

  beforeEach(async () => {
    localStorage.clear()
    vi.restoreAllMocks()
    userKey = await userKeyAnlegen()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('behält beim Speichern Felder, die diese Fassung nicht kennt', async () => {
    await tresorVomServer(userKey, {
      service: 'Bank',
      username: 'ich',
      password: 'geheim',
      category: 'login',
      createdAt: 1,
      updatedAt: 10,
      blobs: [{ blobId: 'x1', rev: 3 }],
      kuenftigesFeld: 'bleibt',
    })
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))

    await useVaultStore.getState().toggleFavorite(EINTRAG)

    const gesendet = warteschlange().find((m) => m.id === EINTRAG)!
    const nutzlast = await decryptVaultEntry(gesendet.ciphertext, userKey, EINTRAG)
    expect(nutzlast.isFavorite).toBe(true)
    expect(nutzlast.kuenftigesFeld).toBe('bleibt')
    expect(nutzlast.blobs).toEqual([{ blobId: 'x1', rev: 3 }])
    expect(nutzlast.format).toBe(1)
  })

  it('schreibt keinen Eintrag, dessen Art sie nicht kennt', async () => {
    await tresorVomServer(userKey, {
      service: 'etwas-kuenftiges',
      category: 'kuenftige_art',
      createdAt: 1,
      updatedAt: 10,
    })
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))
    const item = useVaultStore.getState().items.find((i) => i.id === EINTRAG)!
    expect(item.category).toBe('kuenftige_art')

    await expect(useVaultStore.getState().saveItem({ ...item, service: 'anders' })).rejects.toThrow()
    await expect(useVaultStore.getState().trashItem(EINTRAG)).rejects.toThrow()
    expect(warteschlange()).toHaveLength(0)
  })
})

describe('Tresor: Papierkorb und Archiv', () => {
  let userKey: CryptoKey

  beforeEach(async () => {
    localStorage.clear()
    vi.restoreAllMocks()
    userKey = await userKeyAnlegen()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  const bank = (extra: Record<string, unknown> = {}) => ({
    service: 'Bank',
    username: 'ich',
    password: 'geheim',
    category: 'login',
    createdAt: 1,
    updatedAt: 10,
    ...extra,
  })

  it('legt beim Löschen in den Papierkorb und holt wieder heraus, im Umschlag', async () => {
    await tresorVomServer(userKey, bank())
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))

    await useVaultStore.getState().trashItem(EINTRAG)
    let item = useVaultStore.getState().items.find((i) => i.id === EINTRAG)!
    expect(item.trashedAt).toBeGreaterThan(0)
    let gesendet = warteschlange().find((m) => m.id === EINTRAG)!
    expect(gesendet.is_deleted).toBe(false)
    expect((await decryptVaultEntry(gesendet.ciphertext, userKey, EINTRAG)).trashedAt).toBe(item.trashedAt)

    await useVaultStore.getState().restoreItem(EINTRAG)
    item = useVaultStore.getState().items.find((i) => i.id === EINTRAG)!
    expect(item.trashedAt).toBeUndefined()
    gesendet = warteschlange().find((m) => m.id === EINTRAG)!
    expect((await decryptVaultEntry(gesendet.ciphertext, userKey, EINTRAG)).trashedAt).toBeUndefined()
  })

  it('archiviert und holt aus dem Archiv, ohne andere Felder zu verlieren', async () => {
    await tresorVomServer(userKey, bank({ isFavorite: true }))
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))

    await useVaultStore.getState().setArchived(EINTRAG, true)
    expect(useVaultStore.getState().items[0].archivedAt).toBeGreaterThan(0)
    expect(useVaultStore.getState().items[0].isFavorite).toBe(true)

    await useVaultStore.getState().setArchived(EINTRAG, false)
    expect(useVaultStore.getState().items[0].archivedAt).toBeUndefined()
  })

  it('löscht endgültig nur die Fassung, die das Gerät vom Server kennt', async () => {
    await tresorVomServer(userKey, bank({ trashedAt: Date.now() - TAG }), 17)
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))

    await useVaultStore.getState().deleteItem(EINTRAG)

    const grab = warteschlange().find((m) => m.id === EINTRAG)!
    expect(grab.is_deleted).toBe(true)
    expect(grab.expected_revision).toBe(17)
    expect(useVaultStore.getState().items).toHaveLength(0)
  })

  it('behält die Wiederherstellung eines anderen Geräts, wenn der Server das Löschen ablehnt', async () => {
    await tresorVomServer(userKey, bank({ trashedAt: Date.now() - TAG }), 17)
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))
    await useVaultStore.getState().deleteItem(EINTRAG)
    await vi.waitFor(() => expect(useVaultStore.getState().syncStatus).toBe('offline'))
    vi.restoreAllMocks()

    // Das andere Gerät hat den Eintrag aus dem Papierkorb geholt, bevor das
    // Löschen ankam. Seine Uhr geht nach: der Stand liegt über dem der Fassung
    // im Papierkorb, aber unter dem des eigenen Tombstones.
    const wiederhergestellt = await encryptVaultEntry(bank({ updatedAt: 20, password: 'neu' }), userKey, EINTRAG)
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        server_revision: 18,
        conflicts: [EINTRAG],
        entries: [{ id: EINTRAG, ciphertext: wiederhergestellt, revision: 18, is_deleted: false, updated_at: '2026-09-30T00:00:00Z' }],
      }),
    } as Response)

    await useVaultStore.getState().syncWithServer()

    const item = useVaultStore.getState().items.find((i) => i.id === EINTRAG)
    expect(item?.password).toBe('neu')
    expect(item?.trashedAt).toBeUndefined()
    expect(warteschlange()).toHaveLength(0)
  })

  it('lässt sich vom Server keinen Konflikt für eine fremde Fassung unterschieben', async () => {
    await tresorVomServer(userKey, bank({ trashedAt: Date.now() - TAG }), 17)
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))
    await useVaultStore.getState().deleteItem(EINTRAG)
    await vi.waitFor(() => expect(useVaultStore.getState().syncStatus).toBe('offline'))
    vi.restoreAllMocks()

    // Eine uralte Fassung (Stand 5) samt behauptetem Konflikt: zurück kommt
    // höchstens, was das Gerät selbst vorher hatte, und das ist neuer.
    const uralt = await encryptVaultEntry(bank({ updatedAt: 5, password: 'uralt' }), userKey, EINTRAG)
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        server_revision: 18,
        conflicts: [EINTRAG],
        entries: [{ id: EINTRAG, ciphertext: uralt, revision: 18, is_deleted: false, updated_at: '2026-09-30T00:00:00Z' }],
      }),
    } as Response)

    await useVaultStore.getState().syncWithServer()

    const cache = JSON.parse(localStorage.getItem(`mss:vault_blobs_${BUCKET}`) || '[]') as { id: string; ciphertext: string }[]
    const lokal = await decryptVaultEntry(cache.find((b) => b.id === EINTRAG)!.ciphertext, userKey, EINTRAG)
    expect(lokal.password).toBe('geheim')
    expect(useVaultStore.getState().items.find((i) => i.id === EINTRAG)?.password).not.toBe('uralt')
  })

  it(`leert nach ${PAPIERKORB_TAGE} Tagen, aber nur nach vollständigem Abgleich`, async () => {
    const jetzt = Date.now()
    await tresorVomServer(userKey, bank({ trashedAt: jetzt - (PAPIERKORB_TAGE - 1) * TAG }), 17)
    expect(useVaultStore.getState().items).toHaveLength(1)
    // Zwei Tage später ist die Frist um.
    vi.spyOn(Date, 'now').mockReturnValue(jetzt + 2 * TAG)

    // Offline: kein Abgleich, kein endgültiges Löschen.
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))
    await useVaultStore.getState().syncWithServer()
    expect(warteschlange()).toHaveLength(0)
    expect(useVaultStore.getState().items).toHaveLength(1)

    const koerper = echoServer()
    await useVaultStore.getState().syncWithServer()
    await vi.waitFor(() => expect(koerper.some((k) => k.mutations.some((m) => m.is_deleted))).toBe(true))

    const grab = koerper.flatMap((k) => k.mutations).find((m) => m.is_deleted)!
    expect(grab.expected_revision).toBe(17)
    const nutzlast = await decryptVaultEntry(grab.ciphertext, userKey, EINTRAG)
    expect(nutzlast[VAULT_TOMBSTONE_MARKER]).toBe(true)
    await vi.waitFor(() => expect(useVaultStore.getState().items).toHaveLength(0))
    // Der Sync, den das Löschen anstößt, soll nicht in den nächsten Test laufen.
    await vi.waitFor(() => {
      expect(warteschlange()).toHaveLength(0)
      expect(useVaultStore.getState().syncStatus).toBe('synced')
    })
  })

  it('behält die Löschaufträge einer Datei, deren Ordner mit ihr im Papierkorb liegt', async () => {
    // Bis 02.10.2026: „Papierkorb leeren“ löschte zuerst den Ordner und mit ihm
    // die Datei, danach die Datei noch einmal. Der zweite Tombstone ersetzte
    // den ersten ohne Löschaufträge, und ihre Blobs blieben für immer liegen.
    const kopf = (id: string) => ({ id: id.repeat(32), groesse: gepolsterteGroesse(10), echt: 10, schluessel: 'k', loeschen: id.repeat(64) })
    const weg = Date.now() - TAG
    useVaultStore.setState({
      userKey,
      bucketId: BUCKET,
      bucketAuthToken: 'b'.repeat(64),
      isUnlocked: true,
      items: [
        { id: 'ordner-1', service: 'Fotos', username: '', password: '', category: 'ordner', createdAt: 1, updatedAt: 10, trashedAt: weg },
        {
          id: 'datei-1', service: 'bild.jpg', username: '', password: '', category: 'datei', ordner: 'ordner-1',
          createdAt: 1, updatedAt: 10, trashedAt: weg,
          datei: { typ: 'image/jpeg', original: kopf('a'), vorschau: kopf('b'), miniatur: kopf('c') },
        },
      ] as never,
    })
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))

    await useVaultStore.getState().emptyTrash()

    const cache = JSON.parse(localStorage.getItem(`mss:vault_blobs_${BUCKET}`) || '[]') as {
      id: string
      is_deleted: boolean
      loeschBlobs?: { id: string }[]
    }[]
    const grab = cache.find((b) => b.id === 'datei-1')!
    expect(grab.is_deleted).toBe(true)
    expect(grab.loeschBlobs?.map((b) => b.id)).toEqual(['a'.repeat(32), 'b'.repeat(32), 'c'.repeat(32)])
    expect(warteschlange().filter((m) => m.id === 'datei-1')).toHaveLength(1)
  })

  it('lässt einen Eintrag, der erst kurz im Papierkorb liegt', async () => {
    await tresorVomServer(userKey, bank({ trashedAt: Date.now() - (PAPIERKORB_TAGE - 1) * TAG }), 17)
    const koerper = echoServer()

    await useVaultStore.getState().syncWithServer()

    expect(koerper.flatMap((k) => k.mutations)).toHaveLength(0)
    expect(useVaultStore.getState().items).toHaveLength(1)
  })
})

describe('Tresor: Hochstufen gegen alte Apps', () => {
  let userKey: CryptoKey

  beforeEach(async () => {
    localStorage.clear()
    vi.restoreAllMocks()
    userKey = await userKeyAnlegen()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('meldet sein Format immer und stuft erst hoch, wenn der Tresor es braucht', async () => {
    await tresorVomServer(userKey, { service: 'Bank', category: 'login', createdAt: 1, updatedAt: 10 })
    let koerper = echoServer()
    await useVaultStore.getState().syncWithServer()
    expect(koerper[0].client_format).toBe(1)
    expect(koerper[0].min_client_format).toBeUndefined()
    vi.restoreAllMocks()

    koerper = echoServer()
    await useVaultStore.getState().trashItem(EINTRAG)
    await vi.waitFor(() => expect(koerper.length).toBeGreaterThan(0))
    expect(koerper[0].min_client_format).toBe(1)
    await vi.waitFor(() => expect(useVaultStore.getState().syncStatus).toBe('synced'))
  })
})

describe('Tresor: seitenweiser Sync', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('holt weiter, solange der Server mehr meldet', async () => {
    const userKey = await userKeyAnlegen()
    useVaultStore.setState({ userKey, bucketId: BUCKET, bucketAuthToken: 'b'.repeat(64), isUnlocked: true, syncStatus: 'synced', items: [] })
    const seiten = await Promise.all(
      ['eins', 'zwei'].map(async (id, i) => ({
        server_revision: i + 1,
        has_more: i === 0,
        entries: [
          {
            id,
            ciphertext: await encryptVaultEntry({ service: id, category: 'login', createdAt: 1, updatedAt: 1 }, userKey, id),
            revision: i + 1,
            is_deleted: false,
            updated_at: '2026-09-30T00:00:00Z',
          },
        ],
      })),
    )
    const anfragen: VaultBlindSyncPayload[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => {
      anfragen.push(JSON.parse(String(init?.body)))
      const seite = seiten[anfragen.length - 1] ?? { server_revision: 2, has_more: false, entries: [] }
      return { ok: true, status: 200, json: async () => seite } as Response
    })

    await useVaultStore.getState().syncWithServer()

    expect(anfragen.map((a) => a.since_revision)).toEqual([0, 1])
    expect(useVaultStore.getState().items.map((i) => i.service).sort()).toEqual(['eins', 'zwei'])
  })
})

describe('Tresor: Alben', () => {
  let userKey: CryptoKey

  beforeEach(async () => {
    localStorage.clear()
    vi.restoreAllMocks()
    userKey = await userKeyAnlegen()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('führt die Liste im Album-Eintrag, ohne Doppelte, und nimmt heraus', async () => {
    useVaultStore.setState({ userKey, bucketId: BUCKET, bucketAuthToken: 'b'.repeat(64), isUnlocked: true, syncStatus: 'synced', items: [] })
    echoServer()
    const id = await useVaultStore.getState().albumAnlegen('Urlaub', ['f1', 'f2', 'f1'])
    await useVaultStore.getState().albumAendern(id, { hinzu: ['f3', 'f2'] })
    await useVaultStore.getState().albumAendern(id, { weg: ['f1'] })

    const album = useVaultStore.getState().items.find((i) => i.id === id)!
    expect(album.category).toBe('album')
    expect(album.album).toEqual({ eintraege: ['f2', 'f3'] })
  })

  it('lässt ein Album, das diese Fassung nicht lesen kann, beim Umbenennen unverändert', async () => {
    const kaputt = { eintraege: 'nicht-eine-liste', neu: 1 }
    await tresorVomServer(userKey, { service: 'Alt', category: 'album', album: kaputt, createdAt: 1, updatedAt: 10 })
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))
    const item = useVaultStore.getState().items.find((i) => i.id === EINTRAG)!
    expect(item.album).toBeUndefined()

    await useVaultStore.getState().saveItem({ ...item, service: 'Neu' })
    const gesendet = warteschlange().find((m) => m.id === EINTRAG)!
    const nutzlast = await decryptVaultEntry(gesendet.ciphertext, userKey, EINTRAG)
    expect(nutzlast.service).toBe('Neu')
    expect(nutzlast.album).toEqual(kaputt)
  })

  it('verliert keine von zwei gleichzeitigen Änderungen am selben Album', async () => {
    // Bis 02.10.2026 lasen beide vor dem Verschlüsseln denselben alten Stand.
    useVaultStore.setState({ userKey, bucketId: BUCKET, bucketAuthToken: 'b'.repeat(64), isUnlocked: true, syncStatus: 'synced', items: [] })
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))
    const id = await useVaultStore.getState().albumAnlegen('Urlaub', ['f1'])

    await Promise.all([
      useVaultStore.getState().albumAendern(id, { hinzu: ['f2'] }),
      useVaultStore.getState().albumAendern(id, { hinzu: ['f3'] }),
      useVaultStore.getState().toggleFavorite(id),
    ])

    const album = useVaultStore.getState().items.find((i) => i.id === id)!
    expect(album.album?.eintraege).toEqual(['f1', 'f2', 'f3'])
    expect(album.isFavorite).toBe(true)
  })
})

describe('Tresor: Umbenennen nach einem Abgleich', () => {
  it('behält den Inhalt, den ein anderes Gerät während der Rückfrage ersetzt hat', async () => {
    // Bis 02.10.2026 schrieb Umbenennen den Schnappschuss von vor der Rückfrage
    // zurück: die neue Fassung des anderen Geräts war weg, ihre Blobs belegten
    // den Speicher ohne Eintrag.
    localStorage.clear()
    vi.restoreAllMocks()
    const userKey = await userKeyAnlegen()
    const kopf = (id: string) => ({ id: id.repeat(32), groesse: gepolsterteGroesse(10), echt: 10, schluessel: 'k', loeschen: id.repeat(64) })
    const alt = { typ: 'image/jpeg', original: kopf('a'), vorschau: kopf('b'), miniatur: kopf('c') }
    await tresorVomServer(userKey, { service: 'bild.jpg', category: 'datei', datei: alt, createdAt: 1, updatedAt: 10 }, 10)
    const schnappschuss = useVaultStore.getState().items.find((i) => i.id === EINTRAG)!

    // Während die Rückfrage offen ist, bringt der Abgleich den Inhalt von Gerät B.
    const neu = { typ: 'image/jpeg', original: kopf('d'), vorschau: kopf('e'), miniatur: kopf('f'), frueher: [{ ...alt, ersetzt: 20 }] }
    const vonB = await encryptVaultEntry({ service: 'bild.jpg', category: 'datei', datei: neu, createdAt: 1, updatedAt: 20 }, userKey, EINTRAG)
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        server_revision: 11,
        entries: [{ id: EINTRAG, ciphertext: vonB, revision: 11, is_deleted: false, updated_at: '2026-09-30T00:00:00Z' }],
      }),
    } as Response)
    await useVaultStore.getState().syncWithServer()
    vi.restoreAllMocks()
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))

    await useVaultStore.getState().aendern(schnappschuss.id, { service: 'urlaub.jpg' })

    const gesendet = warteschlange().find((m) => m.id === EINTRAG)!
    const nutzlast = await decryptVaultEntry(gesendet.ciphertext, userKey, EINTRAG)
    expect(nutzlast.service).toBe('urlaub.jpg')
    expect((nutzlast.datei as { original: { id: string } }).original.id).toBe('d'.repeat(32))
    expect(gesendet.expected_revision).toBe(11)
    vi.restoreAllMocks()
  })
})

describe('Tresor: zu lange Einträge', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('speichert nichts, was der Server nicht annimmt', async () => {
    // Bis 02.10.2026 ging ein Umschlag über 1 MiB in die Warteschlange, und
    // jeder folgende Abgleich scheiterte mit 422.
    localStorage.clear()
    vi.restoreAllMocks()
    const userKey = await userKeyAnlegen()
    await tresorVomServer(userKey, { service: 'Notiz', category: 'secure_note', notes: 'kurz', createdAt: 1, updatedAt: 10 })
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))
    const vorher = useVaultStore.getState().items.find((i) => i.id === EINTRAG)!

    await expect(useVaultStore.getState().aendern(EINTRAG, { notes: 'x'.repeat(1_100_000) })).rejects.toThrow(
      i18n.t('mss.vault.eintragZuGross'),
    )
    expect(warteschlange()).toEqual([])
    expect(useVaultStore.getState().items.find((i) => i.id === EINTRAG)).toEqual(vorher)
  })
})

describe('Tresor: Konflikt, während ein Abgleich läuft', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('übernimmt die Fassung des anderen Geräts und kreist nicht', async () => {
    // Bis 02.10.2026: wurde während der Anfrage noch einmal gespeichert, galt
    // die Fassung des anderen Geräts als Rücksprung, ging verloren, und der
    // Konflikt kam bei jedem Abgleich wieder.
    localStorage.clear()
    vi.restoreAllMocks()
    const userKey = await userKeyAnlegen()
    const kopf = (id: string) => ({ id: id.repeat(32), groesse: gepolsterteGroesse(10), echt: 10, schluessel: 'k', loeschen: id.repeat(64) })
    const alt = { typ: 'image/jpeg', original: kopf('a'), vorschau: kopf('b'), miniatur: kopf('c') }
    await tresorVomServer(userKey, { service: 'bild.jpg', category: 'datei', datei: alt, createdAt: 1, updatedAt: 10 }, 10)
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))
    await useVaultStore.getState().aendern(EINTRAG, { service: 'eins.jpg' })
    await vi.waitFor(() => expect(useVaultStore.getState().syncStatus).toBe('offline'))
    vi.restoreAllMocks()

    // Gerät B hat inzwischen den Inhalt ersetzt, Revision 11.
    const neu = { typ: 'image/jpeg', original: kopf('d'), vorschau: kopf('e'), miniatur: kopf('f'), frueher: [{ ...alt, ersetzt: 20 }] }
    const vonB = await encryptVaultEntry({ service: 'bild.jpg', category: 'datei', datei: neu, createdAt: 1, updatedAt: 20 }, userKey, EINTRAG)
    const antwort = (daten: unknown) => ({ ok: true, status: 200, json: async () => daten }) as Response
    const gesendet: VaultBlindSyncPayload[] = []
    let freigeben = () => {}
    let revision = 11
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => {
      const body = JSON.parse(String(init?.body)) as VaultBlindSyncPayload
      gesendet.push(body)
      if (gesendet.length === 1) {
        await new Promise<void>((weiter) => (freigeben = weiter))
        const zeile = { id: EINTRAG, ciphertext: vonB, revision: 11, is_deleted: false, updated_at: '2026-09-30T00:00:00Z' }
        return antwort({ server_revision: 11, conflicts: [EINTRAG], entries: [zeile] })
      }
      // Wie der echte Server: angenommen wird nur, was auf Revision 11 aufsetzt.
      const m = body.mutations.find((x) => x.id === EINTRAG)
      if (m && m.expected_revision !== revision) return antwort({ server_revision: revision, conflicts: [EINTRAG], entries: [] })
      const entries = m ? [{ ...m, revision: ++revision, updated_at: '2026-09-30T00:00:00Z' }] : []
      return antwort({ server_revision: revision, entries })
    })

    const lauf = useVaultStore.getState().syncWithServer()
    await vi.waitFor(() => expect(gesendet).toHaveLength(1))
    await useVaultStore.getState().aendern(EINTRAG, { service: 'zwei.jpg' })
    freigeben()
    await lauf

    await vi.waitFor(() => expect(warteschlange()).toEqual([]))
    const item = useVaultStore.getState().items.find((i) => i.id === EINTRAG)!
    expect(item.service).toBe('zwei.jpg')
    expect(item.datei?.original.id).toBe('d'.repeat(32))
    expect(gesendet.length).toBeLessThan(6)
  })
})

describe('Tresor: auf einem anderen Gerät zurückgesetzt', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('hört bei 410 auf, behält alles hier und lädt nichts hoch', async () => {
    const userKey = await userKeyAnlegen()
    await tresorVomServer(userKey, { service: 'Bank', username: 'ich', password: 'geheim', category: 'login', createdAt: 1, updatedAt: 10 })
    const anfragen = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 410,
      json: async () => ({ detail: { code: 'VAULT_ZURUECKGESETZT', message: 'errors.vault_zurueckgesetzt' } }),
    } as Response)

    await useVaultStore.getState().toggleFavorite(EINTRAG)
    await vi.waitFor(() => expect(useVaultStore.getState().zurueckgesetzt).toBe(true))
    expect(useVaultStore.getState().syncStatus).toBe('error')
    expect(useVaultStore.getState().items.map((i) => i.id)).toEqual([EINTRAG])
    expect(warteschlange()).toHaveLength(1)

    // Kein neuer Anlauf, wenn das Netz wiederkommt: hochgeladen würde der gelöschte Tresor.
    const bisher = anfragen.mock.calls.length
    window.dispatchEvent(new Event('online'))
    await new Promise((r) => setTimeout(r, 10))
    expect(anfragen.mock.calls.length).toBe(bisher)

    // Erst eine neue Sitzung vergisst die Meldung.
    useVaultStore.getState().lock()
    expect(useVaultStore.getState().zurueckgesetzt).toBe(false)
  })
})
