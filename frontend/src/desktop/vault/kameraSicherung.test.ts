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
import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import type { Aufnahme, MedienArt } from '@/desktop/tauri'
import * as tauri from '@/desktop/tauri'
import { ablageLoeschen } from './tresorAblage'
import { blobStand } from './tresorBlobApi'
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
  medienHoechsteId: vi.fn(),
  medienAufnahmen: vi.fn(),
  medienLesen: vi.fn(),
  medienPruefsumme: vi.fn(),
  medienPapierkorb: vi.fn(),
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
}))

const BUCKET = 'b'.repeat(64)
const MiB = 1024 * 1024

interface Medium {
  art: MedienArt
  name: string
  typ: string
  bytes: Uint8Array
}

/** Der MediaStore des Geräts: Kennung → Aufnahme. */
let medien: Map<number, Medium>
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
  return { id, art: m.art, name: m.name, typ: m.typ, groesse: m.bytes.length, aufgenommen: 1_700_000_000_000 + id, geaendert: 0 }
}

function mediaStoreNachbauen() {
  vi.mocked(tauri.medienZugriff).mockResolvedValue({ stand: 'voll', papierkorb: true })
  vi.mocked(tauri.medienHoechsteId).mockImplementation(async () => Math.max(0, ...medien.keys()))
  vi.mocked(tauri.medienAufnahmen).mockImplementation(async (nachId, hoechstens) =>
    [...medien.keys()].filter((id) => id > nachId).sort((a, b) => a - b).slice(0, hoechstens).map(aufnahme),
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

/** Schaltet die Sicherung ein, als lägen schon Aufnahmen bis `bis` auf dem Gerät. */
async function eingeschaltet(bis = 0) {
  const vorher = medien
  medien = new Map(bis > 0 ? [[bis, { art: 'bild', name: 'alt.jpg', typ: 'image/jpeg', bytes: bytes(10, 1) }]] : [])
  // Gesperrt, damit der Lauf, den das Einschalten anstößt, sofort endet und
  // nicht in den Test hineinläuft. Danach genau ein Lauf.
  useVaultStore.setState({ isUnlocked: false })
  await kameraEinschalten(BUCKET)
  useVaultStore.setState({ isUnlocked: true })
  await kameraAnstossen(BUCKET)
  medien = vorher
  hinzugefuegt = []
}

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory()
  setzeAngemeldetesKonto(1)
  localStorage.clear()
  medien = new Map()
  hinzugefuegt = []
  mediaStoreNachbauen()
  tresorOeffnen()
  useKameraSicherung.setState({ stand: null, laeuft: false, warten: null })
})

afterEach(async () => {
  await ablageLoeschen()
  setzeAngemeldetesKonto(null)
  vi.clearAllMocks()
})

describe('Kamera-Sicherung', () => {
  it('sichert nur, was nach dem Einschalten aufgenommen wurde, Bit für Bit und ohne zweite Kopie im Cache', async () => {
    await eingeschaltet(5)
    const gross = bytes(9 * MiB + 17, 3) // drei Stücke zu 4 MiB
    medien.set(5, { art: 'bild', name: 'vorher.jpg', typ: 'image/jpeg', bytes: bytes(10, 9) })
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
    expect(stand).toMatchObject({ bisId: 9, gesichert: 2 })
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
    expect((await kameraStandLaden(BUCKET))?.bisId).toBe(0)

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
    expect((await kameraStandLaden(BUCKET))?.bisId).toBe(4)
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
