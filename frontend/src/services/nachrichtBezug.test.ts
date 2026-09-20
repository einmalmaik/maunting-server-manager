/**
 * Worauf ein Steuerumschlag zeigt.
 *
 * Zwei Kennungen, weil keine allein reicht: die Umschlagkennung kennt nur, wer
 * den Umschlag gesehen hat, die logische nur, wer die Nachricht selbst
 * geschrieben oder empfangen hat. Diese Tests halten fest, dass beide Wege zum
 * Ziel führen und dass derselbe Umschlag dabei nicht doppelt zählt.
 */

import { describe, expect, it } from 'vitest'

import {
  bezugFelder,
  istSteuerpaket,
  neueBezugstafel,
  neueSammeltafel,
  STEUERTYPEN,
} from './nachrichtBezug'

describe('Zielangabe', () => {
  it('schickt beide Kennungen mit, solange beide bekannt sind', () => {
    expect(bezugFelder({ id: 42, clientUuid: 'abc' })).toEqual({
      target_id: 42,
      target_client_uuid: 'abc',
    })
  })

  it('lässt die logische Kennung weg, wenn es keine gibt', () => {
    expect(bezugFelder({ id: 42 })).toEqual({ target_id: 42, target_client_uuid: undefined })
  })
})

describe('Steuertypen', () => {
  it('kennt die drei neuen', () => {
    for (const typ of ['reaction', 'pin_message', 'retention']) {
      expect(istSteuerpaket(typ)).toBe(true)
      expect(STEUERTYPEN.has(typ)).toBe(true)
    }
  })

  it('hält alles andere für eine gewöhnliche Nachricht', () => {
    // Ein unbekannter Typ darf nicht als Steuerpaket durchgehen: er landete
    // sonst nirgends und wäre stillschweigend verloren.
    for (const typ of ['text', 'reactions', '', null, 7]) {
      expect(istSteuerpaket(typ)).toBe(false)
    }
  })
})

describe('Bezugstafel', () => {
  it('findet über die Umschlagkennung', () => {
    const tafel = neueBezugstafel<string>()
    tafel.merke({ target_id: 7 }, 'gelöscht')
    expect(tafel.finde({ id: 7 })).toBe('gelöscht')
  })

  it('findet über die logische Kennung, auch wenn die Umschlagkennung fehlt', () => {
    // Der eigene Gesprächsanteil kommt aus der Ablage und hat dort oft eine
    // andere `id` als der Umschlag beim Gegenüber. Ohne diesen Weg fände eine
    // Löschung die eigene Nachricht nie.
    const tafel = neueBezugstafel<string>()
    tafel.merke({ target_client_uuid: 'u-1' }, 'gelöscht')
    expect(tafel.finde({ id: 999, clientUuid: 'u-1' })).toBe('gelöscht')
  })

  it('nimmt den zuletzt gemerkten Stand', () => {
    const tafel = neueBezugstafel<string>()
    tafel.merke({ target_id: 7 }, 'erste Fassung')
    tafel.merke({ target_id: 7 }, 'zweite Fassung')
    expect(tafel.finde({ id: 7 })).toBe('zweite Fassung')
  })

  it('findet nichts, wenn nichts passt', () => {
    const tafel = neueBezugstafel<string>()
    tafel.merke({ target_id: 7 }, 'x')
    expect(tafel.finde({ id: 8, clientUuid: 'fremd' })).toBeUndefined()
  })
})

describe('Sammeltafel', () => {
  it('sammelt mehrere Meldungen zu einer Nachricht', () => {
    const tafel = neueSammeltafel<string>()
    tafel.ergaenze({ target_id: 1 }, 'a')
    tafel.ergaenze({ target_id: 1 }, 'b')
    expect(tafel.finde({ id: 1 })).toEqual(['a', 'b'])
  })

  it('zählt denselben Umschlag nicht doppelt', () => {
    // Er steht unter beiden Kennungen. Würde `finde` beide Listen
    // aneinanderhängen, bekäme jede Reaktion die doppelte Anzahl.
    const tafel = neueSammeltafel<string>()
    tafel.ergaenze({ target_id: 1, target_client_uuid: 'u-1' }, 'a')
    expect(tafel.finde({ id: 1, clientUuid: 'u-1' })).toEqual(['a'])
    expect(tafel.anzahl).toBe(1)
  })

  it('übergeht ein Paket ohne jede Zielangabe', () => {
    const tafel = neueSammeltafel<string>()
    tafel.ergaenze({}, 'ins Leere')
    expect(tafel.anzahl).toBe(0)
  })

  it('gibt eine leere Liste statt undefined', () => {
    expect(neueSammeltafel<string>().finde({ id: 1 })).toEqual([])
  })
})
