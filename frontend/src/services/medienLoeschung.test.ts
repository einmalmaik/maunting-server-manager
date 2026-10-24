/**
 * Löschen und Verfall erreichen alles, was zu einem Anhang gehört.
 *
 * Ein gelöschtes Bild war bis 10/2026 nur aus der Nachricht verschwunden. Sein
 * Klartext lag weiter in `medien`, angefangene Stücke in `medien_teile`, und
 * ein offener Nachholauftrag holte es beim nächsten Anlauf zurück. Diese Datei
 * prüft die drei Kopien und den Grabstein, der einen verspäteten Download oder
 * Historientransfer abweist.
 */

import 'fake-indexeddb/auto'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/social', () => ({
  loescheChatMedium: vi.fn(async () => ({ ok: true, deleted: true })),
  loescheBlindeUmschlaege: vi.fn(async () => ({ ok: true, deleted: 1 })),
}))

import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import { setzeSiegelAktiv } from './lokaleVersiegelung'
import {
  GRABSTEINE_MAX,
  holeMedienTeile,
  istMediumGeloescht,
  ladeMedienAuftraege,
  legeMedienAuftragAn,
  legeMedienGrabstein,
  legeMedienTeilAb,
  leseMedium,
  loadLocalMessages,
  saveLocalMessages,
  speichereMedium,
} from './messengerLocalStore'
import { tilgeNachrichtLokal } from './nachrichtLoeschen'
import { raeumeAlleChats } from './nachrichtVerfall'
import type { LocalStoredMessage } from './messengerLocalStore'

const BILD = 'data:image/png;base64,iVBORw0KGgo='
const MAILBOX = 'mb-loeschen'

let konto = 900

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  konto += 1
  setzeAngemeldetesKonto(konto)
  setzeSiegelAktiv(false)
})

function nachricht(zusatz: Partial<LocalStoredMessage> = {}): LocalStoredMessage {
  return {
    id: 11,
    clientUuid: 'u-11',
    senderId: 2,
    text: 'Das Bild',
    createdAt: '2026-10-01T12:00:00.000Z',
    isSelf: false,
    imageAttachment: { mediaId: 'm-1' },
    ...zusatz,
  }
}

async function auftragAnlegen() {
  await legeMedienAuftragAn({
    id: `${MAILBOX}:m-1`,
    mailbox: MAILBOX,
    mediaId: 'm-1',
    paketSchluessel: 'paket',
    fileId: 'datei-1',
    absenderId: 2,
    name: 'bild.png',
    versuche: 0,
    naechsterVersuch: Date.now() + 60_000,
    angelegtAm: Date.now(),
  })
}

async function teilAnlegen() {
  await legeMedienTeilAb({
    transferId: 't-1',
    mediaId: 'm-1',
    name: 'bild.png',
    teil: 0,
    teile: 1,
    sha256: 'a'.repeat(64),
    groesse: BILD.length,
    daten: BILD,
  })
}

describe('Löschen und Verfall von Anhängen', () => {
  it('nimmt beim Löschen Anhang, Staging und Nachholauftrag mit', async () => {
    const msg = nachricht()
    await saveLocalMessages(MAILBOX, [msg])
    await speichereMedium('m-1', BILD, 'bild.png')
    await teilAnlegen()
    await auftragAnlegen()

    await tilgeNachrichtLokal(MAILBOX, msg, '2026-10-02T12:00:00.000Z')

    expect(await leseMedium('m-1')).toBeNull()
    expect(await holeMedienTeile('t-1')).toEqual([])
    expect(await ladeMedienAuftraege()).toEqual([])
    expect(await istMediumGeloescht('m-1')).toBe(true)

    const [zeile] = await loadLocalMessages(MAILBOX)
    expect(zeile.text).toBe('')
    expect(zeile.isDeleted).toBe(true)
  })

  it('lässt einen verspäteten Download nach dem Löschen nicht wieder speichern', async () => {
    await speichereMedium('m-1', BILD, 'bild.png')
    await tilgeNachrichtLokal(MAILBOX, nachricht(), '2026-10-02T12:00:00.000Z')

    // Der Download war schon unterwegs, als gelöscht wurde: das Speichern
    // trifft den Grabstein und legt nichts an.
    expect(await speichereMedium('m-1', BILD, 'bild.png')).toBe(false)
    expect(await leseMedium('m-1')).toBeNull()

    // Dasselbe für ein Stück eines Historientransfers.
    await teilAnlegen()
    expect(await holeMedienTeile('t-1')).toEqual([])
  })

  it('räumt beim Verfall dieselben Kopien ab', async () => {
    const alt = nachricht({
      verfaelltAm: new Date(Date.now() - 60_000).toISOString(),
    })
    await saveLocalMessages(MAILBOX, [alt])
    await speichereMedium('m-1', BILD, 'bild.png')
    await teilAnlegen()
    await auftragAnlegen()

    expect(await raeumeAlleChats()).toBe(1)

    expect(await leseMedium('m-1')).toBeNull()
    expect(await holeMedienTeile('t-1')).toEqual([])
    expect(await ladeMedienAuftraege()).toEqual([])
    expect(await istMediumGeloescht('m-1')).toBe(true)

    // Auch nach dem Verfall bleibt die Kennung gesperrt.
    expect(await speichereMedium('m-1', BILD, 'bild.png')).toBe(false)
    await teilAnlegen()
    expect(await holeMedienTeile('t-1')).toEqual([])
  })

  it('begrenzt die Grabsteine und lässt die ältesten fallen', async () => {
    for (let i = 0; i < GRABSTEINE_MAX + 1; i += 1) {
      await legeMedienGrabstein(`m-${i}`)
    }

    expect(await istMediumGeloescht('m-0')).toBe(false)
    expect(await istMediumGeloescht(`m-${GRABSTEINE_MAX}`)).toBe(true)
  })
})
