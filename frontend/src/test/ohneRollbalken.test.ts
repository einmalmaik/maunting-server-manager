// @vitest-environment node
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import postcss from 'postcss'
import tailwindcss from 'tailwindcss'
import { describe, expect, it } from 'vitest'

import tailwindConfig from '../../tailwind.config'

const globalesCss = readFileSync(resolve(__dirname, '../index.css'), 'utf-8')

/**
 * Den Rollbalken einer wischbaren Leiste versteckt `msm-ohne-rollbalken` aus
 * `index.css`. Bis 02.10.2026 stand an neun Stellen `no-scrollbar`, eine Klasse,
 * die nirgends definiert war: der Balken blieb sichtbar, ohne Fehler (AGENTS.md
 * Punkt 89). Daneben trug jede andere Stelle ihre eigene Kette.
 */
const quellen = import.meta.glob(['/src/**/*.{ts,tsx}', '!/src/**/*.test.{ts,tsx}'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const EIGENBAU = /no-scrollbar|scrollbar-hide|\[scrollbar-width:none\]|\[&::-webkit-scrollbar\]:hidden/g

describe('Leisten ohne Rollbalken', () => {
  it('nutzt überall die eine Klasse, nie eine eigene Kette oder eine erfundene', () => {
    const funde = Object.entries(quellen).flatMap(([pfad, text]) =>
      [...text.matchAll(EIGENBAU)].map((t) => `${pfad}:${text.slice(0, t.index).split('\n').length} ${t[0]}`),
    )
    expect(funde).toEqual([])
  })

  it('erzeugt die Klasse für Firefox und WebKit, auch mit Breitenvariante', async () => {
    const { css } = await postcss([
      tailwindcss({
        ...tailwindConfig,
        content: [{ raw: '<div class="msm-ohne-rollbalken max-md:msm-ohne-rollbalken"></div>', extension: 'html' }],
      }),
    ]).process(globalesCss, { from: undefined })
    expect(css).toMatch(/\.msm-ohne-rollbalken\s*\{\s*scrollbar-width:\s*none/)
    expect(css).toMatch(/\.msm-ohne-rollbalken::-webkit-scrollbar\s*\{\s*display:\s*none/)
    expect(css).toMatch(/\.max-md\\:msm-ohne-rollbalken\s*\{\s*scrollbar-width:\s*none/)
  })
})
