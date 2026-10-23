/**
 * Der dauerhafte Nachholauftrag für Anhänge.
 *
 * Ein Anhang, der beim Verarbeiten einer Nachricht nicht zu holen war, wurde
 * bis 10/2026 nur beim nächsten Verarbeiten derselben Umschläge erneut
 * versucht. Eine bereits quittierte Postfach-Nachricht kommt nie wieder, und
 * ohne offenen Chat gab es keinen zweiten Anlauf — der Anhang blieb für immer
 * ungesichert. Diese Datei prüft den Auftrag, der das überlebt.
 */

import 'fake-indexeddb/auto'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/social', () => ({
  ladeAnhangHerunter: vi.fn(),
}))

import { ladeAnhangHerunter } from '@/api/social'
import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import { setzeSiegelAktiv } from './lokaleVersiegelung'
import {
  ladeMedienAuftraege,
  leseMedium,
  loescheMedium,
} from './messengerLocalStore'
import { holeOffeneMedien, MAX_VERSUCHE } from './medienNachholen'
import { sichereAnhaenge } from './medienSicherung'

const BILD = 'data:image/png;base64,iVBORw0KGgo='
const ZEIGER = { mediaId: 'm-1', paketSchluessel: 'paket', fileId: 'datei-1' }
const MAILBOX = 'mb-nachholen'

/** Jeder Test bekommt eine eigene Datenbank; `messengerLocalStore` merkt sie sich. */
let konto = 500

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  konto += 1
  setzeAngemeldetesKonto(konto)
  setzeSiegelAktiv(false)
})

function nachricht() {
  return {
    senderId: 2,
    isSelf: false,
    imageAttachment: { ...ZEIGER, name: 'bild.png' },
  }
}

/** Ein Auftrag entsteht nur aus einem Fehler, nicht aus einem Wunsch. */
async function auftragAnlegen() {
  vi.mocked(ladeAnhangHerunter).mockRejectedValueOnce(new Error('Netz weg'))
  const ergebnis = await sichereAnhaenge([nachricht()], MAILBOX)
  expect(ergebnis).toEqual({ gesichert: 0, offen: 1 })
  const auftraege = await ladeMedienAuftraege()
  expect(auftraege).toHaveLength(1)
  return auftraege[0]
}

describe('Nachholauftrag für Anhänge', () => {
  it('holt einen gescheiterten Hintergrund-Download beim nächsten Anlauf nach', async () => {
    const auftrag = await auftragAnlegen()
    expect(auftrag).toMatchObject({
      mailbox: MAILBOX,
      mediaId: 'm-1',
      absenderId: 2,
      versuche: 0,
    })
    expect(auftrag.naechsterVersuch).toBeGreaterThan(Date.now())

    vi.mocked(ladeAnhangHerunter).mockClear()
    vi.mocked(ladeAnhangHerunter).mockResolvedValue(BILD)
    await holeOffeneMedien({ jetzt: () => Date.now() + 5 * 60_000 })

    expect(ladeAnhangHerunter).toHaveBeenCalledWith(ZEIGER, {
      absenderId: 2,
      blindMailboxId: MAILBOX,
    })
    expect(await leseMedium('m-1')).toBe(BILD)
    expect(await ladeMedienAuftraege()).toEqual([])
  })

  it('versucht es begrenzt oft und gibt den Auftrag danach auf', async () => {
    await auftragAnlegen()
    vi.mocked(ladeAnhangHerunter).mockReset()
    vi.mocked(ladeAnhangHerunter).mockRejectedValue(new Error('Server weg'))

    let jetzt = Date.now()
    for (let i = 0; i < MAX_VERSUCHE; i += 1) {
      jetzt += 24 * 60 * 60_000
      await holeOffeneMedien({ jetzt: () => jetzt })
    }

    expect(ladeAnhangHerunter).toHaveBeenCalledTimes(MAX_VERSUCHE)
    expect(await ladeMedienAuftraege()).toEqual([])
  })

  it.each([404, 410])('gibt bei einem definitiv verschwundenen Anhang (%i) sofort auf', async (status) => {
    await auftragAnlegen()
    vi.mocked(ladeAnhangHerunter).mockReset()
    vi.mocked(ladeAnhangHerunter).mockRejectedValue(
      Object.assign(new Error('Medienanhang nicht mehr da'), { status }),
    )

    await holeOffeneMedien({ jetzt: () => Date.now() + 5 * 60_000 })

    expect(await ladeMedienAuftraege()).toEqual([])
    expect(await leseMedium('m-1')).toBeNull()
  })

  it('läuft bei parallelen Anstößen nicht doppelt', async () => {
    await auftragAnlegen()
    vi.mocked(ladeAnhangHerunter).mockReset()

    let aufloesen: (wert: string) => void = () => {}
    const antwort = new Promise<string>((fertig) => {
      aufloesen = fertig
    })
    vi.mocked(ladeAnhangHerunter).mockReturnValue(antwort)

    const jetzt = Date.now() + 5 * 60_000
    const erster = holeOffeneMedien({ jetzt: () => jetzt })
    const zweiter = holeOffeneMedien({ jetzt: () => jetzt })
    // Der Lauf beginnt mit einem Lesevorgang; der Download kommt einen Tick
    // spaeter. Erst darauf warten, dann den einen Aufruf pruefen.
    await vi.waitFor(() => expect(ladeAnhangHerunter).toHaveBeenCalledTimes(1))

    aufloesen(BILD)
    await Promise.all([erster, zweiter])
    expect(ladeAnhangHerunter).toHaveBeenCalledTimes(1)
    expect(await leseMedium('m-1')).toBe(BILD)
  })

  it('nimmt einen gelöschten Anhang nicht wieder auf', async () => {
    await auftragAnlegen()
    await loescheMedium('m-1')
    vi.mocked(ladeAnhangHerunter).mockReset()
    vi.mocked(ladeAnhangHerunter).mockResolvedValue(BILD)

    await holeOffeneMedien({ jetzt: () => Date.now() + 5 * 60_000 })

    expect(ladeAnhangHerunter).not.toHaveBeenCalled()
    expect(await ladeMedienAuftraege()).toEqual([])
  })

  it('legt den Auftrag nicht wieder an, wenn die Löschung während des Versuchs kommt', async () => {
    await auftragAnlegen()
    vi.mocked(ladeAnhangHerunter).mockReset()
    let scheitern: (fehler: Error) => void = () => {}
    const antwort = new Promise<string>((_fertig, ablehnen) => {
      scheitern = ablehnen
    })
    vi.mocked(ladeAnhangHerunter).mockReturnValue(antwort)

    const lauf = holeOffeneMedien({ jetzt: () => Date.now() + 5 * 60_000 })
    await vi.waitFor(() => expect(ladeAnhangHerunter).toHaveBeenCalledTimes(1))
    await loescheMedium('m-1')
    scheitern(new Error('Netz weg'))
    await lauf

    // Der Lauf scheitert nach der Loeschung. Ohne die Pruefung im Schreibweg
    // legte er den gerade entfernten Auftrag wieder an.
    expect(await ladeMedienAuftraege()).toEqual([])
    expect(await leseMedium('m-1')).toBeNull()
  })

  it('schreibt bei einem Kontowechsel während des Downloads nichts in die fremde Ablage', async () => {
    await auftragAnlegen()
    const altesKonto = konto
    vi.mocked(ladeAnhangHerunter).mockReset()
    let aufloesen: (wert: string) => void = () => {}
    vi.mocked(ladeAnhangHerunter).mockReturnValue(
      new Promise<string>((fertig) => {
        aufloesen = fertig
      }),
    )

    const lauf = holeOffeneMedien({ jetzt: () => Date.now() + 5 * 60_000 })
    await vi.waitFor(() => expect(ladeAnhangHerunter).toHaveBeenCalledTimes(1))

    konto += 1
    setzeAngemeldetesKonto(konto)
    aufloesen(BILD)
    await lauf

    // Das neue Konto sieht weder Anhang noch Auftrag.
    expect(await leseMedium('m-1')).toBeNull()
    expect(await ladeMedienAuftraege()).toEqual([])

    // Beim alten Konto liegt beides weiter — dort wird es beim naechsten
    // Anlass erneut versucht.
    setzeAngemeldetesKonto(altesKonto)
    expect(await leseMedium('m-1')).toBeNull()
    expect(await ladeMedienAuftraege()).toHaveLength(1)
  })

  it('legt für eine gelöschte Medienkennung keinen neuen Auftrag an', async () => {
    await loescheMedium('m-1')
    vi.mocked(ladeAnhangHerunter).mockClear()

    const ergebnis = await sichereAnhaenge([nachricht()], MAILBOX)

    expect(ergebnis).toEqual({ gesichert: 0, offen: 0 })
    expect(ladeAnhangHerunter).not.toHaveBeenCalled()
    expect(await ladeMedienAuftraege()).toEqual([])
  })
})
