/**
 * @vitest-environment node
 *
 * Löschen einer Nachricht.
 *
 * Die Ablage und der API-Client sind hier gefälscht; was sie tun, steht in ihren
 * eigenen Tests. Geprüft wird das, was nur hier passieren kann: dass jedes
 * Inhaltsfeld wirklich verschwindet statt nur zu fehlen, dass die Kennung der
 * Zeile unangetastet bleibt, dass die Anhänge einzeln mitgehen und dass ein
 * Fehlschlag nicht als Erfolg durchgeht.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = {
  loescheChatMedium: vi.fn(async (_mediaId: string) => ({ ok: true, deleted: true })),
  loescheBlindeUmschlaege: vi.fn(async (_mid: string, _uuid: string) => ({ ok: true, deleted: 2 })),
}
vi.mock('@/api/social', () => api)

const ablage = {
  updateMessageInLocalStore: vi.fn(async () => {}),
  leereUmschlagKlartext: vi.fn(async () => {}),
}
vi.mock('./messengerLocalStore', () => ablage)

const {
  grabsteinFelder,
  medienKennungen,
  tilgeInhalt,
  tilgeNachrichtBeimServer,
  tilgeNachrichtLokal,
} = await import('./nachrichtLoeschen')

const MAILBOX = 'a'.repeat(64)

function nachricht(zusatz: Record<string, unknown> = {}) {
  return {
    id: 42,
    clientUuid: 'kennung-1',
    text: 'Stand hier',
    createdAt: '2026-09-20T00:00:00.000Z',
    senderId: 1,
    isSelf: true,
    ...zusatz,
  }
}

describe('nachrichtLoeschen', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('sammelt die Kennungen aller hochgeladenen Anhänge', () => {
    const kennungen = medienKennungen(
      nachricht({
        imageAttachment: { mediaId: 'bild-1' },
        fileAttachment: { mediaId: 'datei-1' },
        audioAttachment: { mediaId: 'ton-1' },
        videoNoteAttachment: { mediaId: 'ton-1' },
        // Ohne Blob gibt es nichts zu löschen: der Aufkleber liegt im Text.
        stickerAttachment: { id: 'daumen', label: 'Daumen', svg: '<svg/>' },
      })
    )

    expect(kennungen.sort()).toEqual(['bild-1', 'datei-1', 'ton-1'])
  })

  it('setzt jedes Inhaltsfeld ausdrücklich auf undefined', () => {
    const alt = nachricht({
      imageAttachment: { mediaId: 'bild-1' },
      fileAttachment: { mediaId: 'datei-1' },
      originalText: 'Erste Fassung',
    })

    // So führt `mischeVerlauf` die alte und die neue Fassung zusammen. Fehlte
    // ein Schlüssel im Grabstein, käme der Anhang hier wieder durch.
    const zusammengefuehrt = { ...alt, ...tilgeInhalt(alt, '2026-09-20T01:00:00.000Z') }

    expect(zusammengefuehrt.text).toBe('')
    expect(zusammengefuehrt.isDeleted).toBe(true)
    expect(zusammengefuehrt.deletedAt).toBe('2026-09-20T01:00:00.000Z')
    expect(zusammengefuehrt.imageAttachment).toBeUndefined()
    expect(zusammengefuehrt.fileAttachment).toBeUndefined()
    expect(zusammengefuehrt.originalText).toBeUndefined()
    // Kennung und Zeitpunkt bleiben: der Grabstein steht an derselben Stelle.
    expect(zusammengefuehrt.id).toBe(42)
    expect(zusammengefuehrt.clientUuid).toBe('kennung-1')
    expect(zusammengefuehrt.createdAt).toBe('2026-09-20T00:00:00.000Z')
  })

  it('fasst beim lokalen Tilgen die Kennung der Zeile nicht an', async () => {
    await tilgeNachrichtLokal(MAILBOX, nachricht(), '2026-09-20T01:00:00.000Z')

    const [mid, schluessel, aenderung] = ablage.updateMessageInLocalStore.mock.calls[0] as [
      string,
      string,
      Record<string, unknown>,
    ]
    expect(mid).toBe(MAILBOX)
    expect(schluessel).toBe('kennung-1')
    // Eine mitgeschickte `id` ließe `updateMessageInLocalStore` die Zeile unter
    // neuem Schlüssel anlegen und die alte löschen.
    expect('id' in aenderung).toBe(false)
    expect('clientUuid' in aenderung).toBe(false)
    expect(aenderung).toEqual(grabsteinFelder('2026-09-20T01:00:00.000Z'))
  })

  it('leert auch den abgelegten Umschlag-Klartext', async () => {
    await tilgeNachrichtLokal(MAILBOX, nachricht(), '2026-09-20T01:00:00.000Z')

    // Ohne diesen Schritt bliebe die Fassung liegen, aus der ein späterer Abruf
    // die Nachricht wieder aufbaut.
    expect(ablage.leereUmschlagKlartext).toHaveBeenCalledWith(MAILBOX, 42)
  })

  it('rührt den Klartextspeicher bei einer noch nicht bestätigten Nachricht nicht an', async () => {
    // Eine optimistische Zeile trägt die Uhr als Kennung, keine Umschlagkennung.
    await tilgeNachrichtLokal(MAILBOX, nachricht({ id: -1 }), '2026-09-20T01:00:00.000Z')

    expect(ablage.leereUmschlagKlartext).not.toHaveBeenCalled()
  })

  it('nimmt jeden Anhang einzeln und dann die Umschläge vom Server', async () => {
    await tilgeNachrichtBeimServer(
      MAILBOX,
      nachricht({
        imageAttachment: { mediaId: 'bild-1' },
        audioAttachment: { mediaId: 'ton-1' },
      })
    )

    expect(api.loescheChatMedium.mock.calls.map(([id]) => id).sort()).toEqual([
      'bild-1',
      'ton-1',
    ])
    // Adressiert über die logische Kennung: der Server räumt damit auch die
    // Kopien für die übrigen Geräte weg.
    expect(api.loescheBlindeUmschlaege).toHaveBeenCalledWith(MAILBOX, 'kennung-1')
  })

  it('meldet einen Fehlschlag, statt gelöscht zu melden', async () => {
    api.loescheBlindeUmschlaege.mockRejectedValueOnce(new Error('403'))

    await expect(
      tilgeNachrichtBeimServer(MAILBOX, nachricht())
    ).rejects.toThrow()
  })

  it('löscht den Anhang auch dann, wenn die Nachricht keine logische Kennung hat', async () => {
    await tilgeNachrichtBeimServer(
      MAILBOX,
      nachricht({ clientUuid: undefined, imageAttachment: { mediaId: 'bild-1' } })
    )

    expect(api.loescheChatMedium).toHaveBeenCalledWith('bild-1')
    // Ohne Kennung gibt es keinen Umschlag anzusprechen — und ein Aufruf ohne
    // sie dürfte niemals die ganze Mailbox treffen.
    expect(api.loescheBlindeUmschlaege).not.toHaveBeenCalled()
  })
})
