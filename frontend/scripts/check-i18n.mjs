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

for (const [code, keys] of locales) {
  if (code === 'en' || code === 'de') continue
  const extras = [...keys.keys()].filter(key => !enKeys.has(key))
  if (extras.length) errors.push(`${code}.json has unknown keys:\n  ${extras.join('\n  ')}`)
  const covered = [...keys.keys()].filter(key => enKeys.has(key)).length
  const coverage = enKeys.size === 0 ? 100 : (covered / enKeys.size) * 100
  console.log(`${code}: ${covered}/${enKeys.size} keys (${coverage.toFixed(1)}% coverage; English fallback for the rest)`)
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
// The operator saw a raw key instead of a sentence, across eleven locales.
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

// Checked against en and de only — the other nine locales are deliberate partial
// subsets with English fallback, and demanding completeness there would be wrong.
const missingReferenced = [...referenced].filter(key => !enKeys.has(key) || !deKeys.has(key)).sort()
if (missingReferenced.length) errors.push(`UI references keys missing from the en/de base locales:\n  ${missingReferenced.join('\n  ')}`)

if (errors.length) {
  console.error(`\n${errors.join('\n\n')}\n`)
  process.exitCode = 1
} else {
  console.log(`en/de parity: ${enKeys.size} keys`)
  console.log(`referenced literal UI keys: ${referenced.size}`)
}
