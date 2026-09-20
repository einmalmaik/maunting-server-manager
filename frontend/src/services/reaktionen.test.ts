/**
 * Reaktionen: setzen, nehmen, und vor allem — überleben.
 *
 * Die Mailbox gibt die letzten hundert Umschläge her. Wer eine Reaktion bei
 * jedem Abruf neu aus den Umschlägen zusammensetzt, verliert sie, sobald hundert
 * neue darüber gelaufen sind. Deshalb prüft der letzte Block hier, dass der
 * Stand aus der lokalen Zeile auch ohne jede Meldung stehen bleibt.
 */

import { describe, expect, it } from 'vitest'

import {
  reaktionsknoepfe,
  schalteReaktion,
  wendeReaktionenAn,
  type RohReaktion,
} from './reaktionen'

const ANNA = 10
const BERT = 11
const CARLA = 12

function meldung(teil: Partial<RohReaktion>): RohReaktion {
  return {
    emoji: '👍',
    actorId: ANNA,
    nehmen: false,
    zeitpunkt: '2026-09-20T10:00:00.000Z',
    ...teil,
  }
}

describe('Setzen und Nehmen', () => {
  it('trägt eine Reaktion ein', () => {
    expect(wendeReaktionenAn(undefined, [meldung({})])).toEqual({ '👍': [ANNA] })
  })

  it('nimmt sie wieder weg und lässt kein leeres Zeichen zurück', () => {
    const stand = wendeReaktionenAn(undefined, [meldung({})])
    const danach = wendeReaktionenAn(stand, [
      meldung({ nehmen: true, zeitpunkt: '2026-09-20T10:01:00.000Z' }),
    ])
    // Nicht `{ '👍': [] }`: ein Knopf mit der Anzahl null wäre ein Geist.
    expect(danach).toBeUndefined()
  })

  it('lässt dasselbe Konto nicht zweimal zählen', () => {
    const stand = wendeReaktionenAn(undefined, [meldung({}), meldung({})])
    expect(stand).toEqual({ '👍': [ANNA] })
  })

  it('sammelt mehrere Konten unter einem Zeichen', () => {
    const stand = wendeReaktionenAn(undefined, [
      meldung({ actorId: ANNA }),
      meldung({ actorId: BERT }),
      meldung({ actorId: CARLA }),
    ])
    expect(stand).toEqual({ '👍': [ANNA, BERT, CARLA] })
  })

  it('nimmt nur die eigene weg, nicht die der anderen', () => {
    const stand = wendeReaktionenAn(undefined, [
      meldung({ actorId: ANNA }),
      meldung({ actorId: BERT }),
    ])
    const danach = wendeReaktionenAn(stand, [
      meldung({ actorId: ANNA, nehmen: true, zeitpunkt: '2026-09-20T11:00:00.000Z' }),
    ])
    expect(danach).toEqual({ '👍': [BERT] })
  })
})

describe('Der Zeitpunkt entscheidet, nicht die Reihenfolge', () => {
  it('lässt die jüngste Meldung je Konto und Zeichen gewinnen', () => {
    // Umschläge kommen nicht zwingend in der Reihenfolge an, in der sie
    // entstanden sind. Wer setzt und gleich wieder wegnimmt, dessen letzter
    // Wille gilt — auch wenn die beiden vertauscht eintreffen.
    const stand = wendeReaktionenAn(undefined, [
      meldung({ nehmen: true, zeitpunkt: '2026-09-20T10:05:00.000Z' }),
      meldung({ nehmen: false, zeitpunkt: '2026-09-20T10:00:00.000Z' }),
    ])
    expect(stand).toBeUndefined()
  })

  it('hält die Zeichen eines Kontos auseinander', () => {
    const stand = wendeReaktionenAn(undefined, [
      meldung({ emoji: '👍' }),
      meldung({ emoji: '❤️', zeitpunkt: '2026-09-20T10:01:00.000Z' }),
    ])
    expect(stand).toEqual({ '👍': [ANNA], '❤️': [ANNA] })
  })

  it('übergeht Meldungen ohne Zeichen oder ohne Konto', () => {
    expect(
      wendeReaktionenAn(undefined, [meldung({ emoji: '' }), meldung({ actorId: 0 })]),
    ).toBeUndefined()
  })
})

describe('Das Fenster der letzten hundert Umschläge', () => {
  it('lässt den Stand aus der Zeile unangetastet, wenn nichts gemeldet wird', () => {
    // Genau der Fall nach hundert neuen Nachrichten: der Reaktionsumschlag ist
    // aus dem Fenster gerutscht, die Reaktion steht trotzdem noch da.
    const ausDerZeile = { '❤️': [BERT] }
    expect(wendeReaktionenAn(ausDerZeile, [])).toBe(ausDerZeile)
  })

  it('ergänzt neue Meldungen, statt den Stand zu ersetzen', () => {
    const ausDerZeile = { '❤️': [BERT] }
    expect(wendeReaktionenAn(ausDerZeile, [meldung({ emoji: '👍', actorId: ANNA })])).toEqual({
      '❤️': [BERT],
      '👍': [ANNA],
    })
  })
})

describe('Der eigene Tipper', () => {
  it('setzt beim ersten Mal und meldet das der Gegenseite', () => {
    const { reaktionen, aktion } = schalteReaktion(undefined, '👍', ANNA)
    expect(aktion).toBe('setzen')
    expect(reaktionen).toEqual({ '👍': [ANNA] })
  })

  it('nimmt beim zweiten Mal zurück', () => {
    const { reaktionen, aktion } = schalteReaktion({ '👍': [ANNA] }, '👍', ANNA)
    expect(aktion).toBe('nehmen')
    expect(reaktionen).toBeUndefined()
  })

  it('setzt, wenn dort bisher nur andere stehen, und hängt sich hinten an', () => {
    // Die Reihenfolge ist die des Eintreffens, nicht die der Konto-Kennungen.
    const { reaktionen, aktion } = schalteReaktion({ '👍': [BERT] }, '👍', ANNA)
    expect(aktion).toBe('setzen')
    expect(reaktionen).toEqual({ '👍': [BERT, ANNA] })
  })
})

describe('Die Leiste unter der Blase', () => {
  it('stellt das meistgenutzte Zeichen nach vorn', () => {
    const knoepfe = reaktionsknoepfe({ '👍': [ANNA], '❤️': [ANNA, BERT, CARLA] }, ANNA)
    expect(knoepfe.map((k) => k.emoji)).toEqual(['❤️', '👍'])
  })

  it('lässt bei Gleichstand das zuerst Gekommene vorn', () => {
    // Sonst springen die Knöpfe bei jeder neuen Reaktion durcheinander.
    const knoepfe = reaktionsknoepfe({ '👍': [ANNA], '❤️': [BERT] }, ANNA)
    expect(knoepfe.map((k) => k.emoji)).toEqual(['👍', '❤️'])
  })

  it('markiert die eigene Reaktion', () => {
    const knoepfe = reaktionsknoepfe({ '👍': [BERT], '❤️': [ANNA] }, ANNA)
    expect(knoepfe.find((k) => k.emoji === '👍')?.eigene).toBe(false)
    expect(knoepfe.find((k) => k.emoji === '❤️')?.eigene).toBe(true)
  })

  it('zeigt nicht mehr, als unter eine Blase passt', () => {
    const viele = Object.fromEntries(
      ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'].map((z) => [z, [ANNA]]),
    )
    expect(reaktionsknoepfe(viele, ANNA)).toHaveLength(6)
  })

  it('bleibt bei nichts leer', () => {
    expect(reaktionsknoepfe(undefined, ANNA)).toEqual([])
  })
})
