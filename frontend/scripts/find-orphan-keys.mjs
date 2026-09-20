import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'

/**
 * Findet Übersetzungsschlüssel, die niemand liest.
 *
 * `check-i18n.mjs` prüft die eine Richtung: benutzt, aber nicht übersetzt. Das
 * ist die laute Hälfte — ein fehlender Schlüssel erscheint als roher Name auf
 * dem Bildschirm. Diese Datei prüft die leise: übersetzt, aber nie benutzt.
 * Davon gab es im September 2026 genau 555 von 4859 Schlüsseln, und ihr Schaden
 * ist nicht der Platz, sondern die Verwechslung: wer einen Text ändern will,
 * findet über die Suche den toten Zwilling zuerst und wundert sich, dass die
 * Oberfläche gleich bleibt.
 *
 * Das Ergebnis ist ein Vorschlag, keine Wahrheit. Die Oberfläche baut Schlüssel
 * zur Laufzeit zusammen (`t(`ai.tools.${name}`)`), und die dynamischen Präfixe
 * unten decken das nur grob ab. Deshalb gibt das Skript aus und löscht nicht:
 * jede Gruppe gehört vor dem Löschen gegen ihre Aufrufstelle gelesen.
 *
 *   node scripts/find-orphan-keys.mjs           # Gruppen mit Anzahl
 *   node scripts/find-orphan-keys.mjs --list    # jeder Schlüssel einzeln
 */

const root = process.cwd()
const sourceDir = path.join(root, 'src')
// Beide Ordner, nicht nur routers: die Fehlerschlüssel des KI-Streams
// (`ai.chat.errors.*`) entstehen in services/ai_stream/. Ein erster Anlauf las
// nur routers/ und hielt neun davon für tot — check-i18n.mjs hat sie gerettet,
// weil es hier schon länger beide Ordner kennt.
const backendDirs = [
  path.join(root, '..', 'backend', 'routers'),
  path.join(root, '..', 'backend', 'services'),
]

function flatten(value, prefix = '', target = new Set()) {
  for (const [key, child] of Object.entries(value)) {
    const next = prefix ? `${prefix}.${key}` : key
    if (child && typeof child === 'object' && !Array.isArray(child)) flatten(child, next, target)
    else target.add(next)
  }
  return target
}

async function files(directory, muster = /\.(?:ts|tsx)$/) {
  let entries
  try {
    entries = await readdir(directory, { withFileTypes: true })
  } catch {
    return []
  }
  const found = await Promise.all(entries.map(entry => {
    const current = path.join(directory, entry.name)
    if (entry.isDirectory()) return entry.name === 'node_modules' ? [] : files(current, muster)
    return muster.test(entry.name) ? [current] : []
  }))
  return found.flat()
}

const de = flatten(JSON.parse(await readFile(path.join(root, 'src/locales/de.json'), 'utf8')))

// Nicht nur `t('…')`: ein Schlüssel wird genauso oft in einer Variablen
// abgelegt und erst später übersetzt — `{ labelKey: 'aiSettings.dailyTokens' }`
// in AiTab.tsx, `issues.set(row.id, 'blueprintBuilder.validation.envEmpty')` in
// BlueprintBuilderEditors.tsx. Eine Suche nur über Aufrufstellen hielt beide
// für tot und hätte 66 gültige Fehlermeldungen gelöscht.
//
// Also jedes Zeichenkettenliteral, das wie ein Schlüssel aussieht, aus dem
// ganzen Quelltext. Das meldet zu wenig statt zu viel — die richtige Richtung,
// wenn am Ende gelöscht wird.
const literal = new Set()
const praefixe = new Set()
for (const file of await files(sourceDir)) {
  const source = await readFile(file, 'utf8')
  for (const m of source.matchAll(/['"`]([A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)+)['"`]/g)) literal.add(m[1])
  // t(`ai.tools.${name}`) — alles unter dem Präfix gilt als möglicherweise benutzt
  for (const m of source.matchAll(/`([A-Za-z0-9_.-]*?)\$\{/g)) if (m[1] && m[1].includes('.')) praefixe.add(m[1])
}
// Das Backend schickt Schlüssel über `message_key` oder als Tupel neben dem
// Fehlercode; sie stehen in keinem t()-Aufruf. Deshalb jedes Literal, das wie
// ein Schlüssel aussieht — dieselbe grosszügige Lesart wie im Frontend.
for (const dir of backendDirs) {
  for (const file of await files(dir, /\.py$/)) {
    const source = await readFile(file, 'utf8')
    for (const m of source.matchAll(/["']([A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)+)["']/g)) literal.add(m[1])
  }
}

const benutzt = key =>
  literal.has(key) ||
  // Pluralformen werden unter dem Basisnamen aufgerufen
  literal.has(key.replace(/_(?:one|other|zero|few|many|two)$/, '')) ||
  [...praefixe].some(p => key.startsWith(p))

const verwaist = [...de].filter(key => !benutzt(key)).sort()

if (process.argv.includes('--list')) {
  verwaist.forEach(key => console.log(key))
} else {
  const gruppen = new Map()
  for (const key of verwaist) {
    const gruppe = key.split('.').slice(0, 2).join('.')
    gruppen.set(gruppe, (gruppen.get(gruppe) ?? 0) + 1)
  }
  for (const [gruppe, anzahl] of [...gruppen].sort((a, b) => b[1] - a[1])) {
    console.log(String(anzahl).padStart(5), gruppe)
  }
}
console.log(`\n${verwaist.length} von ${de.size} Schlüsseln werden nirgends gelesen.`)
console.log(`${praefixe.size} dynamische Präfixe schützen ihre Unterbäume — prüfe sie einzeln, bevor du löschst.`)
