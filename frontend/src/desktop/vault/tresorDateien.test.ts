/**
 * Dateien im Tresor, von der Auswahl bis zum Löschen, gegen einen
 * nachgebauten Server, der sich merkt, was er zu sehen bekommt.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import 'fake-indexeddb/auto'
import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import { useVaultStore, type VaultBlindSyncPayload } from './vaultStore'
import { ablageDb, ablageLaden, ablageSchliessen, BLOB_CACHE, OFFLINE, UPLOAD_CHUNKS, UPLOADS } from './tresorAblage'
import {
  angeheftet,
  blobLesen,
  loeschungenAbarbeiten,
  miniaturenLesen,
  offlineAnheften,
  offlineLoesen,
  uploadsFortsetzen,
  useTresorUploads,
  VERSIONEN,
  ZULETZT_GRENZE,
} from './tresorDateien'
import { miniaturFreigeben, miniaturHolen } from './tresorMiniaturen'
import { CHUNK_KLARTEXT, CHUNK_UEBERHANG } from './tresorDatei'
import { aufGeraetSpeichern } from './tresorAnzeige'

const tauriKern = vi.hoisted(() => ({ invoke: vi.fn() }))
vi.mock('@tauri-apps/api/core', () => tauriKern)

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

/** Miniaturen, wie sie der Bildleser für Fotos erzeugen würde (jsdom kann keine Bilder). */
const bilder = vi.hoisted(() => ({ an: false }))
vi.mock('./tresorBilder', () => ({
  bildAngaben: vi.fn(async (datei: File) =>
    bilder.an && datei.type.startsWith('image/') ? { miniatur: new TextEncoder().encode(`mini:${datei.name}`) } : {},
  ),
}))

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
    /** Hält jeden hochgeladenen Chunk so lange auf (ms). */
    verzoegerung: 0,
    /** Der erste Upload eines zweiten Chunks bricht ab wie ein Netzfehler. */
    abbruchBeiChunk1: false,
    puts: [] as string[],
    kleinAnfragen: 0,
    revision: 10,
    eintraege: new Map<string, { ciphertext: string; revision: number; is_deleted: boolean }>(),
    konflikte: new Set<string>(),
    blobs: new Map<string, ServerBlob>(),
    geloescht: [] as string[],
    /** Alles, was über die Leitung kam, als Text: für den Nachweis, dass Namen fehlen. */
    mitschnitt: [] as Uint8Array[],
    /** Der Server kennt den Bucket des Kontos nicht, bis `/salt` ihn meldet. */
    ohneZuordnung: false,
    saltMeldungen: [] as { kdf_salt: string; bucket_id: string }[],
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

    if (pfad === '/api/vault/salt' && methode === 'POST') {
      const body = JSON.parse(String(koerper))
      server.saltMeldungen.push(body)
      server.ohneZuordnung = false
      return json({ kdf_salt: body.kdf_salt, bucket_id: body.bucket_id, has_vault: true })
    }
    if (pfad.startsWith('/api/vault/blobs') && server.ohneZuordnung) {
      return json({ detail: { code: 'VAULT_BUCKET_UNBEKANNT', message: 'errors.vault_bucket_unbekannt' } }, 409)
    }

    if (pfad === '/api/vault/blobs/klein' && methode === 'POST') {
      server.kleinAnfragen += 1
      const { ids } = JSON.parse(String(koerper)) as { ids: string[] }
      const teile: number[] = []
      for (const id of ids) {
        const b = server.blobs.get(id)
        const daten = b && b.state === 'fertig' && b.chunk_count === 1 ? b.chunks.get(0)! : new Uint8Array(0)
        teile.push(daten.length >>> 24, (daten.length >> 16) & 0xff, (daten.length >> 8) & 0xff, daten.length & 0xff, ...daten)
      }
      return new Response(new Uint8Array(teile) as BodyInit, { status: 200 })
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
        if (index === 1 && server.abbruchBeiChunk1) {
          server.abbruchBeiChunk1 = false
          throw new TypeError('Failed to fetch')
        }
        server.puts.push(`${id}:${index}`)
        if (server.verzoegerung) await new Promise((r) => setTimeout(r, server.verzoegerung))
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
    bilder.an = false
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

  it('meldet den Bucket neu, wenn der Server ihn nicht kennt, und lädt dann hoch', async () => {
    // Die Dateien hängen am Bucket, nicht am Konto. Scheiterte das Melden beim
    // Einrichten, antworten die Datei-Routen 409; dann einmal nachmelden.
    const server = serverStarten()
    server.ohneZuordnung = true
    localStorage.setItem('mss:vault_salt', 'ab'.repeat(16))
    await tresorOeffnen()

    await useVaultStore.getState().dateiHinzufuegen(foto(1000, 'a.jpg'))
    await useVaultStore.getState().dateiHinzufuegen(foto(1000, 'b.jpg'))
    await allesErledigt()

    expect(server.saltMeldungen).toEqual([{ kdf_salt: 'ab'.repeat(16), bucket_id: BUCKET, auth_token: null }])
    expect(server.blobs.size).toBe(6)
    expect([...server.blobs.values()].every((b) => b.state === 'fertig')).toBe(true)
  })

  it('lädt auch hoch, was während eines laufenden Uploads dazukommt', async () => {
    const server = serverStarten()
    server.verzoegerung = 150
    await tresorOeffnen()

    await useVaultStore.getState().dateiHinzufuegen(foto(9 * 1024 * 1024, 'gross.jpg'))
    // Der Upload der ersten läuft noch, wenn die weiteren abgelegt sind.
    await useVaultStore.getState().dateiHinzufuegen(foto(1000, 'klein-1.jpg'))
    await useVaultStore.getState().dateiHinzufuegen(foto(1000, 'klein-2.jpg'))
    await vi.waitFor(async () => expect(await zeilen(UPLOADS)).toEqual([]), { timeout: 5000 })

    expect(server.blobs.size).toBe(9)
    expect([...server.blobs.values()].every((b) => b.state === 'fertig')).toBe(true)
  })

  it('setzt einen abgebrochenen Upload fort, ohne Angekommenes erneut zu senden', async () => {
    const server = serverStarten()
    server.abbruchBeiChunk1 = true
    await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(foto(5 * 1024 * 1024))
    await vi.waitFor(() => expect(server.puts.length).toBeGreaterThan(0))
    await new Promise((r) => setTimeout(r, 50))

    await uploadsFortsetzen(BUCKET)
    await vi.waitFor(async () => expect(await zeilen(UPLOADS)).toEqual([]))

    const original = useVaultStore.getState().items.find((i) => i.id === id)!.datei!.original.id
    expect(server.puts.filter((p) => p.startsWith(original))).toEqual([`${original}:0`, `${original}:1`])
    expect(server.blobs.get(original)!.state).toBe('fertig')
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

  describe('Miniaturen', () => {
    async function dreiFotos() {
      bilder.an = true
      const server = serverStarten()
      const userKey = await tresorOeffnen()
      for (const name of ['a.jpg', 'b.jpg', 'c.jpg']) await useVaultStore.getState().dateiHinzufuegen(foto(1000, name))
      await allesErledigt()
      const items = useVaultStore.getState().items.filter((i) => i.datei)
      const anfragen = items.map((i) => ({ kopf: i.datei!.miniatur, eintragId: i.id }))
      return { server, userKey, items, anfragen }
    }

    async function cacheLeeren() {
      const db = (await ablageDb())!
      await new Promise<void>((ok) => {
        const tx = db.transaction(BLOB_CACHE, 'readwrite')
        tx.objectStore(BLOB_CACHE).clear()
        tx.oncomplete = () => ok()
      })
    }

    const text = (daten?: Uint8Array) => (daten ? new TextDecoder().decode(daten) : undefined)

    it('holt fehlende in einer Anfrage, behält sie und zeigt sie offline', async () => {
      const { server, userKey, items, anfragen } = await dreiFotos()
      await cacheLeeren() // wie auf einem zweiten Gerät

      const geladen = await miniaturenLesen(anfragen, userKey)
      expect(server.kleinAnfragen).toBe(1)
      for (const item of items) expect(text(geladen.get(item.datei!.miniatur.id))).toBe(`mini:${item.service}`)

      server.offline = true
      const offline = await miniaturenLesen(anfragen, userKey)
      expect(offline.size).toBe(3)
      expect(server.kleinAnfragen).toBe(1)
    })

    it('zeigt keine Miniatur, die der Server einem anderen Eintrag untergeschoben hat', async () => {
      const { server, userKey, items, anfragen } = await dreiFotos()
      await cacheLeeren()
      const [a, b] = items.map((i) => server.blobs.get(i.datei!.miniatur.id)!)
      const tausch = a.chunks.get(0)!
      a.chunks.set(0, b.chunks.get(0)!)
      b.chunks.set(0, tausch)

      const geladen = await miniaturenLesen(anfragen, userKey)
      expect(geladen.has(items[0].datei!.miniatur.id)).toBe(false)
      expect(geladen.has(items[1].datei!.miniatur.id)).toBe(false)
      expect(text(geladen.get(items[2].datei!.miniatur.id))).toBe(`mini:${items[2].service}`)
    })

    it('lädt ohne Schlüssel nur vor und entschlüsselt dabei nichts', async () => {
      const { server, anfragen } = await dreiFotos()
      await cacheLeeren()
      expect(await miniaturenLesen(anfragen, null)).toEqual(new Map())
      expect(await zeilen(BLOB_CACHE)).toHaveLength(3)
      // Was schon da ist, wird nicht noch einmal geholt.
      await miniaturenLesen(anfragen, null)
      expect(server.kleinAnfragen).toBe(1)
    })

    it('bündelt gleichzeitige Kacheln und fängt nach dem Sperren neu an', async () => {
      const { server, userKey, anfragen } = await dreiFotos()
      await cacheLeeren()
      const urls = await Promise.all(anfragen.map((a) => miniaturHolen(a.kopf, a.eintragId, userKey)))
      expect(server.kleinAnfragen).toBe(1)
      expect(urls.every((u) => typeof u === 'string' && u.startsWith('blob:'))).toBe(true)
      // Dieselbe Miniatur ein zweites Mal kommt aus dem Speicher.
      expect(await miniaturHolen(anfragen[0].kopf, anfragen[0].eintragId, userKey)).toBe(urls[0])
      for (const a of anfragen) miniaturFreigeben(a.kopf.id)

      const widerruf = vi.spyOn(URL, 'revokeObjectURL')
      useVaultStore.getState().lock()
      expect(widerruf).toHaveBeenCalledWith(urls[0])
      const neu = await tresorOeffnen()
      const danach = await miniaturHolen(anfragen[0].kopf, anfragen[0].eintragId, neu)
      expect(danach).not.toBe(urls[0])
      expect(danach).toMatch(/^blob:/)
    })
  })

  describe('Bearbeiten', () => {
    const text = (inhalt: string) => new Blob([inhalt], { type: 'text/plain' })
    const lesen = async (userKey: CryptoKey, id: string) => {
      const item = useVaultStore.getState().items.find((i) => i.id === id)!
      return (await blobLesen(item.datei!.original, id, userKey, item.datei!.typ)).text()
    }

    it('behält frühere Fassungen und löscht erst, was über die Grenze fällt', async () => {
      const server = serverStarten()
      const userKey = await tresorOeffnen()
      const id = await useVaultStore.getState().dateiHinzufuegen(new File(['Fassung 0'], 'notiz.txt', { type: 'text/plain' }))
      await allesErledigt()
      const erste = useVaultStore.getState().items.find((i) => i.id === id)!.datei!

      for (let n = 1; n <= VERSIONEN; n++) {
        await useVaultStore.getState().dateiErsetzen(id, text(`Fassung ${n}`))
        await allesErledigt()
      }
      expect(server.geloescht).toEqual([])
      const datei = useVaultStore.getState().items.find((i) => i.id === id)!.datei!
      expect(datei.frueher).toHaveLength(VERSIONEN)
      expect(datei.frueher![VERSIONEN - 1].original.id).toBe(erste.original.id)
      expect(await lesen(userKey, id)).toBe(`Fassung ${VERSIONEN}`)

      // Offline bearbeitet: die älteste Fassung fällt heraus, bleibt aber, bis der Server die neue hat.
      server.offline = true
      await useVaultStore.getState().dateiErsetzen(id, text('Fassung neu'))
      await vi.waitFor(() => expect(useVaultStore.getState().syncStatus).toBe('offline'))
      await loeschungenAbarbeiten(BUCKET)
      expect(server.geloescht).toEqual([])

      server.offline = false
      await useVaultStore.getState().syncWithServer()
      await allesErledigt()
      await vi.waitFor(() => expect(new Set(server.geloescht)).toEqual(new Set([erste.original.id, erste.vorschau.id, erste.miniatur.id])))
      expect(await lesen(userKey, id)).toBe('Fassung neu')
    })

    it('holt eine frühere Fassung als neue zurück, die aktuelle bleibt erhalten', async () => {
      serverStarten()
      const userKey = await tresorOeffnen()
      const id = await useVaultStore.getState().dateiHinzufuegen(new File(['erste'], 'notiz.txt', { type: 'text/plain' }))
      await allesErledigt()
      await useVaultStore.getState().dateiErsetzen(id, text('zweite'))
      await allesErledigt()
      const alt = useVaultStore.getState().items.find((i) => i.id === id)!.datei!.frueher![0]

      await useVaultStore.getState().fassungZurueckholen(id, alt.original.id)
      await allesErledigt()
      expect(await lesen(userKey, id)).toBe('erste')
      const frueher = useVaultStore.getState().items.find((i) => i.id === id)!.datei!.frueher!
      const texte = await Promise.all(frueher.map(async (v) => (await blobLesen(v.original, id, userKey, v.typ)).text()))
      expect(texte).toEqual(['zweite', 'erste'])

      await expect(useVaultStore.getState().fassungZurueckholen(id, 'gibt-es-nicht')).rejects.toThrow()
    })

    it('löscht mit der Datei auch alle früheren Fassungen', async () => {
      const server = serverStarten()
      await tresorOeffnen()
      const id = await useVaultStore.getState().dateiHinzufuegen(new File(['a'], 'a.txt', { type: 'text/plain' }))
      await allesErledigt()
      await useVaultStore.getState().dateiErsetzen(id, text('b'))
      await allesErledigt()
      const alle = [...server.blobs.keys()]
      expect(alle).toHaveLength(6)

      await useVaultStore.getState().deleteItem(id)
      await allesErledigt()
      await vi.waitFor(() => expect(new Set(server.geloescht)).toEqual(new Set(alle)))
    })
  })

  describe('Offline verfügbar', () => {
    const originalImCache = async (blobId: string) =>
      ((await zeilen(BLOB_CACHE)) as { blobId: string }[]).some((z) => z.blobId === blobId)

    async function hochgeladen(inhalt = 'Vertrag', name = 'vertrag.txt') {
      const server = serverStarten()
      const userKey = await tresorOeffnen()
      const id = await useVaultStore.getState().dateiHinzufuegen(new File([inhalt], name, { type: 'text/plain' }))
      await allesErledigt()
      const item = () => useVaultStore.getState().items.find((i) => i.id === id)!
      return { server, userKey, id, item }
    }

    it('hält ein angeheftetes Original ohne Netz lesbar, bis es gelöst wird', async () => {
      const { server, userKey, id, item } = await hochgeladen()
      const original = item().datei!.original
      expect(await originalImCache(original.id)).toBe(false)

      await offlineAnheften(original)
      expect(await angeheftet([original.id, 'f'.repeat(32)])).toEqual(new Set([original.id]))
      server.offline = true
      expect(await (await blobLesen(original, id, userKey, 'text/plain')).text()).toBe('Vertrag')

      await offlineLoesen(original.id)
      expect(await angeheftet([original.id])).toEqual(new Set())
      expect(await originalImCache(original.id)).toBe(false)
      await expect(blobLesen(original, id, userKey, 'text/plain')).rejects.toThrow()
    })

    it('behält zuletzt Geöffnetes bis zur Grenze, die ältesten zuerst raus, Angeheftetes nie', async () => {
      const { server, userKey, id, item } = await hochgeladen()
      // Drei ältere Einträge, zusammen über der Grenze; der älteste ist angeheftet.
      const gross = Math.floor(ZULETZT_GRENZE / 2)
      const db = (await ablageDb())!
      await new Promise<void>((ok) => {
        const tx = db.transaction([OFFLINE, BLOB_CACHE], 'readwrite')
        const alt = [
          { blobId: 'a'.repeat(32), angeheftet: true, zuletzt: 1, bytes: gross },
          { blobId: 'b'.repeat(32), angeheftet: false, zuletzt: 2, bytes: gross },
          { blobId: 'c'.repeat(32), angeheftet: false, zuletzt: 3, bytes: gross },
        ]
        for (const z of alt) {
          tx.objectStore(OFFLINE).put(z)
          tx.objectStore(BLOB_CACHE).put({ blobId: z.blobId, index: 0, daten: new Uint8Array(1) })
        }
        tx.oncomplete = () => ok()
      })

      const original = item().datei!.original
      await blobLesen(original, id, userKey, 'text/plain', { zuletzt: true })

      const offline = ((await zeilen(OFFLINE)) as { blobId: string }[]).map((z) => z.blobId)
      expect(new Set(offline)).toEqual(new Set(['a'.repeat(32), 'c'.repeat(32), original.id]))
      expect(await originalImCache('b'.repeat(32))).toBe(false)
      expect(await originalImCache('a'.repeat(32))).toBe(true)
      server.offline = true
      expect(await (await blobLesen(original, id, userKey, 'text/plain')).text()).toBe('Vertrag')
    })

    it('hält eine bearbeitete Datei weiter angeheftet und gibt die alte Fassung frei', async () => {
      const { server, userKey, id, item } = await hochgeladen('eins')
      const alt = item().datei!.original
      await offlineAnheften(alt)

      await useVaultStore.getState().dateiErsetzen(id, new Blob(['zwei'], { type: 'text/plain' }))
      await allesErledigt()
      const neu = item().datei!.original
      expect(neu.id).not.toBe(alt.id)
      expect(await angeheftet([alt.id, neu.id])).toEqual(new Set([neu.id]))
      expect(await originalImCache(alt.id)).toBe(false)

      server.offline = true
      expect(await (await blobLesen(neu, id, userKey, 'text/plain')).text()).toBe('zwei')
    })

    it('nimmt eine gelöschte Datei auch vom Gerät', async () => {
      const { server, id, item } = await hochgeladen()
      const original = item().datei!.original
      await offlineAnheften(original)

      await useVaultStore.getState().deleteItem(id)
      await allesErledigt()
      await vi.waitFor(() => expect(server.geloescht).toContain(original.id))
      expect(await angeheftet([original.id])).toEqual(new Set())
      expect(await originalImCache(original.id)).toBe(false)
    })
  })
  describe('Auf dem Gerät speichern', () => {
    beforeEach(() => {
      ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    })
    afterEach(() => {
      delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
      tauriKern.invoke.mockReset()
    })

    /**
     * Rust nachgebaut: merkt sich, was ankommt. Die Teile kommen als Base64 im
     * JSON, weil Android keinen rohen IPC-Körper kennt.
     */
    function rust(antwortStart: number | null, beiTeil?: (anzahl: number) => void) {
      const geschrieben: Uint8Array[] = []
      const ende: unknown[] = []
      tauriKern.invoke.mockImplementation(async (befehl: string, nutzlast: unknown) => {
        if (befehl === 'datei_speichern_start') return antwortStart
        if (befehl === 'datei_speichern_teil') {
          const { vorgang, teil } = nutzlast as { vorgang: number; teil: string }
          expect(vorgang).toBe(antwortStart)
          expect(JSON.parse(JSON.stringify(nutzlast))).toEqual(nutzlast)
          geschrieben.push(Uint8Array.from(atob(teil), (z) => z.charCodeAt(0)))
          beiTeil?.(geschrieben.length)
          return null
        }
        if (befehl === 'datei_speichern_ende') {
          ende.push(nutzlast)
          return null
        }
        throw new Error(befehl)
      })
      return { geschrieben, ende }
    }

    it('schreibt Chunk für Chunk als Base64', async () => {
      serverStarten()
      const userKey = await tresorOeffnen()
      const datei = foto(CHUNK_KLARTEXT + 4321, 'gross.jpg')
      const id = await useVaultStore.getState().dateiHinzufuegen(datei)
      await allesErledigt()
      const item = useVaultStore.getState().items.find((i) => i.id === id)!
      const r = rust(7)

      expect(await aufGeraetSpeichern(item.datei!.original, id, userKey, item.service, item.datei!.typ)).toBe(true)

      expect(tauriKern.invoke).toHaveBeenCalledWith('datei_speichern_start', { name: 'gross.jpg' })
      expect(r.geschrieben).toHaveLength(2)
      const zusammen = new Uint8Array(await new Blob(r.geschrieben as BlobPart[]).arrayBuffer())
      const echt = new Uint8Array(await datei.arrayBuffer())
      expect(zusammen.length).toBe(echt.length)
      expect(zusammen.every((x, i) => x === echt[i])).toBe(true)
      expect(r.ende).toEqual([{ vorgang: 7, abbrechen: false }])
    })

    it('schreibt nichts, wenn der Dialog abgebrochen wird', async () => {
      const { userKey, id, item } = await (async () => {
        serverStarten()
        const userKey = await tresorOeffnen()
        const id = await useVaultStore.getState().dateiHinzufuegen(new File(['Vertrag'], 'v.txt', { type: 'text/plain' }))
        await allesErledigt()
        return { userKey, id, item: useVaultStore.getState().items.find((i) => i.id === id)! }
      })()
      const r = rust(null)
      expect(await aufGeraetSpeichern(item.datei!.original, id, userKey, item.service, 'text/plain')).toBe(false)
      expect(r.geschrieben).toEqual([])
      expect(r.ende).toEqual([])
    })

    it('bricht beim Sperren ab und lässt Rust die halbe Datei verwerfen', async () => {
      serverStarten()
      const userKey = await tresorOeffnen()
      const id = await useVaultStore.getState().dateiHinzufuegen(foto(2 * CHUNK_KLARTEXT + 5, 'drei.jpg'))
      await allesErledigt()
      const item = useVaultStore.getState().items.find((i) => i.id === id)!
      const r = rust(3, (anzahl) => {
        if (anzahl === 1) useVaultStore.setState({ userKey: null, isUnlocked: false })
      })

      await expect(aufGeraetSpeichern(item.datei!.original, id, userKey, item.service, item.datei!.typ)).rejects.toThrow()
      expect(r.geschrieben).toHaveLength(1)
      expect(r.ende).toEqual([{ vorgang: 3, abbrechen: true }])
    })
  })
})
