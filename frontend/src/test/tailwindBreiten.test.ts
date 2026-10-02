// @vitest-environment node
import resolveConfig from 'tailwindcss/resolveConfig'
import { describe, expect, it } from 'vitest'

import tailwindConfig from '../../tailwind.config'

/**
 * Eine Breite, die `screens` nicht kennt, erzeugt kein CSS, ohne Fehler und
 * ohne Warnung. `hidden xs:inline` blieb deshalb immer versteckt (bis
 * 02.10.2026, AGENTS.md Punkt 89). Gezählt werden Präfixe, die wie eine
 * Breite aussehen (`xs`, `sm` … `3xl`, auch mit `max-`/`min-`).
 */
const BREITEN = new Set(Object.keys(resolveConfig(tailwindConfig).theme.screens))

const quellen = import.meta.glob(['/src/**/*.{ts,tsx}', '!/src/**/*.test.{ts,tsx}'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const PRAEFIX = /(?<![\w:/[-])(?:max-|min-)?(x{1,2}s|sm|md|lg|x{1,2}l|[2-9]xl):(?=[!\w[-])/g

export function unbekannteBreiten(pfad: string, text: string, breiten: Set<string>): string[] {
  const funde: string[] = []
  for (const treffer of text.matchAll(PRAEFIX)) {
    if (breiten.has(treffer[1])) continue
    const zeile = text.slice(0, treffer.index).split('\n').length
    funde.push(`${pfad}:${zeile} ${treffer[0]}`)
  }
  return funde
}

describe('Tailwind-Breiten', () => {
  it('nutzt nur Breiten, die tailwind.config kennt', () => {
    const funde = Object.entries(quellen).flatMap(([pfad, text]) => unbekannteBreiten(pfad, text, BREITEN))
    expect(funde).toEqual([])
  })

  it('erkennt unbekannte Breiten und lässt bekannte und Fremdes stehen', () => {
    const probe = `className="hidden xs:inline sm:flex max-sm:h-11 3xl:block hover:md:x"\nconst url = 'https://x.lg:8080'`
    expect(unbekannteBreiten('probe.tsx', probe, new Set(['sm', 'md', 'lg', 'xl', '2xl']))).toEqual([
      'probe.tsx:1 xs:',
      'probe.tsx:1 3xl:',
    ])
  })
})
