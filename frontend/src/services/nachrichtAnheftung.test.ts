/**
 * Die Zusicherung hinter der angehefteten Nachricht.
 *
 * Zwei Dinge müssen halten, und beide sind schon einmal woanders gebrochen:
 * dieselben 100 Umschläge kommen bei jedem Abruf erneut vorbei, und das Recht
 * zum Anheften prüft der Empfänger, nicht der Server.
 */

import { beforeEach, describe, expect, it } from 'vitest'

import type { ChatGroupItem } from '@/api/social'
import {
  anheftung,
  durfteAnheften,
  setzeAnheftung,
  uebernehmeAnheftung,
} from './nachrichtAnheftung'

const MID = 'mb-1'

/** So, wie der Server die Gruppe liefert: die Marke steht je Mitglied. */
function gruppe(marken: Record<number, boolean>): Pick<ChatGroupItem, 'members'> {
  return {
    members: Object.entries(marken).map(([id, darf]) => ({
      user_id: Number(id),
      username: `nutzer-${id}`,
      can_pin_messages: darf,
    })),
  } as Pick<ChatGroupItem, 'members'>
}

beforeEach(() => {
  localStorage.clear()
})

describe('Anheftung übernehmen', () => {
  it('übernimmt die Anheftung der Gegenseite', () => {
    expect(uebernehmeAnheftung(MID, 'uuid-a', '2026-09-20T10:00:00.000Z')).toBe(true)
    expect(anheftung(MID).clientUuid).toBe('uuid-a')
  })

  it('meldet denselben Umschlag beim zweiten Sehen nicht noch einmal', () => {
    const wann = '2026-09-20T10:00:00.000Z'
    expect(uebernehmeAnheftung(MID, 'uuid-a', wann)).toBe(true)
    expect(uebernehmeAnheftung(MID, 'uuid-a', wann)).toBe(false)
  })

  it('lässt das eigene spätere Lösen nicht vom alten Anheften überrollen', () => {
    uebernehmeAnheftung(MID, 'uuid-a', '2026-09-20T10:00:00.000Z')
    setzeAnheftung(MID, '', '2026-09-20T11:00:00.000Z')
    expect(uebernehmeAnheftung(MID, 'uuid-a', '2026-09-20T10:00:00.000Z')).toBe(false)
    expect(anheftung(MID).clientUuid).toBe('')
  })

  it('lässt die neuere Anheftung die ältere ablösen', () => {
    uebernehmeAnheftung(MID, 'uuid-a', '2026-09-20T10:00:00.000Z')
    expect(uebernehmeAnheftung(MID, 'uuid-b', '2026-09-20T10:05:00.000Z')).toBe(true)
    expect(anheftung(MID).clientUuid).toBe('uuid-b')
  })

  it('liest einen Zeitpunkt ohne Zeitzone als UTC', () => {
    // Das `created_at` des Servers kommt teils ohne `Z`, ist aber UTC. Als
    // Ortszeit gelesen wäre es hier zwei Stunden früher und verlöre.
    setzeAnheftung(MID, 'uuid-a', '2026-09-20T10:00:00.000Z')
    expect(uebernehmeAnheftung(MID, 'uuid-b', '2026-09-20T10:00:01')).toBe(true)
    expect(anheftung(MID).clientUuid).toBe('uuid-b')
  })

  it('lässt eine unlesbare Zeitangabe gegen jede echte verlieren', () => {
    setzeAnheftung(MID, 'uuid-a', '2026-09-20T10:00:00.000Z')
    expect(uebernehmeAnheftung(MID, 'uuid-b', 'irgendwann')).toBe(false)
    expect(anheftung(MID).clientUuid).toBe('uuid-a')
  })

  it('hält die Chats auseinander', () => {
    setzeAnheftung(MID, 'uuid-a')
    setzeAnheftung('mb-2', 'uuid-b')
    expect(anheftung(MID).clientUuid).toBe('uuid-a')
    expect(anheftung('mb-2').clientUuid).toBe('uuid-b')
  })

  it('gibt für einen unbekannten Chat nichts zurück', () => {
    expect(anheftung('mb-unbekannt')).toEqual({ clientUuid: '', stand: '' })
  })
})

describe('Die Schranke beim Empfänger', () => {
  it('erkennt ein Mitglied ohne das Recht nicht an', () => {
    expect(durfteAnheften(gruppe({ 7: false }), 7)).toBe(false)
  })

  it('erkennt ein Mitglied mit dem Recht an', () => {
    expect(durfteAnheften(gruppe({ 7: true }), 7)).toBe(true)
  })

  it('sagt nein, wenn die Marke fehlt', () => {
    // Eine alte Gruppenantwort ohne das Feld darf keine Leiste erzeugen:
    // eine ausbleibende ist der sichere Ausgang, eine unberechtigte nicht.
    expect(durfteAnheften({ members: [{ user_id: 7, username: 'a' }] } as Pick<ChatGroupItem, 'members'>, 7)).toBe(
      false,
    )
  })

  it('sagt nein für jemanden, der nicht in der Gruppe ist', () => {
    expect(durfteAnheften(gruppe({ 7: true }), 8)).toBe(false)
  })

  it('sagt nein ohne geladene Gruppe', () => {
    expect(durfteAnheften(null, 7)).toBe(false)
    expect(durfteAnheften(undefined, 7)).toBe(false)
  })
})
