import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'

const root = process.cwd()
const localesDir = path.join(root, 'src', 'locales')
const sourceDir = path.join(root, 'src')

function flatten(value, prefix = '', target = new Map()) {
  for (const [key, child] of Object.entries(value)) {
    const next = prefix ? `${prefix}.${key}` : key
    if (child && typeof child === 'object' && !Array.isArray(child)) flatten(child, next, target)
    else target.set(next, child)
  }
  return target
}

async function readJson(file) {
  try {
    return JSON.parse(await readFile(file, 'utf8'))
  } catch (error) {
    throw new Error(`${path.relative(root, file)} is not valid JSON: ${error.message}`)
  }
}

// Tests are not the UI. `i18n.test.ts` asks on purpose what happens with a key
// that does not exist — reading that as "the UI references this key" would turn
// a correct test into a build error and push someone to invent the key.
const isTestFile = name => /\.(?:test|spec)\.(?:ts|tsx)$/.test(name)

async function sourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const files = await Promise.all(entries.map(entry => {
    const current = path.join(directory, entry.name)
    if (entry.isDirectory()) return entry.name === '__tests__' ? [] : sourceFiles(current)
    if (isTestFile(entry.name)) return []
    return /\.(?:ts|tsx)$/.test(entry.name) ? [current] : []
  }))
  return files.flat()
}

const localeNames = (await readdir(localesDir)).filter(name => name.endsWith('.json')).sort()
const locales = new Map()
for (const name of localeNames) locales.set(name.slice(0, -5), flatten(await readJson(path.join(localesDir, name))))

const en = locales.get('en')
const de = locales.get('de')
if (!en || !de) throw new Error('src/locales/en.json and de.json are required')

const errors = []
const enKeys = new Set(en.keys())
const deKeys = new Set(de.keys())
const missingInDe = [...enKeys].filter(key => !deKeys.has(key))
const missingInEn = [...deKeys].filter(key => !enKeys.has(key))
if (missingInDe.length) errors.push(`de.json misses keys from en.json:\n  ${missingInDe.join('\n  ')}`)
if (missingInEn.length) errors.push(`de.json has keys unknown to en.json:\n  ${missingInEn.join('\n  ')}`)

// Genau zwei Sprachen, und die Prüfung sagt es laut.
//
// Bis 09/2026 lagen hier elf Dateien, neun davon mit 772 von 4859 Schlüsseln.
// Sie waren nicht als Entwurf gekennzeichnet, sondern über ein `import.meta.glob`
// automatisch aktiv: ein Browser mit arabischer Spracheinstellung bekam sie
// ausgeliefert. Eine halbe Übersetzung sieht in der Oberfläche nicht nach
// „unfertig" aus, sondern nach einem Fehler in jedem zweiten Satz.
//
// Wer eine Sprache zurückbringen will, muss deshalb hier vorbei — und an dieser
// Stelle steht die Bedingung: vollständig, und von jemandem, der sie spricht.
const ERLAUBTE_SPRACHEN = ['de', 'en']
const unerwartet = [...locales.keys()].filter(code => !ERLAUBTE_SPRACHEN.includes(code))
if (unerwartet.length) {
  errors.push(
    `src/locales/ holds locales beyond de/en: ${unerwartet.join(', ')}.\n` +
      `  A partial locale is worse than none — it ships broken sentences under a real language name.\n` +
      `  Add it to ERLAUBTE_SPRACHEN in this script only once it is complete.`,
  )
}

const referenced = new Set()
const literalKeyPattern = /(?:\bt|\bi18n\.t)\(\s*['"]([A-Za-z0-9_.-]+)['"]/g
for (const file of await sourceFiles(sourceDir)) {
  const source = await readFile(file, 'utf8')
  for (const match of source.matchAll(literalKeyPattern)) referenced.add(match[1])
}

// The backend ships translation keys too. Every AI stream error carries a
// `message_key` that the chat renders verbatim — and for a long time not one of
// the ten it sent existed, because they were written as `ai.errors.*` while the
// texts live under `ai.chat.errors.*`. The check above could not see it: those
// keys never appear in a literal `t('…')` call, they arrive over SSE at runtime.
// The operator saw a raw key instead of a sentence, in both locales.
//
// The pattern is deliberately narrow. A broad `ai\.` would also match the audit
// action names (`ai.action.proposed`, `ai.tool.read`) and report them as missing
// translations — which is exactly why the obvious version of this check does not
// work. A third, entirely different prefix would still slip through; that is the
// accepted limit of a grep.
const backendDir = path.join(root, '..', 'backend')
const backendKeyPattern = /["'](ai\.(?:chat\.)?errors\.[A-Za-z0-9_.]+)["']/g
async function backendFiles(directory) {
  let entries
  try {
    entries = await readdir(directory, { withFileTypes: true })
  } catch {
    return []  // Frontend-only checkout — nothing to compare against.
  }
  const files = await Promise.all(entries.map(entry => {
    if (entry.name === '__pycache__' || entry.name === 'venv' || entry.name === 'migrations') return []
    const current = path.join(directory, entry.name)
    if (entry.isDirectory()) return backendFiles(current)
    return entry.name.endsWith('.py') ? [current] : []
  }))
  return files.flat()
}
const backendReferenced = new Set()
for (const file of await backendFiles(path.join(backendDir, 'services'))) {
  const source = await readFile(file, 'utf8')
  for (const match of source.matchAll(backendKeyPattern)) backendReferenced.add(match[1])
}
for (const file of await backendFiles(path.join(backendDir, 'routers'))) {
  const source = await readFile(file, 'utf8')
  for (const match of source.matchAll(backendKeyPattern)) backendReferenced.add(match[1])
}
for (const key of backendReferenced) referenced.add(key)

// Ein Pluralschlüssel liegt in der Locale nur als `_one`/`_other` vor, aufgerufen
// wird er im Code aber unter dem Basisnamen: `t('ai.chat.toolsUsed', { count })`.
// Nur die Suffixform zu suchen, meldet einen vorhandenen Schlüssel als fehlend —
// und verleitet dazu, einen flachen Zwilling zu erfinden, den i18next mit
// JSON-v4 nie anzeigt. `_zero/_few/_many` kennen en und de nicht.
const vorhanden = (keys, key) => keys.has(key) || keys.has(`${key}_one`) || keys.has(`${key}_other`)

// en and de are the only locales, and both must be complete: a key the UI asks
// for and neither language answers shows up as the raw key name on screen.
const missingReferenced = [...referenced].filter(key => !vorhanden(enKeys, key) || !vorhanden(deKeys, key)).sort()
if (missingReferenced.length) errors.push(`UI references keys missing from the en/de base locales:\n  ${missingReferenced.join('\n  ')}`)

if (errors.length) {
  console.error(`\n${errors.join('\n\n')}\n`)
  process.exitCode = 1
} else {
  console.log(`en/de parity: ${enKeys.size} keys`)
  console.log(`referenced literal UI keys: ${referenced.size}`)
}
