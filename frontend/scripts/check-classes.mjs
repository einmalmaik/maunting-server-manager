import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'

/**
 * Klassen, die nichts rendern, fallen niemandem auf.
 *
 * Ein fehlender Schatten sieht aus wie ein flaches Design, eine fehlende
 * Animation wie eine schnelle, und ein fehlender Modal-Hintergrund wie eine
 * Designentscheidung. Genau deshalb lagen 80 solcher Stellen monatelang im
 * Quelltext: `shadow-xs` (55x), `backdrop-blur-xs` (9x), `animate-in` (12x),
 * `bg-scrim`, `msm-badge-warn`, `msm-badge-neutral`.
 *
 * Zwei Ursachen, beide hier gesperrt:
 *
 * 1. Tailwind-v4-Namen auf einem v3.4-Build. `shadow-xs`, `backdrop-blur-xs`
 *    und `blur-xs` heissen in v3 `-sm`. Wer ein Beispiel aus der v4-Doku
 *    kopiert, bekommt stillschweigend nichts.
 * 2. Vokabular eines Plugins, das nicht eingebunden ist. `animate-in`,
 *    `fade-in`, `zoom-in-95`, `slide-in-from-bottom-2` gehoeren zu
 *    `tailwindcss-animate`; `plugins: []` in tailwind.config.ts.
 *
 * Dazu die Gegenprobe: jede `msm-*`-Klasse im Quelltext muss in index.css
 * stehen, und jede `animate-*`-Klasse in tailwind.config.ts oder index.css.
 */

const root = process.cwd()
const sourceDir = path.join(root, 'src')
const cssFile = path.join(root, 'src', 'index.css')
const configFile = path.join(root, 'tailwind.config.ts')

const isTestFile = name => /\.(?:test|spec)\.(?:ts|tsx)$/.test(name)

async function sourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const files = await Promise.all(entries.map(entry => {
    const current = path.join(directory, entry.name)
    if (entry.isDirectory()) return entry.name === 'node_modules' ? [] : sourceFiles(current)
    if (isTestFile(entry.name)) return []
    return /\.(?:ts|tsx)$/.test(entry.name) ? [current] : []
  }))
  return files.flat()
}

/** Namen, die auf diesem Build nichts erzeugen. Wert = was stattdessen gilt. */
const GESPERRT = new Map([
  ['shadow-xs', 'shadow-sm (Tailwind v3 kennt kein xs)'],
  ['backdrop-blur-xs', 'backdrop-blur-sm'],
  ['blur-xs', 'blur-sm'],
  ['animate-in', 'animate-fade-in / animate-scale-in / animate-slide-up'],
  ['animate-out', 'es gibt keine Ausblendung; Element entfernen'],
  ['zoom-in', 'animate-scale-in'],
  ['slide-in-from-bottom', 'animate-slide-up'],
  ['slide-in-from-top', 'animate-slide-up'],
  ['slide-in-from-left', 'animate-slide-in-left'],
  ['bg-scrim', 'msm-modal-overlay'],
])

const css = await readFile(cssFile, 'utf8')
const config = await readFile(configFile, 'utf8')

// Was index.css als .msm-* definiert, und was die Config als animation kennt.
const definierteMsm = new Set([...css.matchAll(/^\s*\.(msm-[a-z0-9-]+)/gm)].map(m => m[1]))
const definierteAnimationen = new Set([
  // Tailwind-Kern
  'animate-spin', 'animate-ping', 'animate-pulse', 'animate-bounce', 'animate-none',
  // eigene Utilities direkt in index.css
  ...[...css.matchAll(/^\s*\.(animate-[a-z0-9-]+)/gm)].map(m => m[1]),
  // extend.animation in tailwind.config.ts
  ...[...config.matchAll(/^\s*'([a-z0-9-]+)':\s*'[a-z0-9-]+\s+\d+m?s/gm)].map(m => `animate-${m[1]}`),
])

const fehler = []
const klassenMuster = /class(?:Name)?=(?:"([^"]*)"|'([^']*)'|\{`([^`]*)`\})/g

for (const file of await sourceFiles(sourceDir)) {
  const source = await readFile(file, 'utf8')
  const relativ = path.relative(root, file).replace(/\\/g, '/')

  for (const treffer of source.matchAll(klassenMuster)) {
    const klassen = (treffer[1] ?? treffer[2] ?? treffer[3] ?? '')
      .split(/[\s\n]+/)
      .map(k => k.replace(/^(?:hover|focus|active|group-hover|peer-focus|sm|md|lg|xl|2xl|dark):/, ''))
      .filter(Boolean)

    for (const klasse of klassen) {
      const basis = klasse.replace(/[/-]\d+$/, '')
      const ersatz = GESPERRT.get(klasse) ?? GESPERRT.get(basis)
      if (ersatz) fehler.push(`${relativ}: "${klasse}" erzeugt kein CSS — stattdessen ${ersatz}`)

      if (/^msm-[a-z0-9-]+$/.test(klasse) && !definierteMsm.has(klasse)) {
        fehler.push(`${relativ}: "${klasse}" ist in src/index.css nicht definiert`)
      }
      if (/^animate-[a-z0-9-]+$/.test(klasse) && !definierteAnimationen.has(klasse)) {
        fehler.push(`${relativ}: "${klasse}" ist weder in tailwind.config.ts noch in src/index.css definiert`)
      }
    }
  }
}

if (fehler.length) {
  console.error(`\nKlassen ohne Wirkung (${fehler.length}):\n  ${[...new Set(fehler)].join('\n  ')}\n`)
  process.exitCode = 1
} else {
  console.log(`classes: ${definierteMsm.size} msm-*, ${definierteAnimationen.size} animate-* definiert, keine toten Verweise`)
}
