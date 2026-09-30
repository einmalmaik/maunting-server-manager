import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ZipEintrag } from '@/lib/zipSchreiben'

const apiMock = vi.fn()
vi.mock('@/api/client', () => ({ api: (...a: unknown[]) => apiMock(...a) }))

let konto: number | null = 7
vi.mock('@/lib/angemeldetesKonto', () => ({ angemeldetesKonto: () => konto }))

let eintraege: ZipEintrag[] = []
vi.mock('@/lib/zipSchreiben', () => ({
  zipSchreiben: (e: ZipEintrag[]) => {
    eintraege = e
    return new Blob(['zip'])
  },
}))

let schluesselDa = true
let nebenDa = false
vi.mock('@/services/notesCalendarCrypto', () => ({
  NOTE_CIPHERTEXT_PREFIX: 'sv-note-v1:',
  CALENDAR_CIPHERTEXT_PREFIX: 'sv-cal-v1:',
  getUserNotesKey: async () => (schluesselDa ? ({} as CryptoKey) : null),
  altschluessel: async () => null,
  nebenschluessel: async () => (nebenDa ? [{} as CryptoKey] : []),
  decryptNoteTitle: async (w: string, _uid: string, k?: CryptoKey) => {
    if (!k) throw new Error('kein Schlüssel')
    return `klar:${w.slice('sv-note-v1:'.length)}`
  },
  decryptNoteContent: async (w: string, _uid: string, k?: CryptoKey) => {
    if (!k) throw new Error('kein Schlüssel')
    return `klar:${w.slice('sv-note-v1:'.length)}`
  },
  decryptCalendarField: async (w: string, _uid: string, _f: string, k?: CryptoKey) => {
    if (!k) throw new Error('kein Schlüssel')
    return `klar:${w.slice('sv-cal-v1:'.length)}`
  },
}))

let offen = true
vi.mock('@/services/lokaleVersiegelung', () => ({ istOffen: () => offen }))
vi.mock('@/services/messengerLocalStore', () => ({
  listeLokaleMailboxen: async () => ['mb-1'],
  loadLocalMessages: async () => [{ id: 1, text: 'Hallo Anna', createdAt: '2026-09-27T10:00:00Z' }],
  ladeAlleEntwuerfe: async () => ({}),
  ladeFunkenAkten: async () => [],
}))
vi.mock('@/services/gespraechsListe', () => ({ ladeGespraeche: async () => new Map() }))
vi.mock('@/services/gruppenName', () => ({ ladeGruppenNamen: async () => new Map() }))

import { exportErstellen } from './datenexport'

function paket() {
  return {
    manifest: { format: 'msm-datenexport-v1', zugangsdaten_enthalten: true },
    tabellen: {
      notes: [{ note_uid: 'n1', title: 'sv-note-v1:Einkauf', content: 'Server-Klartext' }],
      calendar_events: [{ event_uid: 'e1', title: 'sv-cal-v1:Arzt', description: '', location: null, recurrence: null }],
    },
    dateien: [{ pfad: 'dateien/avatar/a.png', base64: btoa('PNG') }],
  }
}

const datei = (pfad: string) => eintraege.find((e) => e.pfad === pfad)
const inhalt = (pfad: string) => JSON.parse(String(datei(pfad)?.inhalt))

describe('exportErstellen', () => {
  beforeEach(() => {
    apiMock.mockReset().mockResolvedValue(paket())
    konto = 7
    schluesselDa = true
    nebenDa = false
    offen = true
    eintraege = []
  })

  it('schickt nur den Nachweis zum Server, nie Gerätedaten', async () => {
    await exportErstellen({ password: 'geheim' })
    expect(apiMock).toHaveBeenCalledTimes(1)
    expect(apiMock).toHaveBeenCalledWith('/auth/data-export', {
      method: 'POST',
      body: JSON.stringify({ password: 'geheim' }),
    })
  })

  it('öffnet Geräte-Chiffrat mit dem Schlüssel dieses Geräts', async () => {
    const ergebnis = await exportErstellen({})
    expect(inhalt('server/notes.json')[0]).toMatchObject({ title: 'klar:Einkauf', content: 'Server-Klartext' })
    expect(inhalt('server/calendar_events.json')[0].title).toBe('klar:Arzt')
    expect(ergebnis.ohneSchluessel).toBe(0)
    expect(new TextDecoder().decode(datei('dateien/avatar/a.png')?.inhalt as Uint8Array)).toBe('PNG')
    expect(inhalt('geraet/messenger/verlauf.json')).toEqual({
      'mb-1': [{ id: 1, text: 'Hallo Anna', createdAt: '2026-09-27T10:00:00Z' }],
    })
  })

  it('öffnet auch, was nur ein Nebenschlüssel öffnet', async () => {
    // Ein Gerät, das den Kontoschlüssel eben übernommen hat: die Altnotizen
    // öffnet noch sein bisheriger, bis sie neu verschlüsselt sind.
    schluesselDa = false
    nebenDa = true
    const ergebnis = await exportErstellen({})
    expect(inhalt('server/notes.json')[0].title).toBe('klar:Einkauf')
    expect(ergebnis.ohneSchluessel).toBe(0)
  })

  it('ohne Schlüssel bleibt das Chiffrat stehen und wird gezählt', async () => {
    schluesselDa = false
    const ergebnis = await exportErstellen({})
    expect(inhalt('server/notes.json')[0].title).toBe('sv-note-v1:Einkauf')
    expect(ergebnis.ohneSchluessel).toBe(2)
    expect(String(datei('LIESMICH.txt')?.inhalt)).toMatch(/2/)
  })

  it('gesperrter Messenger: kein Verlauf, im Manifest vermerkt', async () => {
    offen = false
    const ergebnis = await exportErstellen({})
    expect(ergebnis.messengerGesperrt).toBe(true)
    expect(eintraege.some((e) => e.pfad.startsWith('geraet/messenger/'))).toBe(false)
    expect(inhalt('manifest.json').geraet.messenger).toBe('gesperrt')
  })

  it('den Tresor nur, wenn er entsperrt ist', async () => {
    const eintraegeTresor = vi.fn(() => [{ service: 'Bank', password: 'pw' }])
    const gesperrt = await exportErstellen({}, { entsperrt: false, eintraege: eintraegeTresor })
    expect(gesperrt.tresor).toBe('gesperrt')
    expect(eintraegeTresor).not.toHaveBeenCalled()
    expect(datei('tresor/eintraege.json')).toBeUndefined()

    const offenerTresor = await exportErstellen({}, { entsperrt: true, eintraege: eintraegeTresor })
    expect(offenerTresor.tresor).toBe('enthalten')
    expect(inhalt('tresor/eintraege.json')).toEqual([{ service: 'Bank', password: 'pw' }])
  })

  it('bricht ab, wenn während der Anfrage ein anderes Konto angemeldet wurde', async () => {
    apiMock.mockImplementation(async () => {
      konto = 8
      return paket()
    })
    await expect(exportErstellen({})).rejects.toThrow()
    expect(eintraege).toEqual([])
  })
})
