import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BlindEnvelopeItem } from '@/api/social'
import type { Lesung } from '@/hooks/useKonversation'

/**
 * Die Wache zählt nur Augenblicke mit Beleg über den Absender. Unterschrift
 * und Signaturverzeichnis sind Attrappen wie in `umschlagAuswertung.test.ts`.
 */

const signierer = new Set<number>()

vi.mock('./nutzlastSignatur', () => ({
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

import { werteAugenblickeAus } from './funkenLesung'

const ICH = 1
const BERT = 2
const CARLA = 3
let id = 1

function lesung(nutzlast: Record<string, unknown>, vonKonto?: number, clientUuid = `u${id}`): Lesung {
  const env: BlindEnvelopeItem = {
    id: id++,
    blind_mailbox_id: 'mb',
    ciphertext_envelope: '',
    client_uuid: `${clientUuid}#geraet`,
    created_at: '2026-09-24T10:00:00Z',
  }
  return { art: 'klartext', env, text: JSON.stringify(nutzlast), vonKonto }
}

const marke = { zeitpunkt: '2026-09-24T09:59:00Z' }
const art = { mid: 'mb', ich: ICH, partner: BERT }

beforeEach(() => signierer.clear())

describe('werteAugenblickeAus', () => {
  it('zählt einen unterschriebenen Augenblick der Gegenseite, mit logischer Kennung', async () => {
    const e = await werteAugenblickeAus([lesung({ augenblick: marke, _beleg: `geprueft:${BERT}` }, undefined, 'abc')], art)
    expect(e).toEqual([
      { kennung: `${BERT}:abc`, von: BERT, at: Date.parse(marke.zeitpunkt), art: 'augenblick' },
    ])
  })

  it('verwirft Fälschung, Widerspruch zum Ratchet und Fremde', async () => {
    const e = await werteAugenblickeAus(
      [
        lesung({ augenblick: marke, _beleg: `gefaelscht:${BERT}` }),
        lesung({ augenblick: marke, _beleg: `geprueft:${BERT}` }, ICH),
        lesung({ augenblick: marke, _beleg: `geprueft:${CARLA}` }),
        lesung({ augenblick: marke, sender_id: ICH, _beleg: `geprueft:${BERT}` }),
      ],
      art,
    )
    expect(e).toEqual([])
  })

  it('ohne Unterschrift zählt nur der Ratchet — und nur für ein Konto, das nicht unterschreibt', async () => {
    const ohne = await werteAugenblickeAus([lesung({ augenblick: marke })], art)
    expect(ohne).toEqual([])
    const ratchet = await werteAugenblickeAus([lesung({ augenblick: marke }, BERT)], art)
    expect(ratchet).toHaveLength(1)
    signierer.add(BERT)
    const schrankeGreift = await werteAugenblickeAus([lesung({ augenblick: marke }, BERT)], art)
    expect(schrankeGreift).toEqual([])
  })

  it('Steuerpakete und gewöhnliche Nachrichten zählen nicht', async () => {
    const e = await werteAugenblickeAus(
      [
        lesung({ type: 'reaction', augenblick: marke, _beleg: `geprueft:${BERT}` }),
        lesung({ text: 'hallo', _beleg: `geprueft:${BERT}` }),
      ],
      art,
    )
    expect(e).toEqual([])
  })

  it('liest eine Wiederherstellung', async () => {
    const e = await werteAugenblickeAus(
      [lesung({ funken_rettung: { ...marke, verloren: 9 }, _beleg: `geprueft:${ICH}` })],
      art,
    )
    expect(e[0]).toMatchObject({ von: ICH, art: 'rettung' })
  })
})
