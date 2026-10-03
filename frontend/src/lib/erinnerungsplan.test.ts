import { describe, expect, it } from 'vitest'
import { erinnerungenAus, HOECHSTENS } from './erinnerungsplan'
import type { KalenderVorkommen } from '@/pages/Calendar'

const STUNDE = 3_600_000
const JETZT = Date.UTC(2026, 9, 3, 12, 0, 0)

const t = (schluessel: string, werte?: Record<string, unknown>) =>
  werte ? `${schluessel}${JSON.stringify(werte)}` : schluessel

function termin(id: string, inStunden: number, titel = 'Zahnarzt', vorkommen = ''): KalenderVorkommen {
  return {
    event_id: id,
    title: titel,
    start: new Date(JETZT + inStunden * STUNDE).toISOString(),
    vorkommen,
    istSerie: vorkommen !== '',
    schluessel: `${id}:${vorkommen}`,
  } as KalenderVorkommen
}

function planen(termine: KalenderVorkommen[]) {
  return erinnerungenAus(termine, JETZT, 7, t, (d) => d.toISOString(), (titel) => titel.startsWith('sv-cal-v1:'))
}

describe('Erinnerungsplan für Android', () => {
  it('plant 49 und 25 Stunden vor Beginn, mit demselben Schlüssel wie der Server', () => {
    const plan = planen([termin('a', 100, 'Zahnarzt', '2026-10-07')])
    expect(plan.map((e) => [e.schluessel, (e.zeit - JETZT) / STUNDE])).toEqual([
      ['7_a_2026-10-07_48h', 51],
      ['7_a_2026-10-07_24h', 75],
    ])
    expect(plan[0].text).toContain('Zahnarzt')
    // Auf dem Sperrbildschirm steht kein Titel.
    expect(plan[0].oeffentlich).toBe('notifications.reminderPublic')
  })

  it('meldet eine Stufe, in deren Fenster wir schon stehen, sofort und eine vergangene nie', () => {
    const plan = planen([termin('b', 30), termin('c', 1)])
    expect(plan.map((e) => [e.schluessel, e.zeit - JETZT])).toEqual([
      ['7_b__48h', 0],
      ['7_c__24h', 0],
      ['7_b__24h', 5 * STUNDE],
    ])
    expect(plan.find((e) => e.schluessel === '7_c__24h')!.titel).toContain('notifications.whenSoon')
  })

  it('lässt Vergangenes weg und zeigt keinen unlesbaren Titel', () => {
    const plan = planen([termin('alt', -1), termin('zu', 10, 'sv-cal-v1:abc')])
    expect(plan).toHaveLength(1)
    expect(plan[0].text).toContain('notifications.reminderUntitled')
    expect(plan[0].text).not.toContain('sv-cal-v1')
  })

  it('bleibt unter der Grenze und nimmt die nächsten zuerst', () => {
    const viele = Array.from({ length: 200 }, (_, i) => termin(`t${i}`, 60 + i))
    const plan = planen(viele)
    expect(plan).toHaveLength(HOECHSTENS)
    expect(plan.every((e, i) => i === 0 || plan[i - 1].zeit <= e.zeit)).toBe(true)
  })
})
