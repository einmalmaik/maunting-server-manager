/**
 * Die dauerhafte Sicherung der Anhänge.
 *
 * Bis 10/2026 lag ein empfangenes Bild nur im Arbeitsspeicher. Nach dem
 * Neuladen war es weg, und der Server behält den Blob nur 30 Tage — die
 * Nachricht trägt keinen Inhalt, sondern einen Zeiger darauf. Diese Datei
 * prüft die Zusage „liegt auf diesem Gerät".
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/social', () => ({
  ladeAnhangHerunter: vi.fn(),
}))

vi.mock('./messengerLocalStore', () => ({
  leseMedium: vi.fn(async () => null),
  speichereMedium: vi.fn(async () => {}),
}))

import { ladeAnhangHerunter } from '@/api/social'
import { leereKlartextSpeicher } from './klartextSpeicher'
import { leseMedium, speichereMedium } from './messengerLocalStore'
import { sichereAnhaenge, sichereMedium, zeigerVon } from './medienSicherung'

const BILD = 'data:image/png;base64,iVBORw0KGgo='
const ZEIGER = { mediaId: 'media-1', paketSchluessel: 'paket', fileId: 'datei-1' }

describe('medienSicherung', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    leereKlartextSpeicher()
    vi.mocked(ladeAnhangHerunter).mockResolvedValue(BILD)
    vi.mocked(leseMedium).mockResolvedValue(null)
  })

  it('holt einen Anhang und legt ihn dauerhaft ab', async () => {
    const ergebnis = await sichereAnhaenge(
      [
        {
          senderId: 2,
          isSelf: false,
          imageAttachment: { ...ZEIGER, name: 'bild.png' },
        },
      ],
      'mailbox-1',
    )

    expect(ergebnis).toEqual({ gesichert: 1, offen: 0 })
    expect(ladeAnhangHerunter).toHaveBeenCalledTimes(1)
    // Die Bindung kommt aus dem Gespräch, nicht aus dem Anhang: Absender und
    // Mailbox stehen in den gebundenen Daten jedes Stücks.
    expect(ladeAnhangHerunter).toHaveBeenCalledWith(ZEIGER, {
      absenderId: 2,
      blindMailboxId: 'mailbox-1',
    })
    expect(speichereMedium).toHaveBeenCalledWith('media-1', BILD, 'bild.png')
  })

  it('holt einen Anhang kein zweites Mal', async () => {
    // Liegt er schon in der Ablage, ist der Download erledigt. Sonst holte
    // jeder Abruf denselben Anhang erneut — bei jedem Fünf-Sekunden-Takt.
    vi.mocked(leseMedium).mockResolvedValue(BILD)
    const ergebnis = await sichereAnhaenge(
      [{ senderId: 2, isSelf: false, imageAttachment: { ...ZEIGER } }],
      'mailbox-1',
    )

    expect(ergebnis).toEqual({ gesichert: 1, offen: 0 })
    expect(ladeAnhangHerunter).not.toHaveBeenCalled()
  })

  it('meldet einen Anhang, der nicht zu holen war, als offen', async () => {
    // Kein Netz, abgelaufen, Kontingent: der Anhang bleibt offen und wird
    // später erneut versucht. Er wird **nicht** als gesichert gemeldet.
    vi.mocked(ladeAnhangHerunter).mockRejectedValue(new Error('404'))
    const ergebnis = await sichereAnhaenge(
      [{ senderId: 2, isSelf: false, imageAttachment: { ...ZEIGER } }],
      'mailbox-1',
    )

    expect(ergebnis).toEqual({ gesichert: 0, offen: 1 })
    expect(speichereMedium).not.toHaveBeenCalled()
  })

  it('holt die eigenen Anhänge nicht', async () => {
    // Was dieses Gerät geschickt hat, liegt beim Hochladen schon in der
    // Ablage (`nachrichtVersand.ts`).
    const ergebnis = await sichereAnhaenge(
      [{ senderId: 1, isSelf: true, imageAttachment: { ...ZEIGER } }],
      'mailbox-1',
    )

    expect(ergebnis).toEqual({ gesichert: 0, offen: 0 })
    expect(ladeAnhangHerunter).not.toHaveBeenCalled()
  })

  it('lässt einen Anhang ohne Zeiger in Ruhe', async () => {
    // Altbestand aus der Zeit vor dem Paketschlüssel: ohne `mediaId` gibt es
    // nichts zu holen, und ohne `fileId` auch nicht.
    expect(zeigerVon({ mediaId: 'media-1' })).toBeNull()
    expect(zeigerVon({ paketSchluessel: 'paket', fileId: 'datei-1' })).toBeNull()
    expect(zeigerVon(ZEIGER)).toEqual(ZEIGER)

    const ergebnis = await sichereAnhaenge(
      [{ senderId: 2, isSelf: false, imageAttachment: { mediaId: 'media-1' } }],
      'mailbox-1',
    )
    expect(ergebnis).toEqual({ gesichert: 0, offen: 0 })
    expect(ladeAnhangHerunter).not.toHaveBeenCalled()
  })

  it('sichert auch Sprachnachrichten und Dateien', async () => {
    const ergebnis = await sichereAnhaenge(
      [
        {
          senderId: 2,
          isSelf: false,
          audioAttachment: { mediaId: 'media-2', paketSchluessel: 'paket', fileId: 'datei-2' },
          fileAttachment: { mediaId: 'media-3', paketSchluessel: 'paket', fileId: 'datei-3' },
        },
      ],
      'mailbox-1',
    )

    expect(ergebnis).toEqual({ gesichert: 2, offen: 0 })
    expect(speichereMedium).toHaveBeenCalledTimes(2)
  })

  it('meldet einen einzelnen Anhang als gesichert, wenn er schon in der Ablage liegt', async () => {
    vi.mocked(leseMedium).mockResolvedValue(BILD)
    expect(await sichereMedium(ZEIGER, { absenderId: 2, blindMailboxId: 'mailbox-1' })).toBe(true)
    expect(ladeAnhangHerunter).not.toHaveBeenCalled()
  })
})
