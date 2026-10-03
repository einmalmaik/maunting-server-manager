/**
 * Kamera-Sicherung von der App aus: einschalten (Posteingang, Zugang mit
 * frischem Nachweis, Job), Konto wechseln und „Speicher freigeben“.
 *
 * Gesichert wird im Hintergrund-Job (`KameraArbeit.kt`); was er mit den
 * Aufnahmen tut, prüft die Laufzeitprobe im Emulator, das Format
 * `kameraVektoren.test.ts` und die Übernahme `tresorEingang.test.ts`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import * as tauri from '@/desktop/tauri'
import type { KameraStand } from '@/desktop/tauri'
import { blobStand, sicherungszugangAnlegen, sicherungszugangEntfernen } from './tresorBlobApi'
import { posteingangEinrichten } from './tresorEingang'
import { useVaultStore, type VaultItem } from './vaultStore'
import {
  freigebbar,
  kameraAusschalten,
  kameraBeobachten,
  kameraEinschalten,
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
  medienPruefsumme: vi.fn(),
  medienPapierkorb: vi.fn(),
  sicherungSchluessel: vi.fn(),
  kameraEinrichten: vi.fn(),
  kameraStand: vi.fn(),
  kameraAendern: vi.fn(),
  kameraVergessen: vi.fn(),
  kameraJetzt: vi.fn(),
}))

vi.mock('./tresorEingang', () => ({ posteingangEinrichten: vi.fn() }))

vi.mock('./tresorBlobApi', () => ({
  bucketMelderSetzen: vi.fn(),
  zurueckgesetztFrage: vi.fn(),
  blobStand: vi.fn(),
  sicherungszugangAnlegen: vi.fn(),
  sicherungszugangEntfernen: vi.fn(),
}))

const BUCKET = 'b'.repeat(64)
const GERAET = '0d6e3c1a-5b2f-4c8e-9a7d-1f2e3d4c5b6a'
// Zusammengesetzt: im öffentlichen Repo steht keine Zeichenkette, die wie ein echter Zugang aussieht.
const ZUGANG = ['msz1', '1', 'familie', 'geheim'].join('.')
const NACHWEIS = { password: 'Konto-' + 'Passwort' }

let rsaJwk: string
let rsaSpki: string

async function rsaPaar() {
  const paar = await crypto.subtle.generateKey(
    { name: 'RSA-OAEP', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' },
    true,
    ['encrypt', 'decrypt'],
  )
  rsaJwk = JSON.stringify(await crypto.subtle.exportKey('jwk', paar.publicKey))
  rsaSpki = Buffer.from(await crypto.subtle.exportKey('spki', paar.publicKey)).toString('base64')
}

function eingerichtet(mehr: Partial<Extract<KameraStand, { eingerichtet: true }>> = {}): KameraStand {
  return { eingerichtet: true, konto: 1, bucket: BUCKET, geraet: GERAET, nurWlan: false, gesichert: 0, zuletzt: 0, offen: 0, ...mehr }
}

beforeEach(async () => {
  vi.clearAllMocks()
  setzeAngemeldetesKonto(1)
  localStorage.clear()
  useVaultStore.setState({ isUnlocked: true, userKey: {} as CryptoKey, bucketId: BUCKET, items: [] })
  useKameraSicherung.setState({ stand: null })
  if (!rsaJwk) await rsaPaar()
  vi.mocked(tauri.medienZugriff).mockResolvedValue({ stand: 'voll', papierkorb: true })
  vi.mocked(tauri.kameraStand).mockResolvedValue({ eingerichtet: false })
  vi.mocked(tauri.kameraEinrichten).mockImplementation(async (e) => eingerichtet({ geraet: e.geraet, nurWlan: e.nurWlan }))
  vi.mocked(tauri.kameraAendern).mockResolvedValue(eingerichtet())
  vi.mocked(posteingangEinrichten).mockResolvedValue({ id: '11111111-2222-4333-8444-555555555555', pqPublicKey: 'cHE=', rsaPublicKey: rsaJwk })
  vi.mocked(sicherungszugangAnlegen).mockResolvedValue(ZUGANG)
  vi.mocked(sicherungszugangEntfernen).mockResolvedValue(undefined)
  vi.mocked(tauri.kameraVergessen).mockResolvedValue(undefined)
})

afterEach(() => {
  setzeAngemeldetesKonto(null)
})

describe('Einschalten', () => {
  it('richtet Posteingang, Zugang und Job ein; der Job bekommt den RSA-Schlüssel als SPKI', async () => {
    expect(await kameraEinschalten(BUCKET, NACHWEIS)).toBe('ok')

    expect(sicherungszugangAnlegen).toHaveBeenCalledWith(BUCKET, NACHWEIS)
    const geraet = vi.mocked(posteingangEinrichten).mock.calls[0][1]
    expect(tauri.kameraEinrichten).toHaveBeenCalledWith({
      konto: 1,
      server: window.location.origin,
      bucket: BUCKET,
      geraet,
      zugang: ZUGANG,
      eingangId: '11111111-2222-4333-8444-555555555555',
      pq: 'cHE=',
      rsa: rsaSpki,
      nurWlan: false,
    })
    // Der Zugang geht nur an den Job, nicht in die Ablage der App.
    expect(JSON.stringify({ ...localStorage })).not.toContain(ZUGANG)
    expect(useKameraSicherung.getState().stand).toMatchObject({ eingerichtet: true, geraet })
  })

  it('fragt ohne vollen Zugriff keinen Nachweis ab', async () => {
    vi.mocked(tauri.medienZugriff).mockResolvedValue({ stand: 'teilweise', papierkorb: true })
    expect(await kameraEinschalten(BUCKET, NACHWEIS)).toBe('teilweise')
    expect(sicherungszugangAnlegen).not.toHaveBeenCalled()
    expect(tauri.kameraEinrichten).not.toHaveBeenCalled()
  })

  it('verbraucht den Nachweis nicht, wenn der Posteingang nicht eingerichtet werden kann', async () => {
    vi.mocked(posteingangEinrichten).mockResolvedValue(null)
    expect(await kameraEinschalten(BUCKET, NACHWEIS)).toBe('eingang')
    expect(sicherungszugangAnlegen).not.toHaveBeenCalled()
  })

  it('bleibt beim selben Gerät, wenn derselbe Tresor desselben Kontos schon eingerichtet ist', async () => {
    vi.mocked(tauri.kameraStand).mockResolvedValue(eingerichtet({ nurWlan: true }))
    await kameraEinschalten(BUCKET, NACHWEIS)
    expect(vi.mocked(posteingangEinrichten).mock.calls[0][1]).toBe(GERAET)
    expect(vi.mocked(tauri.kameraEinrichten).mock.calls[0][0]).toMatchObject({ geraet: GERAET, nurWlan: true })
  })

  it('nimmt ein neues Gerät für ein anderes Konto', async () => {
    vi.mocked(tauri.kameraStand).mockResolvedValue(eingerichtet({ konto: 2 }))
    await kameraEinschalten(BUCKET, NACHWEIS)
    expect(vi.mocked(posteingangEinrichten).mock.calls[0][1]).not.toBe(GERAET)
  })

  it('richtet nichts ein, wenn während der Anfrage das Konto wechselt', async () => {
    vi.mocked(sicherungszugangAnlegen).mockImplementation(async () => {
      setzeAngemeldetesKonto(2)
      return ZUGANG
    })
    expect(await kameraEinschalten(BUCKET, NACHWEIS)).toBe('eingang')
    expect(tauri.kameraEinrichten).not.toHaveBeenCalled()
    // Der eben angelegte Zugang bleibt nicht ohne Job beim Server liegen.
    expect(sicherungszugangEntfernen).toHaveBeenCalled()
  })

  it('nimmt den Zugang wieder weg, wenn das Telefon die Einrichtung ablehnt', async () => {
    vi.mocked(tauri.kameraEinrichten).mockRejectedValue(new Error('Serveradresse ungültig'))
    await expect(kameraEinschalten(BUCKET, NACHWEIS)).rejects.toThrow('Serveradresse ungültig')
    expect(sicherungszugangEntfernen).toHaveBeenCalled()
  })

  it('gibt einen abgelehnten Nachweis weiter und richtet nichts ein', async () => {
    vi.mocked(sicherungszugangAnlegen).mockRejectedValue(new Error('Passwort falsch'))
    await expect(kameraEinschalten(BUCKET, NACHWEIS)).rejects.toThrow('Passwort falsch')
    expect(tauri.kameraEinrichten).not.toHaveBeenCalled()
  })
})

describe('Ausschalten, Vorhandene, Kontowechsel', () => {
  it('nimmt beim Ausschalten den Zugang beim Server weg und vergisst den Job', async () => {
    await kameraAusschalten()
    expect(sicherungszugangEntfernen).toHaveBeenCalled()
    expect(tauri.kameraVergessen).toHaveBeenCalled()
    expect(useKameraSicherung.getState().stand).toEqual({ eingerichtet: false })
  })

  it('schickt bei „Vorhandene sichern“ mit, was schon im Tresor liegt, auch unter einer früheren Gerätekennung', async () => {
    const quelle = (geraet: string, medienId: number) => ({ geraet, medienId, art: 'bild', sha256: String(medienId).repeat(64).slice(0, 64) })
    useVaultStore.setState({
      items: [
        { id: 'a', datei: { quelle: quelle(GERAET, 1) } },
        { id: 'b', datei: { quelle: quelle('anderes-geraet', 2) } },
        { id: 'c' },
      ] as unknown as VaultItem[],
    })
    await kameraVorhandeneSichern()
    expect(tauri.kameraAendern).toHaveBeenCalledWith({ vorhandene: true, bekannt: [`1:${'1'.repeat(64)}`, `2:${'2'.repeat(64)}`] })
  })

  it('vergisst den Job, wenn sich ein anderes Konto anmeldet, und lässt ihn beim Abmelden stehen', async () => {
    vi.mocked(tauri.kameraStand).mockResolvedValue(eingerichtet())
    const ende = kameraBeobachten()
    await vi.waitFor(() => expect(tauri.kameraStand).toHaveBeenCalled())
    expect(tauri.kameraVergessen).not.toHaveBeenCalled()

    // Eine bloß abgelaufene Sitzung: offene Aufträge sollen bleiben.
    setzeAngemeldetesKonto(null)
    await new Promise((r) => setTimeout(r, 0))
    expect(tauri.kameraVergessen).not.toHaveBeenCalled()

    setzeAngemeldetesKonto(2)
    await vi.waitFor(() => expect(tauri.kameraVergessen).toHaveBeenCalledTimes(1))
    ende()
  })
})

describe('Speicher freigeben', () => {
  async function sha256(bytes: Uint8Array): Promise<string> {
    return Buffer.from(await crypto.subtle.digest('SHA-256', bytes as BufferSource)).toString('hex')
  }

  async function datei(id: string, medienId: number, inhalt: Uint8Array, mehr: Partial<VaultItem> = {}, geraet = GERAET) {
    return {
      id,
      service: `${id}.jpg`,
      category: 'datei',
      datei: {
        typ: 'image/jpeg',
        original: { id: `orig-${id}` },
        vorschau: { id: `vor-${id}` },
        miniatur: { id: `min-${id}` },
        quelle: { geraet, medienId, art: 'bild', sha256: await sha256(inhalt) },
      },
      ...mehr,
    } as unknown as VaultItem
  }

  const inhalt = (n: number) => new Uint8Array(100).fill(n)

  it('nimmt nur, was dieses Gerät gesichert hat, beim Server fertig liegt und in der Galerie unverändert ist', async () => {
    const items = [
      await datei('gut', 1, inhalt(1)),
      await datei('offen', 2, inhalt(2)), // Original noch nicht fertig beim Server
      await datei('wartet', 3, inhalt(3)), // Eintrag noch nicht beim Server
      await datei('anders', 4, inhalt(4)), // nach dem Sichern bearbeitet
      await datei('papierkorb', 5, inhalt(5), { trashedAt: Date.now() }),
      await datei('fremd', 6, inhalt(6), {}, 'anderes-geraet'),
    ]
    useVaultStore.setState({ items })
    localStorage.setItem(`mss:vault_pending_${BUCKET}`, JSON.stringify([{ id: 'wartet', ciphertext: 'x', revision: 0, is_deleted: false }]))
    vi.mocked(blobStand).mockImplementation(async (_b, id) => ({ state: id === 'orig-offen' ? 'offen' : 'fertig', chunk_count: 1, vorhanden: [0] }))
    vi.mocked(tauri.medienPruefsumme).mockImplementation(async (id) => ({ sha256: await sha256(id === 4 ? inhalt(99) : inhalt(id)), groesse: 100 }))

    expect(await freigebbar(BUCKET, GERAET)).toEqual({ bilder: [1], videos: [], bytes: 100 })
  })

  it('nimmt nichts, wenn der Server nicht antwortet', async () => {
    useVaultStore.setState({ items: [await datei('gut', 1, inhalt(1))] })
    vi.mocked(blobStand).mockRejectedValue(new Error('offline'))
    vi.mocked(tauri.medienPruefsumme).mockResolvedValue({ sha256: await sha256(inhalt(1)), groesse: 100 })
    expect(await freigebbar(BUCKET, GERAET)).toEqual({ bilder: [], videos: [], bytes: 0 })
  })
})
