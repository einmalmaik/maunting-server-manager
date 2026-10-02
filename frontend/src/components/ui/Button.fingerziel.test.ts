// @vitest-environment node
import postcss from 'postcss'
import tailwindcss from 'tailwindcss'
import { describe, expect, it } from 'vitest'

import tailwindConfig from '../../../tailwind.config'
import { buttonClasses, type ButtonSize } from './Button'

/** Das CSS, das Tailwind für genau diese Klassen erzeugt. */
async function cssFuer(klassen: string): Promise<string> {
  const ergebnis = await postcss([
    tailwindcss({ ...tailwindConfig, content: [{ raw: `<div class="${klassen}"></div>`, extension: 'html' }] }),
  ]).process('@tailwind utilities;', { from: undefined })
  return ergebnis.css
}

describe('Button: Fingerziel', () => {
  it('macht den Knopf unter md 44 px hoch, Symbolknöpfe auch breit', () => {
    expect(buttonClasses('ghost', 'sm', '', true).split(' ')).toContain('max-md:h-11')
    expect(buttonClasses('ghost', 'icon', '', true).split(' ')).toEqual(expect.arrayContaining(['max-md:h-11', 'max-md:w-11']))
    expect(buttonClasses('ghost', 'sm')).not.toMatch(/max-md:/)
  })

  it.each<ButtonSize>(['sm', 'md', 'icon'])('schlägt die Höhe aus size=%s im erzeugten CSS', async (size) => {
    const css = await cssFuer(buttonClasses('ghost', size, '', true))
    const hoehe = css.search(/\.h-(8|10)\s*\{/)
    const finger = css.indexOf('.max-md\\:h-11')
    expect(hoehe).toBeGreaterThanOrEqual(0)
    // Gleiche Spezifität: es gewinnt, was später steht. Die Variante unter md
    // steht hinter jeder Höhe ohne Variante (AGENTS.md Punkt 86).
    expect(finger).toBeGreaterThan(hoehe)
    expect(css.slice(css.lastIndexOf('@media', finger), finger)).toMatch(/not all and \(min-width: 768px\)/)
  })
})
