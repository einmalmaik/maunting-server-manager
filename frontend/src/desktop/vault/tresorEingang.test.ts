/**
 * Posteingang des Tresors: was der Hintergrund-Job eines Telefons sichert,
 * wird beim nächsten Entsperren ein Eintrag. Mit echter Krypto (DIS hybrid,
 * ECDSA P-256 aus WebCrypto anstelle des Android Keystore). Verpackt wird hier
 * so wie in `KameraArbeit.kt`; dass Rust dabei Byte für Byte wie DIS
 * verschlüsselt, prüft `kameraVektoren.test.ts`.
 *
 * Geprüft wird, was die Übernahme annimmt und was sie verwirft: nur Datensätze
 * eines eingetragenen Geräts mit gültiger Unterschrift, gebunden an Bucket und
 * Kennung, nur für Aufnahmen dieses Geräts, und erst wenn alle Blobs fertig
 * beim Server liegen. Gelöscht wird ein Datensatz erst, wenn sein Eintrag beim
 * Server liegt.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import 'fake-indexeddb/auto'
import { IDBFactory } from 'fake-indexeddb'
import { bytesToBase64, utf8ToBytes } from '@msdis/shield/core'
import { importFileKey } from '@msdis/shield/file-encryption'
import { hybridEncrypt } from '@msdis/shield/post-quantum'
import { SanitizedApiError } from '@/api/client'
import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import { eingangVerpacken } from './eingangFormat'
import { ablageLoeschen } from './tresorAblage'
import { blobLoeschen, blobStand, chunkLaden } from './tresorBlobApi'
import { blobSchluessel, chunkEntschluesseln, chunkVerschluesseln, gepolsterteGroesse, type BlobKopf } from './tresorDatei'
import { letzterChunkOeffnetSich, type DateiQuelle } from './tresorDateien'
import { eingangAnstossen, posteingangEinrichten, WARTEN_HOECHSTENS_MS, type EingangInhalt, type EingangOeffentlich } from './tresorEingang'
import { bytesToHex } from './vaultCrypto'
import { SYSTEM_KATEGORIE } from './vaultEintrag'
import { getPendingQueue, useVaultStore, type VaultBlindSyncPayload, type VaultItem } from './vaultStore'

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

// RSA-4096 zu erzeugen dauert Sekunden; ein echtes Paar je Datei reicht.
vi.mock('@msdis/shield/post-quantum', async (original) => {
  const echt = await original<typeof import('@msdis/shield/post-quantum')>()
  let paar: ReturnType<typeof echt.generateHybridKeyPair> | undefined
  return { ...echt, generateHybridKeyPair: () => (paar ??= echt.generateHybridKeyPair()) }
})

/** Der Posteingang des Servers, je Bucket, sortiert nach Kennung wie die Route. */
const { posteingang, angelegt } = vi.hoisted(() => ({
  posteingang: new Map<string, Map<string, string>>(),
  /** Wann der Server einen Datensatz angelegt haben will; ohne Angabe gerade eben. */
  angelegt: new Map<string, string>(),
}))

vi.mock('./tresorBlobApi', () => ({
  bucketMelderSetzen: vi.fn(),
  zurueckgesetztFrage: vi.fn(),
  speicherAbfragen: vi.fn(),
  blobReservieren: vi.fn(),
  // Was beim Server über die Blobs steht; ohne Angabe ist alles hochgeladen.
  blobStand: vi.fn(async () => ({ state: 'fertig', chunk_count: 1, vorhanden: [0] })),
  chunkHochladen: vi.fn(),
  blobFertig: vi.fn(),
  chunkLaden: vi.fn(),
  kleineLaden: vi.fn(),
  eingangListe: vi.fn(async (bucket: string, nach?: string) => {
    const eintraege = [...(posteingang.get(bucket) ?? new Map<string, string>()).entries()]
      .filter(([id]) => !nach || id > nach)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([id, ciphertext]) => ({ id, ciphertext, created_at: angelegt.get(id) ?? new Date().toISOString() }))
    return { eintraege, weiter: null }
  }),
  eingangLoeschen: vi.fn(async (bucket: string, id: string) => {
    posteingang.get(bucket)?.delete(id)
  }),
  blobLoeschen: vi.fn(async () => {}),
}))

const BUCKET = 'a'.repeat(64)
const GERAET = '0d6e3c1a-5b2f-4c8e-9a7d-1f2e3d4c5b6a'

interface Geraet {
  geraet: string
  spki: string
  unterschreiben: (daten: Uint8Array) => Promise<Uint8Array>
}

async function geraetAnlegen(geraet = GERAET): Promise<Geraet> {
  const paar = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign', 'verify'])
  const spki = bytesToBase64(new Uint8Array(await crypto.subtle.exportKey('spki', paar.publicKey)))
  return {
    geraet,
    spki,
    unterschreiben: async (daten) =>
      new Uint8Array(await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, paar.privateKey, daten as BufferSource)),
  }
}

/** Ein Server, der jede Mutation annimmt und sie mit fortlaufender Revision zurückgibt. */
function echoServer() {
  let revision = 10
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => {
    const body = JSON.parse(String(init?.body)) as VaultBlindSyncPayload
    const entries = body.mutations.map((m) => ({ ...m, revision: ++revision, updated_at: '2026-10-03T00:00:00Z' }))
    return { ok: true, status: 200, json: async () => ({ server_revision: revision, entries }) } as Response
  })
}

function offline() {
  vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))
}

let userKey: CryptoKey
let telefon: Geraet
let schluessel: EingangOeffentlich

async function tresorOeffnen() {
  userKey = await crypto.subtle.importKey('raw', new Uint8Array(32).fill(7), { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt'])
  useVaultStore.setState({
    userKey,
    bucketId: BUCKET,
    bucketAuthToken: 'b'.repeat(64),
    isUnlocked: true,
    syncStatus: 'synced',
    items: [],
  })
}

function zufallHex(bytes: number): string {
  return bytesToHex(crypto.getRandomValues(new Uint8Array(bytes)))
}

/** Ein Blob-Kopf mit rohem Schlüssel, wie ihn `KameraArbeit.kt` anlegt. */
async function roherBlob(echt: number, groesse = gepolsterteGroesse(echt)) {
  const kopf: BlobKopf = { id: zufallHex(16), groesse, echt, schluessel: zufallHex(32), loeschen: zufallHex(32) }
  const roh = new Uint8Array(kopf.schluessel.match(/../g)!.map((h) => parseInt(h, 16)))
  return { kopf, schluessel: await importFileKey(roh) }
}

/** Was das Telefon hochlädt: drei Blobs mit rohen Schlüsseln, der Inhalt im Original. */
async function aufnahme(eintragId: string, inhalt: Uint8Array, quelle: Partial<DateiQuelle> = {}) {
  const original = await roherBlob(inhalt.length)
  const vorschau = await roherBlob(0, 512 * 1024)
  const miniatur = await roherBlob(0, 32 * 1024)
  const chunk = await chunkVerschluesseln(inhalt.slice(), original.kopf, 0, original.schluessel, eintragId)
  const angaben: EingangInhalt = {
    name: 'IMG_1.jpg',
    typ: 'image/jpeg',
    aufgenommen: 1_700_000_000_000,
    original: original.kopf,
    vorschau: vorschau.kopf,
    miniatur: miniatur.kopf,
    quelle: { geraet: telefon.geraet, medienId: 7, art: 'bild', sha256: 'c'.repeat(64), ...quelle },
  }
  return { angaben, chunk }
}

/** Wie `verpacken` in `KameraArbeit.kt`: auf 4 KiB gepolstert, hybrid verschlüsselt, unterschrieben. */
async function verpacken(inhalt: EingangInhalt, bucket: string, eingangId: string, geraet: string, unterschreiben: Geraet['unterschreiben']) {
  const json = JSON.stringify(inhalt)
  const bytes = new TextEncoder().encode(json).length
  const gepolstert = json + ' '.repeat(Math.ceil((bytes + 1) / 4096) * 4096 - bytes)
  const daten = await hybridEncrypt(gepolstert, schluessel.pqPublicKey, schluessel.rsaPublicKey, `msm-tresor-eingang-v1:${bucket}:${eingangId}`)
  const signiert = ['msm-tresor-eingang-v1', '1', bucket, eingangId, schluessel.id, geraet, daten].join('\n')
  const signatur = bytesToBase64(await unterschreiben(utf8ToBytes(signiert)))
  return JSON.stringify({ v: 1, schluessel: schluessel.id, geraet, daten, signatur })
}

async function ablegen(
  id: string,
  angaben: EingangInhalt,
  { geraet = telefon, unterschrift = geraet, bucket = BUCKET, verpacktAls = id } = {} as {
    geraet?: Geraet
    unterschrift?: Geraet
    bucket?: string
    verpacktAls?: string
  },
) {
  const text = await verpacken(angaben, bucket, verpacktAls, geraet.geraet, unterschrift.unterschreiben)
  if (!posteingang.has(BUCKET)) posteingang.set(BUCKET, new Map())
  posteingang.get(BUCKET)!.set(id, text)
}

function dateiEintraege(): VaultItem[] {
  return useVaultStore.getState().items.filter((i) => i.category === 'datei')
}

function liegtNoch(id: string): boolean {
  return posteingang.get(BUCKET)?.has(id) ?? false
}

beforeEach(async () => {
  globalThis.indexedDB = new IDBFactory()
  localStorage.clear()
  posteingang.clear()
  angelegt.clear()
  setzeAngemeldetesKonto(1)
  vi.restoreAllMocks()
  vi.clearAllMocks()
  await tresorOeffnen()
  telefon = await geraetAnlegen()
  echoServer()
  schluessel = (await posteingangEinrichten(BUCKET, GERAET, async () => telefon.spki))!
})

afterEach(async () => {
  vi.restoreAllMocks()
  await ablageLoeschen()
  setzeAngemeldetesKonto(null)
})

describe('Posteingang einrichten', () => {
  it('legt Schlüsselpaar und Gerät einmal an und nennt den öffentlichen Teil erst, wenn beides beim Server liegt', async () => {
    useVaultStore.setState({ items: [] })
    localStorage.clear()
    offline()
    expect(await posteingangEinrichten(BUCKET, GERAET, async () => telefon.spki)).toBeNull()
    expect(await posteingangEinrichten(BUCKET, GERAET, async () => telefon.spki)).toBeNull()

    const system = useVaultStore.getState().items.filter((i) => i.category === SYSTEM_KATEGORIE)
    expect(system.map((i) => i.sicherung?.art).sort()).toEqual(['geraet', 'posteingang'])

    vi.restoreAllMocks()
    echoServer()
    const oeffentlich = await posteingangEinrichten(BUCKET, GERAET, async () => telefon.spki)
    const paar = system.find((i) => i.sicherung?.art === 'posteingang')!.sicherung as { id: string; pqPublicKey: string }
    expect(oeffentlich).toMatchObject({ id: paar.id, pqPublicKey: paar.pqPublicKey })
    // Den geheimen Teil bekommt das Gerät nie.
    expect(Object.keys(oeffentlich!).sort()).toEqual(['id', 'pqPublicKey', 'rsaPublicKey'])
  })

  it('gibt bei gesperrtem Tresor nichts zurück', async () => {
    useVaultStore.setState({ isUnlocked: false, userKey: null })
    expect(await posteingangEinrichten(BUCKET, GERAET, async () => telefon.spki)).toBeNull()
  })
})

describe('Übernahme beim Entsperren', () => {
  it('macht aus einem Datensatz einen Eintrag, dessen Schlüssel die hochgeladenen Chunks öffnen', async () => {
    const id = crypto.randomUUID()
    const inhalt = new TextEncoder().encode('Foto vom Gipfel')
    const { angaben, chunk } = await aufnahme(id, inhalt)
    await ablegen(id, angaben)

    await eingangAnstossen(BUCKET)

    const [eintrag] = dateiEintraege()
    expect(eintrag).toMatchObject({ id, service: 'IMG_1.jpg', datei: { typ: 'image/jpeg', aufgenommen: 1_700_000_000_000, quelle: angaben.quelle } })
    const original = eintrag.datei!.original as BlobKopf
    // Gewickelt, nicht mehr roh, und doch derselbe Blob.
    expect(original.schluessel).not.toBe(angaben.original.schluessel)
    expect({ ...original, schluessel: '' }).toEqual({ ...angaben.original, schluessel: '' })
    const geoeffnet = await chunkEntschluesseln(chunk, original, 0, await blobSchluessel(original, userKey, id), id)
    expect(new TextDecoder().decode(geoeffnet)).toBe('Foto vom Gipfel')

    // „Speicher freigeben“ glaubt dem Server erst, wenn sich sein letzter Chunk öffnen lässt.
    vi.mocked(chunkLaden).mockResolvedValueOnce(chunk)
    expect(await letzterChunkOeffnetSich(original, id, userKey)).toBe(true)
    const gekippt = chunk.slice()
    gekippt[20] ^= 1
    vi.mocked(chunkLaden).mockResolvedValueOnce(gekippt)
    expect(await letzterChunkOeffnetSich(original, id, userKey)).toBe(false)
    vi.mocked(chunkLaden).mockRejectedValueOnce(new SanitizedApiError('weg', { status: 404 }))
    expect(await letzterChunkOeffnetSich(original, id, userKey)).toBe(false)
  })

  it('löscht den Datensatz erst, wenn der Eintrag beim Server liegt', async () => {
    const id = crypto.randomUUID()
    const { angaben } = await aufnahme(id, new Uint8Array(10))
    await ablegen(id, angaben)

    vi.restoreAllMocks()
    offline()
    await eingangAnstossen(BUCKET)
    expect(dateiEintraege()).toHaveLength(1)
    expect(liegtNoch(id)).toBe(true)

    // Ein zweiter Lauf ohne Server ändert nichts und legt keinen zweiten Eintrag an.
    await eingangAnstossen(BUCKET)
    expect(dateiEintraege()).toHaveLength(1)
    expect(liegtNoch(id)).toBe(true)

    vi.restoreAllMocks()
    echoServer()
    await useVaultStore.getState().syncWithServer()
    await eingangAnstossen(BUCKET)
    expect(liegtNoch(id)).toBe(false)
    expect(dateiEintraege()).toHaveLength(1)
  })

  it('verwirft einen Datensatz, den ein anderer Schlüssel unterschrieben hat', async () => {
    const id = crypto.randomUUID()
    const { angaben } = await aufnahme(id, new Uint8Array(10))
    // Wer das Telefon ausliest, kennt den öffentlichen Schlüssel und die Gerätekennung, nicht den Keystore.
    await ablegen(id, angaben, { unterschrift: await geraetAnlegen() })

    await eingangAnstossen(BUCKET)
    expect(dateiEintraege()).toEqual([])
    expect(liegtNoch(id)).toBe(false)
  })

  it('lässt den Datensatz eines Geräts liegen, das der Tresor noch nicht kennt', async () => {
    const fremd = await geraetAnlegen('9f8e7d6c-5b4a-4321-8fed-cba987654321')
    const id = crypto.randomUUID()
    const { angaben } = await aufnahme(id, new Uint8Array(10), { geraet: fremd.geraet })
    await ablegen(id, angaben, { geraet: fremd })

    await eingangAnstossen(BUCKET)
    expect(dateiEintraege()).toEqual([])
    expect(liegtNoch(id)).toBe(true)
  })

  it('räumt den Datensatz eines Geräts, das nach einer Woche noch unbekannt ist, samt Blobs ab', async () => {
    const fremd = await geraetAnlegen('9f8e7d6c-5b4a-4321-8fed-cba987654321')
    const id = crypto.randomUUID()
    const { angaben } = await aufnahme(id, new Uint8Array(10), { geraet: fremd.geraet })
    await ablegen(id, angaben, { geraet: fremd })
    angelegt.set(id, new Date(Date.now() - WARTEN_HOECHSTENS_MS - 60_000).toISOString())

    await eingangAnstossen(BUCKET)
    expect(dateiEintraege()).toEqual([])
    expect(liegtNoch(id)).toBe(false)
    const freigegeben = vi.mocked(blobLoeschen).mock.calls.map(([, blob, loeschen]) => [blob, loeschen])
    expect(freigegeben).toEqual(
      [angaben.original, angaben.vorschau, angaben.miniatur].map((k) => [k.id, k.loeschen]),
    )
  })

  it('verwirft, wenn ein Gerät für die Aufnahmen eines anderen spricht', async () => {
    const id = crypto.randomUUID()
    const { angaben } = await aufnahme(id, new Uint8Array(10), { geraet: '9f8e7d6c-5b4a-4321-8fed-cba987654321' })
    await ablegen(id, angaben)

    await eingangAnstossen(BUCKET)
    expect(dateiEintraege()).toEqual([])
    expect(liegtNoch(id)).toBe(false)
  })

  it.each([
    ['unter anderer Kennung', { verpacktAls: '11111111-2222-4333-8444-555555555555' }],
    ['für einen anderen Bucket', { bucket: 'f'.repeat(64) }],
  ])('verwirft einen Datensatz, der %s verpackt wurde', async (_fall, abweichung) => {
    const id = crypto.randomUUID()
    const { angaben } = await aufnahme(id, new Uint8Array(10))
    await ablegen(id, angaben, abweichung)

    await eingangAnstossen(BUCKET)
    expect(dateiEintraege()).toEqual([])
    expect(liegtNoch(id)).toBe(false)
  })

  it('übernimmt eine aus einer anderen App geteilte Datei, die keine Aufnahme ist', async () => {
    // Geteilt hat keine Quelle: „Speicher freigeben“ fasst sie nie an, und zweimal geteilt sind zwei Einträge.
    const ids = [crypto.randomUUID(), crypto.randomUUID()]
    for (const id of ids) {
      const { angaben } = await aufnahme(id, new Uint8Array(10))
      await ablegen(id, { ...angaben, name: 'Rechnung.pdf', typ: 'application/pdf', aufgenommen: undefined, quelle: undefined })
    }

    await eingangAnstossen(BUCKET)
    const eintraege = dateiEintraege()
    expect(eintraege.map((e) => e.id).sort()).toEqual([...ids].sort())
    for (const e of eintraege) {
      expect(e).toMatchObject({ service: 'Rechnung.pdf', datei: { typ: 'application/pdf' } })
      expect(e.datei!.quelle).toBeUndefined()
    }
  })

  it('legt eine geteilte Datei nicht an, die das Telefon schon als Aufnahme gesichert hat', async () => {
    // WhatsApp legt ein Foto in einen gesicherten Ordner, danach teilt es jemand in den Tresor.
    const aufgenommen = crypto.randomUUID()
    const erste = await aufnahme(aufgenommen, new Uint8Array(10))
    await ablegen(aufgenommen, erste.angaben)
    await eingangAnstossen(BUCKET)

    const id = crypto.randomUUID()
    const { angaben } = await aufnahme(id, new Uint8Array(10))
    await ablegen(id, { ...angaben, quelle: undefined, sha256: erste.angaben.quelle!.sha256 })

    await eingangAnstossen(BUCKET)
    expect(dateiEintraege().map((e) => e.id)).toEqual([aufgenommen])
    expect(liegtNoch(id)).toBe(false)
    const freigegeben = vi.mocked(blobLoeschen).mock.calls.map(([, blob]) => blob)
    expect(freigegeben).toEqual([angaben.original.id, angaben.vorschau.id, angaben.miniatur.id])
  })

  it('legt eine geteilte Datei an, deren Aufnahme im Papierkorb liegt', async () => {
    const aufgenommen = crypto.randomUUID()
    const erste = await aufnahme(aufgenommen, new Uint8Array(10))
    await ablegen(aufgenommen, erste.angaben)
    await eingangAnstossen(BUCKET)
    await useVaultStore.getState().trashItem(aufgenommen)

    const id = crypto.randomUUID()
    const { angaben } = await aufnahme(id, new Uint8Array(10))
    await ablegen(id, { ...angaben, quelle: undefined, sha256: erste.angaben.quelle!.sha256 })

    await eingangAnstossen(BUCKET)
    expect(dateiEintraege().map((e) => e.id).sort()).toEqual([aufgenommen, id].sort())
  })

  it('legt dieselbe Aufnahme kein zweites Mal an und gibt ihre Blobs frei', async () => {
    // Dieselbe Aufnahme zweimal, etwa nach einem neu aufgebauten MediaStore.
    const erste = crypto.randomUUID()
    await ablegen(erste, (await aufnahme(erste, new Uint8Array(10))).angaben)
    await eingangAnstossen(BUCKET)
    expect(dateiEintraege().map((e) => e.id)).toEqual([erste])

    const id = crypto.randomUUID()
    const { angaben } = await aufnahme(id, new Uint8Array(10))
    await ablegen(id, angaben)

    await eingangAnstossen(BUCKET)
    expect(dateiEintraege().map((e) => e.id)).toEqual([erste])
    expect(liegtNoch(id)).toBe(false)
    const freigegeben = vi.mocked(blobLoeschen).mock.calls.map(([, blob, loeschen]) => [blob, loeschen])
    expect(freigegeben).toEqual([angaben.original, angaben.vorschau, angaben.miniatur].map((k) => [k.id, k.loeschen]))
  })

  it('übernimmt erst, wenn das Telefon alle drei Blobs fertig hochgeladen hat', async () => {
    const id = crypto.randomUUID()
    const { angaben } = await aufnahme(id, new Uint8Array(10))
    await ablegen(id, angaben)
    vi.mocked(blobStand).mockImplementation(async (_bucket, blob) => ({
      state: blob === angaben.original.id ? 'offen' : 'fertig',
      chunk_count: 1,
      vorhanden: [],
    }))

    await eingangAnstossen(BUCKET)
    expect(dateiEintraege()).toEqual([])
    expect(liegtNoch(id)).toBe(true)

    vi.mocked(blobStand).mockImplementation(async () => ({ state: 'fertig', chunk_count: 1, vorhanden: [0] }))
    await eingangAnstossen(BUCKET)
    expect(dateiEintraege().map((e) => e.id)).toEqual([id])
  })

  it('verwirft den Datensatz und gibt die übrigen Blobs frei, wenn einer beim Server weg ist', async () => {
    const id = crypto.randomUUID()
    const { angaben } = await aufnahme(id, new Uint8Array(10))
    await ablegen(id, angaben)
    // Abgebrochen und vom Server aufgeräumt: das Telefon fängt diese Aufnahme neu an.
    vi.mocked(blobStand).mockImplementation(async (_bucket, blob) => {
      if (blob === angaben.original.id) throw new SanitizedApiError('weg', { status: 404 })
      return { state: 'offen', chunk_count: 1, vorhanden: [] }
    })

    await eingangAnstossen(BUCKET)
    expect(dateiEintraege()).toEqual([])
    expect(liegtNoch(id)).toBe(false)
    const freigegeben = vi.mocked(blobLoeschen).mock.calls.map(([, blob]) => blob)
    expect(freigegeben).toEqual([angaben.original.id, angaben.vorschau.id, angaben.miniatur.id])
  })

  it('übernimmt nichts bei gesperrtem Tresor', async () => {
    const id = crypto.randomUUID()
    const { angaben } = await aufnahme(id, new Uint8Array(10))
    await ablegen(id, angaben)
    useVaultStore.setState({ isUnlocked: false, userKey: null })

    await eingangAnstossen(BUCKET)
    expect(liegtNoch(id)).toBe(true)
  })
})

describe('Zugangsdaten aus dem Browser', () => {
  /** Wie der Browser bei gesperrtem Tresor ablegt: nur mit dem öffentlichen Schlüssel. */
  async function zugangAblegen(id: string, zugang: Record<string, unknown>, geraet = telefon) {
    const text = await eingangVerpacken(zugang, BUCKET, id, schluessel, geraet.geraet, geraet.unterschreiben)
    if (!posteingang.has(BUCKET)) posteingang.set(BUCKET, new Map())
    posteingang.get(BUCKET)!.set(id, text)
  }
  const zugang = { art: 'zugang', url: 'https://www.example.com/login', benutzer: 'ada@example.com', passwort: 'Gipfel-2026!', zeit: 1_790_000_000_000 }
  const anmeldungen = () => useVaultStore.getState().items.filter((i) => (i.category ?? 'login') === 'login')

  it('macht beim Entsperren einen Eintrag daraus und löscht den Datensatz erst danach', async () => {
    const id = crypto.randomUUID()
    await zugangAblegen(id, zugang)
    // Der Datensatz verrät seine Länge nicht.
    expect(JSON.parse(posteingang.get(BUCKET)!.get(id)!).daten.length).toBeGreaterThan(4096)

    await eingangAnstossen(BUCKET)
    expect(anmeldungen()).toEqual([
      expect.objectContaining({ id, service: 'example.com', url: zugang.url, username: zugang.benutzer, password: zugang.passwort }),
    ])
    await useVaultStore.getState().syncWithServer()
    // Das Speichern stößt selbst einen Abgleich an; erst danach liegt der Eintrag beim Server.
    await vi.waitFor(() => expect(getPendingQueue(BUCKET)).toEqual([]))
    await eingangAnstossen(BUCKET)
    expect(liegtNoch(id)).toBe(false)
  })

  it('legt dieselben Zugangsdaten nicht zweimal an, ein geändertes Passwort schon', async () => {
    await useVaultStore.getState().saveItem({ service: 'example.com', url: 'https://example.com', username: zugang.benutzer, password: zugang.passwort })
    const gleich = crypto.randomUUID()
    const neu = crypto.randomUUID()
    await zugangAblegen(gleich, zugang)
    await zugangAblegen(neu, { ...zugang, passwort: 'Neu-2026!' })

    await eingangAnstossen(BUCKET)
    expect(liegtNoch(gleich)).toBe(false)
    expect(anmeldungen().map((i) => i.password).sort()).toEqual(['Gipfel-2026!', 'Neu-2026!'])
  })

  it('verwirft, was nicht von einem eingetragenen Gerät kommt oder keine Webadresse trägt', async () => {
    const fremd = crypto.randomUUID()
    const skript = crypto.randomUUID()
    await zugangAblegen(fremd, zugang, await geraetAnlegen('unbekannt'))
    // Die unbekannte Kennung wartet; ein falsch unterschriebener Datensatz eines bekannten Geräts fällt.
    const gefaelscht = crypto.randomUUID()
    await zugangAblegen(gefaelscht, zugang, { ...(await geraetAnlegen()), geraet: GERAET })
    await zugangAblegen(skript, { ...zugang, url: 'javascript:alert(1)' })

    await eingangAnstossen(BUCKET)
    expect(anmeldungen()).toEqual([])
    expect(liegtNoch(fremd)).toBe(true)
    expect(liegtNoch(gefaelscht)).toBe(false)
    expect(liegtNoch(skript)).toBe(false)
  })
})
