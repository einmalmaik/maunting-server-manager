import { describe, expect, it } from 'vitest'

import de from './de.json'
import en from './en.json'

/**
 * Deutsch fällt seit 27.09.2026 nicht mehr auf Englisch zurück
 * (`fallbackLng` in `i18n.ts`), damit deutsche Nutzer die englische
 * Sprachdatei nicht mitladen. Ein Schlüssel, der nur in `en.json` steht,
 * erschiene auf Deutsch als roher Schlüssel. Beide Dateien tragen deshalb
 * genau dieselben Schlüssel.
 */
function schluessel(baum: unknown, praefix = '', ziel: string[] = []): string[] {
  for (const [name, wert] of Object.entries(baum as Record<string, unknown>)) {
    const pfad = praefix ? `${praefix}.${name}` : name
    if (wert && typeof wert === 'object' && !Array.isArray(wert)) schluessel(wert, pfad, ziel)
    else ziel.push(pfad)
  }
  return ziel
}

describe('Sprachdateien', () => {
  it('tragen auf Deutsch und Englisch dieselben Schlüssel', () => {
    const deutsch = new Set(schluessel(de))
    const englisch = new Set(schluessel(en))
    expect([...englisch].filter((k) => !deutsch.has(k))).toEqual([])
    expect([...deutsch].filter((k) => !englisch.has(k))).toEqual([])
  })
})
