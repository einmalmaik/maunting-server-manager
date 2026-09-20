/**
 * Weiterleiten heißt neu verschlüsseln, nicht umadressieren.
 *
 * Ein Anhang ist per DIS an `absenderId` **und** `blindMailboxId` gebunden. Wer
 * den alten Zeiger einfach mitschickt, verschickt etwas, das beim Empfänger
 * nicht aufgeht — und merkt es nicht, weil die Nachricht selbst ankommt. Genau
 * diese Verwechslung prüfen die Tests hier.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/social', () => ({
  ladeAnhangHerunter: vi.fn(),
  ladeAnhangHoch: vi.fn(),
}))

import { ladeAnhangHerunter, ladeAnhangHoch } from '@/api/social'
import { baueWeiterleitung, istWeiterleitbar } from './nachrichtWeiterleiten'

const HERKUNFT = { absenderId: 10, blindMailboxId: 'mb-alt' }
const ZIEL = { blindMailboxId: 'mb-neu', name: 'Gruppe', groupId: 7 }
const ICH = 11

const ALTER_ANHANG = {
  mediaId: 'alt-1',
  paketSchluessel: 'schluessel-alt',
  fileId: 'datei-alt',
  name: 'bild.png',
  mimeType: 'image/png',
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(ladeAnhangHerunter).mockResolvedValue(new Uint8Array([1, 2, 3]) as never)
  vi.mocked(ladeAnhangHoch).mockResolvedValue({
    mediaId: 'neu-1',
    paketSchluessel: 'schluessel-neu',
    fileId: 'datei-neu',
  } as never)
})

describe('Text und einfache Anhänge', () => {
  it('nimmt Text unverändert mit', async () => {
    const neu = await baueWeiterleitung({ text: 'hallo' }, HERKUNFT, ZIEL, ICH)
    expect(neu.text).toBe('hallo')
    expect(vi.mocked(ladeAnhangHoch)).not.toHaveBeenCalled()
  })

  it('nimmt Notiz, Termin, Aufkleber und Status-Antwort direkt mit', async () => {
    // Die tragen keinen Blob; für sie gibt es nichts umzuhängen.
    const quelle = {
      noteAttachment: { title: 'Notiz' },
      calendarAttachment: { title: 'Termin' },
      stickerAttachment: { id: 's1' },
      storyReply: { storyId: 3 },
    }
    const neu = await baueWeiterleitung(quelle, HERKUNFT, ZIEL, ICH)
    expect(neu).toMatchObject(quelle)
    expect(vi.mocked(ladeAnhangHerunter)).not.toHaveBeenCalled()
  })
})

describe('Medien gehen wirklich noch einmal hoch', () => {
  it('lädt herunter und für das Ziel wieder hoch', async () => {
    const neu = await baueWeiterleitung({ imageAttachment: ALTER_ANHANG }, HERKUNFT, ZIEL, ICH)

    expect(vi.mocked(ladeAnhangHerunter)).toHaveBeenCalledWith(
      { mediaId: 'alt-1', paketSchluessel: 'schluessel-alt', fileId: 'datei-alt' },
      HERKUNFT,
    )
    expect(vi.mocked(ladeAnhangHoch)).toHaveBeenCalledWith(
      expect.objectContaining({
        blindMailboxId: 'mb-neu',
        // Der Weiterleitende wird Eigentümer des neuen Blobs — nur der
        // Hochladende darf ihn später löschen.
        absenderId: ICH,
        groupId: 7,
        dateiname: 'bild.png',
        mimeType: 'image/png',
      }),
    )
    expect(neu.imageAttachment).toMatchObject({
      mediaId: 'neu-1',
      paketSchluessel: 'schluessel-neu',
      fileId: 'datei-neu',
    })
  })

  it('lässt keinen alten Zeiger stehen', async () => {
    // Der eigentliche Fehler, gegen den dieses Modul gebaut ist: mit dem alten
    // Zeiger geht der Anhang in der fremden Mailbox nicht auf.
    const neu = await baueWeiterleitung({ imageAttachment: ALTER_ANHANG }, HERKUNFT, ZIEL, ICH)
    const roh = JSON.stringify(neu)
    expect(roh).not.toContain('alt-1')
    expect(roh).not.toContain('schluessel-alt')
    expect(roh).not.toContain('datei-alt')
  })

  it('behält die beschreibenden Felder — sie beschreiben den Inhalt, nicht den Ablageort', async () => {
    const mitMassen = { ...ALTER_ANHANG, width: 800, height: 600, size: 4096 }
    const neu = await baueWeiterleitung({ imageAttachment: mitMassen }, HERKUNFT, ZIEL, ICH)
    expect(neu.imageAttachment).toMatchObject({ name: 'bild.png', width: 800, height: 600, size: 4096 })
  })

  it('hängt jeden Anhang einzeln um', async () => {
    await baueWeiterleitung(
      {
        imageAttachment: ALTER_ANHANG,
        fileAttachment: { ...ALTER_ANHANG, mediaId: 'alt-2', name: 'doku.pdf' },
        audioAttachment: { ...ALTER_ANHANG, mediaId: 'alt-3' },
      },
      HERKUNFT,
      ZIEL,
      ICH,
    )
    expect(vi.mocked(ladeAnhangHoch)).toHaveBeenCalledTimes(3)
  })

  it('meldet den Fortschritt, statt den Knopf einfrieren zu lassen', async () => {
    const schritte: { gesamt: number; fertig: number }[] = []
    await baueWeiterleitung(
      { imageAttachment: ALTER_ANHANG, fileAttachment: { ...ALTER_ANHANG, mediaId: 'alt-2' } },
      HERKUNFT,
      ZIEL,
      ICH,
      (f) => schritte.push({ ...f }),
    )
    expect(schritte).toEqual([
      { gesamt: 2, fertig: 0 },
      { gesamt: 2, fertig: 1 },
      { gesamt: 2, fertig: 2 },
    ])
  })

  it('wirft, statt stillschweigend nur den Text zu schicken', async () => {
    // Eine Weiterleitung, bei der nur der Text ankommt, wäre schlimmer als gar
    // keine: der Absender glaubt, das Bild sei draußen.
    vi.mocked(ladeAnhangHoch).mockRejectedValue(new Error('Netz weg'))
    await expect(
      baueWeiterleitung({ text: 'schau mal', imageAttachment: ALTER_ANHANG }, HERKUNFT, ZIEL, ICH),
    ).rejects.toThrow('Netz weg')
  })

  it('lässt einen Altbestand ohne vollständigen Zeiger unverändert mitgehen', async () => {
    const ohneZeiger = { name: 'alt.png', mimeType: 'image/png' }
    const neu = await baueWeiterleitung({ imageAttachment: ohneZeiger }, HERKUNFT, ZIEL, ICH)
    expect(neu.imageAttachment).toEqual(ohneZeiger)
    expect(vi.mocked(ladeAnhangHerunter)).not.toHaveBeenCalled()
  })

  it('schickt bei einem Direktchat die Empfängerkennung statt einer Gruppe', async () => {
    await baueWeiterleitung(
      { imageAttachment: ALTER_ANHANG },
      HERKUNFT,
      { blindMailboxId: 'mb-direkt', name: 'bert', recipientId: 11 },
      ICH,
    )
    expect(vi.mocked(ladeAnhangHoch)).toHaveBeenCalledWith(
      expect.objectContaining({ recipientId: 11, groupId: null }),
    )
  })
})

describe('Was sich weiterleiten lässt', () => {
  it('sagt nein zu einem Grabstein', () => {
    expect(istWeiterleitbar({ text: 'stand mal hier', isDeleted: true })).toBe(false)
  })

  it('sagt nein zu einer leeren Zeile', () => {
    expect(istWeiterleitbar({ text: '   ' })).toBe(false)
    expect(istWeiterleitbar({})).toBe(false)
  })

  it('sagt ja zu Text und zu jedem Anhang', () => {
    expect(istWeiterleitbar({ text: 'hallo' })).toBe(true)
    expect(istWeiterleitbar({ imageAttachment: ALTER_ANHANG })).toBe(true)
    expect(istWeiterleitbar({ noteAttachment: { title: 'x' } })).toBe(true)
  })
})
