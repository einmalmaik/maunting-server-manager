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
  istSteuerzeile,
  neueBezugstafel,
  neueSammeltafel,
  STEUERTYPEN,
  zeitAlsZahl,
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

describe('Ein Steuerpaket, das als Zeile gespeichert wurde', () => {
  it('erkennt den rohen Umschlag im Text', () => {
    // Genau diese Zeile stand nach der ersten Laufzeitprobe des Anheftens im
    // Verlauf beider Testkonten.
    const roh =
      '{"type":"pin_message","target_id":4111,"target_client_uuid":"d030d999","aktion":"anheften","actor_id":10}'
    expect(istSteuerzeile(roh)).toBe(true)
  })

  it('lässt gewöhnliche Nachrichten in Ruhe', () => {
    for (const text of [
      'Kommst du morgen?',
      '{ kein JSON',
      '{"type":"text","body":"hallo"}',
      '{"foo":1}',
      '[]',
      '',
      undefined,
      null,
    ]) {
      expect(istSteuerzeile(text)).toBe(false)
    }
  })

  it('verschont eine Nachricht, die nur so aussieht', () => {
    // Wer über Steuerpakete schreibt, zitiert sie: das ist Gesprächsinhalt.
    expect(istSteuerzeile('Schau mal: {"type":"reaction"} stand im Verlauf')).toBe(false)
  })
})

describe('Zeitpunkt als Zahl', () => {
  it('liest einen Zeitpunkt ohne Zeitzone als UTC', () => {
    // Das `created_at` des Servers kommt teils ohne `Z`, ist aber UTC. Als
    // Ortszeit gelesen verschöbe es sich um den Zonenversatz.
    expect(zeitAlsZahl('2026-09-20T10:00:00')).toBe(zeitAlsZahl('2026-09-20T10:00:00.000Z'))
  })

  it('achtet einen mitgegebenen Versatz', () => {
    expect(zeitAlsZahl('2026-09-20T12:00:00+02:00')).toBe(zeitAlsZahl('2026-09-20T10:00:00Z'))
  })

  it('macht aus Unlesbarem eine Null', () => {
    for (const roh of ['', 'irgendwann', 'null']) {
      expect(zeitAlsZahl(roh)).toBe(0)
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
