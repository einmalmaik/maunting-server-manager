/**
 * Die wichtigste Prüfung des ganzen Messenger-Umbaus.
 *
 * Der Server sieht den Umschlag, nicht den Text. Er kann also nicht prüfen, ob
 * jemand `@everyone` geschrieben hat, und soll es auch nicht können. Ein Recht,
 * das nur der sendende Client abfragt, ist aber kein Recht: ein veränderter
 * Client schreibt es trotzdem hinein.
 *
 * Also entscheidet der **Empfänger**. Er hat den Klartext und, aus der
 * Gruppenantwort, die vom Server ausgerechnete Rechtelage aller Mitglieder.
 * Fällt diese Prüfung aus, klingelt ein Handy bei jemandem, der das nicht
 * auslösen durfte — und kein 403 fängt es ab, weil nie ein Aufruf stattfindet.
 */

import { describe, expect, it } from 'vitest'

import type { ChatGroupMemberItem } from '@/api/social'
import {
  binIchGemeint,
  darfAlleWecken,
  findeErwaehnungen,
  hervorzuhebendeWorte,
  teileText,
} from './erwaehnungen'

const ANNA = 10
const BERT = 11
const CARLA = 12

function mitglied(
  user_id: number,
  username: string,
  darfWecken = false,
): ChatGroupMemberItem {
  return {
    user_id,
    username,
    role: 'member',
    can_mention_everyone: darfWecken,
    joined_at: '2026-09-01T00:00:00.000Z',
  } as ChatGroupMemberItem
}

const OHNE_RECHT = { members: [mitglied(ANNA, 'anna'), mitglied(BERT, 'bert')] }
const MIT_RECHT = { members: [mitglied(ANNA, 'anna', true), mitglied(BERT, 'bert')] }

describe('Wer im Text steht', () => {
  it('erkennt genannte Mitglieder als Kennung, nicht als Namen', () => {
    // Kennungen, weil eine Umbenennung sonst jede alte Erwähnung bräche.
    const fund = findeErwaehnungen('hallo @bert und @anna', OHNE_RECHT.members)
    expect(fund.erwaehnungen.sort()).toEqual([ANNA, BERT])
    expect(fund.erwaehntAlle).toBe(false)
  })

  it('übergeht einen Tippfehler im Namen', () => {
    expect(findeErwaehnungen('hallo @berd', OHNE_RECHT.members).erwaehnungen).toEqual([])
  })

  it('erkennt alle drei Worte für „alle"', () => {
    for (const wort of ['@everyone', '@here', '@alle']) {
      expect(findeErwaehnungen(`${wort} bitte lesen`, OHNE_RECHT.members).erwaehntAlle).toBe(true)
    }
  })

  it('schluckt den Satzpunkt nicht', () => {
    expect(findeErwaehnungen('frag mal @bert.', OHNE_RECHT.members).erwaehnungen).toEqual([BERT])
  })

  it('nennt niemanden doppelt', () => {
    expect(findeErwaehnungen('@bert @bert @bert', OHNE_RECHT.members).erwaehnungen).toEqual([BERT])
  })
})

describe('Die Schranke sitzt beim Empfänger', () => {
  it('weckt niemanden, wenn der Absender das Recht nicht hatte', () => {
    // Der Kern des Ganzen: derselbe Text, dieselbe Nachricht, nur die
    // Rechtelage des Absenders unterscheidet sich.
    const nachricht = { senderId: ANNA, erwaehntAlle: true }
    expect(binIchGemeint(nachricht, BERT, OHNE_RECHT)).toBe(false)
  })

  it('weckt, sobald der Absender das Recht hat', () => {
    const nachricht = { senderId: ANNA, erwaehntAlle: true }
    expect(binIchGemeint(nachricht, BERT, MIT_RECHT)).toBe(true)
  })

  it('hält den Text ohne Recht für gewöhnlichen Text', () => {
    const nachricht = { senderId: ANNA, erwaehntAlle: true }
    expect(hervorzuhebendeWorte(nachricht, OHNE_RECHT).size).toBe(0)
    expect(teileText('@everyone kommt', hervorzuhebendeWorte(nachricht, OHNE_RECHT))).toEqual([
      { art: 'text', inhalt: '@everyone kommt' },
    ])
  })

  it('hebt hervor, sobald das Recht da ist', () => {
    const nachricht = { senderId: ANNA, erwaehntAlle: true }
    const stuecke = teileText('@everyone kommt', hervorzuhebendeWorte(nachricht, MIT_RECHT))
    expect(stuecke[0]).toEqual({ art: 'erwaehnung', inhalt: '@everyone' })
  })

  it('sagt nein, wenn die Marke fehlt', () => {
    // Alte Serverfassung oder ein Absender, der nicht mehr in der Liste steht.
    // Der sichere Ausgangszustand ist eine ausbleibende Meldung, nie eine
    // unberechtigte.
    expect(darfAlleWecken({ members: [{ user_id: ANNA, username: 'anna' } as ChatGroupMemberItem] }, ANNA)).toBe(false)
    expect(darfAlleWecken(null, ANNA)).toBe(false)
    expect(darfAlleWecken(MIT_RECHT, 999)).toBe(false)
  })
})

describe('Die einzelne Erwähnung braucht kein Recht', () => {
  it('erreicht mich auch von einem Absender ohne Weckrecht', () => {
    // Wer schreiben darf, darf jemanden ansprechen. Beschränkt ist nur das
    // Wecken aller.
    const nachricht = { senderId: ANNA, erwaehnungen: [BERT] }
    expect(binIchGemeint(nachricht, BERT, OHNE_RECHT)).toBe(true)
  })

  it('geht an mir vorbei, wenn jemand anders gemeint ist', () => {
    const nachricht = { senderId: ANNA, erwaehnungen: [CARLA] }
    expect(binIchGemeint(nachricht, BERT, OHNE_RECHT)).toBe(false)
  })

  it('weckt mich nicht bei meiner eigenen Nachricht', () => {
    const nachricht = { senderId: BERT, erwaehnungen: [BERT], isSelf: true }
    expect(binIchGemeint(nachricht, BERT, OHNE_RECHT)).toBe(false)
  })

  it('hebt einen genannten Namen ohne jedes Recht hervor', () => {
    const worte = hervorzuhebendeWorte({ senderId: ANNA, erwaehnungen: [BERT] }, OHNE_RECHT)
    expect([...worte]).toEqual(['bert'])
  })
})

describe('Text zerlegen', () => {
  it('lässt Text ohne Treffer in einem Stück', () => {
    expect(teileText('nichts besonderes', new Set(['bert']))).toEqual([
      { art: 'text', inhalt: 'nichts besonderes' },
    ])
  })

  it('trennt vorher, Erwähnung und nachher', () => {
    expect(teileText('hallo @bert, na?', new Set(['bert']))).toEqual([
      { art: 'text', inhalt: 'hallo ' },
      { art: 'erwaehnung', inhalt: '@bert' },
      { art: 'text', inhalt: ', na?' },
    ])
  })

  it('lässt nicht hervorzuhebende @worte stehen', () => {
    expect(teileText('@carla und @bert', new Set(['bert']))).toEqual([
      { art: 'text', inhalt: '@carla und ' },
      { art: 'erwaehnung', inhalt: '@bert' },
    ])
  })
})
