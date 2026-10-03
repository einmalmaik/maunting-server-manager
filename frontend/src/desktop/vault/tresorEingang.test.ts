/**
 * Posteingang des Tresors: was ein Telefon bei gesperrtem Tresor sichert, wird
 * beim nächsten Entsperren ein Eintrag. Mit echter Krypto (DIS hybrid,
 * ECDSA P-256 aus WebCrypto anstelle des Android Keystore).
 *
 * Geprüft wird, was die Übernahme annimmt und was sie verwirft: nur Datensätze
 * eines eingetragenen Geräts mit gültiger Unterschrift, gebunden an Bucket und
 * Kennung, und nur für Aufnahmen dieses Geräts. Gelöscht wird ein Datensatz
 * erst, wenn sein Eintrag beim Server liegt.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import 'fake-indexeddb/auto'
import { IDBFactory } from 'fake-indexeddb'
import { bytesToBase64 } from '@msdis/shield/core'
import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import { ablageLoeschen } from './tresorAblage'
import { blobLoeschen } from './tresorBlobApi'
import { blobAnlegen, blobSchluessel, chunkEntschluesseln, chunkVerschluesseln, type BlobKopf } from './tresorDatei'
import type { DateiQuelle } from './tresorDateien'
import { eingangAnstossen, eingangVerpacken, posteingangEinrichten, type EingangInhalt, type EingangOeffentlich } from './tresorEingang'
import { SYSTEM_KATEGORIE } from './vaultEintrag'
import { useVaultStore, type VaultBlindSyncPayload, type VaultItem } from './vaultStore'

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
const { posteingang } = vi.hoisted(() => ({ posteingang: new Map<string, Map<string, string>>() }))

vi.mock('./tresorBlobApi', () => ({
  bucketMelderSetzen: vi.fn(),
  zurueckgesetztFrage: vi.fn(),
  speicherAbfragen: vi.fn(),
  blobReservieren: vi.fn(),
  blobStand: vi.fn(),
  chunkHochladen: vi.fn(),
  blobFertig: vi.fn(),
  chunkLaden: vi.fn(),
  kleineLaden: vi.fn(),
  eingangAblegen: vi.fn(),
  eingangListe: vi.fn(async (bucket: string, nach?: string) => {
    const eintraege = [...(posteingang.get(bucket) ?? new Map<string, string>()).entries()]
      .filter(([id]) => !nach || id > nach)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([id, ciphertext]) => ({ id, ciphertext }))
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

/** Was das Telefon bei gesperrtem Tresor hochlädt: drei Blobs mit rohen Schlüsseln, der Inhalt im Original. */
async function aufnahme(eintragId: string, inhalt: Uint8Array, quelle: Partial<DateiQuelle> = {}) {
  const original = await blobAnlegen(null, eintragId, inhalt.length)
  const vorschau = await blobAnlegen(null, eintragId, 0, 512 * 1024)
  const miniatur = await blobAnlegen(null, eintragId, 0, 32 * 1024)
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
  const text = await eingangVerpacken(angaben, bucket, verpacktAls, schluessel, geraet.geraet, unterschrift.unterschreiben)
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

  it('übernimmt nichts bei gesperrtem Tresor', async () => {
    const id = crypto.randomUUID()
    const { angaben } = await aufnahme(id, new Uint8Array(10))
    await ablegen(id, angaben)
    useVaultStore.setState({ isUnlocked: false, userKey: null })

    await eingangAnstossen(BUCKET)
    expect(liegtNoch(id)).toBe(true)
  })
})
