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

async function sourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const files = await Promise.all(entries.map(entry => {
    const current = path.join(directory, entry.name)
    if (entry.isDirectory()) return sourceFiles(current)
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
const missingReferenced = [...referenced].filter(key => !enKeys.has(key) || !deKeys.has(key)).sort()
if (missingReferenced.length) errors.push(`UI references keys missing from the en/de base locales:\n  ${missingReferenced.join('\n  ')}`)

if (errors.length) {
  console.error(`\n${errors.join('\n\n')}\n`)
  process.exitCode = 1
} else {
  console.log(`en/de parity: ${enKeys.size} keys`)
  console.log(`referenced literal UI keys: ${referenced.size}`)
}
