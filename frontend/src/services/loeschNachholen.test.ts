/**
 * Der dauerhafte Auftrag für das Server-Aufräumen beim Löschen.
 *
 * Bis 10/2026 lief `tilgeNachrichtBeimServer` genau einmal. Scheiterte es an
 * Netz oder 5xx, blieb die Serverkopie bis zum Ablauf ihrer Frist liegen, und
 * niemand versuchte es erneut. Der Plan (§10) verlangt dafür einen eigenen
 * dauerhaften Auftrag: vor dem Serveraufruf angelegt, bei jedem Anlass
 * abgearbeitet, begrenzt wiederholt und endgültig sichtbar gemeldet.
 *
 * Der Server ist hier ein Stellvertreter. Was zählt, ist der Auftrag: überlebt
 * er einen Neustart, geht er beim nächsten Anlass hinaus, fällt eine
 * endgültige Abweisung heraus, und rührt er fremde Konten oder eine neu
 * angelegte Fassung derselben Nachricht nicht an?
 */

import 'fake-indexeddb/auto'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  loescheChatMedium: vi.fn(async (_mediaId: string) => ({ ok: true, deleted: true })),
  loescheBlindeUmschlaege: vi.fn(async (_mid: string, _uuid: string) => ({ ok: true, deleted: 2 })),
}))

vi.mock('@/api/social', () => api)

// Die echte Datei zieht den halben Messenger herein; für den Auftrag zählt nur,
// dass eine umgezogene Mailbox nicht doppelt angesprochen wird.
const gruppe = vi.hoisted(() => ({ umgezogen: null as string | null }))
vi.mock('./gruppenSchluessel', () => ({ umgezogeneMailbox: () => gruppe.umgezogen }))

import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import { setzeSiegelAktiv } from './lokaleVersiegelung'
import {
  clearLocalMessengerStore,
  ladeLoeschAuftraege,
  legeLoeschauftragAn,
  loadLocalMessages,
  saveLocalMessages,
  schliesseLokaleAblageFuerTest,
} from './messengerLocalStore'
import { baueLoeschauftrag, tilgeNachrichtLokal } from './nachrichtLoeschen'
import {
  MAX_VERSUCHE,
  merkeUndRaeumeServerkopie,
  merkeUndRaeumeServerkopien,
  setzeLoeschMelder,
  stosseLoeschungenAn,
} from './loeschNachholen'
import type { LoeschAuftrag, LoeschGrund } from './loeschAuftraege'

const MAILBOX = 'mb-loeschen'
const ICH = 1

let konto = 700
let meldungen: { id: string; grund: LoeschGrund; medien: number }[] = []

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  gruppe.umgezogen = null
  konto += 1
  setzeAngemeldetesKonto(konto)
  setzeSiegelAktiv(false)
  meldungen = []
  setzeLoeschMelder((auftrag, grund, medien) => {
    meldungen.push({ id: auftrag.id, grund, medien })
  })
  api.loescheChatMedium.mockResolvedValue({ ok: true, deleted: true })
  api.loescheBlindeUmschlaege.mockResolvedValue({ ok: true, deleted: 2 })
})

function nachricht(zusatz: Record<string, unknown> = {}) {
  return {
    id: 42,
    clientUuid: 'u-42',
    senderId: 101,
    text: 'Das Bild',
    createdAt: '2026-10-20T12:00:00.000Z',
    isSelf: true,
    ...zusatz,
  }
}

function auftrag(zusatz: Record<string, unknown> = {}): LoeschAuftrag {
  const gebaut = baueLoeschauftrag(MAILBOX, nachricht(zusatz), false, 1000)
  if (!gebaut) throw new Error('Der Auftrag wurde nicht gebaut')
  return gebaut
}

function netzfehler() {
  return Object.assign(new Error('Netz weg'), { status: 0 })
}

describe('dauerhafter Löschauftrag', () => {
  it('legt den Auftrag an, bevor der Server aufgeräumt wird', async () => {
    const a = auftrag()
    let beimAufruf: string[] = []
    api.loescheBlindeUmschlaege.mockImplementation(async () => {
      beimAufruf = (await ladeLoeschAuftraege()).map((x) => x.id)
      return { ok: true, deleted: 2 }
    })

    const ausgang = await merkeUndRaeumeServerkopie(a, { jetzt: () => 1000 })

    expect(beimAufruf).toEqual([a.id])
    expect(ausgang).toEqual({ offen: false, medienGeblieben: 0 })
    expect(await ladeLoeschAuftraege()).toEqual([])
  })

  it('behält den Auftrag ohne Netz und räumt ihn beim nächsten Anlass ab', async () => {
    api.loescheBlindeUmschlaege.mockRejectedValueOnce(netzfehler())
    const a = auftrag()

    const erst = await merkeUndRaeumeServerkopie(a, { jetzt: () => 1000 })
    expect(erst.offen).toBe(true)

    const [gespeichert] = await ladeLoeschAuftraege()
    expect(gespeichert.id).toBe(a.id)
    expect(gespeichert.versuche).toBe(1)
    expect(gespeichert.naechsterVersuch).toBeGreaterThan(1000)

    // Das ist der Anstoß, den `online` auslöst — nach dem Rückzug.
    await stosseLoeschungenAn({ jetzt: () => gespeichert.naechsterVersuch })

    expect(api.loescheBlindeUmschlaege).toHaveBeenCalledTimes(2)
    expect(await ladeLoeschAuftraege()).toEqual([])
  })

  it('verliert den Auftrag bei einem Neustart nicht', async () => {
    api.loescheBlindeUmschlaege.mockRejectedValueOnce(netzfehler())
    const a = auftrag()
    await merkeUndRaeumeServerkopie(a, { jetzt: () => 1000 })

    schliesseLokaleAblageFuerTest()

    const offen = await ladeLoeschAuftraege()
    expect(offen.map((x) => x.id)).toEqual([a.id])
    expect(offen[0].medien).toEqual([])
  })

  it.each([400, 403, 404, 410, 422])(
    'entfernt den Auftrag bei endgültiger Abweisung (%i) und meldet es',
    async (status) => {
      api.loescheBlindeUmschlaege.mockRejectedValue(
        Object.assign(new Error('abgelehnt'), { status }),
      )
      const a = auftrag()

      const ausgang = await merkeUndRaeumeServerkopie(a, { jetzt: () => 1000 })

      expect(ausgang.offen).toBe(false)
      expect(await ladeLoeschAuftraege()).toEqual([])
      expect(meldungen).toEqual([{ id: a.id, grund: 'abgelehnt', medien: 0 }])
    },
  )

  it.each([0, 429, 500, 503])('behält den Auftrag bei einem flüchtigen Fehler (%i)', async (status) => {
    api.loescheBlindeUmschlaege.mockRejectedValue(
      Object.assign(new Error('später'), { status }),
    )
    const a = auftrag()

    const ausgang = await merkeUndRaeumeServerkopie(a, { jetzt: () => 1000 })

    expect(ausgang.offen).toBe(true)
    expect((await ladeLoeschAuftraege()).map((x) => x.id)).toEqual([a.id])
    expect(meldungen).toEqual([])
  })

  it('gibt nach sechs Versuchen auf und meldet es', async () => {
    api.loescheBlindeUmschlaege.mockRejectedValue(
      Object.assign(new Error('Server weg'), { status: 503 }),
    )
    const a = auftrag()
    await legeLoeschauftragAn(a)

    let jetzt = 1000
    for (let i = 0; i < MAX_VERSUCHE; i += 1) {
      // Weit über jeden Rückzug hinaus: jeder Durchgang ist fällig.
      jetzt += 7 * 24 * 60 * 60_000
      await stosseLoeschungenAn({ jetzt: () => jetzt })
    }

    expect(api.loescheBlindeUmschlaege).toHaveBeenCalledTimes(MAX_VERSUCHE)
    expect(await ladeLoeschAuftraege()).toEqual([])
    expect(meldungen).toEqual([{ id: a.id, grund: 'aufgegeben', medien: 0 }])
  })

  it('löscht bei zwei Anstößen zugleich nur einmal', async () => {
    await legeLoeschauftragAn(auftrag())
    let aufloesen: (wert: { ok: boolean; deleted: number }) => void = () => {}
    api.loescheBlindeUmschlaege.mockReturnValue(
      new Promise((fertig) => {
        aufloesen = fertig
      }),
    )

    const erster = stosseLoeschungenAn({ jetzt: () => 1000 })
    const zweiter = stosseLoeschungenAn({ jetzt: () => 1000 })
    await vi.waitFor(() => expect(api.loescheBlindeUmschlaege).toHaveBeenCalledTimes(1))

    aufloesen({ ok: true, deleted: 2 })
    await Promise.all([erster, zweiter])

    expect(api.loescheBlindeUmschlaege).toHaveBeenCalledTimes(1)
    expect(await ladeLoeschAuftraege()).toEqual([])
  })

  it('behält die Medienkennungen samt Bindung, auch nach dem lokalen Grabstein', async () => {
    const msg = nachricht({
      imageAttachment: { mediaId: 'bild-1' },
      audioAttachment: { mediaId: 'ton-1' },
    })

    // Wie im Löschpfad: erst lokal tilgen, dann den Auftrag bauen. Die
    // Nachricht ist danach in der Ablage nur noch ein Grabstein; die Zeiger
    // auf die Blobs stehen ausschließlich im `msg`-Objekt und im Auftrag.
    await tilgeNachrichtLokal(MAILBOX, msg, '2026-10-20T13:00:00.000Z')
    const a = baueLoeschauftrag(MAILBOX, msg, false, 1000)!
    await legeLoeschauftragAn(a)

    const [gespeichert] = await ladeLoeschAuftraege()
    expect(gespeichert.medien).toEqual([
      { mediaId: 'bild-1', absenderId: 101, mailbox: MAILBOX },
      { mediaId: 'ton-1', absenderId: 101, mailbox: MAILBOX },
    ])

    await stosseLoeschungenAn({ jetzt: () => 1000 })

    expect(api.loescheChatMedium.mock.calls.map(([id]) => id).sort()).toEqual([
      'bild-1',
      'ton-1',
    ])
    expect(api.loescheBlindeUmschlaege).toHaveBeenCalledWith(MAILBOX, 'u-42')
    expect(await ladeLoeschAuftraege()).toEqual([])
  })

  it('rührt eine inzwischen neu angelegte Fassung derselben Nachricht nicht an', async () => {
    const a = baueLoeschauftrag(
      MAILBOX,
      nachricht({ id: 42, clientUuid: 'u-alt' }),
      false,
      1000,
    )!
    await legeLoeschauftragAn(a)
    await saveLocalMessages(MAILBOX, [
      {
        blindMailboxId: MAILBOX,
        id: 43,
        clientUuid: 'u-neu',
        senderId: ICH,
        text: 'Neue Fassung',
        createdAt: '2026-10-20T12:30:00.000Z',
        isSelf: true,
      },
    ])

    await stosseLoeschungenAn({ jetzt: () => 1000 })

    const zeilen = await loadLocalMessages(MAILBOX)
    expect(zeilen).toHaveLength(1)
    expect(zeilen[0].clientUuid).toBe('u-neu')
    // Der Auftrag nennt die alte logische Kennung, nicht die neue Zeile.
    expect(api.loescheBlindeUmschlaege).toHaveBeenCalledWith(MAILBOX, 'u-alt')
  })

  it('räumt beim Umzug einer Gruppe beide Mailboxen', async () => {
    // Während des Umzugs liegen Nachrichten unter der alten und der neuen
    // Kennung. Nur eine zu räumen hieße: „gelöscht" zeigen, während der
    // Chiffretext in der anderen liegen bleibt.
    gruppe.umgezogen = 'mb-neu'
    await legeLoeschauftragAn(auftrag())

    await stosseLoeschungenAn({ jetzt: () => 1000 })

    expect(api.loescheBlindeUmschlaege.mock.calls.map(([mid]) => mid).sort()).toEqual(
      [MAILBOX, 'mb-neu'].sort(),
    )
    expect(await ladeLoeschAuftraege()).toEqual([])
  })

  it('meldet einen fremden Anhang, den der Server nicht herausgibt, und behält keinen Auftrag', async () => {
    api.loescheChatMedium.mockRejectedValue(
      Object.assign(new Error('kein Recht'), { status: 403 }),
    )
    const a = baueLoeschauftrag(
      MAILBOX,
      nachricht({ imageAttachment: { mediaId: 'fremd-1' } }),
      true,
      1000,
    )!

    const ausgang = await merkeUndRaeumeServerkopie(a, { jetzt: () => 1000 })

    // Der Text geht weg; der Blob gehört dem Hochladenden und bleibt.
    expect(ausgang).toEqual({ offen: false, medienGeblieben: 1 })
    expect(await ladeLoeschAuftraege()).toEqual([])
    expect(api.loescheBlindeUmschlaege).toHaveBeenCalledWith(MAILBOX, 'u-42')
    expect(meldungen).toEqual([{ id: a.id, grund: 'medien', medien: 1 }])
  })

  it('rührt den Auftrag eines anderen Kontos nicht an', async () => {
    const a = auftrag()
    await legeLoeschauftragAn(a)
    const altesKonto = konto

    konto += 1
    setzeAngemeldetesKonto(konto)
    await stosseLoeschungenAn({ jetzt: () => 1000 })

    expect(api.loescheBlindeUmschlaege).not.toHaveBeenCalled()
    expect(await ladeLoeschAuftraege()).toEqual([])

    setzeAngemeldetesKonto(altesKonto)
    expect((await ladeLoeschAuftraege()).map((x) => x.id)).toEqual([a.id])
  })

  it('schreibt bei einem Kontowechsel während des Aufräumens nichts in die fremde Ablage', async () => {
    const a = auftrag()
    await legeLoeschauftragAn(a)
    const altesKonto = konto
    let aufloesen: (wert: { ok: boolean; deleted: number }) => void = () => {}
    api.loescheBlindeUmschlaege.mockReturnValue(
      new Promise((fertig) => {
        aufloesen = fertig
      }),
    )

    const lauf = stosseLoeschungenAn({ jetzt: () => 1000 })
    await vi.waitFor(() => expect(api.loescheBlindeUmschlaege).toHaveBeenCalledTimes(1))

    konto += 1
    setzeAngemeldetesKonto(konto)
    aufloesen({ ok: true, deleted: 2 })
    await lauf

    // Das neue Konto hat weder Auftrag noch Aufräumspur geerbt.
    expect(await ladeLoeschAuftraege()).toEqual([])

    // Beim alten Konto steht der Auftrag weiter und wird dort abgearbeitet.
    setzeAngemeldetesKonto(altesKonto)
    expect((await ladeLoeschAuftraege()).map((x) => x.id)).toEqual([a.id])
    api.loescheBlindeUmschlaege.mockResolvedValue({ ok: true, deleted: 2 })
    await stosseLoeschungenAn({ jetzt: () => 1000 })
    expect(await ladeLoeschAuftraege()).toEqual([])
  })

  it('hält nach einem gescheiterten Auftrag den nächsten nicht an', async () => {
    const erste = auftrag({ clientUuid: 'u-1', id: 41 })
    const zweite = baueLoeschauftrag(
      MAILBOX,
      nachricht({ clientUuid: 'u-2', id: 42 }),
      false,
      1000,
    )!
    await legeLoeschauftragAn(erste)
    await legeLoeschauftragAn(zweite)
    api.loescheBlindeUmschlaege.mockImplementation(async (_mid: string, uuid: string) => {
      if (uuid === 'u-1') throw netzfehler()
      return { ok: true, deleted: 2 }
    })

    await stosseLoeschungenAn({ jetzt: () => 1000 })

    // Der erste bleibt für den nächsten Anlass liegen, der zweite ist weg.
    const offen = await ladeLoeschAuftraege()
    expect(offen.map((a) => a.clientUuid)).toEqual(['u-1'])
    expect(api.loescheBlindeUmschlaege).toHaveBeenCalledTimes(2)
  })

  it('baut ohne logische Kennung und ohne Anhang keinen leeren Auftrag', () => {
    expect(
      baueLoeschauftrag(MAILBOX, nachricht({ clientUuid: undefined }), false, 1000),
    ).toBeNull()
  })

  it('räumt offene Aufträge beim Abmelden ab', async () => {
    await legeLoeschauftragAn(auftrag())

    await clearLocalMessengerStore()

    expect(await ladeLoeschAuftraege()).toEqual([])
  })
})

/**
 * Der Verfall räumt mehrere Zeilen in einem Durchgang ab. Sie müssen **alle**
 * vor dem ersten Serveraufruf liegen, sonst sähe der Lauf die noch nicht
 * abgelegten nicht und der nächste Durchgang fände sie nicht mehr (die Zeilen
 * sind lokal schon Grabstein).
 */
describe('mehrere Aufträge aus einem Verfallsdurchgang', () => {
  it('legt alle ab, bevor der erste Serveraufruf läuft, und räumt in einem Lauf', async () => {
    const erste = auftrag({ clientUuid: 'u-1', id: 41 })
    const zweite = baueLoeschauftrag(
      MAILBOX,
      nachricht({ clientUuid: 'u-2', id: 42 }),
      false,
      1000,
    )!
    let beimErstenAufruf: string[] = []
    api.loescheBlindeUmschlaege.mockImplementation(async () => {
      // Der Lauf arbeitet die Aufträge nacheinander ab; der erste Aufruf muss
      // beide sehen, sonst wurde der zweite erst nach dem Aufräumen abgelegt.
      if (beimErstenAufruf.length === 0) {
        beimErstenAufruf = (await ladeLoeschAuftraege()).map((x) => x.id)
      }
      return { ok: true, deleted: 2 }
    })

    await merkeUndRaeumeServerkopien([erste, zweite], { jetzt: () => 1000 })

    expect(beimErstenAufruf.sort()).toEqual([erste.id, zweite.id].sort())
    expect(api.loescheBlindeUmschlaege).toHaveBeenCalledTimes(2)
    expect(await ladeLoeschAuftraege()).toEqual([])
  })

  it('versucht ohne Ablage jede Zeile einmal und meldet ein Scheitern', async () => {
    // `id: ''` heißt: die Ablage nimmt den Auftrag nicht an (voll, gesperrt).
    // Dann gibt es keinen zweiten Anlass; der direkte Versuch muss laufen.
    const ohneAblage = { ...auftrag(), id: '' }
    api.loescheBlindeUmschlaege.mockRejectedValueOnce(netzfehler())

    await merkeUndRaeumeServerkopien([ohneAblage], { jetzt: () => 1000 })

    expect(api.loescheBlindeUmschlaege).toHaveBeenCalledTimes(1)
    expect(await ladeLoeschAuftraege()).toEqual([])
    expect(meldungen).toEqual([{ id: '', grund: 'aufgegeben', medien: 0 }])
  })
})
