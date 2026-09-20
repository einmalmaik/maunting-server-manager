/**
 * Die Zusicherung hinter „gilt für beide Seiten".
 *
 * Geprüft wird hier nicht, dass eine Zahl gespeichert wird, sondern dass die
 * Frist unter den Bedingungen hält, die sie sonst kaputtmachen: dasselbe
 * Mailbox-Fenster liefert denselben Umschlag bei jedem Abruf erneut, und beide
 * Seiten dürfen umstellen.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./messengerLocalStore', () => ({
  listeLokaleMailboxen: vi.fn(async () => []),
  loadLocalMessages: vi.fn(async () => []),
}))
vi.mock('./nachrichtLoeschen', () => ({
  tilgeNachrichtBeimServer: vi.fn(async () => {}),
  tilgeNachrichtLokal: vi.fn(async () => {}),
}))

import { listeLokaleMailboxen, loadLocalMessages } from './messengerLocalStore'
import { tilgeNachrichtBeimServer, tilgeNachrichtLokal } from './nachrichtLoeschen'
import {
  faelligeZeilen,
  istVerfallen,
  raeumeAlleChats,
  setzeVerfallsfrist,
  uebernehmeVerfall,
  verfaelltAm,
  verfallsfrist,
  verfallStand,
} from './nachrichtVerfall'

const MID = 'mb-1'

beforeEach(() => {
  localStorage.clear()
  vi.clearAllMocks()
})

describe('Frist übernehmen', () => {
  it('übernimmt die Umstellung der Gegenseite', () => {
    expect(uebernehmeVerfall(MID, 86_400, '2026-09-20T10:00:00.000Z')).toBe(true)
    expect(verfallsfrist(MID)).toBe(86_400)
  })

  it('meldet denselben Umschlag beim zweiten Sehen nicht noch einmal', () => {
    const wann = '2026-09-20T10:00:00.000Z'
    expect(uebernehmeVerfall(MID, 86_400, wann)).toBe(true)
    // Genau das passiert bei jedem Abruf: die letzten 100 Umschläge kommen
    // erneut vorbei. Eine zweite Systemzeile je Abruf wäre unerträglich.
    expect(uebernehmeVerfall(MID, 86_400, wann)).toBe(false)
    expect(verfallsfrist(MID)).toBe(86_400)
  })

  it('lässt die eigene spätere Abschaltung nicht vom alten Umschlag überrollen', () => {
    uebernehmeVerfall(MID, 86_400, '2026-09-20T10:00:00.000Z')
    setzeVerfallsfrist(MID, 0, '2026-09-20T11:00:00.000Z')
    expect(uebernehmeVerfall(MID, 86_400, '2026-09-20T10:00:00.000Z')).toBe(false)
    expect(verfallsfrist(MID)).toBe(0)
  })

  it('lässt die spätere Umstellung gewinnen, egal von welcher Seite', () => {
    setzeVerfallsfrist(MID, 86_400, '2026-09-20T10:00:00.000Z')
    expect(uebernehmeVerfall(MID, 604_800, '2026-09-20T12:00:00.000Z')).toBe(true)
    expect(verfallsfrist(MID)).toBe(604_800)
  })

  it('vergleicht Zeitpunkte als Zeit, nicht als Zeichenkette', () => {
    // Der Umschlag trägt einen `toISOString`, das Eingangsdatum des Servers
    // kann anders aussehen. Lexikografisch wäre das eine falsche Reihenfolge.
    setzeVerfallsfrist(MID, 0, '2026-09-20T10:00:00')
    expect(uebernehmeVerfall(MID, 86_400, '2026-09-20T10:30:00.000Z')).toBe(true)
  })

  it('liest einen Altbestand ohne Zeitpunkt und lässt ihn von allem schlagen', () => {
    localStorage.setItem('msm:chat_retention', JSON.stringify({ [MID]: 604_800 }))
    expect(verfallsfrist(MID)).toBe(604_800)
    expect(verfallStand(MID).stand).toBe('')
    expect(uebernehmeVerfall(MID, 86_400, '2020-01-01T00:00:00.000Z')).toBe(true)
  })

  it('merkt sich auch die Null, statt den Eintrag zu vergessen', () => {
    setzeVerfallsfrist(MID, 0, '2026-09-20T11:00:00.000Z')
    expect(verfallStand(MID)).toEqual({ sekunden: 0, stand: '2026-09-20T11:00:00.000Z' })
  })
})

describe('Fälligkeit', () => {
  it('rechnet den Verfall ab dem Absendezeitpunkt', () => {
    const ab = new Date('2026-09-20T10:00:00.000Z')
    expect(verfaelltAm(86_400, ab)).toBe('2026-09-21T10:00:00.000Z')
    expect(verfaelltAm(0, ab)).toBeUndefined()
  })

  it('lässt eine bereits gelöschte Zeile in Ruhe', () => {
    const alt = { verfaelltAm: '2020-01-01T00:00:00.000Z', isDeleted: true }
    expect(istVerfallen(alt)).toBe(false)
  })

  it('findet nur die fälligen', () => {
    const jetzt = Date.parse('2026-09-20T12:00:00.000Z')
    const zeilen = [
      { id: 1, verfaelltAm: '2026-09-20T11:00:00.000Z' },
      { id: 2, verfaelltAm: '2026-09-20T13:00:00.000Z' },
      { id: 3 },
    ]
    expect(faelligeZeilen(zeilen, jetzt).map((z) => z.id)).toEqual([1])
  })
})

describe('Durchgang über alle Chats', () => {
  it('räumt auch Chats auf, die gerade nicht offen sind', async () => {
    vi.mocked(listeLokaleMailboxen).mockResolvedValue(['mb-a', 'mb-b'])
    vi.mocked(loadLocalMessages).mockImplementation(
      async (mid: string) =>
        [
          {
            id: 1,
            clientUuid: `${mid}-eigen`,
            verfaelltAm: '2020-01-01T00:00:00.000Z',
            isSelf: true,
          },
          {
            id: 2,
            clientUuid: `${mid}-fremd`,
            verfaelltAm: '2020-01-01T00:00:00.000Z',
            isSelf: false,
          },
          { id: 3, clientUuid: `${mid}-frisch` },
        ] as never,
    )

    expect(await raeumeAlleChats()).toBe(4)
    // Lokal geht alles Fällige weg, beim Server nur das eigene: mehr darf
    // keine der beiden Seiten, und zusammen ist danach nichts mehr da.
    expect(vi.mocked(tilgeNachrichtLokal)).toHaveBeenCalledTimes(4)
    expect(vi.mocked(tilgeNachrichtBeimServer)).toHaveBeenCalledTimes(2)
    for (const [, msg] of vi.mocked(tilgeNachrichtBeimServer).mock.calls) {
      expect((msg as { isSelf: boolean }).isSelf).toBe(true)
    }
  })

  it('macht mit dem nächsten Chat weiter, wenn einer nicht lesbar ist', async () => {
    vi.mocked(listeLokaleMailboxen).mockResolvedValue(['kaputt', 'mb-b'])
    vi.mocked(loadLocalMessages).mockImplementation(async (mid: string) => {
      if (mid === 'kaputt') throw new Error('versiegelt')
      return [{ id: 1, clientUuid: 'x', verfaelltAm: '2020-01-01T00:00:00.000Z', isSelf: false }] as never
    })

    expect(await raeumeAlleChats()).toBe(1)
  })
})
