import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

import { designTokens, voiceStateTones, voiceTokens } from '@maunting/design-dna'
import { SWARM_STATES } from '@maunting/design-dna/swarm'

/**
 * Die Design-DNA hält ihre Farben an zwei Stellen: als CSS-Variablen für
 * alles, was Tailwind zeichnet, und als Werte in `index.js` für das, was keine
 * CSS-Variable lesen kann — der Schwarm zeichnet in WebGL. Zwei Stellen, die
 * niemand abgleicht, laufen auseinander; bei `primary` war das schon einmal
 * so. Dieser Test ist der Abgleich.
 */
const css = readFileSync(resolve(__dirname, '../../packages/design-dna/tokens.css'), 'utf8')

function variable(name: string): string | undefined {
  return css.match(new RegExp(`--dna-${name}:\\s*([^;]+);`))?.[1].trim()
}

describe('Design-DNA', () => {
  it('hat in index.js dieselben Farben wie in tokens.css', () => {
    for (const [name, wert] of Object.entries(designTokens)) {
      expect(`hsl(${variable(name)})`, name).toBe(wert)
    }
  })

  it('hat für den Schwarm dieselben Töne wie die Zustandsmarken daneben', () => {
    for (const [name, wert] of Object.entries(voiceTokens)) {
      expect(variable(`voice-${name}`), name).toBe(wert)
    }
    // Leuchten und Eis sind keine eigenen Farben, sondern Fokus und Primär.
    expect(voiceTokens.glow).toBe(variable('focus'))
    expect(voiceTokens.ice).toBe(variable('primary'))
  })

  it('gibt jedem Zustand des Schwarms genau einen vorhandenen Ton', () => {
    // Ein Zustand ohne Ton färbte die Marke neben dem Schwarm mit
    // `var(--dna-voice-undefined)` — also gar nicht.
    expect(Object.keys(voiceStateTones).sort()).toEqual([...SWARM_STATES].sort())
    for (const ton of Object.values(voiceStateTones)) {
      expect(Object.keys(voiceTokens)).toContain(ton)
    }
  })
})
