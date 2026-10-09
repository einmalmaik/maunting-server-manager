// Holt autoconsent von DuckDuckGo in einer festen Fassung nach
// `src-tauri/src/cookies/autoconsent/` (cookies.rs). Die Dateien bleiben unverändert,
// damit sie sich gegen das npm-Paket vergleichen lassen.
//
// Neue Fassung: VERSION und INTEGRITAET anheben
// (`npm view @duckduckgo/autoconsent@<fassung> dist.integrity`), dann
// `node browser/scripts/autoconsent-holen.mjs` und die Tests in cookies.rs.
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { copyFileSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const VERSION = '16.49.0'
const INTEGRITAET = 'sha512-NzeH9NBf4p+1FQgFwhHxjXT8g13KsNk0375QFddQOMDgAgghukGjclqt1oAJOzJF3O95yQKgv9cnWXjw8vg3OA=='
const DATEIEN = {
  'package/dist/autoconsent.esm.js': 'autoconsent.esm.js',
  'package/rules/compact-rules.json': 'compact-rules.json',
  'package/LICENSE': 'LICENSE',
}

const ziel = resolve(dirname(fileURLToPath(import.meta.url)), '../src-tauri/src/cookies/autoconsent')
const arbeit = mkdtempSync(join(tmpdir(), 'autoconsent-'))
try {
  const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm'
  const name = execFileSync(npm, ['pack', `@duckduckgo/autoconsent@${VERSION}`, '--pack-destination', arbeit], {
    encoding: 'utf-8',
    shell: process.platform === 'win32',
    stdio: ['ignore', 'pipe', 'ignore'],
  })
    .trim()
    .split('\n')
    .pop()
  const paket = join(arbeit, name)
  const ist = 'sha512-' + createHash('sha512').update(readFileSync(paket)).digest('base64')
  if (ist !== INTEGRITAET) throw new Error(`Prüfsumme passt nicht: ${ist}`)
  // Relativ: GNU tar hielte `D:` für einen fremden Rechner.
  execFileSync('tar', ['-xzf', name, ...Object.keys(DATEIEN)], { cwd: arbeit })
  for (const [von, nach] of Object.entries(DATEIEN)) copyFileSync(join(arbeit, von), join(ziel, nach))
  console.log(`autoconsent ${VERSION} liegt in ${ziel}`)
} finally {
  rmSync(arbeit, { recursive: true, force: true })
}
