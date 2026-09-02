import { beforeEach, describe, expect, it } from 'vitest'

import {
  aiChatPreferenceKeys,
  readClosedGeoAnalysis,
  readAiProviderChoice,
  readAiReasoningChoice,
  writeClosedGeoAnalysis,
  writeAiProviderChoice,
  writeAiReasoningChoice,
} from './aiChatPreferences'

describe('aiChatPreferences', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('gibt die geschriebene Denkwahl unveraendert zurueck', () => {
    const keys = aiChatPreferenceKeys(42)
    writeAiReasoningChoice(keys.reasoning, { an: true, stufe: 'high' })

    expect(readAiReasoningChoice(keys.reasoning)).toEqual({ an: true, stufe: 'high' })
  })

  it('haelt „an, aber ohne Stufe" von „aus" auseinander', () => {
    // 145 der 272 denkenden Modelle kennen ueberhaupt keine Stufen. Faende
    // `null` als Stufe hier den Weg auf `an: false`, waere bei ihnen jedes
    // Neuladen ein stilles Abschalten des Nachdenkens.
    const keys = aiChatPreferenceKeys(42)
    writeAiReasoningChoice(keys.reasoning, { an: true, stufe: null })

    expect(readAiReasoningChoice(keys.reasoning)).toEqual({ an: true, stufe: null })
  })

  it('gibt die geschriebene Modellkennung unveraendert zurueck', () => {
    const keys = aiChatPreferenceKeys(42)
    writeAiProviderChoice(keys.provider, 7)

    expect(readAiProviderChoice(keys.provider)).toBe(7)
  })

  it('trennt Modell und Denkstufe voneinander', () => {
    // Zwei Schluessel, nicht ein Kasten: wer nur das Modell wechselt, soll
    // seine Denkstufe nicht mit ueberschreiben — und umgekehrt.
    const keys = aiChatPreferenceKeys(42)
    writeAiProviderChoice(keys.provider, 7)

    expect(readAiReasoningChoice(keys.reasoning)).toBeNull()
  })

  it('trennt die Benutzer voneinander', () => {
    // localStorage gehoert der Herkunft und nicht der Anmeldung. Was der eine
    // sehen darf, entscheidet seine Rolle — der naechste am selben Rechner
    // faende sonst dessen Modell und dessen Stufe vor.
    const einer = aiChatPreferenceKeys(1)
    writeAiReasoningChoice(einer.reasoning, { an: true, stufe: 'high' })
    writeAiProviderChoice(einer.provider, 7)

    const anderer = aiChatPreferenceKeys(2)
    expect(readAiReasoningChoice(anderer.reasoning)).toBeNull()
    expect(readAiProviderChoice(anderer.provider)).toBeNull()
    expect(readClosedGeoAnalysis(anderer.closedGeoAnalysis)).toBeNull()
  })

  it('merkt nur die Kennung der bewusst geschlossenen Kartenanalyse', () => {
    const keys = aiChatPreferenceKeys(42)
    writeClosedGeoAnalysis(keys.closedGeoAnalysis, 'camera-command-17')

    expect(readClosedGeoAnalysis(keys.closedGeoAnalysis)).toBe('camera-command-17')
    expect(localStorage.getItem(keys.closedGeoAnalysis)).not.toContain('Moskau')
  })

  it('behandelt kaputten oder fremden Inhalt als „nichts gemerkt"', () => {
    const keys = aiChatPreferenceKeys(42)

    for (const muell of ['{kein json', 'null', '"high"', '{"an":"ja"}', '{"an":true,"stufe":7}']) {
      localStorage.setItem(keys.reasoning, muell)
      expect(readAiReasoningChoice(keys.reasoning)).toBeNull()
    }

    for (const muell of ['', 'sieben', '0', '-3', '1.5', 'NaN']) {
      localStorage.setItem(keys.provider, muell)
      expect(readAiProviderChoice(keys.provider)).toBeNull()
    }
  })

  it('laesst den Aufrufer nicht scheitern, wenn der Speicher gesperrt ist', () => {
    // Privates Fenster oder voller Speicher: an den gemerkten Werten haengt
    // nur der Komfort, der Chat muss trotzdem laufen.
    const keys = aiChatPreferenceKeys(42)
    const original = Storage.prototype.setItem
    Storage.prototype.setItem = () => { throw new Error('QuotaExceededError') }
    try {
      expect(() => writeAiReasoningChoice(keys.reasoning, { an: true, stufe: 'low' })).not.toThrow()
      expect(() => writeAiProviderChoice(keys.provider, 7)).not.toThrow()
      expect(() => writeClosedGeoAnalysis(keys.closedGeoAnalysis, 'camera-command-17')).not.toThrow()
    } finally {
      Storage.prototype.setItem = original
    }
  })
})
