/**
 * Dateien im Tresor, von der Auswahl bis zum Löschen, gegen einen
 * nachgebauten Server, der sich merkt, was er zu sehen bekommt.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import 'fake-indexeddb/auto'
import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import { useVaultStore, type VaultBlindSyncPayload } from './vaultStore'
import { ablageDb, ablageLaden, ablageSchliessen, BLOB_CACHE, UPLOAD_CHUNKS, UPLOADS } from './tresorAblage'
import { blobLesen, loeschungenAbarbeiten, uploadsFortsetzen, useTresorUploads } from './tresorDateien'
import { CHUNK_KLARTEXT, CHUNK_UEBERHANG } from './tresorDatei'

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

const BUCKET = 'e'.repeat(64)

interface ServerBlob {
  chunk_count: number
  bytes_total: number
  delete_verifier: string
  state: 'offen' | 'fertig' | 'geloescht'
  chunks: Map<number, Uint8Array>
}

async function sha256Hex(daten: Uint8Array): Promise<string> {
  const h = new Uint8Array(await crypto.subtle.digest('SHA-256', daten as BufferSource))
  return Array.from(h, (b) => b.toString(16).padStart(2, '0')).join('')
}

function json(wert: unknown, status = 200): Response {
  return new Response(JSON.stringify(wert), { status, headers: { 'Content-Type': 'application/json' } })
}

/**
 * Ein Server mit Tresor-Sync und Blob-Speicher. `offline` lässt jede Anfrage
 * scheitern, `konflikte` lehnt Tombstones für diese Einträge ab.
 */
function serverStarten() {
  const server = {
    offline: false,
    speicherVoll: false,
    revision: 10,
    eintraege: new Map<string, { ciphertext: string; revision: number; is_deleted: boolean }>(),
    konflikte: new Set<string>(),
    blobs: new Map<string, ServerBlob>(),
    geloescht: [] as string[],
    /** Alles, was über die Leitung kam, als Text: für den Nachweis, dass Namen fehlen. */
    mitschnitt: [] as Uint8Array[],
  }
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (eingabe, init) => {
    if (server.offline) throw new TypeError('Failed to fetch')
    const url = new URL(String(eingabe), 'http://panel.test')
    const pfad = url.pathname
    const methode = (init?.method ?? 'GET').toUpperCase()
    const koerper = init?.body
    if (typeof koerper === 'string') server.mitschnitt.push(new TextEncoder().encode(koerper))
    else if (koerper instanceof Uint8Array) server.mitschnitt.push(koerper)

    if (pfad.endsWith('/sync') || pfad.endsWith('/blind-sync')) {
      const body = JSON.parse(String(koerper)) as VaultBlindSyncPayload
      const conflicts: string[] = []
      const entries = []
      for (const m of body.mutations) {
        if (m.is_deleted && server.konflikte.has(m.id)) {
          conflicts.push(m.id)
          continue
        }
        server.revision += 1
        server.eintraege.set(m.id, { ciphertext: m.ciphertext, revision: server.revision, is_deleted: m.is_deleted })
        entries.push({ ...m, revision: server.revision, updated_at: '2026-09-30T00:00:00Z' })
      }
      return json({ server_revision: server.revision, entries, conflicts })
    }

    const blob = pfad.match(/\/api\/vault\/blobs\/([0-9a-f]{32})(\/.*)?$/)
    if (pfad === '/api/vault/blobs' && methode === 'POST') {
      const b = JSON.parse(String(koerper))
      if (server.speicherVoll) return json({ detail: 'voll' }, 507)
      if (server.blobs.has(b.id)) return json({ detail: 'vergeben' }, 409)
      server.blobs.set(b.id, { ...b, state: 'offen', chunks: new Map() })
      return json({ id: b.id }, 201)
    }
    if (blob) {
      const [, id, rest = ''] = blob
      const eintrag = server.blobs.get(id)
      if (!eintrag) return json({ detail: 'nicht gefunden' }, 404)
      if (rest === '/status') {
        return json({ state: eintrag.state, chunk_count: eintrag.chunk_count, vorhanden: [...eintrag.chunks.keys()] })
      }
      if (rest === '/fertig') {
        if (eintrag.chunks.size !== eintrag.chunk_count) return json({ detail: 'fehlt' }, 409)
        eintrag.state = 'fertig'
        return json({ state: 'fertig' })
      }
      const chunk = rest.match(/^\/chunks\/(\d+)$/)
      if (chunk && methode === 'PUT') {
        const index = Number(chunk[1])
        const voll = CHUNK_KLARTEXT + CHUNK_UEBERHANG
        const erwartet = index < eintrag.chunk_count - 1 ? voll : eintrag.bytes_total - (eintrag.chunk_count - 1) * voll
        const daten = koerper as Uint8Array
        if (daten.length !== erwartet) return json({ detail: 'Länge' }, 422)
        eintrag.chunks.set(index, daten.slice())
        return new Response(null, { status: 204 })
      }
      if (chunk && methode === 'GET') {
        if (eintrag.state !== 'fertig') return json({ detail: 'nicht gefunden' }, 404)
        return new Response(eintrag.chunks.get(Number(chunk[1])) as BodyInit, { status: 200 })
      }
      if (rest === '' && methode === 'DELETE') {
        const { schluessel } = JSON.parse(String(koerper))
        const bytes = new Uint8Array(schluessel.match(/../g).map((h: string) => parseInt(h, 16)))
        if ((await sha256Hex(bytes)) !== eintrag.delete_verifier) return json({ detail: 'falsch' }, 403)
        eintrag.state = 'geloescht'
        server.geloescht.push(id)
        return json({ state: 'geloescht' })
      }
    }
    return json({ detail: `unbekannt: ${methode} ${pfad}` }, 500)
  })
  return server
}

async function tresorOeffnen() {
  const userKey = await crypto.subtle.importKey('raw', new Uint8Array(32).fill(5), { name: 'AES-GCM', length: 256 }, false, [
    'encrypt',
    'decrypt',
  ])
  await ablageLaden(BUCKET)
  useVaultStore.setState({ userKey, bucketId: BUCKET, bucketAuthToken: null, isUnlocked: true, syncStatus: 'synced', items: [] })
  return userKey
}

async function zeilen(store: string): Promise<unknown[]> {
  const db = (await ablageDb())!
  return new Promise((resolve, reject) => {
    const req = db.transaction(store).objectStore(store).getAll()
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

async function allesErledigt() {
  await vi.waitFor(async () => {
    expect(useVaultStore.getState().syncStatus).toBe('synced')
    expect(await zeilen(UPLOADS)).toEqual([])
  })
}

/** Eine Datei mit JPEG-Kopf und erkennbarem Inhalt. */
function foto(groesse: number, name = 'urlaub-am-strand.jpg'): File {
  const daten = new Uint8Array(groesse)
  daten.set([0xff, 0xd8, 0xff, 0xe0])
  for (let i = 4; i < groesse; i++) daten[i] = i % 251
  return new File([daten], name, { type: 'image/jpeg', lastModified: 1_700_000_000_000 })
}

function enthaelt(heuhaufen: Uint8Array, nadel: Uint8Array): boolean {
  outer: for (let i = 0; i + nadel.length <= heuhaufen.length; i++) {
    for (let j = 0; j < nadel.length; j++) if (heuhaufen[i + j] !== nadel[j]) continue outer
    return true
  }
  return false
}

describe('Tresor-Dateien', () => {
  beforeEach(() => {
    ablageSchliessen()
    localStorage.clear()
    globalThis.indexedDB = new IDBFactory()
    setzeAngemeldetesKonto(1)
    vi.restoreAllMocks()
    useTresorUploads.setState({ je: {} })
  })

  afterEach(() => {
    ablageSchliessen()
    setzeAngemeldetesKonto(null)
    vi.restoreAllMocks()
  })

  it('lädt hoch, und der Server erfährt weder Name noch Typ noch Inhalt', async () => {
    const server = serverStarten()
    const userKey = await tresorOeffnen()
    const datei = foto(5 * 1024 * 1024 + 17)

    const id = await useVaultStore.getState().dateiHinzufuegen(datei)
    await allesErledigt()

    const item = useVaultStore.getState().items.find((i) => i.id === id)!
    expect(item.category).toBe('datei')
    expect(item.service).toBe('urlaub-am-strand.jpg')
    expect(item.datei!.typ).toBe('image/jpeg')
    expect(item.datei!.original.echt).toBe(datei.size)

    // Drei Blobs, alle fertig; das Original gepolstert.
    const blobs = [...server.blobs.values()]
    expect(blobs).toHaveLength(3)
    expect(blobs.every((b) => b.state === 'fertig')).toBe(true)
    const original = server.blobs.get(item.datei!.original.id)!
    expect(original.bytes_total).toBeGreaterThan(datei.size + 2 * CHUNK_UEBERHANG)

    // Nichts, was über die Leitung ging, enthält Namen, Typ oder JPEG-Signatur.
    const verboten = ['urlaub-am-strand', 'image/jpeg'].map((s) => new TextEncoder().encode(s))
    for (const stueck of server.mitschnitt) {
      for (const nadel of verboten) expect(enthaelt(stueck, nadel)).toBe(false)
      expect(stueck[0] === 0xff && stueck[1] === 0xd8 && stueck[2] === 0xff).toBe(false)
    }

    // Die Upload-Ablage ist leer, Miniatur und Vorschau bleiben im Cache.
    expect(await zeilen(UPLOAD_CHUNKS)).toEqual([])
    const cache = (await zeilen(BLOB_CACHE)) as { blobId: string }[]
    expect(new Set(cache.map((c) => c.blobId))).toEqual(new Set([item.datei!.vorschau.id, item.datei!.miniatur.id]))

    // Vom Server zurück entschlüsselt ist es dieselbe Datei.
    const zurueck = await blobLesen(item.datei!.original, id, userKey, item.datei!.typ)
    const a = new Uint8Array(await zurueck.arrayBuffer())
    const b = new Uint8Array(await datei.arrayBuffer())
    expect(a.length).toBe(b.length)
    expect(a.every((x, i) => x === b[i])).toBe(true)
    expect(zurueck.type).toBe('image/jpeg')
  })

  it('wartet offline und lädt nach einem Neustart hoch', async () => {
    const server = serverStarten()
    server.offline = true
    await tresorOeffnen()

    const id = await useVaultStore.getState().dateiHinzufuegen(foto(100_000))
    await vi.waitFor(() => expect(useVaultStore.getState().syncStatus).toBe('offline'))
    expect(await zeilen(UPLOADS)).toHaveLength(3)

    // Neustart: Ablage neu laden, wieder online.
    ablageSchliessen()
    await ablageLaden(BUCKET)
    server.offline = false
    await useVaultStore.getState().syncWithServer()
    await allesErledigt()

    expect(server.eintraege.has(id)).toBe(true)
    expect([...server.blobs.values()].every((b) => b.state === 'fertig')).toBe(true)
  })

  it('verwirft die Vorbereitung, wenn der Tresor währenddessen gesperrt wird', async () => {
    serverStarten()
    await tresorOeffnen()
    const datei = foto(9 * 1024 * 1024)
    // Sperren, sobald der erste Chunk gelesen wird.
    const slice = datei.slice.bind(datei)
    vi.spyOn(datei, 'slice').mockImplementation((...args) => {
      useVaultStore.getState().lock()
      return slice(...args)
    })

    await expect(useVaultStore.getState().dateiHinzufuegen(datei)).rejects.toThrow()

    expect(await zeilen(UPLOADS)).toEqual([])
    expect(await zeilen(UPLOAD_CHUNKS)).toEqual([])
  })

  it('löscht die Blobs erst, wenn der Server den Tombstone angenommen hat', async () => {
    const server = serverStarten()
    await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(foto(1000))
    await allesErledigt()
    const kopf = useVaultStore.getState().items.find((i) => i.id === id)!.datei!

    server.offline = true
    await useVaultStore.getState().deleteItem(id)
    await vi.waitFor(() => expect(useVaultStore.getState().syncStatus).toBe('offline'))
    expect(server.geloescht).toEqual([])

    // Wieder im Netz, aber der Tombstone wartet noch: nichts wird gelöscht.
    server.offline = false
    await loeschungenAbarbeiten(BUCKET)
    expect(server.geloescht).toEqual([])

    await useVaultStore.getState().syncWithServer()
    await vi.waitFor(() => expect(server.geloescht).toHaveLength(3))
    expect(new Set(server.geloescht)).toEqual(new Set([kopf.original.id, kopf.vorschau.id, kopf.miniatur.id]))
    expect(await zeilen(BLOB_CACHE)).toEqual([])
  })

  it('lässt die Blobs stehen, wenn ein anderes Gerät die Datei inzwischen geändert hat', async () => {
    const server = serverStarten()
    await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(foto(1000))
    await allesErledigt()

    server.konflikte.add(id)
    await useVaultStore.getState().deleteItem(id)
    await vi.waitFor(() => expect(useVaultStore.getState().syncStatus).toBe('synced'))
    await new Promise((r) => setTimeout(r, 30))

    expect(server.geloescht).toEqual([])
    expect([...server.blobs.values()].every((b) => b.state === 'fertig')).toBe(true)
  })

  it('lädt nichts hoch, wenn die Datei gelöscht wurde, bevor sie oben war', async () => {
    const server = serverStarten()
    server.offline = true
    await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(foto(1000))
    await useVaultStore.getState().deleteItem(id)
    await vi.waitFor(() => expect(useVaultStore.getState().syncStatus).toBe('offline'))

    server.offline = false
    await useVaultStore.getState().syncWithServer()
    await vi.waitFor(async () => expect(await zeilen(UPLOADS)).toEqual([]))

    expect([...server.blobs.values()].filter((b) => b.state !== 'geloescht')).toEqual([])
  })

  it('meldet vollen Speicher und behält den Upload für später', async () => {
    const server = serverStarten()
    server.speicherVoll = true
    await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(foto(1000))
    await vi.waitFor(() => expect(useTresorUploads.getState().je[id]?.fehler).toBe('speicherVoll'))
    expect(await zeilen(UPLOADS)).toHaveLength(3)

    server.speicherVoll = false
    await uploadsFortsetzen(BUCKET)
    await vi.waitFor(async () => expect(await zeilen(UPLOADS)).toEqual([]))
    expect(useTresorUploads.getState().je[id]).toBeUndefined()
  })
})
