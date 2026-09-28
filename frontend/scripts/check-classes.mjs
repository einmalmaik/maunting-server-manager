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
 *
 * Seit 09/2026 kommt eine dritte Pruefung dazu: Roh-Paletten. `text-emerald-400`
 * rendert zwar, sagt aber nichts — und weil es rendert, faellt es erst auf, wenn
 * dasselbe Gruen an der naechsten Stelle `text-status-success` heisst. Genau so
 * entstanden vier Rottoene, drei Gelbtoene und zwei Gruentoene nebeneinander.
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

/**
 * Roh-Paletten von Tailwind, wo ein Token gehoert. `from-`, `via-` und `to-`
 * fehlen in der Praefixliste mit Absicht: ein Verlauf ist Schmuck und darf
 * jede Farbe haben.
 */
const ROHFARBE = new RegExp(
  '(?<![\\w-])(?:(?:hover|focus|focus-visible|active|group-hover|peer-focus|peer-checked|disabled|sm|md|lg|xl|2xl|dark):)*' +
  '(?:bg|text|border|ring|shadow|fill|stroke|divide|outline|decoration|placeholder|accent|caret)-' +
  '(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-' +
  '\\d{2,3}(?:/\\d{1,3})?(?![\\w-])',
  'g',
)

/**
 * Die einzige Datei, in der Roh-Paletten stehen duerfen: die sechs
 * Auswahlfarben fuer Kalender und Notizen. Drei davon bedeuten nichts und
 * haben deshalb auch keinen Status-Token.
 */
const ROHFARBEN_ERLAUBT = new Set(['src/config/farbpalette.ts'])

/** Tokennamen, die es einmal gab. Wert = was sie heute heissen. */
const ABGESCHAFFT = new Map([
  ['status-error', 'status-destructive'],
  ['status-danger', 'status-destructive'],
  ['status-info', 'primary'],
  ['destructive', 'status-destructive'],
  ['deep-background', 'background'],
])
const ABGESCHAFFT_MUSTER = new RegExp(
  '(?<![\\w-])(?:(?:hover|focus|focus-visible|active|group-hover|peer-focus|peer-checked|disabled|sm|md|lg|xl|2xl|dark):)*' +
  '(?:bg|text|border|ring|shadow|fill|stroke|divide|outline|decoration|placeholder|accent|caret|from|via|to)-' +
  `(${[...ABGESCHAFFT.keys()].join('|')})(?:/\\d{1,3})?(?![\\w-])`,
  'g',
)

/**
 * Schriftgroessen unterhalb der Skala. 8 bis 11 Pixel lagen unter jeder
 * definierten Stufe und an der Grenze des Lesbaren; `text-label-sm` (12 px)
 * ist die Untergrenze.
 */
const ZU_KLEIN = /(?<![\w-])text-\[(?:[1-9]|1[01])px\](?![\w-])/g

/**
 * Halbe Schritte gibt es in Tailwinds Abstandsskala nur bis 3.5 — `h-8.5`
 * erzeugt nichts, und der Knopf bleibt so hoch, wie er ohnehin war. Genau so
 * stand der „Neue Notiz"-Knopf auf 32 px neben zwei 40-px-Feldern.
 */
const HALBER_SCHRITT = /(?<![\w-])(?:[wh]|p[xytrbl]?|m[xytrbl]?|gap(?:-[xy])?|space-[xy]|inset|top|right|bottom|left|min-[wh]|max-[wh])-(?:[4-9]|[1-9]\d)\.5(?![\w-])/g

/** `text-<rolle>-<stufe>` — jede Stufe muss in der Config stehen. */
const SKALA_NAME = /(?<![\w-])text-(display|headline|title|body|label|mono)-([a-z-]+)(?![\w-])/g

const css = await readFile(cssFile, 'utf8')
const config = await readFile(configFile, 'utf8')

// Was `extend.fontSize` definiert.
const fontSizeBlock = config.slice(config.indexOf('fontSize: {'))
const definierteStufen = new Set(
  [...fontSizeBlock.slice(0, fontSizeBlock.indexOf('\n      },')).matchAll(/^\s*'([a-z0-9-]+)':\s*\[/gm)]
    .map(m => m[1]),
)

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

  // Diese beiden Pruefungen lesen die ganze Datei, nicht nur `className=`:
  // Klassennamen stehen oft in einer Variablen oder einer Tabelle.
  if (!ROHFARBEN_ERLAUBT.has(relativ)) {
    for (const treffer of source.matchAll(ROHFARBE)) {
      fehler.push(`${relativ}: "${treffer[0]}" ist eine Roh-Palette — nimm den Token der Bedeutung (status-*, primary, secondary, surface-*)`)
    }
  }
  for (const treffer of source.matchAll(ABGESCHAFFT_MUSTER)) {
    fehler.push(`${relativ}: "${treffer[0]}" gibt es nicht mehr — heute ${ABGESCHAFFT.get(treffer[1])}`)
  }
  for (const treffer of source.matchAll(HALBER_SCHRITT)) {
    fehler.push(`${relativ}: "${treffer[0]}" erzeugt kein CSS — halbe Schritte gibt es nur bis 3.5`)
  }
  for (const treffer of source.matchAll(ZU_KLEIN)) {
    fehler.push(`${relativ}: "${treffer[0]}" liegt unter der Skala — text-label-sm (12px) ist die Untergrenze`)
  }
  for (const treffer of source.matchAll(SKALA_NAME)) {
    const stufe = `${treffer[1]}-${treffer[2]}`
    if (!definierteStufen.has(stufe)) {
      fehler.push(`${relativ}: "${treffer[0]}" ist in tailwind.config.ts unter fontSize nicht definiert — erzeugt keine Schriftgroesse`)
    }
  }

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
