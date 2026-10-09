/**
 * `cookies/eingang.js` (browser/src-tauri/src) startet autoconsent in jedem
 * Rahmen. Hier mit nachgestelltem autoconsent: ob es startet, entscheidet
 * dasselbe `pausiert` wie in Rust und in der Oberfläche.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it, vi } from 'vitest'

import faelle from '../services/schildAusnahmen.faelle.json'

const eingang = readFileSync(resolve(process.cwd(), '../browser/src-tauri/src/cookies/eingang.js'), 'utf-8')

interface Lauf {
  pausiert: (host: string, ausnahmen: string[]) => boolean
  gestartet: { konfig: Record<string, unknown>; regeln: unknown }[]
  gefiltert: unknown[]
}

function ausfuehren(schild: { aktiv: boolean; ausnahmen: string[] }): Lauf {
  const gestartet: Lauf['gestartet'] = []
  const gefiltert: unknown[] = []
  class AutoConsent {
    initialize(konfig: Record<string, unknown>, regeln: unknown) {
      gestartet.push({ konfig, regeln })
    }
  }
  const filterCompactRules = vi.fn((regeln: unknown, kontext: unknown) => {
    gefiltert.push(kontext)
    return regeln
  })
  const lauf = new Function('AutoConsent', 'filterCompactRules', 'SCHILD', 'REGELN', `${eingang}\nreturn pausiert`)
  const pausiert = lauf(AutoConsent, filterCompactRules, schild, '{"v":1,"s":[],"r":[]}')
  return { pausiert, gestartet, gefiltert }
}

describe('Cookie-Skript', () => {
  it('pausiert dieselben Seiten wie das Schild in Rust', () => {
    const { pausiert } = ausfuehren({ aktiv: false, ausnahmen: [] })
    for (const fall of faelle) expect(pausiert(fall.host, fall.ausnahmen), fall.host).toBe(fall.pausiert)
  })

  it('lehnt auf einer Seite mit Schild ab, mit den Regeln des obersten Rahmens', () => {
    const { gestartet, gefiltert } = ausfuehren({ aktiv: true, ausnahmen: ['andere.example'] })
    expect(gestartet).toHaveLength(1)
    expect(gestartet[0].konfig).toMatchObject({ autoAction: 'optOut', heuristicMode: 'reject', isMainWorld: true })
    expect(gestartet[0].regeln).toEqual({ autoconsent: [], compact: { v: 1, s: [], r: [] } })
    expect(gefiltert).toEqual([{ url: location.href, mainFrame: true }])
  })

  it('bleibt still, wenn das Schild aus oder die Seite pausiert ist', () => {
    expect(ausfuehren({ aktiv: false, ausnahmen: [] }).gestartet).toHaveLength(0)
    expect(ausfuehren({ aktiv: true, ausnahmen: [location.hostname] }).gestartet).toHaveLength(0)
  })
})
