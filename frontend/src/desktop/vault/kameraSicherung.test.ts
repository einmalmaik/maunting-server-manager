/**
 * Kamera-Sicherung: was gesichert wird, was nicht, und was „Speicher freigeben“
 * aus der Galerie nehmen darf.
 *
 * Die Aufnahmen des Geräts stellt ein nachgebauter MediaStore, die Ablage eine
 * echte IndexedDB-Nachbildung. `dateiHinzufuegen` wird abgefangen: wie eine
 * Datei verschlüsselt und hochgeht, prüfen die Tests von `tresorDateien`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import 'fake-indexeddb/auto'
import { SanitizedApiError } from '@/api/client'
import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import type { Aufnahme, MedienArt } from '@/desktop/tauri'
import * as tauri from '@/desktop/tauri'
import { ablageDb, ablageLoeschen, anfrage, UPLOADS } from './tresorAblage'
import { blobStand, eingangAblegen } from './tresorBlobApi'
import { uploadsFortsetzen } from './tresorDateien'
import { eingangVerpacken, posteingangEinrichten, type EingangInhalt } from './tresorEingang'
import { useVaultStore, type VaultItem } from './vaultStore'
import {
  freigebbar,
  kameraAnstossen,
  kameraAusschalten,
  kameraEinschalten,
  kameraNurWlan,
  kameraStandLaden,
  kameraVorhandeneSichern,
  useKameraSicherung,
} from './kameraSicherung'

vi.mock('@/desktop/tauri', () => ({
  FACH_TRESOR: 'vault_biometric_key',
  biometrieSpeichern: vi.fn(),
  biometrieEntsperren: vi.fn(),
  biometrieLoeschen: vi.fn(),
  pruefeBiometrieVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherFragtSelbst: vi.fn().mockResolvedValue(false),
  verifiziereBiometrie: vi.fn().mockResolvedValue(false),
  setzeTresorSchutz: vi.fn(),
  medienZugriff: vi.fn(),
  medienStand: vi.fn(),
  medienAufnahmen: vi.fn(),
  medienLesen: vi.fn(),
  medienPruefsumme: vi.fn(),
  medienPapierkorb: vi.fn(),
  sicherungSchluessel: vi.fn(),
  sicherungSignieren: vi.fn(),
}))

// Verschlüsseln und Unterschreiben prüft `tresorEingang.test.ts`; hier nur, was hineingeht.
vi.mock('./tresorEingang', () => ({
  posteingangEinrichten: vi.fn(),
  eingangVerpacken: vi.fn(async () => 'umschlag'),
}))

vi.mock('./tresorBilder', () => ({ bildAngaben: vi.fn(async () => ({})) }))

// Hochgeladen wird in den Tests nicht: die Ablage soll so bleiben, wie die Sicherung sie hinterlässt.
vi.mock('./tresorDateien', async (original) => ({
  ...(await original<typeof import('./tresorDateien')>()),
  uploadsFortsetzen: vi.fn(),
}))

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
  blobLoeschen: vi.fn(),
  eingangAblegen: vi.fn(),
}))

const BUCKET = 'b'.repeat(64)
const MiB = 1024 * 1024

interface Medium {
  art: MedienArt
  name: string
  typ: string
  bytes: Uint8Array
  /** Wird noch geschrieben (`IS_PENDING`): der MediaStore liefert sie nicht. */
  wartend?: boolean
  marke?: number
}

/** Generation des MediaStore: jede Änderung bekommt die nächste. */
let generation: number
let fassung: string

/** Der MediaStore des Geräts: Kennung → Aufnahme. Anlegen zählt die Generation hoch. */
class MediaStore extends Map<number, Medium> {
  set(id: number, m: Medium) {
    m.marke = ++generation
    return super.set(id, m)
  }
}

/** Die Kamera schreibt eine Aufnahme fertig: neue Generation, wie in Android. */
function fertigGeschrieben(id: number) {
  const m = medien.get(id)!
  m.wartend = false
  m.marke = ++generation
}

let medien: MediaStore
let hinzugefuegt: { datei: File; quelle: unknown; original: unknown }[]

async function sha256(bytes: Uint8Array): Promise<string> {
  const h = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes as BufferSource))
  return [...h].map((b) => b.toString(16).padStart(2, '0')).join('')
}

/** `toEqual` auf Megabytes braucht Minuten. */
async function gleich(datei: File, erwartet: Uint8Array): Promise<boolean> {
  return Buffer.from(await datei.arrayBuffer()).equals(Buffer.from(erwartet))
}

function bytes(laenge: number, saat: number): Uint8Array {
  const b = new Uint8Array(laenge)
  for (let i = 0; i < laenge; i++) b[i] = (i * 31 + saat) & 0xff
  return b
}

function aufnahme(id: number): Aufnahme {
  const m = medien.get(id)!
  return { id, marke: m.marke!, art: m.art, name: m.name, typ: m.typ, groesse: m.bytes.length, aufgenommen: 1_700_000_000_000 + id, geaendert: 0 }
}

function mediaStoreNachbauen() {
  vi.mocked(tauri.medienZugriff).mockResolvedValue({ stand: 'voll', papierkorb: true })
  vi.mocked(tauri.medienStand).mockImplementation(async () => ({ marke: generation, fassung }))
  vi.mocked(tauri.medienAufnahmen).mockImplementation(async (nach, hoechstens) =>
    [...medien.keys()]
      .map(aufnahme)
      .filter((a) => !medien.get(a.id)!.wartend && a.marke > nach)
      .sort((a, b) => a.marke - b.marke)
      .slice(0, hoechstens),
  )
  vi.mocked(tauri.medienLesen).mockImplementation(async (id, _art, von, laenge) => {
    const m = medien.get(id)
    if (!m) throw new Error('weg')
    return m.bytes.slice(von, von + laenge)
  })
  vi.mocked(tauri.medienPruefsumme).mockImplementation(async (id) => {
    const m = medien.get(id)
    if (!m) throw new Error('NICHT_LESBAR')
    return { sha256: await sha256(m.bytes), groesse: m.bytes.length }
  })
}

function tresorOeffnen(items: VaultItem[] = []) {
  useVaultStore.setState({
    isUnlocked: true,
    userKey: {} as CryptoKey,
    bucketId: BUCKET,
    items,
    dateiHinzufuegen: vi.fn(async (datei: File, _ordner?: string, optionen?: { quelle?: unknown; original?: unknown }) => {
      hinzugefuegt.push({ datei, quelle: optionen?.quelle, original: optionen?.original })
      return crypto.randomUUID()
    }),
  })
}

/** Schaltet die Sicherung ein; was jetzt schon im MediaStore liegt, gilt als alt. */
async function eingeschaltet() {
  // Gesperrt, damit der Lauf, den das Einschalten anstößt, sofort endet und
  // nicht in den Test hineinläuft. Danach genau ein Lauf.
  useVaultStore.setState({ isUnlocked: false })
  await kameraEinschalten(BUCKET)
  useVaultStore.setState({ isUnlocked: true })
  await kameraAnstossen(BUCKET)
  hinzugefuegt = []
}

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory()
  setzeAngemeldetesKonto(1)
  localStorage.clear()
  generation = 0
  fassung = 'gen:1'
  medien = new MediaStore()
  hinzugefuegt = []
  mediaStoreNachbauen()
  tresorOeffnen()
  useKameraSicherung.setState({ stand: null, laeuft: false, warten: null })
  vi.mocked(posteingangEinrichten).mockResolvedValue(null)
})

afterEach(async () => {
  await ablageLoeschen()
  setzeAngemeldetesKonto(null)
  vi.clearAllMocks()
})

describe('Kamera-Sicherung', () => {
  it('sichert nur, was nach dem Einschalten aufgenommen wurde, Bit für Bit und ohne zweite Kopie im Cache', async () => {
    medien.set(5, { art: 'bild', name: 'vorher.jpg', typ: 'image/jpeg', bytes: bytes(10, 9) })
    await eingeschaltet()
    const gross = bytes(9 * MiB + 17, 3) // drei Stücke zu 4 MiB
    medien.set(7, { art: 'video', name: 'VID_7.mp4', typ: 'video/mp4', bytes: gross })
    medien.set(9, { art: 'bild', name: 'IMG_9.jpg', typ: 'image/jpeg', bytes: bytes(1000, 4) })

    await kameraAnstossen(BUCKET)

    expect(hinzugefuegt.map((h) => h.datei.name)).toEqual(['VID_7.mp4', 'IMG_9.jpg'])
    const video = hinzugefuegt[0]
    expect(await gleich(video.datei, gross)).toBe(true)
    expect(video.datei.type).toBe('video/mp4')
    expect(video.original).toBe('nein')
    expect(video.quelle).toMatchObject({ medienId: 7, art: 'video', sha256: await sha256(gross) })
    const stand = await kameraStandLaden(BUCKET)
    expect(stand).toMatchObject({ bis: medien.get(9)!.marke, gesichert: 2 })
  })

  it('sichert nichts, solange der Schalter aus ist', async () => {
    await eingeschaltet()
    await kameraAusschalten(BUCKET)
    medien.set(3, { art: 'bild', name: 'a.jpg', typ: 'image/jpeg', bytes: bytes(10, 1) })
    await kameraAnstossen(BUCKET)
    expect(hinzugefuegt).toEqual([])
    expect(tauri.medienLesen).not.toHaveBeenCalled()
  })

  it('sichert eine Datei nicht, die sich während des Lesens ändert, und versucht es später noch einmal', async () => {
    await eingeschaltet()
    const m: Medium = { art: 'bild', name: 'a.jpg', typ: 'image/jpeg', bytes: bytes(5 * MiB, 1) }
    medien.set(3, m)
    vi.mocked(tauri.medienLesen).mockImplementationOnce(async (_id, _art, von, laenge) => {
      const teil = m.bytes.slice(von, von + laenge)
      m.bytes = bytes(5 * MiB, 2) // die Kamera schreibt noch
      return teil
    })
    await kameraAnstossen(BUCKET)
    expect(hinzugefuegt).toEqual([])
    expect((await kameraStandLaden(BUCKET))?.bis).toBe(0)

    await kameraAnstossen(BUCKET)
    expect(hinzugefuegt).toHaveLength(1)
    expect(await gleich(hinzugefuegt[0].datei, m.bytes)).toBe(true)
  })

  it('sichert eine Aufnahme nicht doppelt, die schon im Tresor liegt', async () => {
    await eingeschaltet()
    const inhalt = bytes(100, 5)
    medien.set(4, { art: 'bild', name: 'a.jpg', typ: 'image/jpeg', bytes: inhalt })
    const geraet = useKameraSicherung.getState().stand!.geraet
    tresorOeffnen([
      { id: 'x', category: 'datei', datei: { quelle: { geraet, medienId: 4, art: 'bild', sha256: await sha256(inhalt) } } } as unknown as VaultItem,
    ])
    await kameraAnstossen(BUCKET)
    expect(hinzugefuegt).toEqual([])
    expect((await kameraStandLaden(BUCKET))?.bis).toBe(medien.get(4)!.marke)
  })

  it('sichert eine Aufnahme, die erst nach einer späteren fertig wird (Foto während eines Videos)', async () => {
    await eingeschaltet()
    medien.set(3, { art: 'video', name: 'VID_3.mp4', typ: 'video/mp4', bytes: bytes(2000, 1), wartend: true })
    medien.set(4, { art: 'bild', name: 'IMG_4.jpg', typ: 'image/jpeg', bytes: bytes(100, 2) })
    await kameraAnstossen(BUCKET)
    expect(hinzugefuegt.map((h) => h.datei.name)).toEqual(['IMG_4.jpg'])

    fertigGeschrieben(3)
    await kameraAnstossen(BUCKET)
    expect(hinzugefuegt.map((h) => h.datei.name)).toEqual(['IMG_4.jpg', 'VID_3.mp4'])
  })

  it('bleibt nach einem neu aufgebauten MediaStore nicht stehen und sichert dabei nichts Altes', async () => {
    medien.set(1, { art: 'bild', name: 'alt.jpg', typ: 'image/jpeg', bytes: bytes(10, 1) })
    await eingeschaltet()
    generation += 1000 // ein lange benutzter MediaStore hat viele Änderungen hinter sich
    for (let i = 2; i <= 6; i++) medien.set(i, { art: 'bild', name: `${i}.jpg`, typ: 'image/jpeg', bytes: bytes(10, i) })
    await kameraAnstossen(BUCKET)
    expect(hinzugefuegt).toHaveLength(5)
    hinzugefuegt = []

    // Android baut die Datenbank neu: neue Fassung, die Generationen beginnen klein.
    const alt = [...medien.entries()]
    generation = 0
    fassung = 'gen:2'
    medien = new MediaStore()
    for (const [id, m] of alt) medien.set(id + 100, { ...m })
    await kameraAnstossen(BUCKET)
    expect(hinzugefuegt).toEqual([])

    medien.set(200, { art: 'bild', name: 'neu.jpg', typ: 'image/jpeg', bytes: bytes(10, 77) })
    await kameraAnstossen(BUCKET)
    expect(hinzugefuegt.map((h) => h.datei.name)).toEqual(['neu.jpg'])
  })

  it('überspringt eine Aufnahme, die inzwischen gelöscht ist, und macht mit der nächsten weiter', async () => {
    await eingeschaltet()
    medien.set(2, { art: 'bild', name: 'weg.jpg', typ: 'image/jpeg', bytes: bytes(10, 1) })
    medien.set(3, { art: 'bild', name: 'da.jpg', typ: 'image/jpeg', bytes: bytes(10, 2) })
    vi.mocked(tauri.medienPruefsumme).mockRejectedValueOnce(new Error('NICHT_LESBAR'))
    await kameraAnstossen(BUCKET)
    expect(hinzugefuegt.map((h) => h.datei.name)).toEqual(['da.jpg'])
  })

  it('wartet auf WLAN, wenn das Netz unbekannt ist, und liest dann nichts', async () => {
    await eingeschaltet()
    await kameraNurWlan(BUCKET, true)
    medien.set(3, { art: 'bild', name: 'a.jpg', typ: 'image/jpeg', bytes: bytes(10, 1) })
    await kameraAnstossen(BUCKET)
    expect(useKameraSicherung.getState().warten).toBe('wlan')
    expect(tauri.medienLesen).not.toHaveBeenCalled()
  })

  it('sichert ohne vollen Zugriff nichts (nur ausgewählte Fotos oder ohne Aufnahmeort)', async () => {
    await eingeschaltet()
    vi.mocked(tauri.medienZugriff).mockResolvedValue({ stand: 'teilweise', papierkorb: true })
    medien.set(3, { art: 'bild', name: 'a.jpg', typ: 'image/jpeg', bytes: bytes(10, 1) })
    await kameraAnstossen(BUCKET)
    expect(useKameraSicherung.getState().warten).toBe('zugriff')
    expect(tauri.medienLesen).not.toHaveBeenCalled()
  })

  it('schaltet ohne vollen Zugriff nicht ein', async () => {
    vi.mocked(tauri.medienZugriff).mockResolvedValue({ stand: 'teilweise', papierkorb: true })
    expect((await kameraEinschalten(BUCKET)).stand).toBe('teilweise')
    expect(await kameraStandLaden(BUCKET)).toBeNull()
  })

  it('hört auf, wenn der Tresor während des Lesens gesperrt wird', async () => {
    await eingeschaltet()
    medien.set(3, { art: 'bild', name: 'a.jpg', typ: 'image/jpeg', bytes: bytes(9 * MiB, 1) })
    vi.mocked(tauri.medienLesen).mockImplementationOnce(async (_id, _art, von, laenge) => {
      useVaultStore.setState({ isUnlocked: false, userKey: null })
      return medien.get(3)!.bytes.slice(von, von + laenge)
    })
    await kameraAnstossen(BUCKET)
    expect(hinzugefuegt).toEqual([])
    expect(tauri.medienLesen).toHaveBeenCalledTimes(1)
    expect(useKameraSicherung.getState().warten).toBeNull()
  })

  it('sichert auf Wunsch auch, was vor dem Einschalten aufgenommen wurde', async () => {
    medien.set(2, { art: 'bild', name: 'alt.jpg', typ: 'image/jpeg', bytes: bytes(10, 1) })
    await kameraEinschalten(BUCKET)
    await kameraAnstossen(BUCKET)
    expect(hinzugefuegt).toEqual([])
    await kameraVorhandeneSichern(BUCKET)
    await kameraAnstossen(BUCKET)
    expect(hinzugefuegt.map((h) => h.datei.name)).toEqual(['alt.jpg'])
  })
})

describe('Kamera-Sicherung bei gesperrtem Tresor', () => {
  const EINGANG = { id: '11111111-2222-4333-8444-555555555555', pqPublicKey: 'pq', rsaPublicKey: 'rsa' }

  async function eingerichtetUndGesperrt() {
    vi.mocked(posteingangEinrichten).mockResolvedValue(EINGANG)
    await eingeschaltet()
    await vi.waitFor(async () => expect((await kameraStandLaden(BUCKET))?.eingang).toEqual(EINGANG))
    useVaultStore.setState({ isUnlocked: false, userKey: null, bucketId: null, items: [] })
  }

  async function uploads(): Promise<{ blobId: string; eintragId: string; eingang?: boolean; bereit: boolean }[]> {
    const db = (await ablageDb())!
    return (await anfrage(db.transaction(UPLOADS).objectStore(UPLOADS).getAll())) as never
  }

  it('lädt hoch und legt einen Datensatz in den Posteingang; einen Eintrag gibt es noch nicht', async () => {
    await eingerichtetUndGesperrt()
    const inhalt = bytes(5000, 3)
    medien.set(3, { art: 'bild', name: 'IMG_3.jpg', typ: 'image/jpeg', bytes: inhalt })

    await kameraAnstossen(BUCKET)

    const store = useVaultStore.getState()
    expect(store.dateiHinzufuegen).not.toHaveBeenCalled()
    expect(eingangAblegen).toHaveBeenCalledTimes(1)
    const [bucket, eintragId, umschlag] = vi.mocked(eingangAblegen).mock.calls[0]
    expect([bucket, umschlag]).toEqual([BUCKET, 'umschlag'])

    const [daten, verpackBucket, verpackId, schluessel, geraet] = vi.mocked(eingangVerpacken).mock.calls[0]
    const angaben = daten as EingangInhalt
    expect([verpackBucket, verpackId, schluessel, geraet]).toEqual([BUCKET, eintragId, EINGANG, (await kameraStandLaden(BUCKET))?.geraet])
    expect(angaben.name).toBe('IMG_3.jpg')
    expect(angaben.quelle).toMatchObject({ medienId: 3, art: 'bild', sha256: await sha256(inhalt), geraet })
    // Die Schlüssel sind roh, nur so lassen sie sich bei der Übernahme wickeln.
    for (const kopf of [angaben.original, angaben.vorschau, angaben.miniatur]) expect(kopf.schluessel).toMatch(/^[0-9a-f]{64}$/)

    const zeilen = await uploads()
    expect(zeilen).toHaveLength(3)
    expect(zeilen.every((z) => z.eingang && z.bereit && z.eintragId === eintragId)).toBe(true)
    expect(uploadsFortsetzen).toHaveBeenCalledWith(BUCKET)
    expect((await kameraStandLaden(BUCKET))?.gesichert).toBe(1)
  })

  it('nimmt die Blobs wieder aus der Ablage, wenn der Datensatz nicht ankommt, und versucht es später noch einmal', async () => {
    await eingerichtetUndGesperrt()
    medien.set(3, { art: 'bild', name: 'IMG_3.jpg', typ: 'image/jpeg', bytes: bytes(100, 1) })
    vi.mocked(eingangAblegen).mockRejectedValueOnce(new Error('offline'))

    await kameraAnstossen(BUCKET)
    expect(await uploads()).toEqual([])
    expect((await kameraStandLaden(BUCKET))?.gesichert).toBe(0)

    await kameraAnstossen(BUCKET)
    expect(eingangAblegen).toHaveBeenCalledTimes(2)
    expect(await uploads()).toHaveLength(3)
  })

  it('sichert nichts, solange der Posteingang nicht beim Server eingerichtet ist', async () => {
    await eingeschaltet()
    useVaultStore.setState({ isUnlocked: false, userKey: null, bucketId: null, items: [] })
    medien.set(3, { art: 'bild', name: 'IMG_3.jpg', typ: 'image/jpeg', bytes: bytes(100, 1) })
    await kameraAnstossen(BUCKET)
    expect(tauri.medienLesen).not.toHaveBeenCalled()
    expect(eingangAblegen).not.toHaveBeenCalled()
  })

  it('hört nach einem Zurücksetzen auf einem anderen Gerät auf und vergisst den Stand', async () => {
    await eingerichtetUndGesperrt()
    medien.set(3, { art: 'bild', name: 'IMG_3.jpg', typ: 'image/jpeg', bytes: bytes(100, 1) })
    vi.mocked(eingangAblegen).mockRejectedValueOnce(new SanitizedApiError('zurückgesetzt', { status: 410, code: 'VAULT_ZURUECKGESETZT' }))
    await kameraAnstossen(BUCKET)
    expect(await kameraStandLaden(BUCKET)).toBeNull()
    expect(await uploads()).toEqual([])
  })
})

describe('Speicher freigeben', () => {
  async function datei(id: string, medienId: number, inhalt: Uint8Array, mehr: Partial<VaultItem> = {}, geraet?: string) {
    const g = geraet ?? useKameraSicherung.getState().stand!.geraet
    return {
      id,
      service: `${id}.jpg`,
      category: 'datei',
      datei: {
        typ: 'image/jpeg',
        original: { id: `orig-${id}` },
        vorschau: { id: `vor-${id}` },
        miniatur: { id: `min-${id}` },
        quelle: { geraet: g, medienId, art: 'bild', sha256: await sha256(inhalt) },
      },
      ...mehr,
    } as unknown as VaultItem
  }

  it('nimmt nur, was dieses Gerät gesichert hat, beim Server fertig liegt und in der Galerie unverändert ist', async () => {
    await eingeschaltet()
    const inhalt = (n: number) => bytes(100, n)
    for (const n of [1, 2, 3, 4, 5, 6]) medien.set(n, { art: 'bild', name: `${n}.jpg`, typ: 'image/jpeg', bytes: inhalt(n) })
    medien.get(4)!.bytes = bytes(100, 99) // nach dem Sichern bearbeitet
    const items = [
      await datei('gut', 1, inhalt(1)),
      await datei('offen', 2, inhalt(2)), // Original noch nicht fertig beim Server
      await datei('wartet', 3, inhalt(3)), // Eintrag noch nicht beim Server
      await datei('anders', 4, inhalt(4)),
      await datei('papierkorb', 5, inhalt(5), { trashedAt: Date.now() }),
      await datei('fremd', 6, inhalt(6), {}, 'anderes-geraet'),
    ]
    tresorOeffnen(items)
    localStorage.setItem(`mss:vault_pending_${BUCKET}`, JSON.stringify([{ id: 'wartet', ciphertext: 'x', revision: 0, is_deleted: false }]))
    vi.mocked(blobStand).mockImplementation(async (_b, id) => ({
      state: id === 'orig-offen' ? 'offen' : 'fertig',
      chunk_count: 1,
      vorhanden: [0],
    }))

    const freigabe = await freigebbar(BUCKET)
    expect(freigabe).toEqual({ bilder: [1], videos: [], bytes: 100 })
  })

  it('nimmt nichts, wenn der Server nicht antwortet', async () => {
    await eingeschaltet()
    medien.set(1, { art: 'bild', name: '1.jpg', typ: 'image/jpeg', bytes: bytes(100, 1) })
    tresorOeffnen([await datei('gut', 1, bytes(100, 1))])
    vi.mocked(blobStand).mockRejectedValue(new Error('offline'))
    expect(await freigebbar(BUCKET)).toEqual({ bilder: [], videos: [], bytes: 0 })
  })
})
