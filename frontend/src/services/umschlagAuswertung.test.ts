import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BlindEnvelopeItem, ChatGroupItem, ChatGroupMemberItem } from '@/api/social'
import type { Lesung } from '@/hooks/useKonversation'

/**
 * Die Absenderprüfung des Empfängers, ohne die Seite drumherum.
 *
 * Unterschrift und Signaturverzeichnis sind hier Attrappen: ob eine Signatur
 * mathematisch stimmt, prüft `nutzlastSignatur.test.ts`. Hier geht es darum,
 * was aus dem Ergebnis folgt.
 */

const signierer = new Set<number>()

vi.mock('./nutzlastSignatur', () => ({
  // Die Attrappe liest ihr Urteil aus dem Paket selbst: `_beleg` ist
  // 'geprueft:<konto>' oder 'gefaelscht:<konto>', ohne Feld unsigniert.
  pruefeNutzlast: vi.fn(async (_mailbox: string, paket: Record<string, unknown>) => {
    const beleg = paket._beleg
    if (typeof beleg !== 'string') return { art: 'unsigniert' }
    const [art, konto] = beleg.split(':')
    return art === 'geprueft'
      ? { art: 'geprueft', vonKonto: Number(konto) }
      : { art: 'gefaelscht', behauptet: Number(konto) }
  }),
}))

vi.mock('./e2eeGeraet', () => ({
  kontoNutztSignaturen: vi.fn(async (konto: number) => signierer.has(Number(konto))),
}))

vi.mock('./nachrichtVerfall', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./nachrichtVerfall')>()),
  uebernehmeVerfall: vi.fn(() => true),
}))

vi.mock('./nachrichtAnheftung', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./nachrichtAnheftung')>()),
  uebernehmeAnheftung: vi.fn(),
}))

import { pruefeNutzlast } from './nutzlastSignatur'
import { uebernehmeVerfall } from './nachrichtVerfall'
import { uebernehmeAnheftung } from './nachrichtAnheftung'
import { werteUmschlaegeAus, type Auswertungskontext } from './umschlagAuswertung'

const ICH = 1
const BERT = 2
const CARLA = 3

let naechsteId = 1

function umschlag(clientUuid?: string): BlindEnvelopeItem {
  return {
    id: naechsteId++,
    blind_mailbox_id: 'mb',
    ciphertext_envelope: '',
    client_uuid: clientUuid,
    created_at: '2026-09-24T10:00:00Z',
  }
}

function klartext(
  nutzlast: Record<string, unknown> | string,
  weiter: { vonKonto?: number; uuid?: string } = {},
): Lesung {
  return {
    art: 'klartext',
    env: umschlag(weiter.uuid),
    text: typeof nutzlast === 'string' ? nutzlast : JSON.stringify(nutzlast),
    vonKonto: weiter.vonKonto,
  }
}

function kontext(weiter: Partial<Auswertungskontext> = {}): Auswertungskontext {
  return {
    currentMid: 'mb',
    currentUserId: ICH,
    activeContact: { userId: BERT, username: 'Bert' },
    activeGroup: null,
    gruppenrechteVon: () => new Set<string>(),
    t: (schluessel, werte) => (werte ? `${schluessel} ${JSON.stringify(werte)}` : schluessel),
    quittiertGelesen: vi.fn(),
    quittiertZugestellt: vi.fn(),
    markAsRead: vi.fn(),
    setVerfallSekunden: vi.fn(),
    zeigeSystemzeile: vi.fn(),
    ...weiter,
  }
}

function mitglied(userId: number, username: string, weiter: Partial<ChatGroupMemberItem> = {}): ChatGroupMemberItem {
  return { user_id: userId, username, role: 'member', joined_at: '2026-09-01T00:00:00Z', ...weiter }
}

/** Eine Gruppe mit mir, Bert und Carla. `rechte` je Konto, wie `wirksameGruppenrechte` sie liefert. */
function gruppenkontext(
  rechte: Record<number, string[]>,
  mitglieder: ChatGroupMemberItem[] = [mitglied(ICH, 'Ich'), mitglied(BERT, 'Bert'), mitglied(CARLA, 'Carla')],
): Auswertungskontext {
  const gruppe = { id: 9, owner_user_id: ICH, members: mitglieder } as unknown as ChatGroupItem
  return kontext({
    activeContact: null,
    activeGroup: gruppe,
    gruppenrechteVon: (konto) => new Set(rechte[konto] ?? []),
  })
}

const ALLES = ['send_messages', 'attach_media']

beforeEach(() => {
  signierer.clear()
  vi.mocked(uebernehmeVerfall).mockClear()
  vi.mocked(uebernehmeAnheftung).mockClear()
  vi.spyOn(console, 'warn').mockImplementation(() => {})
})

describe('Direktchat', () => {
  it('verwirft eine gefälschte Unterschrift', async () => {
    const { decryptedList } = await werteUmschlaegeAus(
      [klartext({ text: 'hallo', _beleg: `gefaelscht:${BERT}` })],
      kontext(),
    )
    expect(decryptedList).toEqual([])
  })

  it('verwirft, wenn Unterschrift und Ratchet verschiedene Absender nennen', async () => {
    const { decryptedList } = await werteUmschlaegeAus(
      [klartext({ text: 'hallo', _beleg: `geprueft:${BERT}` }, { vonKonto: CARLA })],
      kontext(),
    )
    expect(decryptedList).toEqual([])
  })

  it('verwirft eine unsignierte Ratchet-Nachricht von einem Konto, das unterschreibt', async () => {
    signierer.add(BERT)
    const { decryptedList } = await werteUmschlaegeAus(
      [klartext({ text: 'hallo' }, { vonKonto: BERT })],
      kontext(),
    )
    expect(decryptedList).toEqual([])
  })

  it('nimmt eine unsignierte Ratchet-Nachricht, solange das Konto nicht unterschreiben kann', async () => {
    const { decryptedList } = await werteUmschlaegeAus(
      [klartext({ text: 'hallo' }, { vonKonto: BERT })],
      kontext(),
    )
    expect(decryptedList).toHaveLength(1)
    expect(decryptedList[0]).toMatchObject({ senderId: BERT, isSelf: false, text: 'hallo' })
  })

  it('verwirft eine sender_id, die dem Beleg widerspricht', async () => {
    const { decryptedList } = await werteUmschlaegeAus(
      [klartext({ text: 'bin ich', sender_id: ICH }, { vonKonto: BERT })],
      kontext(),
    )
    expect(decryptedList).toEqual([])
  })

  it('nimmt den Absender aus dem Beleg und den Namen vom Kontakt', async () => {
    const { decryptedList } = await werteUmschlaegeAus(
      [klartext({ text: 'hallo', sender_name: 'Eve', _beleg: `geprueft:${BERT}` })],
      kontext(),
    )
    expect(decryptedList[0]).toMatchObject({ senderId: BERT, senderName: 'Bert', isSelf: false })
  })

  it('verwirft eine Nachricht ganz ohne Beleg, wenn die Gegenseite unterschreiben kann', async () => {
    signierer.add(BERT)
    const { decryptedList } = await werteUmschlaegeAus([klartext({ text: 'hallo' })], kontext())
    expect(decryptedList).toEqual([])
  })

  it('schreibt eine Nachricht ohne Beleg der Gegenseite zu, solange sie nicht unterschreibt', async () => {
    const { decryptedList } = await werteUmschlaegeAus(
      [klartext({ text: 'hallo', sender_id: BERT })],
      kontext(),
    )
    expect(decryptedList[0]).toMatchObject({ senderId: BERT, isSelf: false })
  })
})

describe('Gruppe', () => {
  it('verwirft eine sender_id, die der Unterschrift widerspricht', async () => {
    const { decryptedList } = await werteUmschlaegeAus(
      [klartext({ text: 'x', sender_id: CARLA, _beleg: `geprueft:${BERT}` })],
      gruppenkontext({ [BERT]: ALLES, [CARLA]: ALLES }),
    )
    expect(decryptedList).toEqual([])
  })

  it('verwirft eine unsignierte Behauptung, wenn das behauptete Konto unterschreibt', async () => {
    signierer.add(CARLA)
    const { decryptedList } = await werteUmschlaegeAus(
      [klartext({ text: 'x', sender_id: CARLA })],
      gruppenkontext({ [CARLA]: ALLES }),
    )
    expect(decryptedList).toEqual([])
  })

  it('verwirft die Nachricht eines Mitglieds ohne send_messages', async () => {
    const { decryptedList } = await werteUmschlaegeAus(
      [klartext({ text: 'x', _beleg: `geprueft:${BERT}` })],
      gruppenkontext({ [BERT]: [] }),
    )
    expect(decryptedList).toEqual([])
  })

  it('behält die Nachricht eines früheren Mitglieds, das in keiner Rolle mehr steht', async () => {
    const { decryptedList } = await werteUmschlaegeAus(
      [klartext({ text: 'alt', _beleg: `geprueft:${CARLA}` })],
      gruppenkontext({}, [mitglied(ICH, 'Ich'), mitglied(BERT, 'Bert')]),
    )
    expect(decryptedList).toHaveLength(1)
    expect(decryptedList[0].senderId).toBe(CARLA)
  })

  it('nimmt ohne attach_media den Anhang weg und lässt den Text stehen', async () => {
    const bild = { media_id: 'm1' }
    const { decryptedList } = await werteUmschlaegeAus(
      [klartext({ text: 'schau', image_attachment: bild, _beleg: `geprueft:${BERT}` })],
      gruppenkontext({ [BERT]: ['send_messages'] }),
    )
    expect(decryptedList[0].text).toBe('schau')
    expect(decryptedList[0].imageAttachment).toBeUndefined()
  })

  it('lässt den Anhang mit attach_media stehen', async () => {
    const bild = { media_id: 'm1' }
    const { decryptedList } = await werteUmschlaegeAus(
      [klartext({ text: 'schau', image_attachment: bild, _beleg: `geprueft:${BERT}` })],
      gruppenkontext({ [BERT]: ALLES }),
    )
    expect(decryptedList[0].imageAttachment).toEqual(bild)
  })

  it('nimmt den Anzeigenamen aus der Mitgliederliste, nie aus der Nutzlast', async () => {
    const { decryptedList } = await werteUmschlaegeAus(
      [klartext({ text: 'x', sender_name: 'Carla', _beleg: `geprueft:${BERT}` })],
      gruppenkontext({ [BERT]: ALLES }),
    )
    expect(decryptedList[0]).toMatchObject({ senderId: BERT, senderName: 'Bert' })
  })
})

describe('Steuerpakete', () => {
  const ziel = { target_id: 50, target_client_uuid: 'n-1' }
  const nachricht = { id: 50, clientUuid: 'n-1' }

  it('merkt eine Bearbeitung mit dem belegten Urheber', async () => {
    const { aenderungen } = await werteUmschlaegeAus(
      [klartext({ type: 'edit_message', new_text: 'neu', ...ziel, _beleg: `geprueft:${BERT}` })],
      kontext(),
    )
    expect(aenderungen.finde(nachricht, BERT)?.newText).toBe('neu')
  })

  it('ignoriert Bearbeiten, Löschen und Reaktion mit gefälschter actor_id', async () => {
    const { aenderungen, loeschungen, reaktionen } = await werteUmschlaegeAus(
      [
        klartext({ type: 'edit_message', new_text: 'neu', actor_id: ICH, ...ziel, _beleg: `geprueft:${BERT}` }),
        klartext({ type: 'delete_message', actor_id: ICH, ...ziel, _beleg: `geprueft:${BERT}` }),
        klartext({ type: 'reaction', emoji: '👍', actor_id: ICH, ...ziel, _beleg: `geprueft:${BERT}` }),
      ],
      kontext(),
    )
    expect(aenderungen.anzahl).toBe(0)
    expect(loeschungen.anzahl).toBe(0)
    expect(reaktionen.anzahl).toBe(0)
  })

  it('ignoriert unsignierte Steuerpakete im Namen eines Kontos, das unterschreibt', async () => {
    signierer.add(BERT)
    const k = kontext()
    const { aenderungen, loeschungen, reaktionen } = await werteUmschlaegeAus(
      [
        klartext({ type: 'edit_message', new_text: 'neu', sender_id: BERT, ...ziel }),
        klartext({ type: 'delete_message', actor_id: BERT, ...ziel }),
        klartext({ type: 'reaction', emoji: '👍', actor_id: BERT, ...ziel }),
        klartext({ type: 'retention', dauer: 86_400, actor_id: BERT }),
      ],
      k,
    )
    expect(aenderungen.anzahl).toBe(0)
    expect(loeschungen.anzahl).toBe(0)
    expect(reaktionen.anzahl).toBe(0)
    expect(uebernehmeVerfall).not.toHaveBeenCalled()
    expect(k.zeigeSystemzeile).not.toHaveBeenCalled()
  })

  it('schreibt eine Reaktion dem belegten Urheber zu', async () => {
    const { reaktionen } = await werteUmschlaegeAus(
      [klartext({ type: 'reaction', emoji: '👍', ...ziel, _beleg: `geprueft:${BERT}` })],
      kontext(),
    )
    expect(reaktionen.finde(nachricht)).toEqual([
      expect.objectContaining({ emoji: '👍', actorId: BERT, nehmen: false }),
    ])
  })

  it('übernimmt eine Verfallsfrist in der Gruppe nur mit dem Recht dazu', async () => {
    const ohne = gruppenkontext({}, [mitglied(ICH, 'Ich'), mitglied(BERT, 'Bert')])
    await werteUmschlaegeAus([klartext({ type: 'retention', dauer: 86_400, _beleg: `geprueft:${BERT}` })], ohne)
    expect(uebernehmeVerfall).not.toHaveBeenCalled()
    expect(ohne.setVerfallSekunden).not.toHaveBeenCalled()

    const mit = gruppenkontext({}, [
      mitglied(ICH, 'Ich'),
      mitglied(BERT, 'Bert', { can_set_disappearing_messages: true }),
    ])
    await werteUmschlaegeAus([klartext({ type: 'retention', dauer: 86_400, _beleg: `geprueft:${BERT}` })], mit)
    expect(uebernehmeVerfall).toHaveBeenCalledWith('mb', 86_400, '2026-09-24T10:00:00Z')
    expect(mit.setVerfallSekunden).toHaveBeenCalledWith(86_400)
    expect(vi.mocked(mit.zeigeSystemzeile).mock.calls[0][0]).toContain('Bert')
  })

  it('ignoriert eine Verfallsfrist, die sich als ein anderes Konto ausgibt', async () => {
    const k = kontext()
    await werteUmschlaegeAus(
      [klartext({ type: 'retention', dauer: 86_400, actor_id: ICH, _beleg: `geprueft:${BERT}` })],
      k,
    )
    expect(uebernehmeVerfall).not.toHaveBeenCalled()
    expect(k.zeigeSystemzeile).not.toHaveBeenCalled()
  })

  it('heftet nur mit dem Recht dazu an', async () => {
    await werteUmschlaegeAus(
      [klartext({ type: 'pin_message', target_client_uuid: 'n-1', _beleg: `geprueft:${BERT}` })],
      gruppenkontext({}, [mitglied(ICH, 'Ich'), mitglied(BERT, 'Bert')]),
    )
    expect(uebernehmeAnheftung).not.toHaveBeenCalled()

    await werteUmschlaegeAus(
      [klartext({ type: 'pin_message', target_client_uuid: 'n-1', _beleg: `geprueft:${BERT}` })],
      gruppenkontext({}, [mitglied(ICH, 'Ich'), mitglied(BERT, 'Bert', { can_pin_messages: true })]),
    )
    expect(uebernehmeAnheftung).toHaveBeenCalledWith('mb', 'n-1', '2026-09-24T10:00:00Z')
  })

  it('meldet Quittungen der Gegenseite und das Lesen auf einem eigenen Gerät', async () => {
    const k = kontext()
    const ergebnis = await werteUmschlaegeAus(
      [
        klartext({ type: 'read_receipt', reader_id: BERT, read_up_to_id: 7 }),
        klartext({ type: 'delivery_receipt', receiver_id: BERT, delivered_up_to_id: 9 }),
        klartext({ type: 'read_receipt', reader_id: ICH, read_up_to_id: 12 }),
      ],
      k,
    )
    expect(k.quittiertGelesen).toHaveBeenCalledWith(7)
    expect(k.quittiertZugestellt).toHaveBeenCalledWith(9)
    expect(k.quittiertGelesen).not.toHaveBeenCalledWith(12)
    expect(k.markAsRead).toHaveBeenCalledWith('mb')
    expect(ergebnis).toMatchObject({ maxPartnerReadId: 7, maxPartnerDeliveredId: 9 })
    expect(ergebnis.decryptedList).toEqual([])
  })

  it('meldet eine Quittung sofort und nicht erst am Ende des Durchlaufs', async () => {
    const k = kontext()
    let gemeldetVorDemZweiten = -1
    vi.mocked(pruefeNutzlast).mockImplementationOnce(async () => ({ art: 'unsigniert' }))
    vi.mocked(pruefeNutzlast).mockImplementationOnce(async () => {
      gemeldetVorDemZweiten = vi.mocked(k.quittiertGelesen).mock.calls.length
      return { art: 'unsigniert' }
    })
    await werteUmschlaegeAus(
      [
        klartext({ type: 'read_receipt', reader_id: BERT, read_up_to_id: 7 }),
        klartext({ text: 'danach', sender_id: BERT }),
      ],
      k,
    )
    expect(gemeldetVorDemZweiten).toBe(1)
  })

  it('stellt eine Verfallsfrist sofort um und nicht erst am Ende des Durchlaufs', async () => {
    const k = gruppenkontext({ [BERT]: ALLES }, [
      mitglied(ICH, 'Ich'),
      mitglied(BERT, 'Bert', { can_set_disappearing_messages: true }),
    ])
    let gemeldetVorDemZweiten = -1
    vi.mocked(pruefeNutzlast).mockImplementationOnce(async () => ({ art: 'geprueft', vonKonto: BERT }))
    vi.mocked(pruefeNutzlast).mockImplementationOnce(async () => {
      gemeldetVorDemZweiten = vi.mocked(k.zeigeSystemzeile).mock.calls.length
      return { art: 'geprueft', vonKonto: BERT }
    })
    await werteUmschlaegeAus(
      [klartext({ type: 'retention', dauer: 86_400 }), klartext({ text: 'danach' })],
      k,
    )
    expect(gemeldetVorDemZweiten).toBe(1)
    expect(k.setVerfallSekunden).toHaveBeenCalledWith(86_400)
  })
})

describe('Altbestand und Unlesbares', () => {
  it('lässt ein [ME]: stehen, wenn der Ratchet die Gegenseite nennt', async () => {
    const { decryptedList } = await werteUmschlaegeAus(
      [klartext('[ME]:das war ich', { vonKonto: BERT })],
      kontext(),
    )
    expect(decryptedList[0]).toMatchObject({ isSelf: false, senderId: BERT, text: '[ME]:das war ich' })
  })

  it('nimmt ein [ME]: ohne Ratchet als eigene Zeile', async () => {
    const { decryptedList } = await werteUmschlaegeAus([klartext('[ME]:hallo')], kontext())
    expect(decryptedList[0]).toMatchObject({ isSelf: true, text: 'hallo' })
  })

  it('verwirft Klartext ohne Hülle von einem Konto, das unterschreibt', async () => {
    signierer.add(BERT)
    const { decryptedList } = await werteUmschlaegeAus([klartext('hallo')], kontext())
    expect(decryptedList).toEqual([])
  })

  it('schweigt bei einem unlesbaren Steuerumschlag und zeigt sonst einen Platzhalter', async () => {
    const { decryptedList, maxIncomingId } = await werteUmschlaegeAus(
      [
        { art: 'unlesbar', env: umschlag('receipt:abc') },
        { art: 'unlesbar', env: umschlag('n-9') },
        { art: 'still', env: umschlag('n-10') },
      ],
      kontext(),
    )
    expect(decryptedList).toHaveLength(1)
    expect(decryptedList[0]).toMatchObject({ text: 'messenger.encryptedMessage', senderId: BERT })
    expect(maxIncomingId).toBe(decryptedList[0].id)
  })

  it('zeigt jeden Umschlag und jede logische Nachricht nur einmal', async () => {
    const doppelt = klartext({ text: 'a', sender_id: BERT })
    const { decryptedList } = await werteUmschlaegeAus(
      [
        doppelt,
        doppelt,
        klartext({ text: 'b', sender_id: BERT }, { uuid: 'n-2#geraet-1' }),
        klartext({ text: 'b', sender_id: BERT }, { uuid: 'n-2#geraet-2' }),
      ],
      kontext(),
    )
    expect(decryptedList.map((m) => m.text)).toEqual(['a', 'b'])
  })

  it('zählt eigene Nachrichten nicht als eingehend', async () => {
    const { maxIncomingId, decryptedList } = await werteUmschlaegeAus(
      [klartext({ text: 'von Bert', sender_id: BERT }), klartext('[ME]:von mir')],
      kontext(),
    )
    expect(decryptedList).toHaveLength(2)
    expect(maxIncomingId).toBe(decryptedList[0].id)
  })
})
