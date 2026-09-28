import { describe, expect, it } from 'vitest'

import { STEMPEL_STILE, stempelTexte, zeichneStempel } from './augenblickStempel'

describe('Stempel eines Augenblicks', () => {
  // Sonntag, 27. September 2026, 07:05 Ortszeit — einstellige Stunde und Minute.
  const zeitpunkt = new Date(2026, 8, 27, 7, 5)

  it('schreibt Uhrzeit und Datum in der Sprache der Oberfläche', () => {
    const de = stempelTexte(zeitpunkt, 'de')
    expect(de.zeit).toBe('07:05')
    expect(de.wochentag).toBe('Sonntag')
    expect(de.datumLang).toBe('27. September')
    expect(de.datumMitJahr).toBe('27. September 2026')
    expect(de.datumKurz).toBe('27.09.26')
    expect(de.kurz).toContain('07:05 · So')

    const en = stempelTexte(zeitpunkt, 'en')
    expect(en.wochentag).toBe('Sunday')
    expect(en.datumLang).toBe('September 27')
  })

  it('fängt mit „ohne" an, damit kein Augenblick ungefragt einen Stempel trägt', () => {
    expect(STEMPEL_STILE[0]).toBe('ohne')
  })

  it('zeichnet bei „ohne" ohne Ort nichts', () => {
    const aufrufe: string[] = []
    const ctx = new Proxy({}, { get: (_z, name) => { aufrufe.push(String(name)); return () => {} } })
    zeichneStempel(ctx as CanvasRenderingContext2D, 100, 100, 'ohne', stempelTexte(zeitpunkt, 'de'))
    expect(aufrufe).toEqual([])
  })

  it('nimmt den eingetippten Ort ohne Ränder, und bei „ohne" steht er allein', () => {
    expect(stempelTexte(zeitpunkt, 'de', '  Hamburg ').ort).toBe('Hamburg')
    expect(stempelTexte(zeitpunkt, 'de').ort).toBe('')

    const geschrieben: string[] = []
    const ctx = new Proxy({} as Record<string, unknown>, {
      get: (_z, name) => {
        if (name === 'fillText') return (text: string) => geschrieben.push(text)
        if (name === 'measureText') return (text: string) => ({ width: text.length * 5 })
        return () => {}
      },
      set: () => true,
    })
    zeichneStempel(ctx as unknown as CanvasRenderingContext2D, 1000, 1000, 'ohne', stempelTexte(zeitpunkt, 'de', 'Hamburg'))
    expect(geschrieben).toEqual(['Hamburg'])
  })
})
