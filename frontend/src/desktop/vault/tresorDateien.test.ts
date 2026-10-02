/**
 * Dateien im Tresor, von der Auswahl bis zum Löschen, gegen einen
 * nachgebauten Server, der sich merkt, was er zu sehen bekommt.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import 'fake-indexeddb/auto'
import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import { EINTRAG_MAX_ZEICHEN, getPendingQueue, useVaultStore, type VaultBlindSyncPayload } from './vaultStore'
import { ALBUM_HOECHSTENS, fassungenZusammenfuehren, itemAusUmschlag, umschlagAusItem, VAULT_TOMBSTONE_MARKER, type VaultItem } from './vaultEintrag'
import { decryptVaultEntry, encryptVaultEntry } from './vaultCrypto'
import {
  ablageDb,
  ablageLaden,
  ablageSchliessen,
  blobsLesen,
  blobsSchreiben,
  BLOB_CACHE,
  OFFLINE,
  revisionSchreiben,
  UPLOAD_CHUNKS,
  UPLOADS,
} from './tresorAblage'
import {
  angeheftet,
  blobLesen,
  dateiBlobs,
  dateiVorbereiten,
  loeschungenAbarbeiten,
  miniaturenLesen,
  offlineAnheften,
  offlineLoesen,
  unbenutzteBlobsEntfernen,
  uploadsFortsetzen,
  useTresorUploads,
  vorbereitungAbschliessen,
  VERSIONEN,
  ZULETZT_GRENZE,
} from './tresorDateien'
import { miniaturFreigeben, miniaturHolen } from './tresorMiniaturen'
import { chiffratGroesse, CHUNK_KLARTEXT, CHUNK_UEBERHANG, type BlobKopf } from './tresorDatei'
import { aufGeraetSpeichern } from './tresorAnzeige'
import { useToastStore } from '@/stores/toastStore'

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
    bilder.an && datei.type.startsWith('image/')
      ? { miniatur: new TextEncoder().encode(`mini:${datei.name}`), vorschau: new TextEncoder().encode(`vorschau:${datei.name}`) }
      : {},
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
    vollCode: 'VAULT_SPEICHER_VOLL',
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
    /** Lehnt das nächste DELETE mit 403 ohne Code ab (CSRF, Tresor abgeschaltet). */
    loeschenGesperrt: false,
    /** Weist die erste Reservierung mit 422 ab, und denselben Blob danach immer wieder. */
    abweisen: false,
    /** Der Tresor wurde auf einem anderen Gerät zurückgesetzt: jeder Abgleich 410. */
    zurueckgesetzt: false,
    abgewiesen: null as string | null,
  }
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (eingabe, init) => {
    if (server.offline) throw new TypeError('Failed to fetch')
    const url = new URL(String(eingabe), 'http://panel.test')
    const pfad = url.pathname
    const methode = (init?.method ?? 'GET').toUpperCase()
    const koerper = init?.body
    if (typeof koerper === 'string') server.mitschnitt.push(new TextEncoder().encode(koerper))
    else if (koerper instanceof Uint8Array) server.mitschnitt.push(koerper)

    if ((pfad.endsWith('/sync') || pfad.endsWith('/blind-sync')) && server.zurueckgesetzt) {
      return json({ detail: { code: 'VAULT_ZURUECKGESETZT', message: 'errors.vault_zurueckgesetzt' } }, 410)
    }
    if (pfad.endsWith('/sync') || pfad.endsWith('/blind-sync')) {
      const body = JSON.parse(String(koerper)) as VaultBlindSyncPayload
      const conflicts: string[] = []
      const entries = []
      for (const m of body.mutations) {
        const da = server.eintraege.get(m.id)
        const veraltet = m.expected_revision !== undefined && da !== undefined && da.revision !== m.expected_revision
        if ((m.is_deleted && server.konflikte.has(m.id)) || veraltet) {
          conflicts.push(m.id)
          continue
        }
        server.revision += 1
        server.eintraege.set(m.id, { ciphertext: m.ciphertext, revision: server.revision, is_deleted: m.is_deleted })
      }
      for (const [id, e] of server.eintraege) {
        if (e.revision > body.since_revision) entries.push({ id, ...e, updated_at: '2026-09-30T00:00:00Z' })
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
      if (server.speicherVoll) return json({ detail: { code: server.vollCode, message: 'errors.x' } }, 507)
      if (server.abweisen && (server.abgewiesen ?? b.id) === b.id) {
        server.abgewiesen = b.id
        return json({ detail: 'abgewiesen' }, 422)
      }
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
        if (server.loeschenGesperrt) {
          server.loeschenGesperrt = false
          return json({ detail: 'CSRF' }, 403)
        }
        const { schluessel } = JSON.parse(String(koerper))
        const bytes = new Uint8Array(schluessel.match(/../g).map((h: string) => parseInt(h, 16)))
        if ((await sha256Hex(bytes)) !== eintrag.delete_verifier) {
          return json({ detail: { code: 'VAULT_LOESCHNACHWEIS_FALSCH', message: 'errors.vault_loeschnachweis_falsch' } }, 403)
        }
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

    // Die Upload-Ablage ist leer. Ohne Bild sind Miniatur und Vorschau nur
    // Polster und bleiben nicht im Cache.
    expect(await zeilen(UPLOAD_CHUNKS)).toEqual([])
    expect(await zeilen(BLOB_CACHE)).toEqual([])

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

  it('behält von einem Foto die Miniatur und die Vorschau nur als zuletzt gesehen', async () => {
    // Bis 01.10.2026 blieb je Datei eine gepolsterte Vorschau (512 KiB) für
    // immer liegen, auch ohne Bild: tausend Dateien, ein halbes Gigabyte.
    bilder.an = true
    serverStarten()
    await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(foto(1000))
    await allesErledigt()

    const { vorschau, miniatur } = useVaultStore.getState().items.find((i) => i.id === id)!.datei!
    const cache = (await zeilen(BLOB_CACHE)) as { blobId: string }[]
    expect(new Set(cache.map((c) => c.blobId))).toEqual(new Set([vorschau.id, miniatur.id]))
    const offline = (await zeilen(OFFLINE)) as { blobId: string; angeheftet: boolean }[]
    expect(offline).toEqual([expect.objectContaining({ blobId: vorschau.id, angeheftet: false })])
  })

  it('legt nur in den Cache, was sich entschlüsseln lässt, und holt Kaputtes neu', async () => {
    const server = serverStarten()
    const userKey = await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(new File(['Vertrag'], 'v.txt', { type: 'text/plain' }))
    await allesErledigt()
    const original = useVaultStore.getState().items.find((i) => i.id === id)!.datei!.original
    const imCache = async () => ((await zeilen(BLOB_CACHE)) as { blobId: string }[]).some((z) => z.blobId === original.id)

    // Der Server liefert Kaputtes: nichts davon bleibt auf dem Gerät.
    const echt = server.blobs.get(original.id)!.chunks.get(0)!
    const kaputt = echt.slice()
    kaputt[kaputt.length - 1] ^= 1
    server.blobs.get(original.id)!.chunks.set(0, kaputt)
    await expect(blobLesen(original, id, userKey, 'text/plain', { zuletzt: true })).rejects.toThrow()
    expect(await imCache()).toBe(false)

    // Kaputtes im Cache wird verworfen und frisch geholt.
    server.blobs.get(original.id)!.chunks.set(0, echt)
    const db = (await ablageDb())!
    await new Promise<void>((ok) => {
      const tx = db.transaction(BLOB_CACHE, 'readwrite')
      tx.objectStore(BLOB_CACHE).put({ blobId: original.id, index: 0, daten: kaputt })
      tx.oncomplete = () => ok()
    })
    expect(await (await blobLesen(original, id, userKey, 'text/plain', { zuletzt: true })).text()).toBe('Vertrag')
    server.offline = true
    expect(await (await blobLesen(original, id, userKey, 'text/plain')).text()).toBe('Vertrag')
  })

  it('lädt nach einem Kontowechsel nichts mehr mit der fremden Sitzung hoch', async () => {
    // Die Anfragen gehen mit der Sitzung, die gerade angemeldet ist. Ohne die
    // Prüfung landete der Rest im Bucket des anderen Kontos.
    const server = serverStarten()
    server.verzoegerung = 100
    await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(foto(9 * 1024 * 1024))
    await vi.waitFor(() => expect(server.puts.length).toBeGreaterThan(0))
    setzeAngemeldetesKonto(2)
    const gesendet = server.puts.length
    await new Promise((r) => setTimeout(r, 400))

    const original = useVaultStore.getState().items.find((i) => i.id === id)!.datei!.original.id
    expect(server.puts.length).toBeLessThanOrEqual(gesendet + 1)
    expect(server.blobs.get(original)?.state).not.toBe('fertig')
    expect(await zeilen(UPLOADS)).not.toEqual([])

    // Zurück beim eigenen Konto geht der Rest hinaus.
    setzeAngemeldetesKonto(1)
    server.verzoegerung = 0
    await uploadsFortsetzen(BUCKET)
    await vi.waitFor(async () => expect(await zeilen(UPLOADS)).toEqual([]))
    expect(server.blobs.get(original)!.state).toBe('fertig')
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

  it('versucht das Löschen nach einem vorübergehenden 403 erneut', async () => {
    const server = serverStarten()
    await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(foto(1000))
    await allesErledigt()

    server.loeschenGesperrt = true
    await useVaultStore.getState().deleteItem(id)
    await vi.waitFor(() => expect(useVaultStore.getState().syncStatus).toBe('synced'))
    await loeschungenAbarbeiten(BUCKET)
    // Das erste DELETE scheiterte, der Lauf hörte dort auf; jetzt geht der Rest.
    await loeschungenAbarbeiten(BUCKET)
    expect(server.geloescht).toHaveLength(3)
  })

  it('gibt eine Datei auf, deren Löschnachweis der Server ablehnt', async () => {
    const server = serverStarten()
    await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(foto(1000))
    await allesErledigt()
    for (const b of server.blobs.values()) b.delete_verifier = '0'.repeat(64)
    const warnung = vi.spyOn(console, 'warn').mockImplementation(() => {})

    await useVaultStore.getState().deleteItem(id)
    await vi.waitFor(() => expect(useVaultStore.getState().syncStatus).toBe('synced'))
    await loeschungenAbarbeiten(BUCKET)
    expect(warnung).toHaveBeenCalledTimes(3)

    // Kein zweiter Versuch: die Datei steht nicht mehr zum Löschen an.
    warnung.mockClear()
    await loeschungenAbarbeiten(BUCKET)
    expect(warnung).not.toHaveBeenCalled()
    expect(server.geloescht).toEqual([])
  })

  it('lässt die Blobs stehen, wenn ein anderes Gerät die Datei inzwischen geändert hat', async () => {
    const server = serverStarten()
    await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(foto(1000))
    await allesErledigt()

    server.konflikte.add(id)
    await useVaultStore.getState().deleteItem(id)
    await vi.waitFor(() => expect(useVaultStore.getState().syncStatus).toBe('synced'))
    // Ein eigener Lauf wartet auch einen gerade laufenden ab.
    await loeschungenAbarbeiten(BUCKET)
    await loeschungenAbarbeiten(BUCKET)

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

  it('sagt, ob das Kontingent, die Platte oder die Dateizahl voll ist', async () => {
    // Bis 02.10.2026 hieß jedes 507 „Tresorspeicher voll, lösche Dateien“,
    // auch wenn die Platte des Servers voll war und Löschen nichts half.
    const server = serverStarten()
    server.speicherVoll = true
    server.vollCode = 'VAULT_PLATTE_VOLL'
    await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(foto(1000))
    await vi.waitFor(() => expect(useTresorUploads.getState().je[id]?.fehler).toBe('platteVoll'))
    expect(await zeilen(UPLOADS)).toHaveLength(3)
  })

  it('lädt die übrigen Blobs hoch, wenn der Server einen abweist', async () => {
    const server = serverStarten()
    server.abweisen = true
    await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(foto(1000))
    await vi.waitFor(() => expect(useTresorUploads.getState().je[id]?.fehler).toBe('abgelehnt'))
    await vi.waitFor(async () => expect(await zeilen(UPLOADS)).toHaveLength(1))
    expect([...server.blobs.values()].filter((b) => b.state === 'fertig')).toHaveLength(2)

    // Der abgewiesene wartet auf den nächsten Lauf.
    server.abweisen = false
    await uploadsFortsetzen(BUCKET)
    await vi.waitFor(async () => expect(await zeilen(UPLOADS)).toEqual([]))
  })

  it('lädt nach einem Zurücksetzen auf einem anderen Gerät nichts mehr hoch', async () => {
    const server = serverStarten()
    await tresorOeffnen()
    server.zurueckgesetzt = true
    await useVaultStore.getState().syncWithServer()
    expect(useVaultStore.getState().zurueckgesetzt).toBe(true)

    await useVaultStore.getState().dateiHinzufuegen(foto(1000))
    await vi.waitFor(() => expect(useVaultStore.getState().syncStatus).toBe('error'))
    await uploadsFortsetzen(BUCKET)
    // Die Datei-Routen kennen nur den Bucket des Kontos, also den eines neuen Tresors.
    expect(server.blobs.size).toBe(0)
    expect(await zeilen(UPLOADS)).toHaveLength(3)
    useVaultStore.setState({ zurueckgesetzt: false })
  })

  it('wartet bei einem Fehler, der alle träfe, und meldet nichts als abgewiesen', async () => {
    const server = serverStarten()
    server.offline = true
    await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(foto(1000))
    await uploadsFortsetzen(BUCKET)
    expect(await zeilen(UPLOADS)).toHaveLength(3)
    expect(useTresorUploads.getState().je[id]?.fehler).toBeUndefined()
  })

  it('nimmt den Fortschritt eines verworfenen Uploads weg', async () => {
    // Bis 02.10.2026 blieb er stehen, und die Speicheranzeige, die erst ohne
    // laufenden Upload fragt, fror ein.
    serverStarten()
    const userKey = await tresorOeffnen()
    const eintragId = crypto.randomUUID()
    const angaben = await dateiVorbereiten(foto(1000), userKey, BUCKET, eintragId, () => false)
    vorbereitungAbschliessen(angaben) // der Eintrag wurde nie gespeichert
    expect(useTresorUploads.getState().je[eintragId]).toBeDefined()

    await uploadsFortsetzen(BUCKET)
    expect(await zeilen(UPLOADS)).toEqual([])
    expect(useTresorUploads.getState().je[eintragId]).toBeUndefined()
  })

  it('leert den Fortschritt beim Sperren, und ein weiterlaufender Upload legt ihn nicht neu an', async () => {
    const server = serverStarten()
    server.verzoegerung = 100
    await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(foto(9 * 1024 * 1024))
    await vi.waitFor(() => expect(server.puts.length).toBeGreaterThan(0))
    expect(useTresorUploads.getState().je[id]).toBeDefined()

    useVaultStore.getState().lock()
    expect(useTresorUploads.getState().je).toEqual({})
    const gesendet = server.puts.length
    await vi.waitFor(() => expect(server.puts.length).toBeGreaterThan(gesendet))
    expect(useTresorUploads.getState().je).toEqual({})
    await vi.waitFor(async () => expect(await zeilen(UPLOADS)).toEqual([]), { timeout: 5000 })
    expect(useTresorUploads.getState().je).toEqual({})
  })

  describe('Zwei Geräte an derselben Datei', () => {
    it('führt das Umbenennen auf altem Stand mit dem neueren Inhalt des anderen Geräts zusammen', async () => {
      // Bis 01.10.2026 gewann die zuletzt gesendete Fassung: das Gerät, das
      // offline umbenannt hatte, holte den alten Inhalt zurück.
      const server = serverStarten()
      const userKey = await tresorOeffnen()
      const id = await useVaultStore.getState().dateiHinzufuegen(new File(['eins'], 'a.txt', { type: 'text/plain' }))
      await allesErledigt()
      const v1 = useVaultStore.getState().items.find((i) => i.id === id)!

      // Das andere Gerät ersetzt den Inhalt; seine Blobs kommen hier über die Ablage hoch.
      const zwei = await dateiVorbereiten(new File(['zwei'], 'a.txt', { type: 'text/plain' }), userKey, BUCKET, id, () => false)
      vorbereitungAbschliessen(zwei)
      const alt = v1.datei!
      const anderes: VaultItem = {
        ...v1,
        datei: { ...zwei, frueher: [{ typ: alt.typ, ersetzt: Date.now(), original: alt.original, vorschau: alt.vorschau, miniatur: alt.miniatur }] },
        updatedAt: v1.updatedAt + 1000,
      }
      server.revision += 1
      server.eintraege.set(id, { ciphertext: await encryptVaultEntry(umschlagAusItem(anderes), userKey, id), revision: server.revision, is_deleted: false })

      // Dieses Gerät ist offline und benennt um.
      server.offline = true
      await useVaultStore.getState().saveItem({ ...v1, service: 'b.txt' })
      await vi.waitFor(() => expect(useVaultStore.getState().syncStatus).toBe('offline'))
      server.offline = false
      await useVaultStore.getState().syncWithServer()

      await vi.waitFor(async () => {
        expect(getPendingQueue(BUCKET)).toEqual([])
        expect(await zeilen(UPLOADS)).toEqual([])
        expect(useVaultStore.getState().syncStatus).toBe('synced')
      })
      const item = useVaultStore.getState().items.find((i) => i.id === id)!
      expect(item.service).toBe('b.txt')
      expect(item.datei!.original.id).toBe(zwei.original.id)
      expect(item.datei!.frueher!.map((v) => v.original.id)).toEqual([alt.original.id])
      expect(await (await blobLesen(item.datei!.original, id, userKey, 'text/plain')).text()).toBe('zwei')
      expect(server.geloescht).toEqual([])
    })

    const kopf = (id: string) => ({ id, loeschen: `l-${id}` }) as unknown as BlobKopf
    const datei = (n: string, frueher: string[] = []) => ({
      typ: 'text/plain',
      original: kopf(`${n}o`),
      vorschau: kopf(`${n}v`),
      miniatur: kopf(`${n}m`),
      frueher: frueher.map((f, i) => ({ typ: 'text/plain', ersetzt: 100 - i, original: kopf(`${f}o`), vorschau: kopf(`${f}v`), miniatur: kopf(`${f}m`) })),
    })
    const eintrag = (d: ReturnType<typeof datei>, service = 'a.txt') =>
      ({ id: 'x', service, category: 'datei', datei: d, createdAt: 1, updatedAt: 1, revision: 1 }) as unknown as VaultItem

    it('behält beide Inhalte, wenn beide Geräte ersetzt haben, und löscht nichts davon', () => {
      const { item, weg } = fassungenZusammenfuehren(eintrag(datei('a')), eintrag(datei('e', ['a'])), eintrag(datei('s', ['a'])))
      expect(item.datei!.original.id).toBe('eo')
      expect(item.datei!.frueher!.map((v) => v.original.id)).toEqual(['so', 'ao'])
      expect(weg).toEqual([])
    })

    it('verliert keine Fassung, die dieses Gerät offline angelegt hat', () => {
      // Offline zweimal ersetzt (e1, dann e2), währenddessen hat der Server umbenannt und ersetzt (s).
      const { item, weg } = fassungenZusammenfuehren(
        eintrag(datei('a')),
        eintrag(datei('e2', ['e1', 'a'])),
        eintrag(datei('s', ['a']), 'neu.txt'),
      )
      expect(item.service).toBe('neu.txt')
      expect(item.datei!.original.id).toBe('e2o')
      expect(new Set(item.datei!.frueher!.map((v) => v.original.id))).toEqual(new Set(['so', 'e1o', 'ao']))
      expect(weg).toEqual([])
    })
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

    it('legt keine Miniatur in falscher Länge in den Cache', async () => {
      // Ohne Schlüssel wird nichts entschlüsselt; bis 02.10.2026 lag sie dann
      // ungeprüft im Cache.
      const { server, items, anfragen } = await dreiFotos()
      await cacheLeeren()
      const blob = server.blobs.get(items[0].datei!.miniatur.id)!
      blob.chunks.set(0, blob.chunks.get(0)!.slice(0, -1))

      await miniaturenLesen(anfragen, null)
      const cache = ((await zeilen(BLOB_CACHE)) as { blobId: string }[]).map((z) => z.blobId)
      expect(new Set(cache)).toEqual(new Set([items[1].datei!.miniatur.id, items[2].datei!.miniatur.id]))
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

      await offlineAnheften(original, id, userKey)
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

    it('heftet nicht an, wenn das Holen scheitert, und lässt nichts Halbes liegen', async () => {
      // Bis 02.10.2026 galt die Datei danach als offline verfügbar, obwohl ein Teil fehlte.
      const server = serverStarten()
      const userKey = await tresorOeffnen()
      const id = await useVaultStore.getState().dateiHinzufuegen(foto(5 * 1024 * 1024))
      await allesErledigt()
      const original = useVaultStore.getState().items.find((i) => i.id === id)!.datei!.original

      await expect(
        offlineAnheften(original, id, userKey, (anteil) => {
          if (anteil < 1) server.offline = true // nach dem ersten Chunk ist das Netz weg
        }),
      ).rejects.toThrow()
      expect(await angeheftet([original.id])).toEqual(new Set())
      expect(await originalImCache(original.id)).toBe(false)
      expect(await zeilen(OFFLINE)).toEqual([])
    })

    it('legt beim Anheften kein Chiffrat in falscher Länge ab', async () => {
      const { server, userKey, id, item } = await hochgeladen()
      const original = item().datei!.original
      const blob = server.blobs.get(original.id)!
      blob.chunks.set(0, new Uint8Array([...blob.chunks.get(0)!, 0]))

      await expect(offlineAnheften(original, id, userKey)).rejects.toThrow()
      expect(await originalImCache(original.id)).toBe(false)
      expect(await angeheftet([original.id])).toEqual(new Set())
    })

    it('zählt, was ein abgebrochenes Lesen schon abgelegt hat, gegen die Grenze', async () => {
      // Lichtbox weitergeblättert: bis 02.10.2026 blieb der erste Chunk ohne
      // Zeile im Cache, und keine Grenze nahm ihn je wieder heraus.
      serverStarten()
      const userKey = await tresorOeffnen()
      const id = await useVaultStore.getState().dateiHinzufuegen(foto(5 * 1024 * 1024))
      await allesErledigt()
      const original = useVaultStore.getState().items.find((i) => i.id === id)!.datei!.original

      const abbruch = new AbortController()
      await expect(
        blobLesen(original, id, userKey, 'image/jpeg', { zuletzt: true, signal: abbruch.signal, fortschritt: () => abbruch.abort() }),
      ).rejects.toThrow()
      const chunks = ((await zeilen(BLOB_CACHE)) as { blobId: string; index: number }[]).filter((z) => z.blobId === original.id)
      expect(chunks.map((z) => z.index)).toEqual([0])
      expect(await zeilen(OFFLINE)).toEqual([
        expect.objectContaining({ blobId: original.id, angeheftet: false, bytes: chiffratGroesse(original.groesse) }),
      ])
    })

    it('nimmt vom Gerät, was kein Eintrag mehr nennt, und lässt Uploads in Ruhe', async () => {
      const { server, userKey, id, item } = await hochgeladen()
      const weg = item().datei!.original
      const zweite = await useVaultStore.getState().dateiHinzufuegen(new File(['bleibt'], 'b.txt', { type: 'text/plain' }))
      await allesErledigt()
      const bleibt = useVaultStore.getState().items.find((i) => i.id === zweite)!.datei!
      await offlineAnheften(weg, id, userKey)
      await offlineAnheften(bleibt.original, zweite, userKey)
      // Eine dritte wartet offline auf den Upload; ihr Eintrag ist hier noch nicht in `benutzt`.
      server.offline = true
      await useVaultStore.getState().dateiHinzufuegen(new File(['wartet'], 'c.txt', { type: 'text/plain' }))
      const uploads = await zeilen(UPLOADS)
      const uploadChunks = await zeilen(UPLOAD_CHUNKS)
      expect(uploads).toHaveLength(3)

      await unbenutzteBlobsEntfernen(new Set([bleibt.original.id, bleibt.vorschau.id, bleibt.miniatur.id]))
      expect(await angeheftet([weg.id, bleibt.original.id])).toEqual(new Set([bleibt.original.id]))
      expect(await originalImCache(weg.id)).toBe(false)
      expect(await originalImCache(bleibt.original.id)).toBe(true)
      expect(await zeilen(UPLOADS)).toEqual(uploads)
      expect(await zeilen(UPLOAD_CHUNKS)).toEqual(uploadChunks)
    })

    it('nimmt eine angeheftete Datei vom Gerät, wenn ein anderes Gerät sie gelöscht hat', async () => {
      // Bis 02.10.2026 blieb sie als Chiffrat liegen, auch GB-große Originale.
      const { server, userKey, id, item } = await hochgeladen()
      const original = item().datei!.original
      await offlineAnheften(original, id, userKey)
      expect(await angeheftet([original.id])).toEqual(new Set([original.id]))

      server.revision += 1
      const spaeter = Date.now() + 1000
      const grab = await encryptVaultEntry({ [VAULT_TOMBSTONE_MARKER]: true, deletedAt: spaeter, updatedAt: spaeter }, userKey, id)
      server.eintraege.set(id, { ciphertext: grab, revision: server.revision, is_deleted: true })
      await useVaultStore.getState().syncWithServer()

      await vi.waitFor(async () => expect(await angeheftet([original.id])).toEqual(new Set()))
      expect(await originalImCache(original.id)).toBe(false)
    })

    it('hält eine bearbeitete Datei weiter angeheftet und gibt die alte Fassung frei', async () => {
      const { server, userKey, id, item } = await hochgeladen('eins')
      const alt = item().datei!.original
      await offlineAnheften(alt, id, userKey)

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
      const { server, userKey, id, item } = await hochgeladen()
      const original = item().datei!.original
      await offlineAnheften(original, id, userKey)

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

// ---------------------------------------------------------------------------
// Review 02.10.2026: Negativtests gegen bösartigen Server und Wettläufe
// ---------------------------------------------------------------------------

/**
 * Hält den ersten Verschlüsselungsaufruf mit dieser AAD (der Kennung eines
 * Eintrags) fest, bis `frei()` kommt. So steht ein Speichern genau zwischen
 * Lesen und Schreiben.
 */
function encryptAnhalten(aad: string) {
  const subtle = window.crypto.subtle
  const original = subtle.encrypt.bind(subtle)
  let loslassen!: () => void
  const freigabe = new Promise<void>((r) => (loslassen = r))
  let gestartet!: () => void
  const angehalten = new Promise<void>((r) => (gestartet = r))
  let erwischt = false
  vi.spyOn(subtle, 'encrypt').mockImplementation(async (algo, key, data) => {
    const roh = (algo as AesGcmParams).additionalData
    const text = roh ? new TextDecoder().decode(roh as Uint8Array) : ''
    if (!erwischt && text === aad) {
      erwischt = true
      gestartet()
      await freigabe
    }
    return original(algo as AesGcmParams, key, data)
  })
  return { angehalten, frei: () => loslassen() }
}

/** Ersetzt die Antwort auf passende Anfragen; alles andere geht an den nachgebauten Server. */
function abfangen(passt: (pfad: string, methode: string) => Response | null) {
  const weiter = vi.mocked(globalThis.fetch).getMockImplementation()!
  vi.mocked(globalThis.fetch).mockImplementation(async (eingabe, init) => {
    const pfad = new URL(String(eingabe), 'http://panel.test').pathname
    const ersatz = passt(pfad, (init?.method ?? 'GET').toUpperCase())
    if (ersatz) return ersatz
    return weiter(eingabe, init)
  })
}

const synchron = () => vi.waitFor(() => expect(useVaultStore.getState().syncStatus).toBe('synced'))
const eintrag = (id: string) => useVaultStore.getState().items.find((i) => i.id === id)

describe('Review: Negativtests Tresor-Client', () => {
  beforeEach(() => {
    ablageSchliessen()
    localStorage.clear()
    globalThis.indexedDB = new IDBFactory()
    setzeAngemeldetesKonto(1)
    vi.restoreAllMocks()
    useTresorUploads.setState({ je: {} })
    bilder.an = false
    useVaultStore.setState({ zurueckgesetzt: false })
  })

  afterEach(() => {
    ablageSchliessen()
    setzeAngemeldetesKonto(null)
    vi.restoreAllMocks()
  })

  // ---------------- Befunde (am Stand 1493f41a rot) ----------------

  it('R1: endgültiges Löschen während eines Speicherns lässt keinen lebenden Eintrag mit gelöschten Blobs zurück', async () => {
    const server = serverStarten()
    await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(new File(['Vertrag'], 'a.txt', { type: 'text/plain' }))
    await allesErledigt()

    const halt = encryptAnhalten(id)
    const umbenennen = useVaultStore.getState().aendern(id, { service: 'b.txt' })
    await halt.angehalten
    // Der Mensch löscht endgültig, während das Umbenennen noch verschlüsselt.
    // Das Löschen wartet, bis das Umbenennen gespeichert ist (`nacheinander`).
    const loeschen = useVaultStore.getState().deleteItem(id)
    await new Promise((r) => setTimeout(r, 20))
    halt.frei()
    await umbenennen
    await loeschen
    await synchron()
    await useVaultStore.getState().syncWithServer()
    await loeschungenAbarbeiten(BUCKET)
    await loeschungenAbarbeiten(BUCKET)
    for (let i = 0; i < 3; i++) {
      await useVaultStore.getState().syncWithServer()
      await synchron()
      await loeschungenAbarbeiten(BUCKET)
    }

    const lebtLokal = !!eintrag(id)
    const lebtOben = !server.eintraege.get(id)!.is_deleted
    // Nach dem nächsten Entsperren zählt, was im Cache steht.
    const imCache = blobsLesen(BUCKET).find((b) => b.id === id)
    const lebtNachEntsperren = !!imCache && !imCache.is_deleted
    // Gelöscht wurde zuletzt: der Eintrag ist weg, überall (auch im Cache), und mit ihm seine Blobs.
    expect({ lebtLokal, lebtOben, lebtNachEntsperren, geloescht: server.geloescht.length }).toEqual({
      lebtLokal: false,
      lebtOben: false,
      lebtNachEntsperren: false,
      geloescht: 3,
    })
  })

  it('R1b: wie R1, ohne Anhalten: Favorit setzen und gleich danach endgültig löschen', async () => {
    const server = serverStarten()
    await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(new File(['Vertrag'], 'a.txt', { type: 'text/plain' }))
    await allesErledigt()

    const favorit = useVaultStore.getState().toggleFavorite(id)
    await useVaultStore.getState().deleteItem(id)
    await favorit
    for (let i = 0; i < 3; i++) {
      await synchron()
      await useVaultStore.getState().syncWithServer()
      await loeschungenAbarbeiten(BUCKET)
    }
    const imCache = blobsLesen(BUCKET).find((b) => b.id === id)
    expect({
      lebtLokal: !!eintrag(id),
      lebtOben: !server.eintraege.get(id)!.is_deleted,
      lebtNachEntsperren: !!imCache && !imCache.is_deleted,
      geloescht: server.geloescht.length,
    }).toEqual({ lebtLokal: false, lebtOben: false, lebtNachEntsperren: false, geloescht: 3 })
  })

  it('R2: ein Speichern belebt keine Datei, die ein anderes Gerät währenddessen gelöscht hat', async () => {
    const server = serverStarten()
    const userKey = await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(new File(['Vertrag'], 'a.txt', { type: 'text/plain' }))
    await allesErledigt()
    const kopf = eintrag(id)!.datei!

    const halt = encryptAnhalten(id)
    const umbenennen = useVaultStore.getState().aendern(id, { service: 'b.txt' }).catch(() => {})
    await halt.angehalten

    // Gerät B löscht die Datei endgültig samt ihren Blobs.
    const grab = await encryptVaultEntry({ [VAULT_TOMBSTONE_MARKER]: true, deletedAt: Date.now(), updatedAt: Date.now() + 1000 }, userKey, id)
    server.revision += 1
    server.eintraege.set(id, { ciphertext: grab, revision: server.revision, is_deleted: true })
    for (const k of dateiBlobs(kopf)) server.blobs.get(k.id)!.state = 'geloescht'
    await useVaultStore.getState().syncWithServer()
    expect(eintrag(id)).toBeUndefined()

    halt.frei()
    await umbenennen
    await synchron()
    await useVaultStore.getState().syncWithServer()

    // Sonst steht auf dem Server ein lebender Eintrag, dessen Blobs es nicht mehr gibt.
    expect(server.eintraege.get(id)!.is_deleted).toBe(true)
    expect(eintrag(id)).toBeUndefined()
  })

  it('R3: Ersetzen des Inhalts verliert keinen Namen, der kurz davor gespeichert wurde (Punkt 90)', async () => {
    serverStarten()
    await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(new File(['alt'], 'a.txt', { type: 'text/plain' }))
    await allesErledigt()

    const halt = encryptAnhalten(id)
    const umbenennen = useVaultStore.getState().aendern(id, { service: 'b.txt' })
    await halt.angehalten
    const ersetzen = useVaultStore.getState().dateiErsetzen(id, new Blob(['neu!'], { type: 'text/plain' }))
    // Das Ersetzen hat seine Blobs abgelegt und wartet aufs Speichern.
    await vi.waitFor(() => expect(useTresorUploads.getState().je[id]).toBeDefined())
    await new Promise((r) => setTimeout(r, 20))
    halt.frei()
    await umbenennen
    await ersetzen

    expect(eintrag(id)!.datei!.original.echt).toBe(4)
    expect(eintrag(id)!.service).toBe('b.txt')
  })

  it('R4: zwei Geräte laufen bei einem Passwort nicht auseinander, wenn die ältere Offline-Änderung später ankommt', async () => {
    const server = serverStarten()
    const userKey = await tresorOeffnen()
    await useVaultStore.getState().saveItem({ id: 'pw', service: 'Bank', password: 'alt', category: 'login' })
    await synchron()
    await vi.waitFor(() => expect(getPendingQueue(BUCKET)).toEqual([]))

    // Gerät A (dieses) ändert offline.
    server.offline = true
    await useVaultStore.getState().aendern('pw', { password: 'von-A' })
    await vi.waitFor(() => expect(useVaultStore.getState().syncStatus).toBe('offline'))

    // Gerät B ändert fünf Minuten später und ist zuerst beim Server.
    const standB = Date.now() + 5 * 60_000
    const vonB = await encryptVaultEntry(
      umschlagAusItem({ ...eintrag('pw')!, password: 'von-B', updatedAt: standB }),
      userKey,
      'pw',
    )
    server.revision += 1
    const revB = server.revision
    server.eintraege.set('pw', { ciphertext: vonB, revision: revB, is_deleted: false })

    // A ist wieder im Netz und gleicht ab.
    server.offline = false
    useVaultStore.setState({ syncStatus: 'synced' })
    await useVaultStore.getState().syncWithServer()
    await synchron()

    // Jetzt gleicht Gerät B ab (eigene Ablage, eigene Fassung als Stand).
    ablageSchliessen()
    setzeAngemeldetesKonto(2)
    await ablageLaden(BUCKET)
    blobsSchreiben(BUCKET, [{ id: 'pw', ciphertext: vonB, revision: revB, is_deleted: false, stand: standB, serverRev: revB }])
    revisionSchreiben(BUCKET, revB)
    useVaultStore.setState({
      items: [itemAusUmschlag('pw', revB, await decryptVaultEntry(vonB, userKey, 'pw'))],
      syncStatus: 'synced',
    })
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    await useVaultStore.getState().syncWithServer()
    await synchron()

    const aufDemServer = (await decryptVaultEntry(server.eintraege.get('pw')!.ciphertext, userKey, 'pw')).password
    const aufB = eintrag('pw')!.password
    // Server und Gerät B müssen dasselbe Passwort haben.
    expect({ aufDemServer, aufB }).toEqual({ aufDemServer: aufB, aufB })
  })

  it('R5: heftet kein Chiffrat an, das sich nicht entschlüsseln lässt (Punkt 81)', async () => {
    const server = serverStarten()
    const userKey = await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(new File(['Vertrag'], 'v.txt', { type: 'text/plain' }))
    await allesErledigt()
    const original = eintrag(id)!.datei!.original
    // Richtige Länge, falscher Inhalt (ein Bit gekippt oder ein fremder Blob derselben Größe).
    const chunk = server.blobs.get(original.id)!.chunks.get(0)!.slice()
    chunk[40] ^= 1
    server.blobs.get(original.id)!.chunks.set(0, chunk)

    await expect(offlineAnheften(original, id, userKey)).rejects.toThrow()
    expect(await angeheftet([original.id])).toEqual(new Set())
  })

  // ---------------- geprüft, hält ----------------

  it('G1: verwirft einen Umschlag unter vertauschter entryId', async () => {
    const server = serverStarten()
    await tresorOeffnen()
    await useVaultStore.getState().saveItem({ id: 'pw', service: 'Bank', password: 'echt', category: 'login' })
    await synchron()
    const fremd = server.eintraege.get('pw')!
    server.revision += 1
    server.eintraege.set('anderer', { ...fremd, revision: server.revision })
    await useVaultStore.getState().syncWithServer()
    expect(eintrag('anderer')).toBeUndefined()
    expect(eintrag('pw')!.password).toBe('echt')
  })

  it('G2: eine ältere Fassung nach der neueren in derselben Antwort springt nicht zurück', async () => {
    serverStarten()
    const userKey = await tresorOeffnen()
    const alt = await encryptVaultEntry({ service: 'Bank', password: 'alt', updatedAt: 10, createdAt: 1 }, userKey, 'pw')
    const neu = await encryptVaultEntry({ service: 'Bank', password: 'neu', updatedAt: 20, createdAt: 1 }, userKey, 'pw')
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      json({
        server_revision: 12,
        entries: [
          { id: 'pw', ciphertext: neu, revision: 11, is_deleted: false, updated_at: 'x' },
          { id: 'pw', ciphertext: alt, revision: 12, is_deleted: false, updated_at: 'x' },
        ],
      }),
    )
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    await useVaultStore.getState().syncWithServer()
    expect(eintrag('pw')!.password).toBe('neu')
  })

  it('G3: zurückgesetzte server_revision holt nur neu, ohne Rücksprung', async () => {
    const server = serverStarten()
    await tresorOeffnen()
    await useVaultStore.getState().saveItem({ id: 'pw', service: 'Bank', password: 'v1', category: 'login' })
    await synchron()
    const v1 = server.eintraege.get('pw')!
    await useVaultStore.getState().aendern('pw', { password: 'v2' })
    await synchron()
    // Der Server vergisst alles nach v1 und meldet eine kleinere Revision.
    server.eintraege.set('pw', { ...v1, revision: 3 })
    server.revision = 3
    useVaultStore.setState({ syncStatus: 'synced' })
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    await useVaultStore.getState().syncWithServer()
    expect(eintrag('pw')!.password).toBe('v2')
  })

  it('G4: Konflikt für eine nie gesendete ID ändert nichts', async () => {
    serverStarten()
    await tresorOeffnen()
    await useVaultStore.getState().saveItem({ id: 'pw', service: 'Bank', password: 'p', category: 'login' })
    await synchron()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(json({ server_revision: 99, entries: [], conflicts: ['pw', 'nie-gesendet'] }))
    await useVaultStore.getState().syncWithServer()
    expect(eintrag('pw')!.password).toBe('p')
    expect(getPendingQueue(BUCKET)).toEqual([])
  })

  it.each([429, 503, 'netz'] as const)('G5: Löschlauf behält seine Aufträge bei %s und holt sie nach', async (fall) => {
    const server = serverStarten()
    await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(new File(['x'], 'x.txt', { type: 'text/plain' }))
    await allesErledigt()
    let einmal = true
    abfangen((_pfad, methode) => {
      if (!einmal || methode !== 'DELETE') return null
      einmal = false
      if (fall === 'netz') throw new TypeError('Failed to fetch')
      return json({ detail: 'später' }, fall)
    })
    await useVaultStore.getState().deleteItem(id)
    await synchron()
    await loeschungenAbarbeiten(BUCKET)
    await loeschungenAbarbeiten(BUCKET)
    expect(server.geloescht).toHaveLength(3)
  })

  it('G6: 410 mitten im Upload verwirft nichts, und danach geht keine Datei-Anfrage mehr hinaus', async () => {
    const server = serverStarten()
    await tresorOeffnen()
    abfangen((pfad, methode) =>
      methode === 'PUT' && pfad.includes('/chunks/')
        ? json({ detail: { code: 'VAULT_ZURUECKGESETZT', message: 'errors.vault_zurueckgesetzt' } }, 410)
        : null,
    )
    await useVaultStore.getState().dateiHinzufuegen(new File(['x'], 'x.txt', { type: 'text/plain' }))
    await vi.waitFor(() => expect(server.blobs.size).toBeGreaterThan(0))
    server.zurueckgesetzt = true
    useVaultStore.setState({ syncStatus: 'synced' })
    await useVaultStore.getState().syncWithServer()
    expect(useVaultStore.getState().zurueckgesetzt).toBe(true)
    await uploadsFortsetzen(BUCKET)
    const vorher = vi.mocked(globalThis.fetch).mock.calls.length
    await uploadsFortsetzen(BUCKET)
    await loeschungenAbarbeiten(BUCKET)
    expect(vi.mocked(globalThis.fetch).mock.calls.length).toBe(vorher)
    expect(await zeilen(UPLOADS)).toHaveLength(3)
  })

  it('G7: klein-Antwort mit vertauschten oder überzähligen Teilen landet weder im Bild noch im Cache', async () => {
    const server = serverStarten()
    bilder.an = true
    const userKey = await tresorOeffnen()
    const a = await useVaultStore.getState().dateiHinzufuegen(foto(1000, 'a.jpg'))
    const b = await useVaultStore.getState().dateiHinzufuegen(foto(1000, 'b.jpg'))
    await allesErledigt()
    const ma = eintrag(a)!.datei!.miniatur
    const mb = eintrag(b)!.datei!.miniatur
    const db = (await ablageDb())!
    await new Promise<void>((ok) => {
      const tx = db.transaction(BLOB_CACHE, 'readwrite')
      tx.objectStore(BLOB_CACHE).clear()
      tx.oncomplete = () => ok()
    })
    const da = server.blobs.get(ma.id)!.chunks.get(0)!
    const dbb = server.blobs.get(mb.id)!.chunks.get(0)!
    abfangen((pfad) => {
      if (pfad !== '/api/vault/blobs/klein') return null
      const teile: number[] = []
      for (const d of [dbb, da, new Uint8Array(5)]) teile.push(d.length >>> 24, (d.length >> 16) & 0xff, (d.length >> 8) & 0xff, d.length & 0xff, ...d)
      return new Response(new Uint8Array(teile) as BodyInit, { status: 200 })
    })
    const ergebnis = await miniaturenLesen([{ kopf: ma, eintragId: a }, { kopf: mb, eintragId: b }], userKey)
    expect(ergebnis.size).toBe(0)
    expect(await zeilen(BLOB_CACHE)).toEqual([])
  })

  it('G8: Umschlag an der Servergrenze geht, einer darüber nicht', async () => {
    serverStarten()
    await tresorOeffnen()
    expect(EINTRAG_MAX_ZEICHEN).toBe(1048576) // = max_length in backend/schemas/vault.py
    await useVaultStore.getState().saveItem({ id: 'n', service: 'Notiz', category: 'secure_note', notes: '' })
    await synchron()
    const basis = JSON.stringify(umschlagAusItem({ ...eintrag('n')!, notes: '', updatedAt: 1_700_000_000_000 })).length
    // Polsterung in 4-KiB-Blöcken: 191 Blöcke ergeben 1.043.164 Zeichen, 192 schon 1.048.628.
    const passt = 191 * 4096 - 7 - basis
    await useVaultStore.getState().aendern('n', { notes: 'x'.repeat(passt) })
    expect(eintrag('n')!.notes!.length).toBe(passt)
    await expect(useVaultStore.getState().aendern('n', { notes: 'x'.repeat(passt + 1) })).rejects.toThrow()
  })

  it('G9: Album mit ALBUM_HOECHSTENS langen Kennungen geht, eine mehr nicht', async () => {
    serverStarten()
    await tresorOeffnen()
    const ids = Array.from({ length: ALBUM_HOECHSTENS }, (_, i) => String(i).padStart(64, 'a'))
    const album = await useVaultStore.getState().albumAnlegen('voll', ids)
    expect(eintrag(album)!.album!.eintraege).toHaveLength(ALBUM_HOECHSTENS)
    await expect(useVaultStore.getState().albumAendern(album, { hinzu: ['b'.repeat(64)] })).rejects.toThrow()
    await expect(useVaultStore.getState().albumAnlegen('zu viel', [...ids, 'b'.repeat(64)])).rejects.toThrow()
    expect(eintrag(album)!.album!.eintraege).toHaveLength(ALBUM_HOECHSTENS)
  })

  it('G10: Aufräumen während eines Anheftens nimmt der angehefteten Datei nichts', async () => {
    serverStarten()
    const userKey = await tresorOeffnen()
    const id = await useVaultStore.getState().dateiHinzufuegen(new File(['Vertrag'], 'v.txt', { type: 'text/plain' }))
    await allesErledigt()
    const datei = eintrag(id)!.datei!
    let weiter!: () => void
    const gehalten = new Promise<void>((r) => (weiter = r))
    const impl = vi.mocked(globalThis.fetch).getMockImplementation()!
    vi.mocked(globalThis.fetch).mockImplementation(async (e, i) => {
      if (String(e).includes(`/blobs/${datei.original.id}/chunks/`)) await gehalten
      return impl(e, i)
    })
    const anheften = offlineAnheften(datei.original, id, userKey)
    await vi.waitFor(async () => expect(await zeilen(OFFLINE)).toHaveLength(1))
    await unbenutzteBlobsEntfernen(new Set(useVaultStore.getState().items.flatMap((i) => (i.datei ? dateiBlobs(i.datei).map((k) => k.id) : []))))
    weiter()
    await anheften
    expect(await angeheftet([datei.original.id])).toEqual(new Set([datei.original.id]))
  })

  it('G11: kein Klartext (Passwort, Name, Inhalt) in Konsole, Toast, localStorage, URL oder auf der Leitung', async () => {
    const server = serverStarten()
    const konsole: unknown[][] = []
    for (const art of ['log', 'warn', 'error', 'info', 'debug'] as const) {
      vi.spyOn(console, art).mockImplementation((...a: unknown[]) => void konsole.push(a))
    }
    await tresorOeffnen()
    await useVaultStore.getState().saveItem({ id: 'pw', service: 'Hausbank-Geheim', password: 'Pa55-Geheim-4711', category: 'login' })
    const id = await useVaultStore.getState().dateiHinzufuegen(new File(['Inhalt-Geheim-0815'], 'steuer-geheim.txt', { type: 'text/plain' }))
    await allesErledigt()
    // Eine gefälschte Löschung, damit auch die Warnwege laufen.
    const fremderSchluessel = await crypto.subtle.importKey('raw', new Uint8Array(32).fill(9), 'AES-GCM', false, ['encrypt'])
    const fremd = await encryptVaultEntry({ [VAULT_TOMBSTONE_MARKER]: true, deletedAt: 1 }, fremderSchluessel, 'pw')
    server.revision += 1
    server.eintraege.set('pw', { ciphertext: fremd, revision: server.revision, is_deleted: true })
    await useVaultStore.getState().syncWithServer()
    await useVaultStore.getState().deleteItem(id)
    await synchron()
    await loeschungenAbarbeiten(BUCKET)
    expect(eintrag('pw')!.password).toBe('Pa55-Geheim-4711')

    const geheim = ['Hausbank-Geheim', 'Pa55-Geheim-4711', 'steuer-geheim', 'Inhalt-Geheim-0815']
    const lokal = Object.keys(localStorage).map((k) => `${k}=${localStorage.getItem(k)}`).join('\n')
    const urls = vi.mocked(globalThis.fetch).mock.calls.map(([u]) => String(u)).join('\n')
    const toasts = JSON.stringify(useToastStore.getState())
    const log = konsole.map((a) => a.map((x) => (x instanceof Error ? x.message : String(x))).join(' ')).join('\n')
    const leitung = server.mitschnitt.map((b) => new TextDecoder().decode(b)).join('\n')
    for (const g of geheim) {
      expect(lokal).not.toContain(g)
      expect(urls).not.toContain(g)
      expect(toasts).not.toContain(g)
      expect(log).not.toContain(g)
      expect(leitung).not.toContain(g)
    }
  })
})
